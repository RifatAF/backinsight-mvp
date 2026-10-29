package com.rifat.trainercount.logic

import java.time.LocalDate
import java.time.ZoneId

/*
 * ИИ-помощник по расписанию: понимает просьбу целиком («сегодня будет только Петр в 16:30»,
 * «новый клиент Виталий у метро Карранса, найди удобные дни после 14:00») и предлагает план из
 * понятных действий. Приложение показывает план и выполняет его только после подтверждения.
 */

/** Запрос ИИ к приложению: свободные окна с учётом дороги до точки. */
data class SlotQuery(
    val after: Int,
    val before: Int,
    val minutes: Int,
    val lat: Double?,
    val lng: Double?,
    val place: String,
    val days: Int = 7,
)

/** routes: «сколько ехать от А до Б» (имя клиента или «дом»). */
data class AgentReply(val reply: String, val actions: List<Any?>, val query: SlotQuery?, val routes: List<Pair<String, String>> = emptyList())

data class AgentStep(val text: String, val ok: Boolean)

/** План помощника: ответ, шаги (ok = получится выполнить) и данные после выполнения. */
data class AgentPlan(val reply: String, val steps: List<AgentStep>, val data: AppData) {
    val hasChanges get() = steps.any { it.ok }
    fun text() = buildString {
        append(reply.trim())
        if (steps.isNotEmpty()) {
            append("\n\nПлан:\n")
            steps.forEach { append(if (it.ok) "• " else "✗ ").append(it.text).append('\n') }
        }
    }.trim()
}

private val DOW_SHORT = listOf("пн", "вт", "ср", "чт", "пт", "сб", "вс")

fun dayLabel(date: LocalDate) = "${DOW_SHORT[date.dayOfWeek.value - 1]} ${"%02d.%02d".format(date.dayOfMonth, date.monthValue)}"

private fun ruleText(r: Rule) = DOW_SHORT[r.day - 1] + " " + hhmm(r.min) + when (r.parity) { null -> ""; else -> " (через неделю)" }

/** План дня для ИИ: со статусами и адресами. */
fun agentDay(d: AppData, date: LocalDate, now: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val items = dayPlan(d, date, now, zone).filter { it !is PlanItem.Window }
    val head = dayLabel(date) + " (${date})"
    if (items.isEmpty()) return "$head: занятий нет"
    return head + ":\n" + items.joinToString("\n") { item ->
        "  ${hhmm(item.from)}–${hhmm(item.to)} " + when (item) {
            is PlanItem.Lesson -> {
                val c = d.client(item.occ.clientId)
                val where = c?.placeId?.let { d.place(it)?.name } ?: c?.address?.takeIf { it.isNotBlank() }
                (c?.name ?: "?") + (where?.let { " ($it)" } ?: "") + when {
                    item.status == Status.DONE -> " [уже было]"
                    item.status == Status.MISSED -> " [прошло, отметки нет]"
                    item.occ.movedFrom != null -> " [перенесено с ${dayLabel(item.occ.movedFrom.first)} ${hhmm(item.occ.movedFrom.second)}]"
                    item.occ.extra -> " [разовое]"
                    else -> ""
                }
            }
            is PlanItem.Event -> "${item.ev.title} (личное дело)" + when (item.ev.went) { true -> " [был]"; false -> " [не был]"; null -> "" }
            is PlanItem.Unplanned -> (d.client(item.visit.clientId)?.name ?: "?") + " [было вне расписания]"
            else -> ""
        }
    }
}

