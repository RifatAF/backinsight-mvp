package com.rifat.trainercount.logic

import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneId
import java.util.UUID

private const val MIN = 60_000L
const val SLACK_MIN = 10
const val MAX_PER_VISIT = 4
const val SHORT_TRIP_MIN = 5

fun newId(): String = UUID.randomUUID().toString().take(12)

const val PLACE = "p:"
/** Насколько раньше времени по расписанию можно прийти, чтобы занятие с ним совпало. */
const val SLOT_EARLY_MIN = 20

/** 1 тренировка от minMin, каждая следующая через sessionMin (+10 мин запаса на неточность выхода). */
fun sessionsFor(durationMin: Long, sessionMin: Int, minMin: Int): Int {
    if (durationMin < minMin) return 0
    val extra = (durationMin - minMin - SLACK_MIN).coerceAtLeast(0) / sessionMin.coerceAtLeast(1)
    return (1 + extra.toInt()).coerceAtMost(MAX_PER_VISIT)
}

fun sessionsFor(durationMin: Long, c: Client): Int = sessionsFor(durationMin, c.sessionMin, c.minMin)

/** Минимальная длительность для n тренировок, для подсказки в настройках. */
fun thresholdFor(n: Int, sessionMin: Int, minMin: Int): Int =
    if (n <= 1) minMin else minMin + SLACK_MIN + (n - 1) * sessionMin

fun thresholdFor(n: Int, c: Client): Int = thresholdFor(n, c.sessionMin, c.minMin)

fun distanceM(lat1: Double, lng1: Double, lat2: Double, lng2: Double): Double {
    val r = 6_371_000.0
    val dLat = Math.toRadians(lat2 - lat1)
    val dLng = Math.toRadians(lng2 - lng1)
    val a = Math.sin(dLat / 2).let { it * it } +
        Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2)) * Math.sin(dLng / 2).let { it * it }
    return 2 * r * Math.asin(Math.sqrt(a))
}

// ---------- Баланс ----------

data class Balance(
    val paid: Int,
    val used: Int,
    /** Пакет, который сейчас расходуется (null: всё израсходовано или оплат нет). */
    val current: Pack?,
    val leftInCurrent: Int,
    /** Сумма текущей и следующих оплат: «осталось left из activeTotal». */
    val activeTotal: Int = 0,
) {
    val left get() = paid - used
}

fun balance(d: AppData, clientId: String): Balance {
    val packs = d.packs.filter { it.clientId == clientId }.sortedBy { it.paidAt }
    val used = d.visits.filter { it.clientId == clientId }.sumOf { it.count }
    var u = used
    for (p in packs) {
        if (u < p.sessions) {
            val active = packs.dropWhile { it !== p }.sumOf { it.sessions }
            return Balance(packs.sumOf { it.sessions }, used, p, p.sessions - u, active)
        }
        u -= p.sessions
    }
    return Balance(packs.sumOf { it.sessions }, used, null, 0)
}

/** Визиты, отнесённые к пакету по очереди оплат (для отчёта клиенту). */
fun visitsOfPack(d: AppData, pack: Pack): List<Visit> {
    val packs = d.packs.filter { it.clientId == pack.clientId }.sortedBy { it.paidAt }
    val idx = packs.indexOfFirst { it.id == pack.id }
    if (idx < 0) return emptyList()
    val from = packs.take(idx).sumOf { it.sessions }
    val to = from + pack.sessions
    var pos = 0
    val out = mutableListOf<Visit>()
    for (v in d.visits.filter { it.clientId == pack.clientId }.sortedBy { it.start }) {
        val a = pos
        pos += v.count
        if (pos > from && a < to) out += v
    }
    return out
}

fun pricePerSession(d: AppData, c: Client): Int {
    if (c.price > 0) return c.price
    val last = d.packs.filter { it.clientId == c.id && it.sessions > 0 }.maxByOrNull { it.paidAt } ?: return 0
    return last.amount / last.sessions
}

// ---------- Рабочие часы ----------

fun inHours(s: Settings, t: Long, zone: ZoneId = ZoneId.systemDefault()): Boolean {
    val z = Instant.ofEpochMilli(t).atZone(zone)
    if (z.dayOfWeek.value !in s.days) return false
    val m = z.hour * 60 + z.minute
    return if (s.startMin <= s.endMin) m in s.startMin until s.endMin
    else m >= s.startMin || m < s.endMin
}

// ---------- События ----------

sealed class Effect {
    data class VisitAdded(val visit: Visit) : Effect()
    /** Занятия в общем месте разобраны по расписанию. */
    data class PlaceAuto(val place: Place, val visits: List<Visit>) : Effect()
    /** Занятия в общем месте, нужно отметить с кем. */
    data class PendingAdded(val pending: Pending) : Effect()
}

