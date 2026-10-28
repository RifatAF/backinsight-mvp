package com.rifat.trainercount.logic

import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneId
import java.time.format.TextStyle
import java.util.Locale

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
    /** Сколько осталось, кто должен; words: текст, в нём ищем имя клиента. */
    data class Balances(val words: String) : BotCmd()
    data class Summary(val month: YearMonth) : BotCmd()
    data class Report(val words: String) : BotCmd()
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
    val sc = Scan(text.replace(LEAD, ""))
    if (sc.has(Regex("^\\s*/?(start|help|помощь|команды|что умеешь)"))) return BotCmd.Help
    if (sc.has(Regex("\\s(где|/where)(?=\\s)"))) return BotCmd.Where
    // Деньги и статистика; фраза со временем это запись дела («17:00 оплатить интернет»).
    if (!sc.has(Regex("\\d{1,2}[:.]\\d{2}|\\sв\\s+\\d{1,2}\\s"))) {
        if (sc.has(Regex("\\sотчет"))) return BotCmd.Report(text)
        if (sc.has(Regex("\\s(сводк|итог|статистик|заработ|доход|выручк|сколько\\s+(?:я\\s+)?(?:провел|отработал|получил)|сколько\\s+(?:было\\s+)?(?:занятий|тренировок)\\s+(?:за|в)\\s)"))) {
            val ym = YearMonth.from(today)
            val month = when {
                sc.has(Regex("\\sпрошл")) -> ym.minusMonths(1)
                else -> Regex("[а-я]+").findAll(sc.s).firstNotNullOfOrNull { m -> monthOf(m.value)?.takeIf { m.value.length >= 4 } }
                    ?.let { mon -> YearMonth.of(today.year, mon).let { if (it > ym) it.minusYears(1) else it } } ?: ym
            }
            return BotCmd.Summary(month)
        }
        if (sc.has(Regex("\\s(баланс|остат|пакет|долг|должен|должн|задолж|не\\s*оплач|неоплач|оплат|осталось\\s+(?:у|занят|трен))"))) return BotCmd.Balances(text)
    }
    val remove = sc.take(Regex("\\s(удали|удалить|отмени|отменить|убери|убрать)(?=\\s)")) != null
    val week = sc.take(Regex("\\s(?:на\\s+)?(?:эту\\s+|всю\\s+)?недел[юя](?=\\s)")) != null
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

const val BOT_HELP = """Я помощник Рифата по расписанию и клиентам.

Пишите обычными словами, как человеку, я пойму. Например:
• что у Рифата завтра после обеда?
• запиши на пятницу 17:00 стоматолог Лера
• тридцатого в 5 вечера родительское собрание на час
• удали 30.10 стоматолог
• когда он свободен на этой неделе?
• во сколько и где он сегодня заканчивает?
• сколько осталось у Анны / кто должен
• итоги за прошлый месяц / отчёт Игорь

Частые вопросы есть на кнопках внизу.""""

/** Значки в начале кнопок не мешают разбору. */
private val LEAD = Regex("^[^\\p{L}\\d/]+")

/** Кнопки быстрых вопросов под полем ввода в Telegram. */
val OWNER_BUTTONS = listOf(
    listOf("📅 Сегодня", "📅 Завтра", "🗓 Неделя"),
    listOf("🕐 Свободное время", "📍 Где Рифат"),
    listOf("💰 Остатки", "⚠️ Кто должен", "📊 Сводка"),
    listOf("❓ Помощь"),
)
val CLIENT_BUTTONS = listOf(
    listOf("📋 Сколько занятий осталось", "📅 Мои занятия"),
    listOf("🧾 Отчёт", "✉️ Написать Рифату"),
    listOf("❓ Помощь"),
)

fun isButton(text: String, buttons: List<List<String>>) = buttons.any { row -> text.trim() in row }

