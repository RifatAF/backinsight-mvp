package com.rifat.trainercount.logic

import java.time.LocalDate
import java.time.ZoneId

/**
 * Бот в Telegram без ИИ: понимает простые фразы по-русски.
 * «что в пятницу», «расписание на 30», «30.10 17:00 стоматолог Лера», «тридцатого десятого в 17 стоматолог»,
 * «свободно завтра?», «удали 30.10 стоматолог», «где Рифат».
 */
sealed class BotCmd {
    data object Help : BotCmd()
    data object Where : BotCmd()
    data class Plan(val date: LocalDate) : BotCmd()
    data class Week(val from: LocalDate) : BotCmd()
    data class Free(val date: LocalDate?) : BotCmd()
    data class Add(val date: LocalDate, val min: Int, val duration: Int, val title: String) : BotCmd()
    data class Remove(val date: LocalDate, val words: String) : BotCmd()
    data class Unknown(val reason: String) : BotCmd()
}

private val MONTHS = listOf("январ", "феврал", "март", "апрел", "ма[йя]", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
private fun monthOf(word: String): Int? = MONTHS.indexOfFirst { Regex("^$it").containsMatchIn(word) }.takeIf { it >= 0 }?.plus(1)
private val DOWS = listOf(
    listOf("понедельник", "пн"), listOf("вторник", "вт"), listOf("среду", "среда", "ср"), listOf("четверг", "чт"),
    listOf("пятницу", "пятница", "пт"), listOf("субботу", "суббота", "сб"), listOf("воскресенье", "вс"),
)
private val ORD = linkedMapOf(
    "перв" to 1, "втор" to 2, "трет" to 3, "четверт" to 4, "пят" to 5, "шест" to 6, "седьм" to 7, "восьм" to 8,
    "девят" to 9, "десят" to 10, "одиннадцат" to 11, "двенадцат" to 12, "тринадцат" to 13, "четырнадцат" to 14,
    "пятнадцат" to 15, "шестнадцат" to 16, "семнадцат" to 17, "восемнадцат" to 18, "девятнадцат" to 19,
    "двадцат" to 20, "тридцат" to 30,
)
private val ORD_END = "(?:ого|его|ьего|ое|ье|ий|ый|ой)"

private class Scan(text: String) {
    /** Исходный регистр для названия дела; индексы совпадают с s. */
    var orig = " " + text.replace('ё', 'е').replace('Ё', 'Е').replace(Regex("[,!?;]"), " ") + " "
    var s = orig.lowercase()
    fun cut(m: MatchResult) {
        s = s.substring(0, m.range.first) + " " + s.substring(m.range.last + 1)
        orig = orig.substring(0, m.range.first) + " " + orig.substring(m.range.last + 1)
    }
    /** Найти и вырезать первое совпадение. */
    fun take(re: Regex): MatchResult? = re.find(s)?.also(::cut)
    /** Вырезать первое совпадение, для которого f дал значение. */
    fun <T> takeIf(re: Regex, f: (MatchResult) -> T?): T? {
        for (m in re.findAll(s)) { val v = f(m) ?: continue; cut(m); return v }
        return null
    }
    fun has(re: Regex) = re.containsMatchIn(s)
    fun rest() = orig.replace(Regex("\\s+"), " ").trim()
}

private fun ordinal(word: String): Int? = ORD.entries.firstOrNull { Regex("^${it.key}$ORD_END$").matches(word) }?.value

/** Дата из фразы; вырезает найденное. */
private fun takeDate(sc: Scan, today: LocalDate): LocalDate? {
    sc.take(Regex("\\sпослезавтра\\s"))?.let { return today.plusDays(2) }
    sc.take(Regex("\\sзавтра\\s"))?.let { return today.plusDays(1) }
    sc.take(Regex("\\sсегодня\\s"))?.let { return today }
    // 30.10, 30/10, 30.10.2026
    sc.takeIf(Regex("\\s(\\d{1,2})[./](\\d{1,2})(?:[./](\\d{2,4}))?(?=\\s)")) { m ->
        val day = m.groupValues[1].toInt(); val mon = m.groupValues[2].toInt()
        val y = m.groupValues[3].takeIf { it.isNotEmpty() }?.toInt()?.let { if (it < 100) 2000 + it else it }
        if (mon in 1..12 && day in 1..31) dateOf(today, day, mon, y) else null
    }?.let { return it }
    // 30 октября
    sc.takeIf(Regex("\\s(\\d{1,2})(?:-?го)?\\s+([а-я]+)(?=\\s)")) { m ->
        monthOf(m.groupValues[2])?.let { dateOf(today, m.groupValues[1].toInt(), it, null) }
    }?.let { return it }
    // тридцатого (десятого), двадцать первого октября
    // Месяц после дня только порядковым числом до 12 или названием, иначе это уже не дата.
    sc.takeIf(Regex("\\s(?:(двадцать|тридцать)\\s+)?([а-я]+)\\s+([а-я]+)(?=\\s)")) { m ->
        val base = ordinal(m.groupValues[2]) ?: return@takeIf null
        val day = base + when (m.groupValues[1]) { "двадцать" -> 20; "тридцать" -> 30; else -> 0 }
        val t = m.groupValues[3]
        val mon = ordinal(t)?.takeIf { it in 1..12 } ?: monthOf(t) ?: return@takeIf null
        if (day in 1..31) dateOf(today, day, mon, null) else null
    }?.let { return it }
    sc.takeIf(Regex("\\s(?:(двадцать|тридцать)\\s+)?([а-я]+)(?=\\s)")) { m ->
        val base = ordinal(m.groupValues[2]) ?: return@takeIf null
        val day = base + when (m.groupValues[1]) { "двадцать" -> 20; "тридцать" -> 30; else -> 0 }
        if (day in 1..31) dateOf(today, day, null, null) else null
    }?.let { return it }
    // в пятницу
    for ((i, names) in DOWS.withIndex()) {
        if (sc.take(Regex("\\s(?:в\\s+|во\\s+)?(?:${names.joinToString("|")})(?=\\s)")) != null) {
            val diff = ((i + 1) - today.dayOfWeek.value + 7) % 7
            return today.plusDays(diff.toLong())
        }
    }
    // «на 30», «30-го», «30 числа»: голое число дня, не время
    sc.takeIf(Regex("\\s(?:на\\s+)?(\\d{1,2})(?:-?го|\\s+числа)(?=\\s)")) { dateOf(today, it.groupValues[1].toInt(), null, null) }?.let { return it }
    sc.takeIf(Regex("\\sна\\s+(\\d{1,2})(?=\\s)")) { dateOf(today, it.groupValues[1].toInt(), null, null) }?.let { return it }
    // Голое число: день месяца, если это не время («в 17», «17 часов», «до 18»).
    sc.takeIf(Regex("(?<=\\s)(\\d{1,2})(?=\\s)")) { m ->
        val before = sc.s.substring(0, m.range.first).trimEnd().substringAfterLast(' ')
        val after = sc.s.substring(m.range.last + 1).trimStart().substringBefore(' ')
        if (before in setOf("в", "к", "с", "до", "и") || Regex("^(ч|час|мин|утра|дня|вечера|ночи|\\d)").containsMatchIn(after)) null
        else dateOf(today, m.groupValues[1].toInt(), null, null)
    }?.let { return it }
    return null
}

/** День месяца без месяца: ближайший такой день, не раньше сегодня. */
private fun dateOf(today: LocalDate, day: Int, mon: Int?, year: Int?): LocalDate? {
    if (mon == null) {
        var m = today.withDayOfMonth(1)
        repeat(3) {
            if (day <= m.lengthOfMonth()) {
                val c = m.withDayOfMonth(day)
                if (c >= today) return c
            }
            m = m.plusMonths(1)
        }
        return null
    }
    var y = year ?: today.year
    val c = runCatching { LocalDate.of(y, mon, day) }.getOrNull() ?: return null
    if (year == null && c < today.minusDays(7)) y += 1
    return runCatching { LocalDate.of(y, mon, day) }.getOrNull()
}

/** Время и длительность: «17:00», «17.30», «в 17», «в 5 вечера», «с 17 до 18», «на 2 часа». */
private fun takeTime(sc: Scan): Pair<Int, Int?>? {
    fun hm(h: String, m: String?, part: String?): Int? {
        var hh = h.toInt(); val mm = m?.takeIf { it.isNotEmpty() }?.toInt() ?: 0
        if (part != null && (part.startsWith("вечер") || part.startsWith("дня")) && hh < 12) hh += 12
        return if (hh in 0..23 && mm in 0..59) hh * 60 + mm else null
    }
    val t = "(\\d{1,2})(?:[:.](\\d{2}))?"
    val part = "(?:\\s+(утра|дня|вечера|ночи))?"
    var from: Int? = null
    var to: Int? = null
    sc.take(Regex("\\s(?:с\\s+)?$t\\s*(?:-|–|до)\\s*$t(?=\\s)"))?.let { m ->
        from = hm(m.groupValues[1], m.groupValues[2], null); to = hm(m.groupValues[3], m.groupValues[4], null)
    }
    if (from == null) sc.take(Regex("\\s(\\d{1,2})[:](\\d{2})$part(?=\\s)"))?.let { from = hm(it.groupValues[1], it.groupValues[2], it.groupValues[3]) }
    if (from == null) sc.take(Regex("\\s(\\d{1,2})\\.(\\d{2})$part(?=\\s)"))?.let { from = hm(it.groupValues[1], it.groupValues[2], it.groupValues[3]) }
    if (from == null) sc.take(Regex("\\s(?:в|к)\\s+(\\d{1,2})(?:\\s+(\\d{2}))?(?:\\s*(?:ч|час|часа|часов))?$part(?=\\s)"))?.let {
        from = hm(it.groupValues[1], it.groupValues[2], it.groupValues[3])
    }
    val start = from ?: return null
    return start to to?.let { (it - start).takeIf { d -> d > 0 } }
}

/** «на 2 часа», «на час», «на 1.5 часа», «на 30 минут». */
private fun takeDuration(sc: Scan): Int? = sc.take(Regex("\\sна\\s+(?:(\\d+(?:[.,]5)?)\\s+)?(час|часа|часов|минут|мин)(?=\\s)"))?.let { m ->
    val n = m.groupValues[1].replace(',', '.').toDoubleOrNull() ?: 1.0
    if (m.groupValues[2].startsWith("мин")) n.toInt() else (n * 60).toInt()
}

fun parseBot(text: String, today: LocalDate): BotCmd {
    val sc = Scan(text)
    if (sc.has(Regex("^\\s*/?(start|help|помощь|команды|что умеешь)"))) return BotCmd.Help
    if (sc.has(Regex("\\s(где|/where)(?=\\s)"))) return BotCmd.Where
    val remove = sc.take(Regex("\\s(удали|удалить|отмени|отменить|убери|убрать)(?=\\s)")) != null
    val week = sc.take(Regex("\\s(?:на\\s+)?(?:эту\\s+|всю\\s+)?неделю(?=\\s)")) != null
    val free = sc.take(Regex("\\s(свобод[а-я]*|окн[а-я]*)(?=\\s)")) != null
    val dur = takeDuration(sc)
    val date = takeDate(sc, today)
    val time = takeTime(sc)?.let { (a, b) -> a to (b ?: dur) }
    // Слова-связки и команды не попадают в название дела.
    val noise = Regex("\\s(запиши|записать|добавь|добавить|поставь|поставить|внеси|напомни|запись|пожалуйста|расписание|план|какое|какой|что|у\\s+меня|меня|есть|там|в|во|на|к|и|а|ну|типо|например)(?=\\s)")
    var rest = sc.rest()
    repeat(3) { rest = (" $rest ").replace(noise, " ").replace(Regex("\\s+"), " ").trim() }
    rest = rest.trim('.', ' ', '-')
    return when {
        remove -> if (date == null) BotCmd.Unknown("Какой день? Например: удали 30.10 стоматолог") else BotCmd.Remove(date, rest)
        free -> BotCmd.Free(date)
        week -> BotCmd.Week(date ?: today)
        date != null && time != null && rest.isNotEmpty() ->
            BotCmd.Add(date, time.first, (time.second ?: 60).coerceIn(15, 12 * 60), rest.replaceFirstChar { it.uppercase() })
        date != null && time != null -> BotCmd.Unknown("Что записать на это время? Например: 30.10 17:00 стоматолог")
        date != null -> BotCmd.Plan(date)
        time != null && rest.isNotEmpty() -> BotCmd.Unknown("На какой день? Например: 30.10 ${hhmm(time.first)} $rest")
        else -> BotCmd.Unknown("Не понял. Напишите «помощь», чтобы увидеть примеры.")
    }
}

const val BOT_HELP = """Я помощник по расписанию. Примеры:
• что в пятницу / расписание на 30 / завтра
• расписание на неделю
• свободно в четверг?
• 30.10 17:00 стоматолог Лера
• тридцатого десятого в 17 стоматолог на 2 часа
• удали 30.10 стоматолог
• где Рифат"""

/** Ответ бота. where: где сейчас (считается на телефоне), только для команды «где». */
fun botAnswer(d: AppData, cmd: BotCmd, now: Long, where: () -> String, zone: ZoneId = ZoneId.systemDefault()): Pair<AppData, String> {
    val today = dayOf(now, zone)
    fun title(date: LocalDate): String {
        val t = epochMs(date, 12 * 60, zone)
        val rel = when (date) { today -> " (сегодня)"; today.plusDays(1) -> " (завтра)"; else -> "" }
        return "${fDow(t).replaceFirstChar { it.uppercase() }} ${fDate(t)}$rel"
    }
    return when (cmd) {
        BotCmd.Help -> d to BOT_HELP
        BotCmd.Where -> d to where()
        is BotCmd.Plan -> d to dayText(d, cmd.date, now, true, title(cmd.date), zone)
        is BotCmd.Week -> d to (0 until 7).map { cmd.from.plusDays(it.toLong()) }
            .joinToString("\n\n") { dayText(d, it, now, false, title(it), zone) }
        is BotCmd.Free -> {
            val ws = findWindows(d, cmd.date ?: today, if (cmd.date == null) 7 else 1, 60, d.settings.travelMin, now, zone)
            d to if (ws.isEmpty()) "Свободных окон нет" else windowsText(ws, 60, zone)
        }
        is BotCmd.Add -> {
            val ev = Personal(newId(), cmd.date.toEpochDay(), cmd.min, cmd.duration, cmd.title)
            val nd = d.copy(events = d.events + ev)
            val clash = dayPlan(d, cmd.date, now, zone).filter { it !is PlanItem.Window && it.from < ev.min + ev.duration && it.to > ev.min }
            val names = clash.joinToString(", ") { item ->
                "${hhmm(item.from)}–${hhmm(item.to)} " + when (item) {
                    is PlanItem.Lesson -> d.client(item.occ.clientId)?.name
                    is PlanItem.Event -> item.ev.title
                    is PlanItem.Unplanned -> d.client(item.visit.clientId)?.name
                    else -> ""
                }
            }
            nd to "Записал: ${title(cmd.date)}, ${hhmm(ev.min)}–${hhmm(ev.min + ev.duration)} ${ev.title}" +
                if (clash.isNotEmpty()) "\n⚠ Пересекается: $names" else ""
        }
        is BotCmd.Remove -> {
            val day = d.events.filter { it.date == cmd.date.toEpochDay() }
            val words = cmd.words.split(' ').filter { it.length >= 3 }
            val hit = if (words.isEmpty()) day else day.filter { e -> words.any { w -> e.title.lowercase().contains(w.take(5)) } }
            when {
                hit.isEmpty() -> d to "На ${title(cmd.date)} такого дела нет"
                hit.size > 1 && words.isEmpty() -> d to "На этот день несколько дел: ${hit.joinToString(", ") { it.title }}. Напишите, какое удалить."
                else -> d.copy(events = d.events - hit.toSet()) to "Удалил: " + hit.joinToString(", ") { "${hhmm(it.min)} ${it.title}" }
            }
        }
        is BotCmd.Unknown -> d to cmd.reason
    }
}