fun agentContext(d: AppData, now: Long, status: String, zone: ZoneId = ZoneId.systemDefault()): String = buildString {
    val today = dayOf(now, zone)
    append("Сейчас: ${dayLabel(today)} ${today.year}, ${hhmm(minuteOf(now, zone))}. Дата сегодня: $today.\n")
    append("Где Рифат: $status\n")
    val s = d.settings
    append("Рабочее время: ${hhmm(s.startMin)}–${hhmm(s.endMin)}, рабочие дни: ${s.days.sorted().joinToString(", ") { DOW_SHORT[it - 1] }}. ")
    append("Дорогу между клиентами приложение считает по маршруту (метро, автобус, пешком) и добавляет ${s.travelExtra} мин запаса; " +
        "если у клиента нет места на карте, закладывается ${s.travelMin} мин.\n")
    append("\nКлиенты:\n")
    d.clients.filter { !it.archived }.sortedBy { it.name }.forEach { c ->
        val where = c.placeId?.let { d.place(it)?.name } ?: c.address.takeIf { it.isNotBlank() } ?: "адрес не задан"
        val rules = d.rules.filter { it.clientId == c.id && !it.off }.sortedWith(compareBy({ it.day }, { it.min }))
        append("• ${c.name}" + (if (c.isFamily) " (${c.members.joinToString(", ")})" else "") +
            ": $where; занятие ${lessonMin(d, c)} мин; " +
            (if (rules.isEmpty()) "постоянного расписания нет" else "постоянно " + rules.joinToString(", ") { ruleText(it) }) +
            "; оплата: ${balanceLine(d, c)}\n")
    }
    append("\nРасписание на 14 дней:\n")
    for (i in 0 until 14) append(agentDay(d, today.plusDays(i.toLong()), now, zone)).append('\n')
    append("\nСвободные окна на 14 дней: время, в которое можно НАЧАТЬ часовое занятие, дорога ${s.travelMin} мин до и после соседних уже учтена.\n")
    append("Для занятия длиннее часа поздняя граница раньше на разницу (для 2 часов на 1 час).\n")
    val ws = findWindows(d, today, 14, 60, s.travelMin, now, zone)
    if (ws.isEmpty()) append("нет\n")
    ws.groupBy { it.date }.forEach { (date, list) ->
        append(dayLabel(date) + ": " + list.joinToString(", ") { if (it.earliest == it.latest) "в ${hhmm(it.earliest)}" else "с ${hhmm(it.earliest)} до ${hhmm(it.latest)}" } + "\n")
    }
    val ym = java.time.YearMonth.from(today)
    append("\nСтатистика:\n${summaryText(d, ym, now, zone)}\n")
}