/** reply_markup для Telegram: постоянная клавиатура с кнопками. */
fun keyboardJson(buttons: List<List<String>>): String =
    buttons.joinToString(",", "{\"keyboard\":[", "],\"resize_keyboard\":true,\"is_persistent\":true}") { row ->
        row.joinToString(",", "[", "]") { "{\"text\":\"$it\"}" }
    }

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
        is BotCmd.Balances -> d to balancesText(d, cmd.words, now, zone)
        is BotCmd.Summary -> d to summaryText(d, cmd.month, now, zone)
        is BotCmd.Report -> d to findClients(d, cmd.words).let { cs ->
            if (cs.isEmpty()) "Чей отчёт? Например: отчёт Анна" else cs.joinToString("\n\n") { reportText(d, it) }
        }
        is BotCmd.Unknown -> d to cmd.reason
    }
}

// ---------- Деньги и статистика ----------

/** Клиенты, чьё имя есть во фразе: «у Анны», «Ерлана». */
fun findClients(d: AppData, text: String): List<Client> {
    val words = Regex("[а-яa-z]{3,}").findAll(text.lowercase().replace('ё', 'е')).map { it.value }.toList()
    fun hit(name: String) = Regex("[а-яa-z]{3,}").findAll(name.lowercase().replace('ё', 'е')).map { it.value }.any { n ->
        // Падежи: «Анна» → «Анны», «Игорь» → «Игоря», «Стас» → «Стаса».
        val stem = if (n.last() in "аяьйоеиы") n.dropLast(1) else n
        words.any { w -> w.startsWith(stem) && w.length <= n.length + 2 }
    }
    return d.clients.filter { !it.archived && (hit(it.name) || it.members.any(::hit)) }
}

/** Баланс одной строкой: «осталось 3 из 10», «не оплачено 2 занятия ($60)». */
fun balanceLine(d: AppData, c: Client): String {
    if (c.perVisit) {
        val n = unpaid(d, c)
        val price = pricePerSession(d, c)
        return if (n == 0) "платит за занятие, всё оплачено ✓" else "не оплачено ${lessons(n)}" + if (price > 0) " (${fMoney(n * price)})" else ""
    }
    val b = balance(d, c.id)
    return when {
        b.paid == 0 -> "оплата не внесена в приложение, проведено ${lessons(b.used)}"
        b.left < 0 -> "пакет закончился, сверх оплаты ${lessons(-b.left)}"
        b.current != null -> "осталось ${b.left} из ${b.activeTotal}"
        else -> "пакет закончился"
    }
}

private fun needsPay(d: AppData, c: Client): Boolean =
    if (c.perVisit) unpaid(d, c) > 0 else balance(d, c.id).let { it.paid > 0 && it.left <= 0 }

/** Ближайшие занятия клиента на 8 дней строками «вт 28.10, 13:30–14:30». */
fun nextLessons(d: AppData, c: Client, now: Long, zone: ZoneId = ZoneId.systemDefault()): List<String> {
    val today = dayOf(now, zone)
    val nowMin = minuteOf(now, zone)
    return (0 until 8).flatMap { i ->
        val date = today.plusDays(i.toLong())
        dayPlan(d, date, now, zone).filterIsInstance<PlanItem.Lesson>()
            .filter { it.occ.clientId == c.id && it.status == Status.PLANNED && (i > 0 || it.from > nowMin) }
            .map { l -> val t = epochMs(date, 12 * 60, zone); "${fDow(t)} ${fDate(t)}, ${hhmm(l.from)}–${hhmm(l.to)}" + if (l.occ.movedFrom != null) " (перенос)" else "" }
    }
}

