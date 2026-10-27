package com.rifat.trainercount.logic

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** Сколько недель истории смотреть при обучении расписания. */
const val LEARN_WEEKS = 6
/** Занятие перестаёт считаться постоянным, если его не было столько недель. */
const val STALE_WEEKS = 3
/** Визит относится к занятию по расписанию, если начался в пределах этого отклонения. */
const val MATCH_MIN = 90
/** Окно: свободный промежуток не короче. */
const val WINDOW_MIN = 45
const val READY_DAYS = 7

// ---------- Время ----------

/** Округление до получаса: 16:05 → 16:00, 11:25 → 11:30. */
fun roundSlot(min: Int): Int = ((min + 15) / 30 * 30).coerceAtMost(23 * 60 + 30)

fun minuteOf(t: Long, zone: ZoneId = ZoneId.systemDefault()): Int =
    Instant.ofEpochMilli(t).atZone(zone).let { it.hour * 60 + it.minute }

fun slotOf(t: Long, zone: ZoneId = ZoneId.systemDefault()): Pair<LocalDate, Int> {
    val z = Instant.ofEpochMilli(t).atZone(zone)
    return z.toLocalDate() to roundSlot(z.hour * 60 + z.minute)
}

/** Номер недели с понедельника (одинаковый для пн–вс). */
fun weekIndex(date: LocalDate): Long = Math.floorDiv(date.toEpochDay() + 3, 7)

fun parityOf(date: LocalDate): Int = Math.floorMod(weekIndex(date), 2).toInt()

fun epochMs(date: LocalDate, min: Int, zone: ZoneId = ZoneId.systemDefault()): Long =
    date.atStartOfDay(zone).plusMinutes(min.toLong()).toInstant().toEpochMilli()

/** Длина занятия в расписании: час (sessionMin) на человека, у семьи с раздельным счётом на каждого. */
fun lessonMin(d: AppData, c: Client): Int =
    c.placeId?.let { d.place(it)?.sessionMin } ?: (c.sessionMin * if (c.countsEach) c.members.size.coerceAtLeast(1) else 1)

// ---------- Обучение по визитам ----------

/** Визиты с реальным временем прихода: по ним учится расписание. */
private fun timedVisits(d: AppData) = d.visits.filter { it.kind == VisitKind.AUTO }

/**
 * Пересобирает выученные занятия по визитам последних недель. Ручные занятия и убранные руками не трогает.
 * Время прихода округляется до получаса. Если клиент приходит в это время только по чётным или только по
 * нечётным неделям, занятие считается «через неделю».
 */
fun learnRules(d: AppData, now: Long, zone: ZoneId = ZoneId.systemDefault()): AppData {
    val today = dayOf(now, zone)
    val curWeek = weekIndex(today)
    val fromDay = today.minusWeeks(LEARN_WEEKS.toLong())
    val manual = d.rules.filter { it.manual }
    val learned = mutableListOf<Rule>()

    for (c in d.clients.filter { !it.archived }) {
        // (день недели, время) → недели, когда было занятие
        val seen = mutableMapOf<Pair<Int, Int>, MutableSet<Long>>()
        for (v in timedVisits(d).filter { it.clientId == c.id }) {
            val (date, min) = slotOf(v.start, zone)
            if (date < fromDay || date > today) continue
            seen.getOrPut(date.dayOfWeek.value to min) { mutableSetOf() } += weekIndex(date)
        }
        // Соседние получасы в один день недели склеиваем: приход то в 15:50, то в 16:20.
        val keys = seen.keys.sortedWith(compareBy({ it.first }, { -seen.getValue(it).size }, { it.second }))
        val used = mutableSetOf<Pair<Int, Int>>()
        val cands = mutableListOf<Triple<Int, Int, java.util.SortedSet<Long>>>()
        for (k in keys) {
            if (k in used) continue
            val group = keys.filter { it !in used && it.first == k.first && kotlin.math.abs(it.second - k.second) <= 30 }
            used += group
            val weeks = group.flatMap { seen.getValue(it) }.toSortedSet()
            if (curWeek - weeks.last() > STALE_WEEKS) continue
            cands += Triple(k.first, k.second, weeks)
        }
        // Разовый перенос (одна неделя) не становится постоянным занятием, если у клиента есть другие.
        val steady = cands.filter { it.third.size >= 2 }.ifEmpty { cands }
        // Одно время в день на клиента там, где занятия пересекались бы: остаётся последнее, при равенстве частое.
        // Чередование «чётная неделя 15:00, нечётная 16:00» пересечением не считается.
        fun parity(w: Set<Long>) = w.map { Math.floorMod(it, 2).toInt() }.toSet().let { if (w.size >= 2 && it.size == 1) it.first() else null }
        val len = lessonMin(d, c)
        val picked = mutableListOf<Rule>()
        for ((day, min, weeks) in steady.sortedWith(compareByDescending<Triple<Int, Int, java.util.SortedSet<Long>>> { it.third.last() }.thenByDescending { it.third.size })) {
            val par = parity(weeks)
            fun clash(r: Rule) = r.day == day && kotlin.math.abs(r.min - min) < len && (r.parity == null || par == null || r.parity == par)
            if (picked.any(::clash) || manual.any { it.clientId == c.id && clash(it) }) continue
            picked += Rule("auto-${c.id}-$day-$min", c.id, day, min, par)
        }
        learned += picked
    }
    val keep = d.rules.filter { it.manual }
    val next = keep + learned
    return if (next.toSet() == d.rules.toSet()) d else d.copy(rules = next)
}