fun agentPrompt(
    context: String, history: List<Pair<String, String>>, text: String, slots: String? = null, feedback: String? = null,
): String = buildString {
    append(
        """Ты умный помощник Рифата, персонального тренера в Буэнос-Айресе. Ты ведёшь его расписание в приложении «Тренировки».
Пишет Рифат или его жена. Пойми просьбу целиком, как понял бы толковый секретарь, и выполни её через actions.

Как устроено приложение (соблюдай всегда):
• У каждого клиента своя длительность занятия (указана в списке клиентов). Занятие с 15:30 на 60 мин заканчивается в 16:30.
• Между занятиями у разных клиентов нужна дорога: следующее занятие можно начать не раньше конца предыдущего плюс
  время маршрута на метро/автобусе/пешком плюс запас (указан в сведениях). Сколько ехать, ты не угадываешь:
  спроси приложение через routes, оно построит маршрут и скажет, сколько ехать и сколько идти пешком.
• Рабочие дни бывают и в выходные (смотри сведения), их тоже рассматривай.
• У клиента не больше одного занятия в день. Занятия только в рабочее время и рабочие дни.
• «Через неделю» значит чередование недель: такое занятие бывает не каждую неделю (смотри расписание на 14 дней).
• Новое время (move, extra, add_rule) выбирай ТОЛЬКО из «Свободных окон» ниже, никогда не придумывай сам.
  Для постоянного занятия время должно быть свободно в этот день на обеих ближайших неделях.
• Приложение само проверит каждое действие и отклонит то, что не помещается.

Правила:
1. Сообщение с несколькими просьбами разбери на все действия сразу.
2. «Сегодня будет только Петр в 16:30»: отмени (cancel) все остальные сегодняшние занятия без отметки [уже было];
   Петра перенеси (move) на 16:30, если у него сегодня есть занятие, иначе добавь разовое (extra).
3. «Перенеси Анну с пятницы на субботу 11:00» → move. «Игоря завтра не будет» → cancel. «Стас теперь по вторникам в 15:00» → add_rule
   (и remove_rule для старого дня, если это замена).
4. Новый клиент: add_client (имя, адрес по-испански для поиска на карте, например «Estación Ministro Carranza, Buenos Aires», примерные lat/lng).
   Чтобы подобрать дни, сначала запроси query (поиск окон рядом с его адресом), actions пока оставь пустыми.
   Получив окна, предложи в reply 4–6 пронумерованных вариантов в разные дни (лучшие по дороге первыми): день, время,
   кто до и после, сколько ехать и сколько из этого пешком. actions пока не делай: Рифат выберет («давай 1 и 3»),
   тогда добавь add_client и add_rule на выбранные.
5. Личное дело (врач, встреча, «Лера к стоматологу») → add_event. Никогда не записывай просьбу про клиентов как личное дело.
6. Имена клиентов пиши точно как в списке. Время только :00 или :30. Даты в формате ГГГГ-ММ-ДД.
7. Если непонятно, о ком или о каком дне речь, задай один короткий вопрос в reply и не делай actions.
8. Вопросы (что завтра, сколько осталось у Анны, когда свободен) просто отвечай по сведениям, без actions.
9. reply по-русски, коротко и по делу, без повторения плана: приложение само покажет список действий и спросит подтверждение.

Сведения:
"""
    )
    append(context)
    if (history.isNotEmpty()) {
        append("\nНедавняя переписка:\n")
        history.forEach { (q, a) -> append("— $q\n— $a\n") }
    }
    append("\nНовое сообщение: «$text»\n")
    if (slots != null) append("\nОтвет приложения на твой запрос (окна, маршруты):\n$slots\nТеперь query и routes не нужны: ответь по этим данным.\n")
    if (feedback != null) append("\nПроверка приложения отклонила часть твоих действий:\n$feedback\nИсправь: выбери другое время только из свободных окон и верни полный список actions заново.\n")
    append(
        """
Ответь только JSON:
{"reply": "текст",
 "actions": [
   {"type": "cancel", "client": "Имя", "date": "ГГГГ-ММ-ДД"},
   {"type": "move", "client": "Имя", "date": "ГГГГ-ММ-ДД", "to_date": "ГГГГ-ММ-ДД", "to_time": "ЧЧ:ММ"},
   {"type": "extra", "client": "Имя", "date": "ГГГГ-ММ-ДД", "time": "ЧЧ:ММ"},
   {"type": "add_event", "date": "ГГГГ-ММ-ДД", "time": "ЧЧ:ММ", "minutes": 60, "title": "что"},
   {"type": "remove_event", "date": "ГГГГ-ММ-ДД", "title": "что"},
   {"type": "add_client", "name": "Имя", "address": "адрес", "lat": -34.6, "lng": -58.4, "minutes": 60, "price": 0},
   {"type": "add_rule", "client": "Имя", "day": 1, "time": "ЧЧ:ММ", "biweekly": false},
   {"type": "remove_rule", "client": "Имя", "day": 1}
 ],
 "query": null или {"after": "14:00", "before": "21:00", "minutes": 60, "place": "адрес", "lat": -34.6, "lng": -58.4},
 "routes": [] или [{"from": "Анна", "to": "Стас"}, {"from": "дом", "to": "Игорь"}]}
day: 1 = понедельник … 7 = воскресенье. Пустой список actions, если менять ничего не нужно."""
    )
}

private fun timeOf(s: String?): Int? = s?.let { Regex("^(\\d{1,2})[:.](\\d{2})$").find(it.trim()) }
    ?.let { it.groupValues[1].toInt() * 60 + it.groupValues[2].toInt() }?.takeIf { it in 0 until 24 * 60 }

