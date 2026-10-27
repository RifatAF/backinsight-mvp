package com.rifat.trainercount.logic

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val RU = Locale("ru")
private val dateF = DateTimeFormatter.ofPattern("dd.MM", RU)
private val dateYF = DateTimeFormatter.ofPattern("dd.MM.yyyy", RU)
private val timeF = DateTimeFormatter.ofPattern("HH:mm", RU)
private val dowF = DateTimeFormatter.ofPattern("EE", RU)

private fun z(t: Long) = Instant.ofEpochMilli(t).atZone(ZoneId.systemDefault())

fun fDate(t: Long): String = z(t).format(dateF)
fun fDateY(t: Long): String = z(t).format(dateYF)
fun fTime(t: Long): String = z(t).format(timeF)
fun fDow(t: Long): String = z(t).format(dowF).replace(".", "")

fun fDur(min: Long): String {
    val h = min / 60
    val m = min % 60
    return when {
        h == 0L -> "$m мин"
        m == 0L -> "$h ч"
        else -> "$h ч $m мин"
    }
}

fun fMoney(v: Int): String = "$" + "%,d".format(Locale.US, v)

fun plural(n: Int, one: String, few: String, many: String): String {
    val a = kotlin.math.abs(n) % 100
    val b = a % 10
    return when {
        a in 11..14 -> many
        b == 1 -> one
        b in 2..4 -> few
        else -> many
    }
}

fun trainings(n: Int) = "$n " + plural(n, "тренировка", "тренировки", "тренировок")

fun visitLine(v: Visit): String {
    val time = if (v.kind == VisitKind.AUTO) "${fTime(v.start)}–${fTime(v.end)}" else ""
    val tags = buildList {
        if (v.count > 1) add("×${v.count}")
        if (v.kind == VisitKind.BURNED) add("отмена, списано")
        if (v.members.isNotEmpty()) add(v.members.joinToString(", "))
    }
    return listOf("${fDate(v.start)} ${fDow(v.start)}", time, tags.joinToString(" · "))
        .filter { it.isNotBlank() }.joinToString("  ")
}

fun lessons(n: Int) = "$n " + plural(n, "занятие", "занятия", "занятий")

private fun dowCap(t: Long) = fDow(t).replaceFirstChar { it.uppercase() }

/**
 * Текст для отправки клиенту. Показывает текущий оплаченный период: оплаты, которые ещё не
 * израсходованы, и занятия в счёт них с порядковыми номерами. Если оплата закончилась, показывает
 * занятия сверх оплаты.
 */
fun reportText(d: AppData, c: Client): String {
    if (c.perVisit) return perVisitReport(d, c)
    val packs = d.packs.filter { it.clientId == c.id }.sortedBy { it.paidAt }
    val visits = d.visits.filter { it.clientId == c.id }.sortedBy { it.start }
    val used = visits.sumOf { it.count }
    val paid = packs.sumOf { it.sessions }

    // Начало периода: сколько занятий покрыто полностью израсходованными оплатами.
    var from = 0
    val active = mutableListOf<Pack>()
    for (p in packs) {
        if (from + p.sessions <= used && active.isEmpty()) from += p.sessions else active += p
    }
    val last = packs.lastOrNull()
    // Оплата закончилась ровно: показываем последний пакет целиком.
    if (active.isEmpty() && used == paid && last != null) {
        from = paid - last.sessions
        active += last
    }

    val sb = StringBuilder()
    sb.append("${c.name}: занятия\n")
    when {
        packs.isEmpty() -> sb.append("Оплат пока не было\n")
        active.isNotEmpty() -> {
            val sum = active.sumOf { it.sessions }
            sb.append(
                if (active.size == 1) "Оплачено: ${lessons(sum)} (${fDateY(active[0].paidAt)})\n"
                else "Оплачено: ${lessons(sum)} (" + active.joinToString(" + ") { "${it.sessions} от ${fDate(it.paidAt)}" } + ")\n"
            )
        }
        else -> sb.append("Последняя оплата: ${lessons(last!!.sessions)} (${fDateY(last.paidAt)})\n")
    }

    // Занятия периода с номерами: визит на 2 занятия получает «3–4».
    val lines = mutableListOf<String>()
    var pos = 0
    for (v in visits) {
        val a = pos
        pos += v.count
        val start = maxOf(a, from)
        if (pos <= from) continue
        val n1 = start - from + 1
        val n2 = pos - from
        val num = if (n1 == n2) "$n1" else "$n1–$n2"
        val details = buildList {
            if (v.kind == VisitKind.BURNED) add("отмена, занятие списано")
            if (v.members.isNotEmpty() && c.isFamily) add(v.members.joinToString(", "))
        }
        val time = if (v.kind == VisitKind.AUTO) ", ${fTime(v.start)}" else ""
        lines += "$num. ${dowCap(v.start)} ${fDate(v.start)}$time" + if (details.isEmpty()) "" else " · " + details.joinToString(" · ")
    }
    if (packs.isEmpty() && lines.size > 30) {
        val skip = lines.size - 30
        lines.subList(0, skip).clear()
        sb.append("…\n")
    }
    if (lines.isNotEmpty()) {
        sb.append('\n')
        lines.forEach { sb.append(it).append('\n') }
    }

    sb.append('\n')
    val inPeriod = used - from
    val left = paid - used
    when {
        packs.isEmpty() -> sb.append("Проведено: ${lessons(used)}")
        left > 0 -> sb.append("Проведено: $inPeriod из ${active.sumOf { it.sessions }}\nОсталось: ${lessons(left)}")
        left == 0 -> sb.append("Проведено: $inPeriod из ${active.sumOf { it.sessions }}\nОплаченные занятия закончились")
        else -> sb.append("Сверх оплаты: ${lessons(-left)}, нужна оплата")
    }
    return sb.toString()
}

/** Отчёт, когда клиент платит за каждое занятие: что не оплачено и на какую сумму. */
private fun perVisitReport(d: AppData, c: Client): String {
    val visits = d.visits.filter { it.clientId == c.id }.sortedBy { it.start }
    val paid = d.packs.filter { it.clientId == c.id }.sumOf { it.sessions }
    val used = visits.sumOf { it.count }
    val price = pricePerSession(d, c)
    fun line(v: Visit) = "${dowCap(v.start)} ${fDate(v.start)}" + (if (v.kind == VisitKind.AUTO) ", ${fTime(v.start)}" else "") +
        (if (v.count > 1) " · ${lessons(v.count)}" else "") +
        (if (v.members.isNotEmpty() && c.isFamily) " · " + v.members.joinToString(", ") else "")
    return buildString {
        append("${c.name}: занятия\n")
        if (used <= paid) {
            append("Все занятия оплачены ✓\n")
            visits.takeLast(3).takeIf { it.isNotEmpty() }?.let { last ->
                append("\nПоследние:\n"); last.forEach { append(line(it)).append('\n') }
            }
            return@buildString
        }
        // Неоплаченные: последние занятия сверх оплаченных.
        var skip = paid
        val open = mutableListOf<Visit>()
        for (v in visits) { if (skip >= v.count) skip -= v.count else { open += v; skip = 0 } }
        append("\nНе оплачено:\n")
        open.forEachIndexed { i, v -> append("${i + 1}. ${line(v)}\n") }
        val n = used - paid
        append("\nК оплате: ${lessons(n)}" + if (price > 0) ", ${fMoney(n * price)}" else "")
    }.trimEnd()
}