fun balancesText(d: AppData, words: String, now: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val found = findClients(d, words)
    if (found.isNotEmpty()) return found.joinToString("\n\n") { c ->
        val visits = d.visits.filter { it.clientId == c.id }.sortedBy { it.start }
        val month = visits.filter { YearMonth.from(dayOf(it.start, zone)) == YearMonth.from(dayOf(now, zone)) }.sumOf { it.count }
        val price = pricePerSession(d, c)
        buildString {
            append("${c.name}: ${balanceLine(d, c)}\n")
            append(if (c.perVisit) "Платит за каждое занятие" else "Платит пакетом")
            if (price > 0) append(", ${fMoney(price)} за занятие")
            append("\nВ этом месяце: ${lessons(month)}")
            visits.lastOrNull()?.let { append("\nПоследнее: ${visitLine(it)}") }
            nextLessons(d, c, now, zone).firstOrNull()?.let { append("\nСледующее: $it") }
        }
    }
    val active = d.clients.filter { !it.archived }
    if (active.isEmpty()) return "Клиентов пока нет"
    val debt = active.filter { needsPay(d, it) }.sortedBy { it.name }
    val noPay = active.filter { !it.perVisit && balance(d, it.id).paid == 0 }.sortedBy { it.name }
    val soon = active.filter { !it.perVisit && balance(d, it.id).let { b -> b.current != null && b.left <= 2 } }
    val onlyDebt = Regex("должен|должн|долг|задолж|не\\s*оплач|неоплач").containsMatchIn(words.lowercase())
    return buildString {
        if (!onlyDebt) {
            append("Баланс клиентов:\n")
            active.sortedBy { it.name }.forEach { append("• ${it.name}: ${balanceLine(d, it)}\n") }
            append('\n')
        }
        if (debt.isEmpty()) { if (noPay.size < active.size) append("Должников нет ✓") } else {
            append("Нужна оплата:\n")
            debt.forEach { append("• ${it.name}: ${balanceLine(d, it)}\n") }
            val sum = debt.filter { it.perVisit }.sumOf { unpaid(d, it) * pricePerSession(d, it) } +
                debt.filter { !it.perVisit }.sumOf { (-balance(d, it.id).left).coerceAtLeast(0) * pricePerSession(d, it) }
            if (sum > 0) append("Долг за проведённые занятия: ${fMoney(sum)}")
        }
        if (soon.isNotEmpty()) append("\n\nСкоро закончится пакет: " + soon.joinToString(", ") { "${it.name} (${balance(d, it.id).left})" })
        if (noPay.isNotEmpty()) append("\n\nОплаты не внесены: " + noPay.joinToString(", ") { it.name } +
            ". Внести: клиент → «Оплата» (сколько занятий и сумма), тогда бот посчитает остаток.")
    }.trim()
}

private fun monthName(ym: YearMonth) =
    ym.month.getDisplayName(TextStyle.FULL_STANDALONE, Locale("ru")).replaceFirstChar { it.uppercase() } + " ${ym.year}"

fun summaryText(d: AppData, ym: YearMonth, now: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val m = monthStats(d, ym, now, zone)
    val cur = ym == YearMonth.from(dayOf(now, zone))
    return buildString {
        append("${monthName(ym)}" + if (cur) " (пока)\n" else "\n")
        append("Занятий: ${m.sessions}" + if (m.burned > 0) " (из них списано за отмены: ${m.burned})\n" else "\n")
        append("Заработано: ${fMoney(m.earned)}\n")
        append("Получено оплат: ${fMoney(m.received)}\n")
        if (m.workMin > 0) append("Работа: ${fDur(m.workMin)}, у клиентов ${fDur(m.atClientsMin)}, дорога ${fDur(m.roadMin)}, ${fMoney(m.perHour)} в час\n")
        if (cur) {
            val today = dayOf(now, zone)
            val monday = today.minusDays(today.dayOfWeek.value - 1L)
            val week = d.visits.filter { !dayOf(it.start, zone).isBefore(monday) }.sumOf { it.count }
            append("На этой неделе: ${lessons(week)}\n")
        }
        if (m.perClient.isNotEmpty()) {
            append("\nПо клиентам:\n")
            m.perClient.forEach { append("• ${it.client.name}: ${it.sessions}" + (if (it.earned > 0) ", ${fMoney(it.earned)}" else "") + "\n") }
        }
    }.trim()
}

// ---------- Помощь ИИ (Gemini) для фраз, которые бот не понял сам ----------