/** Напоминания включаются, когда есть ручное расписание или прошла неделя с первого засчитанного визита. */
fun readyInDays(d: AppData, now: Long): Int {
    if (d.rules.any { it.manual && !it.off }) return 0
    val first = timedVisits(d).minOfOrNull { it.start } ?: return READY_DAYS
    val passed = ((now - first) / 86_400_000L).toInt()
    return (READY_DAYS - passed).coerceAtLeast(0)
}

// ---------- План на день ----------

data class Occ(
    val clientId: String,
    val min: Int,
    val duration: Int,
    val rule: Rule? = null,
    /** Перенесено с этого дня и времени. */
    val movedFrom: Pair<LocalDate, Int>? = null,
    val extra: Boolean = false,
)

fun occurrences(d: AppData, date: LocalDate): List<Occ> {
    val dow = date.dayOfWeek.value
    val par = parityOf(date)
    val day = date.toEpochDay()
    val active = d.clients.filter { !it.archived }.associateBy { it.id }
    val out = mutableListOf<Occ>()
    for (r in d.rules) {
        val c = active[r.clientId] ?: continue
        if (r.off || r.day != dow || (r.parity != null && r.parity != par)) continue
        val removed = d.changes.any {
            it.clientId == r.clientId && it.date == day && it.min == r.min &&
                (it.kind == ChangeKind.CANCEL || it.kind == ChangeKind.MOVE)
        }
        if (!removed) out += Occ(c.id, r.min, lessonMin(d, c), rule = r)
    }
    for (ch in d.changes) {
        val c = active[ch.clientId] ?: continue
        when {
            ch.kind == ChangeKind.MOVE && ch.toDate == day && ch.toMin != null ->
                out += Occ(c.id, ch.toMin, lessonMin(d, c), movedFrom = LocalDate.ofEpochDay(ch.date) to ch.min)
            ch.kind == ChangeKind.EXTRA && ch.date == day -> out += Occ(c.id, ch.min, lessonMin(d, c), extra = true)
        }
    }
    return out.distinctBy { it.clientId to it.min }.sortedBy { it.min }
}

enum class Status { PLANNED, DONE, MISSED }

sealed class PlanItem {
    abstract val from: Int
    abstract val to: Int
    data class Lesson(val occ: Occ, val status: Status, val visit: Visit?) : PlanItem() {
        override val from get() = occ.min
        override val to get() = occ.min + occ.duration
    }
    /** Визит без занятия в расписании. */
    data class Unplanned(val visit: Visit, override val from: Int, override val to: Int) : PlanItem()
    data class Window(override val from: Int, override val to: Int) : PlanItem()
}