private fun dateOf(s: String?): LocalDate? = s?.let { runCatching { LocalDate.parse(it.trim()) }.getOrNull() }

fun parseAgent(raw: String): AgentReply? {
    val o = Json.parse(raw) ?: return null
    val q = o.obj("query")?.let { q ->
        SlotQuery(
            after = timeOf(q.str("after")) ?: 0, before = timeOf(q.str("before")) ?: 24 * 60,
            minutes = q.num("minutes")?.toInt()?.coerceIn(30, 240) ?: 60,
            lat = q.num("lat"), lng = q.num("lng"), place = q.str("place").orEmpty(),
        )
    }
    val routes = o.list("routes").mapNotNull { r -> r.str("from")?.let { f -> r.str("to")?.let { t -> f to t } } }
    return AgentReply(o.str("reply").orEmpty(), o.list("actions"), q, routes)
}

/** Клиент по имени из ответа ИИ: точное совпадение, иначе по началу имени. */
fun clientByName(d: AppData, name: String?): Client? {
    if (name.isNullOrBlank()) return null
    val n = name.trim().lowercase()
    val active = d.clients.filter { !it.archived }
    return active.firstOrNull { it.name.lowercase() == n }
        ?: active.firstOrNull { c -> c.members.any { it.lowercase() == n } }
        ?: findClients(d, name).singleOrNull()
}


/**
 * Окна под нового клиента в точке (lat, lng): для каждого окна, кто до и после и сколько добавится дороги.
 * Учитывается текущее расписание на ближайшие days дней.
 */
fun findSlotsText(d: AppData, q: SlotQuery, now: Long, zone: ZoneId = ZoneId.systemDefault(), router: Router? = null): String {
    val today = dayOf(now, zone)
    val s = d.settings
    val home = if (s.hasHome) s.homeLat!! to s.homeLng!! else null
    val target = if (q.lat != null && q.lng != null) q.lat to q.lng else null
    fun leg(a: LatLng?, b: LatLng?, depart: Long): Leg? = if (a == null || b == null) null else router?.leg(a, b, depart) ?: estimateLeg(a, b)
    data class Opt(val date: LocalDate, val start: Int, val text: String, val cost: Int)
    val opts = mutableListOf<Opt>()
    // Окна без общего запаса: дорогу до соседей считаем по настоящему маршруту.
    val ws = findWindows(d, today.plusDays(1), q.days, q.minutes, 0, now, zone)
    for (w in ws) {
        val plan = dayPlan(d, w.date, now, zone).filter { it is PlanItem.Lesson || (it is PlanItem.Event && it.ev.went != false) }
        val from0 = maxOf(w.earliest, (q.after + 29) / 30 * 30)
        val to0 = minOf(w.latest, q.before - q.minutes)
        if (from0 > to0) continue
        val prev = plan.filter { it.to <= from0 }.maxByOrNull { it.to }
        val next = plan.filter { it.from >= from0 + q.minutes }.minByOrNull { it.from }
        fun who(p: PlanItem?) = (p as? PlanItem.Lesson)?.let { d.client(it.occ.clientId) }
        val prevC = who(prev); val nextC = who(next)
        val prevAt = prevC?.let { destOf(d, it) } ?: if (prev == null) home else null
        val nextAt = nextC?.let { destOf(d, it) } ?: if (next == null) home else null
        val legIn = leg(prevAt, target, epochMs(w.date, prev?.to ?: from0, zone))
        val legOut = if (next != null) leg(target, nextAt, epochMs(w.date, from0 + q.minutes, zone)) else null
        // Начало после дороги от предыдущего, конец с запасом до следующего. Только :00 и :30.
        val start = if (prev != null) maxOf(from0, (prev.to + needMin(s, legIn) + 29) / 30 * 30) else from0
        val latest = if (next != null) minOf(to0, (next.from - needMin(s, legOut) - q.minutes) / 30 * 30) else to0
        if (start > latest) continue
        val before = when (prev) {
            is PlanItem.Lesson -> "до этого ${prevC?.name} до ${hhmm(prev.to)}, оттуда " + (legIn?.text() ?: "дорога неизвестна")
            is PlanItem.Event -> "до этого личное дело до ${hhmm(prev.to)}"
            else -> "первое занятие дня" + (legIn?.let { ", из дома " + it.text() } ?: "")
        }
        val after = when (next) {
            is PlanItem.Lesson -> "после: ${nextC?.name} в ${hhmm(next.from)}, туда " + (legOut?.text() ?: "дорога неизвестна")
            is PlanItem.Event -> "после: личное дело в ${hhmm(next.from)}"
            else -> "дальше свободно"
        }
        val range = if (start == latest) "в ${hhmm(start)}" else "с ${hhmm(start)} до ${hhmm(latest)}"
        val cost = (legIn?.min ?: 0) + (legOut?.min ?: 0)
        opts += Opt(w.date, start, "${dayLabel(w.date)} (день ${w.date.dayOfWeek.value}): начать можно $range; $before; $after; запас ${s.travelExtra} мин к дороге учтён", cost)
    }
    if (opts.isEmpty()) return "Подходящих окон нет в ближайшие ${q.days} дней."
    val loc = if (target == null) "Координаты клиента неизвестны, дорогу посчитать не получилось.\n" else ""
    // Каждый день хотя бы одним вариантом, лучшие по дороге первыми.
    val perDay = opts.groupBy { it.date }.values.map { it.minByOrNull { o -> o.cost }!! }
    val rest = (opts - perDay.toSet()).sortedBy { it.cost }.take(6)
    return loc + (perDay.sortedBy { it.cost } + rest).joinToString("\n") { it.text }
}