/** Запрос к ИИ: в него уходит только текст сообщения и сегодняшняя дата. */
fun aiPrompt(text: String, today: LocalDate): String {
    val dow = listOf("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")[today.dayOfWeek.value - 1]
    return """Ты разбираешь сообщения для бота расписания персонального тренера Рифата. Сегодня $today, $dow.
Сообщение: «$text»
Ответь только JSON без пояснений:
{"cmd": "plan" | "week" | "free" | "add" | "remove" | "where" | "balance" | "summary" | "help" | "unknown",
 "date": "ГГГГ-ММ-ДД" или null, "time": "ЧЧ:ММ" или null, "minutes": число или null, "title": "что за дело" или null}
plan: спрашивают, что запланировано в день. week: расписание на неделю. free: когда свободен.
add: просят записать дело (врач, встреча) на дату и время; title коротко, с именем человека, если есть.
remove: просят удалить дело. where: спрашивают, где Рифат сейчас.
balance: сколько осталось занятий, кто должен, оплаты (title: имя клиента, если названо). summary: статистика, итоги, заработок за месяц. Если дата не названа, а речь о сегодня, ставь сегодняшнюю."""
}

private fun jsonField(json: String, key: String): String? =
    Regex("\"$key\"\\s*:\\s*(\"((?:[^\"\\\\]|\\\\.)*)\"|-?\\d+|null)").find(json)?.let { m ->
        if (m.groupValues[1] == "null") null else m.groupValues[2].ifEmpty { m.groupValues[1].trim('"') }
    }

/** Ответ ИИ → команда бота; null, если ответ не разобрать. */
fun parseAi(json: String, today: LocalDate): BotCmd? {
    val cmd = jsonField(json, "cmd") ?: return null
    val date = jsonField(json, "date")?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
    val time = jsonField(json, "time")?.let { t ->
        Regex("^(\\d{1,2}):(\\d{2})$").find(t)?.let { it.groupValues[1].toInt() * 60 + it.groupValues[2].toInt() }
    }?.takeIf { it in 0 until 24 * 60 }
    val minutes = jsonField(json, "minutes")?.toIntOrNull()
    val title = jsonField(json, "title")?.trim()?.takeIf { it.isNotEmpty() }
    return when (cmd) {
        "plan" -> BotCmd.Plan(date ?: today)
        "week" -> BotCmd.Week(date ?: today)
        "free" -> BotCmd.Free(date)
        "where" -> BotCmd.Where
        "balance" -> BotCmd.Balances(title.orEmpty())
        "summary" -> BotCmd.Summary(YearMonth.from(date ?: today))
        "help" -> BotCmd.Help
        "add" -> if (date != null && time != null && title != null)
            BotCmd.Add(date, time, (minutes ?: 60).coerceIn(15, 12 * 60), title.replaceFirstChar { it.uppercase() }) else null
        "remove" -> date?.let { BotCmd.Remove(it, title.orEmpty()) }
        else -> null
    }
}

// ---------- Умные ответы: ИИ видит расписание и где Рифат ----------

/** Сведения для ИИ: сейчас, где Рифат, план на неделю с адресами, где и когда заканчивается рабочий день. */
fun aiContext(d: AppData, now: Long, status: String, zone: ZoneId = ZoneId.systemDefault()): String {
    val today = dayOf(now, zone)
    val nowMin = minuteOf(now, zone)
    fun where(c: Client?) = c?.placeId?.let { d.place(it)?.name } ?: c?.address?.takeIf { it.isNotBlank() }
    return buildString {
        val t = epochMs(today, nowMin, zone)
        append("Сейчас: ${fDow(t)} ${fDate(t)}, ${hhmm(nowMin)}.\n")
        append("Где Рифат: $status\n")
        val plan = dayPlan(d, today, now, zone)
        val lessons = plan.filterIsInstance<PlanItem.Lesson>().filter { it.status != Status.MISSED }
        lessons.maxByOrNull { it.to }?.let { last ->
            val c = d.client(last.occ.clientId)
            append("Последнее занятие сегодня: ${hhmm(last.from)}–${hhmm(last.to)} ${c?.name}" + (where(c)?.let { ", адрес: $it" } ?: "") + "\n")
        }
        append("\nРасписание (тренировки и личные дела):\n")
        for (i in 0 until 7) {
            val date = today.plusDays(i.toLong())
            val et = epochMs(date, 12 * 60, zone)
            append(dayText(d, date, now, true, "${fDow(et)} ${fDate(et)}" + if (i == 0) " (сегодня)" else if (i == 1) " (завтра)" else "", zone))
            append("\n")
        }
        append("Рабочее время по настройкам: ${hhmm(d.settings.startMin)}–${hhmm(d.settings.endMin)}. На дорогу между клиентами около ${d.settings.travelMin} минут.\n")
        append("\nКлиенты (оплата и баланс):\n")
        d.clients.filter { !it.archived }.sortedBy { it.name }.forEach { c ->
            val price = pricePerSession(d, c)
            val last = d.visits.filter { it.clientId == c.id }.maxByOrNull { it.start }
            append("• ${c.name}" + (if (c.isFamily) " (${c.members.joinToString(", ")})" else "") +
                ": ${if (c.perVisit) "платит за каждое занятие" else "пакетом"}, ${balanceLine(d, c)}" +
                (if (price > 0) ", цена ${fMoney(price)}" else "") +
                (last?.let { ", последнее занятие ${fDate(it.start)}" } ?: "") +
                (where(c)?.let { ", адрес: $it" } ?: "") + "\n")
        }
        val ym = YearMonth.from(today)
        append("\nСтатистика:\n${summaryText(d, ym, now, zone)}\n\n${summaryText(d, ym.minusMonths(1), now, zone)}\n")
        append("\nПроведённые занятия за 3 недели:\n")
        val from = epochMs(today.minusDays(21), 0, zone)
        d.visits.filter { it.start >= from }.sortedBy { it.start }.forEach { v ->
            append("${d.client(v.clientId)?.name}: ${visitLine(v)}\n")
        }
    }
}