data class Step(val data: AppData, val effects: List<Effect> = emptyList())

private fun closeVisit(d: AppData, clientId: String, t: Long, forced: Boolean): Step {
    val start = d.open[clientId] ?: return Step(d)
    val base = d.copy(open = d.open - clientId)
    val c = d.client(clientId) ?: return Step(base)
    var per = sessionsFor((t - start) / MIN, c)
    if (per == 0) return Step(base)
    if (forced) per = 1
    val v = withMembers(Visit(newId(), clientId, start, t, per, VisitKind.AUTO, check = forced), c, c.members, per)
    return Step(base.copy(visits = base.visits + v), listOf(Effect.VisitAdded(v)))
}

/** Сколько занятий на одного человека в визите семьи. */
fun perPerson(v: Visit, c: Client?): Int =
    if (c?.countsEach == true && v.members.isNotEmpty()) (v.count / v.members.size).coerceAtLeast(1) else v.count

/** Визит с указанным составом: в семье с раздельным счётом count = занятий на человека × пришедших. */
fun withMembers(v: Visit, c: Client, members: List<String>, per: Int): Visit {
    val who = if (c.isFamily) members.filter { it in c.members } else emptyList()
    val n = if (c.countsEach) per * who.size.coerceAtLeast(1) else per
    return v.copy(members = if (c.countsEach) who.ifEmpty { c.members.take(1) } else who, count = n)
}

/** Клиенты общего места, у которых время по расписанию попало в пребывание, по порядку. */
fun matchSlots(d: AppData, place: Place, start: Long, end: Long, zone: ZoneId = ZoneId.systemDefault()): List<Pair<Client, Long>> {
    val dates = setOf(dayOf(start, zone), dayOf(end, zone))
    val latest = end - place.minMin * MIN
    val out = mutableListOf<Pair<Client, Long>>()
    for (c in d.clients.filter { it.placeId == place.id && !it.archived }) {
        val at = c.slots.flatMap { s ->
            dates.filter { it.dayOfWeek.value == s.day }
                .map { it.atStartOfDay(zone).plusMinutes(s.min.toLong()).toInstant().toEpochMilli() }
        }.filter { it in (start - SLOT_EARLY_MIN * MIN)..latest }.minOrNull() ?: continue
        out += c to at
    }
    return out.sortedBy { it.second }
}

private fun closePlace(d: AppData, placeId: String, t: Long, forced: Boolean): Step {
    val key = PLACE + placeId
    val start = d.open[key] ?: return Step(d)
    val base = d.copy(open = d.open - key)
    val p = d.place(placeId) ?: return Step(base)
    val count = sessionsFor((t - start) / MIN, p.sessionMin, p.minMin)
    if (count == 0) return Step(base)
    val matched = matchSlots(base, p, start, t)
    if (!forced && matched.size == count) {
        val vs = matched.map { (c, at) ->
            val from = maxOf(at, start)
            Visit(newId(), c.id, from, minOf(from + p.sessionMin * MIN, t), 1)
        }
        return Step(base.copy(visits = base.visits + vs), listOf(Effect.PlaceAuto(p, vs)))
    }
    val pd = Pending(newId(), p.id, start, t, count, matched.map { it.first.id })
    return Step(base.copy(pending = base.pending + pd), listOf(Effect.PendingAdded(pd)))
}

private fun closeOpen(d: AppData, key: String, t: Long, forced: Boolean): Step =
    if (key.startsWith(PLACE)) closePlace(d, key.removePrefix(PLACE), t, forced) else closeVisit(d, key, t, forced)

private fun closeAllVisits(d: AppData, t: Long, except: String? = null): Step {
    var cur = Step(d)
    for (key in d.open.keys.filter { it != except }) {
        val s = closeOpen(cur.data, key, t, forced = true)
        cur = Step(s.data, cur.effects + s.effects)
    }
    return cur
}

private fun closeTrip(d: AppData, end: Long): AppData {
    val trip = d.openTrip ?: return d
    val trips = d.trips - trip
    if ((end - trip.start) / MIN < SHORT_TRIP_MIN) return d.copy(trips = trips)
    return d.copy(trips = trips + trip.copy(end = end))
}

private fun enter(d: AppData, key: String, t: Long): Step {
    if (key in d.open || !inHours(d.settings, t)) return Step(d)
    val closed = closeAllVisits(d, t, except = key)
    var nd = closed.data.copy(open = closed.data.open + (key to t))
    if (nd.openTrip == null && nd.settings.hasHome) nd = nd.copy(trips = nd.trips + Trip(newId(), t))
    return Step(nd, closed.effects)
}