/** Выполнить действия ИИ по порядку. Шаг, который не получился, остаётся в плане с пометкой. */
fun applyActions(
    d: AppData, actions: List<Any?>, now: Long, zone: ZoneId = ZoneId.systemDefault(), router: Router? = null,
): Pair<AppData, List<AgentStep>> {
    var nd = d
    val steps = mutableListOf<AgentStep>()
    fun ok(t: String) { steps += AgentStep(t, true) }
    fun bad(t: String) { steps += AgentStep(t, false) }
    for (a in actions) {
        val type = a.str("type") ?: continue
        val date = dateOf(a.str("date"))
        val c = clientByName(nd, a.str("client"))
        val who = a.str("client") ?: "?"
        when (type) {
            "cancel" -> {
                if (c == null || date == null) { bad("Отменить: не нашёл клиента «$who» или день"); continue }
                val occ = occurrences(nd, date).firstOrNull { it.clientId == c.id }
                if (occ == null) { bad("Отменить ${c.name} ${dayLabel(date)}: занятия в этот день нет"); continue }
                nd = cancelOnce(nd, occ, date, burn = false, zone)
                ok("Отменить: ${c.name}, ${dayLabel(date)} ${hhmm(occ.min)}")
            }
            "move", "extra" -> {
                val to = timeOf(a.str(if (type == "move") "to_time" else "time"))
                val toDate = dateOf(a.str("to_date")) ?: date
                if (c == null || date == null || to == null || toDate == null) { bad("Перенести: не понял клиента «$who» или время"); continue }
                val occ = if (type == "move") occurrences(nd, date).firstOrNull { it.clientId == c.id } else null
                val clash = clashText(nd, c.id, toDate, to, now, zone, router)
                if (clash != null) { bad("${c.name} ${dayLabel(toDate)} ${hhmm(to)}: $clash"); continue }
                if (occ != null) {
                    nd = moveOnce(nd, occ, date, toDate, to)
                    ok("Перенести: ${c.name}, ${dayLabel(date)} ${hhmm(occ.min)} → " + (if (toDate != date) dayLabel(toDate) + " " else "") + hhmm(to))
                } else {
                    nd = addExtra(nd, c.id, toDate, to)
                    ok("Разовое занятие: ${c.name}, ${dayLabel(toDate)} ${hhmm(to)}")
                }
            }
            "add_event" -> {
                val t = timeOf(a.str("time")); val title = a.str("title")
                if (date == null || t == null || title == null) { bad("Личное дело: не понял день или время"); continue }
                val dur = (a.num("minutes")?.toInt() ?: 60).coerceIn(15, 12 * 60)
                nd = nd.copy(events = nd.events + Personal(newId(), date.toEpochDay(), t, dur, title.replaceFirstChar { it.uppercase() }))
                ok("Личное дело: ${dayLabel(date)} ${hhmm(t)}–${hhmm(t + dur)} ${title.replaceFirstChar { it.uppercase() }}")
            }
            "remove_event" -> {
                if (date == null) { bad("Удалить дело: какой день?"); continue }
                val words = a.str("title").orEmpty().lowercase().split(' ').filter { it.length >= 3 }
                val hit = nd.events.filter { it.date == date.toEpochDay() && (words.isEmpty() || words.any { w -> w.take(5) in it.title.lowercase() }) }
                if (hit.isEmpty()) { bad("Удалить дело ${dayLabel(date)}: такого нет"); continue }
                nd = nd.copy(events = nd.events - hit.toSet())
                ok("Удалить дело: " + hit.joinToString(", ") { "${dayLabel(date)} ${hhmm(it.min)} ${it.title}" })
            }
            "add_client" -> {
                val name = a.str("name")
                if (name == null) { bad("Новый клиент: без имени"); continue }
                if (nd.clients.any { !it.archived && it.name.equals(name, ignoreCase = true) }) { bad("Клиент «$name» уже есть"); continue }
                val min = (a.num("minutes")?.toInt() ?: 60).coerceIn(30, 240)
                val nc = Client(
                    newId(), name, address = a.str("address").orEmpty(), lat = a.num("lat"), lng = a.num("lng"),
                    sessionMin = min, price = a.num("price")?.toInt() ?: 0, createdAt = now,
                )
                nd = nd.copy(clients = nd.clients + nc)
                ok("Новый клиент: $name" + (nc.address.takeIf { it.isNotBlank() }?.let { " ($it)" } ?: "") +
                    if (nc.hasPlace) "" else ", место уточните в карточке")
            }
            "add_rule" -> {
                val day = a.num("day")?.toInt()?.takeIf { it in 1..7 }
                val t = timeOf(a.str("time"))
                if (c == null || day == null || t == null) { bad("Постоянное занятие: не понял клиента «$who», день или время"); continue }
                val today = dayOf(now, zone)
                val first = (0..6).map { today.plusDays(it.toLong()) }.first { it.dayOfWeek.value == day }
                val parity = if (a.bool("biweekly")) parityOf(first) else null
                val step = if (parity != null) 14L else 7L
                val clash = listOf(first, first.plusDays(step)).firstNotNullOfOrNull { dt -> clashText(nd, c.id, dt, t, now, zone, router)?.let { dayLabel(dt) + ": " + it } }
                if (clash != null) { bad("Постоянно ${c.name}, ${DOW_SHORT[day - 1]} ${hhmm(t)}: $clash"); continue }
                nd = addRule(nd, c.id, day, t, parity)
                ok("Постоянно: ${c.name}, ${DOW_SHORT[day - 1]} ${hhmm(t)}" + (if (parity != null) " через неделю" else "") + ", с ${dayLabel(first)}")
            }
            "remove_rule" -> {
                val day = a.num("day")?.toInt()
                val rules = nd.rules.filter { c != null && it.clientId == c.id && it.day == day && !it.off }
                if (c == null || rules.isEmpty()) { bad("Убрать постоянное занятие «$who»: не нашёл"); continue }
                rules.forEach { nd = removeRule(nd, it) }
                ok("Убрать постоянно: ${c.name}, " + rules.joinToString(", ") { ruleText(it) })
            }
            else -> bad("Непонятное действие: $type")
        }
    }
    return nd to steps
}