fun dayPlan(d: AppData, date: LocalDate, now: Long, zone: ZoneId = ZoneId.systemDefault()): List<PlanItem> {
    val occs = occurrences(d, date)
    val dayVisits = d.visits.filter { it.kind != VisitKind.BURNED && dayOf(it.start, zone) == date }
    val usedVisits = mutableSetOf<String>()
    val nowDate = dayOf(now, zone)
    val nowMin = Instant.ofEpochMilli(now).atZone(zone).let { it.hour * 60 + it.minute }
    val items = mutableListOf<PlanItem>()
    for (o in occs) {
        val v = dayVisits
            .filter { it.clientId == o.clientId && it.id !in usedVisits }
            .minByOrNull { kotlin.math.abs(slotOf(it.start, zone).second - o.min) }
            ?.takeIf { it.kind == VisitKind.MANUAL || kotlin.math.abs(slotOf(it.start, zone).second - o.min) <= MATCH_MIN }
        if (v != null) usedVisits += v.id
        val past = date < nowDate || (date == nowDate && o.min + o.duration < nowMin)
        val status = when {
            v != null -> Status.DONE
            past -> Status.MISSED
            else -> Status.PLANNED
        }
        items += PlanItem.Lesson(o, status, v)
    }
    // Кусок того же занятия (телефон на минуту «вышел» из зоны) не считаем отдельным визитом.
    for (l in items.filterIsInstance<PlanItem.Lesson>().filter { it.visit != null }) {
        dayVisits.filter { it.clientId == l.occ.clientId && it.id !in usedVisits }
            .filter { minuteOf(it.start, zone) < l.to && minuteOf(it.end, zone) > l.from - 15 }
            .forEach { usedVisits += it.id }
    }
    for (v in dayVisits.filter { it.id !in usedVisits && it.kind == VisitKind.AUTO }) {
        val from = slotOf(v.start, zone).second
        val c = d.client(v.clientId) ?: continue
        items += PlanItem.Unplanned(v, from, from + lessonMin(d, c))
    }
    val busy = items.sortedBy { it.from }
    items += windows(d.settings, busy.map { it.from to it.to })
    return items.sortedWith(compareBy({ it.from }, { if (it is PlanItem.Window) 1 else 0 }))
}

/**
 * Свободные окна рабочего дня: единый расчёт для расписания, главного экрана, сводок и поиска окна.
 * После занятия окно начинается через travel минут (дорога), перед занятием заканчивается за travel минут.
 * Начало и конец рабочего дня дорогой не ограничиваются. Начало окна округляется вверх до получаса.
 */
fun windows(s: Settings, busy: List<Pair<Int, Int>>, travel: Int = s.travelMin): List<PlanItem.Window> {
    val out = mutableListOf<PlanItem.Window>()
    var cur = s.startMin
    var first = true
    for ((a, b) in busy.sortedBy { it.first } + listOf(s.endMin to s.endMin)) {
        val isEnd = a == s.endMin && b == s.endMin
        val from = ceil30(if (first) cur else cur + travel)
        val to = minOf(if (isEnd) a else a - travel, s.endMin)
        if (to - from >= WINDOW_MIN) out += PlanItem.Window(from, to)
        cur = maxOf(cur, b)
        first = false
        if (isEnd) break
    }
    return out
}

private fun ceil30(min: Int) = (min + 29) / 30 * 30

/** Свободные окна на ближайшие дни, куда можно перенести занятие длиной duration. */
fun freeSlots(d: AppData, from: LocalDate, days: Int, duration: Int, now: Long, zone: ZoneId = ZoneId.systemDefault()): List<Pair<LocalDate, Int>> =
    findWindows(d, from, days, duration, d.settings.travelMin, now, zone).map { it.date to it.earliest }

// ---------- Изменения ----------

fun cancelOnce(d: AppData, occ: Occ, date: LocalDate, burn: Boolean, zone: ZoneId = ZoneId.systemDefault()): AppData {
    var nd = removeOccurrence(d, occ, date)
    if (burn) {
        val t = epochMs(date, occ.min, zone)
        nd = nd.copy(visits = nd.visits + Visit(newId(), occ.clientId, t, t, 1, VisitKind.BURNED))
    }
    return nd
}

/** Убрать занятие из этого дня: постоянное отменить, перенесённое и разовое удалить. */
private fun removeOccurrence(d: AppData, occ: Occ, date: LocalDate): AppData {
    val day = date.toEpochDay()
    return when {
        occ.extra -> d.copy(changes = d.changes.filterNot { it.kind == ChangeKind.EXTRA && it.clientId == occ.clientId && it.date == day && it.min == occ.min })
        occ.movedFrom != null -> d.copy(changes = d.changes.map {
            if (it.kind == ChangeKind.MOVE && it.clientId == occ.clientId && it.toDate == day && it.toMin == occ.min) it.copy(kind = ChangeKind.CANCEL, toDate = null, toMin = null) else it
        })
        else -> d.copy(changes = d.changes + Change(newId(), occ.clientId, ChangeKind.CANCEL, day, occ.min))
    }
}

fun moveOnce(d: AppData, occ: Occ, date: LocalDate, toDate: LocalDate, toMin: Int): AppData {
    val day = date.toEpochDay()
    return when {
        occ.extra -> d.copy(changes = d.changes.map {
            if (it.kind == ChangeKind.EXTRA && it.clientId == occ.clientId && it.date == day && it.min == occ.min) it.copy(date = toDate.toEpochDay(), min = toMin) else it
        })
        occ.movedFrom != null -> d.copy(changes = d.changes.map {
            if (it.kind == ChangeKind.MOVE && it.clientId == occ.clientId && it.toDate == day && it.toMin == occ.min) it.copy(toDate = toDate.toEpochDay(), toMin = toMin) else it
        })
        else -> d.copy(changes = d.changes + Change(newId(), occ.clientId, ChangeKind.MOVE, day, occ.min, toDate.toEpochDay(), toMin))
    }
}