/** Запрос к ИИ для живого ответа. history: последние реплики этого чата. */
fun aiChatPrompt(context: String, history: List<Pair<String, String>>, text: String): String = buildString {
    append(
        """Ты помощник Рифата, персонального тренера. Тебе пишет его жена (или сам Рифат) в Telegram.
Отвечай по-русски, коротко и по делу, по-человечески, как заботливый секретарь: 1–4 предложения.
Опирайся только на сведения ниже, ничего не выдумывай. Если данных нет, так и скажи.
В сведениях есть расписание, балансы и долги клиентов, статистика за этот и прошлый месяц и история занятий за 3 недели: отвечай по ним.
Если клиент не назван, а спрашивают «сколько осталось» или «кто должен», перечисли всех коротко.
Если спрашивают, где встретить или куда приехать, назови адрес и время окончания последнего занятия дня.
Ссылку на карту давай, только если спрашивают, где он сейчас.
Если просят записать дело (врач, встреча), заполни add. Если просят удалить дело, заполни remove.
Если заполнил add или remove, в reply не повторяй время и название: приложение само подтвердит запись.

Сведения:
"""
    )
    append(context)
    if (history.isNotEmpty()) {
        append("\n\nНедавняя переписка:\n")
        history.forEach { (q, a) -> append("— $q\n— $a\n") }
    }
    append(
        """

Новое сообщение: «$text»
Ответь только JSON:
{"reply": "текст ответа",
 "add": {"date": "ГГГГ-ММ-ДД", "time": "ЧЧ:ММ", "minutes": 60, "title": "что"} или null,
 "remove": {"date": "ГГГГ-ММ-ДД", "title": "что"} или null}"""
    )
}

data class AiReply(val reply: String, val add: BotCmd.Add?, val remove: BotCmd.Remove?)

private fun jsonObject(json: String, key: String): String? {
    val i = Regex("\"$key\"\\s*:\\s*\\{").find(json) ?: return null
    var depth = 0
    for (j in i.range.last until json.length) {
        when (json[j]) { '{' -> depth++; '}' -> if (--depth == 0) return json.substring(i.range.last, j + 1) }
    }
    return null
}

private fun unescape(s: String) = s.replace("\\n", "\n").replace("\\\"", "\"").replace("\\\\", "\\")

fun parseAiReply(json: String, today: LocalDate): AiReply? {
    val reply = Regex("\"reply\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").find(json)?.groupValues?.get(1)?.let(::unescape) ?: return null
    val add = jsonObject(json, "add")?.let { o ->
        parseAi("{\"cmd\":\"add\"," + o.removePrefix("{"), today) as? BotCmd.Add
    }
    val remove = jsonObject(json, "remove")?.let { o ->
        parseAi("{\"cmd\":\"remove\"," + o.removePrefix("{"), today) as? BotCmd.Remove
    }
    return AiReply(reply, add, remove)
}

