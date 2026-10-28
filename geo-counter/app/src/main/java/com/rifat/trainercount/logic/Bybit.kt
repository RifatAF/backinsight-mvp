package com.rifat.trainercount.logic

import kotlin.math.roundToInt

/** Входящий перевод внутри Bybit (по UID, e-mail или телефону). */
data class Deposit(
    val id: String,
    val coin: String,
    val amount: Double,
    /** 1 = в обработке, 2 = зачислен, 3 = ошибка. */
    val status: Int,
    /** E-mail или телефон отправителя. */
    val address: String,
    val fromUid: String,
    val time: Long,
)

sealed class PayEvent {
    abstract val dep: Deposit
    /** Оплата записана клиенту. */
    data class Paid(val client: Client, val sessions: Int, override val dep: Deposit) : PayEvent()
    /** Клиент найден, но непонятно, сколько занятий: нет цены или сумма меньше одного занятия. */
    data class Unclear(val client: Client, override val dep: Deposit) : PayEvent()
    /** Перевод от того, кто не привязан ни к одному клиенту. */
    data class Unknown(override val dep: Deposit) : PayEvent()
}

val PAY_COINS = setOf("USDT", "USDC")

fun bybitPackId(dep: Deposit) = "bb:" + dep.id

fun payerOf(d: AppData, dep: Deposit): Client? = d.clients.firstOrNull { c ->
    val id = c.payId.trim()
    !c.archived && c.autoPay && id.isNotEmpty() &&
        (id == dep.fromUid || id.equals(dep.address.trim(), ignoreCase = true))
}

/**
 * Записать оплаты из Bybit. since: переводы раньше этого времени не трогаем (до подключения).
 * Повторно один перевод не записывается: у оплаты id «bb:<id перевода>».
 */
fun applyDeposits(d: AppData, deps: List<Deposit>, since: Long): Pair<AppData, List<PayEvent>> {
    var nd = d
    val events = mutableListOf<PayEvent>()
    for (dep in deps.sortedBy { it.time }) {
        if (dep.status != 2 || dep.time < since || dep.coin.uppercase() !in PAY_COINS) continue
        if (nd.packs.any { it.id == bybitPackId(dep) }) continue
        val c = payerOf(nd, dep)
        if (c == null) { events += PayEvent.Unknown(dep); continue }
        val price = pricePerSession(nd, c)
        val sessions = if (price > 0) (dep.amount / price).roundToInt() else 0
        if (sessions < 1) { events += PayEvent.Unclear(c, dep); continue }
        nd = nd.copy(packs = nd.packs + Pack(bybitPackId(dep), c.id, dep.time, sessions, dep.amount.roundToInt()))
        events += PayEvent.Paid(c, sessions, dep)
    }
    return nd to events
}

fun fCoin(dep: Deposit): String =
    (if (dep.amount % 1.0 == 0.0) dep.amount.toLong().toString() else "%.2f".format(java.util.Locale.US, dep.amount)) + " " + dep.coin

fun payerText(dep: Deposit) = listOf(dep.address, dep.fromUid.takeIf { it.isNotBlank() }?.let { "UID $it" })
    .filterNotNull().filter { it.isNotBlank() }.joinToString(", ").ifBlank { "неизвестного отправителя" }

/** Сообщение клиенту в бот после автоматического подтверждения. */
fun paidClientText(d: AppData, e: PayEvent.Paid): String =
    "Оплата получена ✓ ${fCoin(e.dep)}, это ${lessons(e.sessions)}. Спасибо!\n" + clientBalanceText(d, e.client)