fun addExtra(d: AppData, clientId: String, date: LocalDate, min: Int): AppData =
    d.copy(changes = d.changes + Change(newId(), clientId, ChangeKind.EXTRA, date.toEpochDay(), min))

/** Постоянное занятие вручную. parity: null каждую неделю, иначе чётность недели первого занятия. */
fun addRule(d: AppData, clientId: String, day: Int, min: Int, parity: Int?): AppData =
    d.copy(rules = d.rules.filterNot { it.clientId == clientId && it.day == day && it.min == min } +
        Rule(newId(), clientId, day, min, parity, manual = true))

/** Изменить постоянное занятие: теперь оно ручное, автоматика его не перезапишет. */
fun editRule(d: AppData, rule: Rule, day: Int, min: Int, parity: Int?): AppData {
    val replaced = d.rules.map { if (it.id == rule.id) it.copy(day = day, min = min, parity = parity, manual = true, off = false) else it }
    // Старое выученное время больше не предлагать.
    val mute = if (!rule.manual && (rule.day != day || rule.min != min)) listOf(rule.copy(id = newId(), manual = true, off = true)) else emptyList()
    return d.copy(rules = replaced + mute)
}

fun removeRule(d: AppData, rule: Rule): AppData =
    if (rule.manual) d.copy(rules = d.rules.filterNot { it.id == rule.id })
    else d.copy(rules = d.rules.map { if (it.id == rule.id) it.copy(id = newId(), manual = true, off = true) else it })

// ---------- Напоминания ----------

fun nextEvening(s: Settings, now: Long, zone: ZoneId = ZoneId.systemDefault()): Long {
    val today = dayOf(now, zone)
    val t = epochMs(today, s.eveningMin, zone)
    return if (t > now) t else epochMs(today.plusDays(1), s.eveningMin, zone)
}

/** Ближайший будильник: за alarmLead минут до первого занятия дня, если оно утреннее. */
fun nextAlarm(d: AppData, now: Long, zone: ZoneId = ZoneId.systemDefault()): Pair<Long, Occ>? {
    val s = d.settings
    if (!s.alarmOn || readyInDays(d, now) > 0) return null
    val today = dayOf(now, zone)
    for (i in 0..1) {
        val date = today.plusDays(i.toLong())
        if (date.dayOfWeek.value !in s.days) continue
        val first = dayPlan(d, date, now, zone).filterIsInstance<PlanItem.Lesson>()
            .firstOrNull { it.status == Status.PLANNED }?.occ ?: continue
        if (first.min >= s.alarmBefore) continue
        val t = epochMs(date, first.min - s.alarmLead, zone)
        if (t > now) return t to first
    }
    return null
}

/** Текст вечерней сводки на завтра; null, если занятий нет. */
fun tomorrowText(d: AppData, now: Long, zone: ZoneId = ZoneId.systemDefault()): Pair<String, String>? {
    val date = dayOf(now, zone).plusDays(1)
    val plan = dayPlan(d, date, now, zone)
    val lessons = plan.filterIsInstance<PlanItem.Lesson>()
    if (lessons.isEmpty()) return null
    val title = "Завтра ${fDow(epochMs(date, 12 * 60, zone))} ${fDate(epochMs(date, 12 * 60, zone))}: ${trainings(lessons.size)}"
    val body = buildString {
        lessons.forEach { l ->
            append("${hhmm(l.from)}–${hhmm(l.to)} ${d.client(l.occ.clientId)?.name}")
            if (l.occ.movedFrom != null) append(" (перенос)")
            if (l.occ.extra) append(" (разово)")
            append('\n')
        }
        val w = plan.filterIsInstance<PlanItem.Window>()
        if (w.isNotEmpty()) append("Окна: " + w.joinToString(", ") { "${hhmm(it.from)}–${hhmm(it.to)}" })
    }.trimEnd()
    return title to body
}

fun hhmm(min: Int) = "%d:%02d".format(min / 60, min % 60)

// ---------- Поиск окна ----------

/** Свободное окно: занятие можно начать с earliest до latest (включительно), шаг 30 минут. */
data class FreeWindow(val date: LocalDate, val earliest: Int, val latest: Int)