fun onClientEnter(d: AppData, clientId: String, t: Long): Step {
    val c = d.client(clientId) ?: return Step(d)
    if (c.archived) return Step(d)
    return enter(d, clientId, t)
}

fun onClientExit(d: AppData, clientId: String, t: Long): Step = closeVisit(d, clientId, t, forced = false)

fun onPlaceEnter(d: AppData, placeId: String, t: Long): Step {
    d.place(placeId) ?: return Step(d)
    return enter(d, PLACE + placeId, t)
}

fun onPlaceExit(d: AppData, placeId: String, t: Long): Step = closePlace(d, placeId, t, forced = false)

/** Отметить, с кем было занятие в общем месте. picks: clientId → сколько занятий. */
fun resolvePending(d: AppData, pendingId: String, picks: List<Pair<String, Int>>): AppData {
    val p = d.pending.firstOrNull { it.id == pendingId } ?: return d
    val total = picks.sumOf { it.second }.coerceAtLeast(1)
    val span = p.end - p.start
    var acc = 0
    val vs = picks.filter { it.second > 0 }.map { (id, n) ->
        val from = p.start + span * acc / total
        acc += n
        Visit(newId(), id, from, p.start + span * acc / total, n)
    }
    return d.copy(pending = d.pending - p, visits = d.visits + vs)
}

fun dismissPending(d: AppData, pendingId: String): AppData = d.copy(pending = d.pending.filter { it.id != pendingId })

/** Место клиента или общее место рядом с домом: геозона дома там тоже срабатывает. */
private fun nearHome(d: AppData, key: String): Boolean {
    val s = d.settings
    val hl = s.homeLat ?: return false
    val hg = s.homeLng ?: return false
    val (lat, lng, r) = if (key.startsWith(PLACE)) {
        val p = d.place(key.removePrefix(PLACE)) ?: return false
        Triple(p.lat, p.lng, p.radius)
    } else {
        val c = d.client(key) ?: return false
        Triple(c.lat ?: return false, c.lng ?: return false, c.radius)
    }
    return distanceM(hl, hg, lat, lng) < s.homeRadius + r
}

fun onHomeLeave(d: AppData, t: Long): Step {
    if (!inHours(d.settings, t)) return Step(d)
    val stale = d.openTrip
    var nd = d
    if (stale != null) {
        // Возвращение домой не заметили: закрываем прошлый выход по последнему визиту.
        val lastEnd = nd.visits.filter { it.start >= stale.start && it.end <= t }.maxOfOrNull { it.end }
        nd = closeTrip(nd, lastEnd ?: stale.start)
    }
    return Step(nd.copy(trips = nd.trips + Trip(newId(), t)))
}

/**
 * Вернулись домой. byGeofence: событие от зоны дома. Если сейчас идёт занятие в месте рядом с домом
 * (кафе через дорогу), зона дома срабатывает и там, поэтому такое событие пропускаем: дом подтвердит Wi‑Fi.
 */
fun onHomeArrive(d: AppData, t: Long, byGeofence: Boolean = false): Step {
    if (byGeofence && d.open.keys.any { nearHome(d, it) }) return Step(d)
    val closed = closeAllVisits(d, t)
    return Step(closeTrip(closed.data, t), closed.effects)
}

// ---------- Страховочная проверка места ----------

const val HOME_KEY = "home"
/** Если с прошлой проверки прошло не больше, время входа/выхода берём посередине. */
const val POLL_GAP_MIN = 40

data class Zone(val key: String, val name: String, val lat: Double, val lng: Double, val radius: Int)

fun zones(d: AppData): List<Zone> = buildList {
    val s = d.settings
    if (s.homeLat != null && s.homeLng != null) add(Zone(HOME_KEY, "Дом", s.homeLat, s.homeLng, s.homeRadius))
    d.clients.filter { !it.archived && it.hasPlace && it.placeId == null }
        .forEach { add(Zone(it.id, it.name, it.lat!!, it.lng!!, it.radius)) }
    d.places.forEach { add(Zone(PLACE + it.id, it.name, it.lat, it.lng, it.radius)) }
}

/** Ближайшая зона и расстояние до её края (отрицательное: внутри). */
fun nearestZone(d: AppData, lat: Double, lng: Double): Pair<Zone, Double>? =
    zones(d).map { it to distanceM(lat, lng, it.lat, it.lng) - it.radius }.minByOrNull { it.second }

