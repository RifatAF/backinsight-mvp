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

fun fMoney(v: Int): String = "%,d".format(RU, v).replace(' ', ' ') + " ₽"

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

/** Текст для отправки клиенту в мессенджер. */
fun reportText(d: AppData, c: Client): String {
    val b = balance(d, c.id)
    val pack = b.current ?: d.packs.filter { it.clientId == c.id }.maxByOrNull { it.paidAt }
    val sb = StringBuilder()
    sb.append("Тренировки: ${c.name}\n")
    if (pack != null) {
        sb.append("Пакет ${trainings(pack.sessions)}, оплачен ${fDateY(pack.paidAt)}\n\n")
        visitsOfPack(d, pack).forEachIndexed { i, v -> sb.append("${i + 1}. ${visitLine(v)}\n") }
    } else {
        d.visits.filter { it.clientId == c.id }.sortedBy { it.start }.takeLast(20)
            .forEachIndexed { i, v -> sb.append("${i + 1}. ${visitLine(v)}\n") }
    }
    sb.append('\n')
    sb.append(
        when {
            b.left > 0 -> "Осталось: ${trainings(b.left)}"
            b.left == 0 -> "Пакет закончился"
            else -> "Сверх оплаты: ${trainings(-b.left)}"
        }
    )
    return sb.toString()
}