/**
 * Окна под занятие длиной duration на ближайшие days дней с запасом travel минут на дорогу до и после
 * соседних занятий. Начало и конец рабочего дня запасом не ограничиваются.
 */
fun findWindows(
    d: AppData, from: LocalDate, days: Int, duration: Int, travel: Int, now: Long, zone: ZoneId = ZoneId.systemDefault(),
): List<FreeWindow> {
    val nowDate = dayOf(now, zone)
    val nowMin = minuteOf(now, zone)
    val out = mutableListOf<FreeWindow>()
    for (i in 0 until days) {
        val date = from.plusDays(i.toLong())
        if (date.dayOfWeek.value !in d.settings.days || date < nowDate) continue
        val busy = dayPlan(d, date, now, zone).filter { it !is PlanItem.Window }.map { it.from to it.to }
        for (w in windows(d.settings, busy, travel)) {
            var a = w.from
            if (date == nowDate) a = maxOf(a, ceil30(nowMin + travel))
            val b = (w.to - duration) / 30 * 30
            if (a <= b) out += FreeWindow(date, a, b)
        }
    }
    return out
}

fun windowsText(ws: List<FreeWindow>, duration: Int, zone: ZoneId = ZoneId.systemDefault()): String = buildString {
    append("Свободное время для занятия (${fDur(duration.toLong())}):\n")
    ws.groupBy { it.date }.forEach { (date, list) ->
        val t = epochMs(date, 12 * 60, zone)
        append("${fDow(t).replaceFirstChar { it.uppercase() }} ${fDate(t)}: ")
        append(list.joinToString(", ") { w -> if (w.earliest == w.latest) "в ${hhmm(w.earliest)}" else "с ${hhmm(w.earliest)} до ${hhmm(w.latest)}" })
        append('\n')
    }
}.trimEnd()

// ---------- Напоминание перед тренировкой ----------

/** Ближайшее тихое напоминание: за soonLead минут до занятия, кроме того, перед которым звонит будильник. */
fun nextSoon(d: AppData, now: Long, zone: ZoneId = ZoneId.systemDefault()): Pair<Long, Occ>? {
    val s = d.settings
    if (!s.soonOn || readyInDays(d, now) > 0) return null
    val today = dayOf(now, zone)
    for (i in 0..1) {
        val date = today.plusDays(i.toLong())
        if (date.dayOfWeek.value !in s.days) continue
        for (l in dayPlan(d, date, now, zone).filterIsInstance<PlanItem.Lesson>().filter { it.status == Status.PLANNED }) {
            if (alarmed(d, date, l.occ.min)) continue
            val t = epochMs(date, l.occ.min - s.soonLead, zone)
            if (t > now) return t to l.occ
        }
    }
    return null
}

/** Перед этим занятием звонит будильник: оно первое за день и раньше alarmBefore. */
private fun alarmed(d: AppData, date: LocalDate, min: Int): Boolean {
    val s = d.settings
    if (!s.alarmOn || min >= s.alarmBefore) return false
    return occurrences(d, date).firstOrNull()?.min == min
}

// ---------- Сообщение в Telegram ----------

/** План на завтра для близких: кто, во сколько и где, когда свободен. */
fun familyText(d: AppData, now: Long, withAddress: Boolean, zone: ZoneId = ZoneId.systemDefault()): String {
    val date = dayOf(now, zone).plusDays(1)
    val t = epochMs(date, 12 * 60, zone)
    val head = "План на завтра, ${fDow(t)} ${fDate(t)}"
    val plan = dayPlan(d, date, now, zone)
    val lessons = plan.filterIsInstance<PlanItem.Lesson>()
    if (date.dayOfWeek.value !in d.settings.days || lessons.isEmpty()) return "$head:\nЗанятий нет"
    return buildString {
        append("$head:\n")
        lessons.forEach { l ->
            val c = d.client(l.occ.clientId)
            append("${hhmm(l.from)}–${hhmm(l.to)} ${c?.name ?: ""}")
            if (withAddress) {
                val where = c?.placeId?.let { d.place(it)?.name } ?: c?.address?.takeIf { it.isNotBlank() }
                if (where != null) append(" ($where)")
            }
            append('\n')
        }
        append("Начало ${hhmm(lessons.first().from)}, конец ${hhmm(lessons.maxOf { it.to })}")
        val w = plan.filterIsInstance<PlanItem.Window>().filter { it.from >= lessons.first().from && it.to <= lessons.maxOf { l -> l.to } }
        if (w.isNotEmpty()) append("\nПерерывы: " + w.joinToString(", ") { "${hhmm(it.from)}–${hhmm(it.to)}" })
    }
}
