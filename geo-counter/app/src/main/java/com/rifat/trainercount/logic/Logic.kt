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

/** 1 тренировка от minMin, каждая следующая через sessionMin (+10 мин запаса на неточность выхода). */
fun sessionsFor(durationMin: Long, c: Client): Int {
    if (durationMin < c.minMin) return 0
    val extra = (durationMin - c.minMin - SLACK_MIN).coerceAtLeast(0) / c.sessionMin.coerceAtLeast(1)
    return (1 + extra.toInt()).coerceAtMost(MAX_PER_VISIT)
}

/** Минимальная длительность для n тренировок, для подсказки в настройках. */
fun thresholdFor(n: Int, c: Client): Int =
    if (n <= 1) c.minMin else c.minMin + SLACK_MIN + (n - 1) * c.sessionMin

// ---------- Баланс ----------

data class Balance(
    val paid: Int,
    val used: Int,
    /** Пакет, который сейчас расходуется (null: всё израсходовано или оплат нет). */
    val current: Pack?,
    val leftInCurrent: Int,
) {
    val left get() = paid - used
}

fun balance(d: AppData, clientId: String): Balance {
    val packs = d.packs.filter { it.clientId == clientId }.sortedBy { it.paidAt }
    val used = d.visits.filter { it.clientId == clientId }.sumOf { it.count }
    var u = used
    for (p in packs) {
        if (u < p.sessions) return Balance(packs.sumOf { it.sessions }, used, p, p.sessions - u)
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
}

data class Step(val data: AppData, val effects: List<Effect> = emptyList())

private fun closeVisit(d: AppData, clientId: String, t: Long, forced: Boolean): Step {
    val start = d.open[clientId] ?: return Step(d)
    val base = d.copy(open = d.open - clientId)
    val c = d.client(clientId) ?: return Step(base)
    var count = sessionsFor((t - start) / MIN, c)
    if (count == 0) return Step(base)
    if (forced) count = 1
    val v = Visit(newId(), clientId, start, t, count, VisitKind.AUTO, check = forced)
    return Step(base.copy(visits = base.visits + v), listOf(Effect.VisitAdded(v)))
}

private fun closeAllVisits(d: AppData, t: Long, except: String? = null): Step {
    var cur = Step(d)
    for (id in d.open.keys.filter { it != except }) {
        val s = closeVisit(cur.data, id, t, forced = true)
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

fun onClientEnter(d: AppData, clientId: String, t: Long): Step {
    val c = d.client(clientId) ?: return Step(d)
    if (c.archived || clientId in d.open || !inHours(d.settings, t)) return Step(d)
    val closed = closeAllVisits(d, t, except = clientId)
    var nd = closed.data.copy(open = closed.data.open + (clientId to t))
    if (nd.openTrip == null && nd.settings.hasHome) nd = nd.copy(trips = nd.trips + Trip(newId(), t))
    return Step(nd, closed.effects)
}

fun onClientExit(d: AppData, clientId: String, t: Long): Step = closeVisit(d, clientId, t, forced = false)

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

fun onHomeArrive(d: AppData, t: Long): Step {
    val closed = closeAllVisits(d, t)
    return Step(closeTrip(closed.data, t), closed.effects)
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