/**
 * Фоновая проверка места раз в 15 минут. Нужна на случай, когда система не прислала событие геозоны.
 * Внутри зоны: расстояние до центра не больше радиуса + 25 м. Точно снаружи: больше радиуса + погрешность + 50 м.
 */
fun onPoll(d: AppData, lat: Double, lng: Double, acc: Float, t: Long): Step {
    val prev = d.lastPoll
    val est = if (prev != null && t - prev in 0..POLL_GAP_MIN * MIN) (prev + t) / 2 else t
    val margin = acc.coerceIn(0f, 150f)
    val all = zones(d).map { it to distanceM(lat, lng, it.lat, it.lng) }
    fun inside(z: Pair<Zone, Double>) = z.second <= z.first.radius + 25
    fun outside(z: Pair<Zone, Double>) = z.second > z.first.radius + margin + 50

    var cur = Step(d.copy(lastPoll = t))
    fun apply(s: Step) { cur = Step(s.data, cur.effects + s.effects) }

    // 1. Ушли от клиента или из кафе, а выход не пришёл.
    for (key in cur.data.open.keys.toList()) {
        val z = all.firstOrNull { it.first.key == key } ?: continue
        if (outside(z)) apply(
            if (key.startsWith(PLACE)) onPlaceExit(cur.data, key.removePrefix(PLACE), est)
            else onClientExit(cur.data, key, est)
        )
    }
    val home = all.firstOrNull { it.first.key == HOME_KEY }
    val atHome = home != null && inside(home)
    // 2. Пришли к клиенту, а вход не пришёл.
    val here = all.filter { it.first.key != HOME_KEY && inside(it) }.minByOrNull { it.second }
    if (here != null && here.first.key !in cur.data.open) {
        val key = here.first.key
        apply(if (key.startsWith(PLACE)) onPlaceEnter(cur.data, key.removePrefix(PLACE), est) else onClientEnter(cur.data, key, est))
    }
    // 3. Дом.
    if (home != null) {
        if (atHome && here == null && cur.data.openTrip != null) apply(onHomeArrive(cur.data, est, byGeofence = true))
        else if (outside(home) && cur.data.openTrip == null) apply(onHomeLeave(cur.data, est))
    }
    return cur
}

// ---------- Статистика ----------

fun isWork(d: AppData, trip: Trip, now: Long): Boolean =
    trip.work ?: d.visits.any { it.kind != VisitKind.BURNED && it.start >= trip.start && it.start <= (trip.end ?: now) }

fun dayOf(t: Long, zone: ZoneId = ZoneId.systemDefault()): LocalDate = Instant.ofEpochMilli(t).atZone(zone).toLocalDate()

data class ClientMonth(val client: Client, val sessions: Int, val burned: Int, val earned: Int)

data class MonthStats(
    val sessions: Int,
    val burned: Int,
    val earned: Int,
    val received: Int,
    val workMin: Long,
    val atClientsMin: Long,
    val perClient: List<ClientMonth>,
    val trips: List<Trip>,
) {
    val roadMin get() = (workMin - atClientsMin).coerceAtLeast(0)
    val perHour get() = if (workMin > 0) (earned * 60 / workMin).toInt() else 0
}

fun monthStats(d: AppData, ym: YearMonth, now: Long, zone: ZoneId = ZoneId.systemDefault()): MonthStats {
    fun inMonth(t: Long) = YearMonth.from(Instant.ofEpochMilli(t).atZone(zone)) == ym
    val visits = d.visits.filter { inMonth(it.start) }
    val per = d.clients.mapNotNull { c ->
        val vs = visits.filter { it.clientId == c.id }
        if (vs.isEmpty()) return@mapNotNull null
        val n = vs.sumOf { it.count }
        ClientMonth(c, n, vs.filter { it.kind == VisitKind.BURNED }.sumOf { it.count }, n * pricePerSession(d, c))
    }.sortedByDescending { it.sessions }
    val trips = d.trips.filter { inMonth(it.start) }.sortedByDescending { it.start }
    val workTrips = trips.filter { isWork(d, it, now) }
    val workMin = workTrips.sumOf { ((it.end ?: now) - it.start) / MIN }
    val atClients = visits.filter { it.kind != VisitKind.BURNED }.sumOf { (it.end - it.start) / MIN }
    return MonthStats(
        sessions = per.sumOf { it.sessions },
        burned = per.sumOf { it.burned },
        earned = per.sumOf { it.earned },
        received = d.packs.filter { inMonth(it.paidAt) }.sumOf { it.amount },
        workMin = workMin,
        atClientsMin = atClients,
        perClient = per,
        trips = trips,
    )
}