/**
 * Помещается ли занятие клиента в день: рабочее время и дорога до и после соседних занятий.
 * null: помещается, иначе причина.
 */
fun clashText(
    d: AppData, clientId: String, date: LocalDate, min: Int, now: Long, zone: ZoneId = ZoneId.systemDefault(), router: Router? = null,
): String? {
    val c = d.client(clientId) ?: return null
    val dur = lessonMin(d, c)
    val s = d.settings
    val end = min + dur
    if (date.dayOfWeek.value !in s.days) return "нерабочий день"
    if (min < s.startMin || end > s.endMin) return "вне рабочего времени ${hhmm(s.startMin)}–${hhmm(s.endMin)}"
    val busy = dayPlan(d, date, now, zone).filter {
        (it is PlanItem.Lesson && it.occ.clientId != clientId) || (it is PlanItem.Event && it.ev.went != false)
    }
    val here = destOf(d, c)
    for (it in busy) {
        val other = (it as? PlanItem.Lesson)?.let { l -> d.client(l.occ.clientId) }
        val there = other?.let { o -> destOf(d, o) }
        val before = it.to <= min
        // Дорога по маршруту в нужную сторону, иначе общий запас из настроек.
        val leg = if (it is PlanItem.Lesson && here != null && there != null)
            (if (before) router?.leg(there, here, epochMs(date, it.to, zone)) else router?.leg(here, there, epochMs(date, end, zone)))
                ?: estimateLeg(if (before) there else here, if (before) here else there)
        else null
        val gap = if (it is PlanItem.Lesson) needMin(s, leg) else 0
        if (min < it.to + gap && end > it.from - gap) {
            val what = other?.name ?: (it as? PlanItem.Event)?.ev?.title ?: "занятием"
            return "занятие ${hhmm(min)}–${hhmm(end)} не помещается рядом с «$what» ${hhmm(it.from)}–${hhmm(it.to)}" +
                if (it is PlanItem.Lesson) " (дорога: " + (leg?.text()?.let { t -> "$t + ${s.travelExtra} мин запас" } ?: "${s.travelMin} мин") + ")" else ""
        }
    }
    return null
}