// ---------- Бот для клиентов ----------

/** Ссылка-приглашение: клиент открывает её, жмёт «Старт», и бот знает, кто это. */
fun inviteLink(botName: String, c: Client) = "https://t.me/${botName.removePrefix("@")}?start=c_${c.id}"

/** Код из «/start c_…»: id клиента. */
fun inviteClientId(text: String): String? = Regex("^/start\\s+c_([\\w-]+)").find(text.trim())?.groupValues?.get(1)

sealed class ClientCmd {
    data object Balance : ClientCmd()
    data object Lessons : ClientCmd()
    data object Where : ClientCmd()
    data object Report : ClientCmd()
    data object Help : ClientCmd()
    /** Нажал «Написать Рифату»: следующее сообщение уйдёт Рифату. */
    data object Compose : ClientCmd()
    /** Всё остальное (перенос, вопрос): передать Рифату. */
    data class Message(val text: String) : ClientCmd()
}

fun parseClientBot(text: String): ClientCmd {
    val s = " " + text.replace(LEAD, "").lowercase().replace('ё', 'е') + " "
    return when {
        Regex("^\\s*/?(start|help|помощь)").containsMatchIn(s) -> ClientCmd.Help
        Regex("^\\s*написать рифату\\s*$").containsMatchIn(s) -> ClientCmd.Compose
        Regex("отч[её]т|истори").containsMatchIn(s) -> ClientCmd.Report
        Regex("сколько|осталось|остаток|баланс|оплат").containsMatchIn(s) -> ClientCmd.Balance
        Regex("\\sгде\\s|скоро|едешь|опаздыва|приедешь|будешь").containsMatchIn(s) -> ClientCmd.Where
        Regex("когда|расписани|следующ|мои занят|мое занят|занятие\\??\\s*$").containsMatchIn(s) -> ClientCmd.Lessons
        else -> ClientCmd.Message(text.trim())
    }
}

fun clientHelp(c: Client) = """Здравствуйте, ${c.name}! Я помощник Рифата.

Пишите обычными словами, как человеку, я пойму. Например:
• сколько у меня осталось занятий?
• когда следующая тренировка?
• Рифат скоро будет?
• пришлите отчёт по занятиям

Если нужно перенести занятие или что-то спросить, просто напишите: я передам Рифату, он ответит сам.
Частые вопросы есть на кнопках внизу. Я вижу только ваши занятия и оплаты."""

fun clientBalanceText(d: AppData, c: Client): String {
    if (c.perVisit) {
        val n = unpaid(d, c)
        val price = pricePerSession(d, c)
        return if (n == 0) "Все занятия оплачены." else "Не оплачено: ${lessons(n)}" + (if (price > 0) " (${fMoney(n * price)})" else "") + "."
    }
    val b = balance(d, c.id)
    return when {
        b.left < 0 -> "Проведено сверх оплаты: ${lessons(-b.left)}. Пора оплатить следующий пакет."
        b.current != null -> "Осталось ${lessons(b.left)} из ${b.activeTotal}." + if (b.left <= 1) " Скоро понадобится оплата." else ""
        b.paid > 0 -> "Оплаченные занятия закончились."
        else -> "Оплат пока нет."
    }
}

/** Ближайшие занятия этого клиента на неделю (без чужих). */
fun clientLessonsText(d: AppData, c: Client, now: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val list = nextLessons(d, c, now, zone)
    return if (list.isEmpty()) "На ближайшую неделю занятий в расписании нет. Напишите, если хотите записаться." else "Ваши занятия:\n" + list.joinToString("\n")
}

/**
 * «Где Рифат» для клиента: без карты и без имён других клиентов.
 * busyUntil: когда закончится текущее занятие (если он на занятии), atClient: у этого ли клиента.
 */