/** «Сколько ехать от Анны до Стаса»: маршрут по именам клиентов или «дом». */
fun routesText(d: AppData, pairs: List<Pair<String, String>>, now: Long, router: Router?): String = pairs.take(8).joinToString("\n") { (a, b) ->
    fun at(n: String): LatLng? = if (n.trim().lowercase() in setOf("дом", "домой", "из дома", "home")) {
        if (d.settings.hasHome) d.settings.homeLat!! to d.settings.homeLng!! else null
    } else clientByName(d, n)?.let { destOf(d, it) }
    val p = at(a); val q = at(b)
    if (p == null || q == null) "$a → $b: нет места на карте" else {
        val l = router?.leg(p, q, now + 3600_000) ?: estimateLeg(p, q)
        "$a → $b: ${l.text()}; с запасом ${l.min + d.settings.travelExtra} мин"
    }
}

/** Подтверждение плана словами в чате. */
fun isYes(text: String) = Regex("^\\s*(да|ага|ок|окей|ok|давай|делай|применить|подтверждаю|верно|все верно|го|\\+|👍)[\\s!.]*$", RegexOption.IGNORE_CASE).containsMatchIn(text)
fun isNo(text: String) = Regex("^\\s*(нет|не надо|отмена|стоп|не нужно)[\\s!.]*$", RegexOption.IGNORE_CASE).containsMatchIn(text)

/**
 * Отмена применённого плана: вернуть расписание, клиентов и дела как было, не трогая визиты и оплаты,
 * которые могли появиться после.
 */
fun undoPlan(now: AppData, before: AppData): AppData =
    now.copy(rules = before.rules, changes = before.changes, events = before.events,
        clients = before.clients + now.clients.filter { c -> before.clients.none { it.id == c.id } && now.visits.any { it.clientId == c.id } })