fun clientWhereText(d: AppData, c: Client, now: Long, status: RifatStatus, zone: ZoneId = ZoneId.systemDefault()): String {
    // Где Рифат и чем занят, клиенту не сообщаем: только про его собственное занятие.
    if (status == RifatStatus.WithYou) return "Рифат уже у вас."
    val today = dayOf(now, zone)
    val nowMin = minuteOf(now, zone)
    val mine = dayPlan(d, today, now, zone).filterIsInstance<PlanItem.Lesson>()
        .firstOrNull { it.occ.clientId == c.id && it.to > nowMin && it.status == Status.PLANNED }
    return if (mine != null) "Ваше занятие сегодня в ${hhmm(mine.from)}, Рифат будет к этому времени. Если что-то изменится, он предупредит."
    else "Сегодня занятия у вас нет. " + (nextLessons(d, c, now, zone).firstOrNull()?.let { "Ближайшее: $it." } ?: "Напишите, если хотите записаться.")
}

sealed class RifatStatus {
    data object WithYou : RifatStatus()
    data class Busy(val until: Int?) : RifatStatus()
    data object OnTheWay : RifatStatus()
    data object Home : RifatStatus()
}

/** Статус Рифата для клиента по данным приложения (дом определяется на телефоне). */
fun rifatStatus(d: AppData, c: Client, now: Long, home: Boolean, zone: ZoneId = ZoneId.systemDefault()): RifatStatus {
    val open = d.open.keys
    if (c.id in open || (c.placeId != null && "p:${c.placeId}" in open)) return RifatStatus.WithYou
    if (open.isNotEmpty()) {
        val nowMin = minuteOf(now, zone)
        val until = dayPlan(d, dayOf(now, zone), now, zone).filterIsInstance<PlanItem.Lesson>()
            .firstOrNull { it.occ.clientId in open && it.to > nowMin }?.to
        return RifatStatus.Busy(until)
    }
    return if (home) RifatStatus.Home else if (d.openTrip != null) RifatStatus.OnTheWay else RifatStatus.Home
}

fun clientAnswer(d: AppData, c: Client, cmd: ClientCmd, now: Long, status: RifatStatus): String = when (cmd) {
    ClientCmd.Help -> clientHelp(c)
    ClientCmd.Compose -> "Напишите сообщение, я сразу передам его Рифату."
    ClientCmd.Balance -> clientBalanceText(d, c)
    ClientCmd.Lessons -> clientLessonsText(d, c, now)
    ClientCmd.Where -> clientWhereText(d, c, now, status)
    ClientCmd.Report -> reportText(d, c)
    is ClientCmd.Message -> "Передал Рифату, он ответит вам сам."
}

/** Сведения для ИИ в чате клиента: только его данные. */
fun clientAiPrompt(d: AppData, c: Client, now: Long, status: RifatStatus, history: List<Pair<String, String>>, text: String): String = buildString {
    append(
        """Ты помощник персонального тренера Рифата. Тебе пишет его клиент «${c.name}» в Telegram.
Отвечай по-русски, вежливо, на «вы», коротко: 1–3 предложения. Опирайся только на сведения ниже, ничего не выдумывай.
Не обещай переносов и не назначай занятия сам: такие просьбы передавай Рифату (forward = true).
Строго нельзя сообщать: где сейчас Рифат и чем он занят, его адрес и дом, других клиентов и их занятия,
его заработок, цены других клиентов, личные дела и семью. На такие вопросы вежливо отвечай, что этого ты не подскажешь.
Если человек спрашивает, скоро ли Рифат, отвечай только про его собственное занятие.

Сведения:
"""
    )
    append(clientBalanceText(d, c)).append('\n')
    append(clientLessonsText(d, c, now)).append('\n')
    append(clientWhereText(d, c, now, status)).append('\n')
    if (history.isNotEmpty()) {
        append("\nНедавняя переписка:\n")
        history.forEach { (q, a) -> append("— $q\n— $a\n") }
    }
    append("\nНовое сообщение: «$text»\nОтветь только JSON: {\"reply\": \"текст\", \"forward\": true или false}")
}

/** Ответ ИИ клиенту: текст и нужно ли передать сообщение Рифату. */
fun parseClientAi(json: String): Pair<String, Boolean>? {
    val reply = Regex("\"reply\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").find(json)?.groupValues?.get(1)
        ?.replace("\\n", "\n")?.replace("\\\"", "\"") ?: return null
    return reply to Regex("\"forward\"\\s*:\\s*true").containsMatchIn(json)
}
