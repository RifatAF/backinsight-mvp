package com.rifat.trainercount.logic

import java.time.LocalDateTime
import java.time.YearMonth
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class LogicTest {
    private val sveta = Client("s", "Светлана", lat = 1.0, lng = 1.0)
    private val egor = Client("e", "Егор", lat = 2.0, lng = 2.0)
    private val home = Settings(homeLat = 0.0, homeLng = 0.0)
    private val base = AppData(clients = listOf(sveta, egor), settings = home)

    private fun t(h: Int, m: Int = 0, day: Int = 7) =
        LocalDateTime.of(2026, 10, day, h, m).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()

    private fun visit(d: AppData, id: String, from: Long, to: Long): Step {
        val a = onClientEnter(d, id, from)
        val b = onClientExit(a.data, id, to)
        return Step(b.data, a.effects + b.effects)
    }

    @Test fun counting() {
        assertEquals(0, sessionsFor(29, sveta))
        assertEquals(1, sessionsFor(30, sveta))
        assertEquals(1, sessionsFor(99, sveta))
        assertEquals(2, sessionsFor(100, sveta))
        assertEquals(3, sessionsFor(160, sveta))
        assertEquals(MAX_PER_VISIT, sessionsFor(900, sveta))
        assertEquals(100, thresholdFor(2, sveta))
    }

    @Test fun shortStopIsIgnored() {
        val s = visit(base, "s", t(10), t(10, 20))
        assertTrue(s.data.visits.isEmpty())
        assertTrue(s.data.open.isEmpty())
    }

    @Test fun doubleSessionAndBalance() {
        val paid = base.copy(packs = listOf(Pack("p", "s", t(8, day = 1), 10, 10000)))
        val s = visit(paid, "s", t(10), t(11, 45))
        assertEquals(2, s.data.visits.single().count)
        val b = balance(s.data, "s")
        assertEquals(8, b.left)
        assertEquals(8, b.leftInCurrent)
        assertEquals(1000, pricePerSession(s.data, sveta))
    }

    @Test fun fifoPacks() {
        val d = base.copy(
            packs = listOf(Pack("p1", "s", t(8, day = 1), 2, 0), Pack("p2", "s", t(8, day = 2), 10, 0)),
            visits = (1..3).map { Visit("v$it", "s", t(10 + it), t(10 + it), 1) },
        )
        val b = balance(d, "s")
        assertEquals("p2", b.current?.id)
        assertEquals(9, b.leftInCurrent)
        assertEquals(listOf("v3"), visitsOfPack(d, d.packs[1]).map { it.id })
        assertEquals(listOf("v1", "v2"), visitsOfPack(d, d.packs[0]).map { it.id })
    }

    @Test fun debtWhenNoPack() {
        val s = visit(base, "s", t(10), t(11))
        val b = balance(s.data, "s")
        assertEquals(-1, b.left)
        assertNull(b.current)
    }

    @Test fun workDayFlow() {
        var d = onHomeLeave(base, t(9)).data
        assertEquals(t(9), d.openTrip?.start)
        d = visit(d, "s", t(10), t(11)).data
        d = visit(d, "e", t(12), t(13, 50)).data
        d = onHomeArrive(d, t(15)).data
        assertNull(d.openTrip)
        val trip = d.trips.single()
        assertEquals(t(15), trip.end)
        assertTrue(isWork(d, trip, t(16)))
        val m = monthStats(d, YearMonth.of(2026, 10), t(16))
        assertEquals(3, m.sessions)
        assertEquals(6 * 60L, m.workMin)
        assertEquals(60L + 110L, m.atClientsMin)
    }

    @Test fun personalTripAndWifiBlip() {
        var d = onHomeLeave(base, t(18)).data
        d = onHomeArrive(d, t(18, 40)).data
        assertTrue(!isWork(d, d.trips.single(), t(20)))
        // Wi‑Fi моргнул дома: выход на 2 минуты не сохраняется.
        d = onHomeLeave(d, t(21)).data
        d = onHomeArrive(d, t(21, 2)).data
        assertEquals(1, d.trips.size)
    }

    @Test fun missedExitIsForcedAndFlagged() {
        var d = onClientEnter(base, "s", t(10)).data
        val s = onClientEnter(d, "e", t(14))
        d = s.data
        val v = d.visits.single()
        assertEquals("s", v.clientId)
        assertEquals(1, v.count)
        assertTrue(v.check)
        assertEquals(setOf("e"), d.open.keys)
    }

    @Test fun duplicateEnterKeepsFirst() {
        var d = onClientEnter(base, "s", t(10)).data
        d = onClientEnter(d, "s", t(10, 40)).data
        d = onClientExit(d, "s", t(11, 45)).data
        assertEquals(2, d.visits.single().count)
    }

    @Test fun outsideHoursIgnored() {
        val d = base.copy(settings = home.copy(startMin = 8 * 60, endMin = 20 * 60))
        assertTrue(onClientEnter(d, "s", t(21)).data.open.isEmpty())
        assertNull(onHomeLeave(d, t(7)).data.openTrip)
    }

    @Test fun missedHomeArrivalClosedOnNextLeave() {
        var d = onHomeLeave(base, t(9)).data
        d = visit(d, "s", t(10), t(11)).data
        d = onHomeLeave(d, t(9, day = 8)).data
        assertEquals(2, d.trips.size)
        assertEquals(t(11), d.trips.first().end)
        assertEquals(t(9, day = 8), d.openTrip?.start)
    }

    @Test fun report() {
        val paid = base.copy(packs = listOf(Pack("p", "s", t(8, day = 1), 10, 10000)))
        val d = visit(paid, "s", t(10), t(11)).data
        val r = reportText(d, sveta)
        assertTrue("Оплачено: 10 занятий (01.10.2026)" in r, r)
        assertTrue("1. Ср 07.10, 10:00" in r, r)
        assertTrue("Проведено: 1 из 10\nОсталось: 9 занятий" in r, r)
    }

    @Test fun reportSkipsUsedPacksAndNumbersFamily() {
        val fam = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), lat = 3.0, lng = 3.0)
        val d = base.copy(
            clients = base.clients + fam,
            packs = listOf(Pack("p1", "f", t(8, day = 1), 2, 0), Pack("p2", "f", t(8, day = 5), 4, 0)),
            visits = listOf(
                Visit("v1", "f", t(10, day = 2), t(10, day = 2), 2, VisitKind.MANUAL, listOf("Ерлан", "Рита")),
                Visit("v2", "f", t(10, day = 6), t(10, day = 6), 2, VisitKind.MANUAL, listOf("Ерлан", "Рита")),
                Visit("v3", "f", t(10, day = 7), t(10, day = 7), 1, VisitKind.MANUAL, listOf("Рита")),
            ),
        )
        val r = reportText(d, fam)
        assertTrue("Оплачено: 4 занятия (05.10.2026)" in r, r)
        assertTrue("1–2. Вт 06.10 · Ерлан, Рита" in r, r)
        assertTrue("3. Ср 07.10 · Рита" in r, r)
        assertTrue("02.10" !in r, r)
        assertTrue("Проведено: 3 из 4\nОсталось: 1 занятие" in r, r)
    }

    @Test fun reportDebt() {
        val d = base.copy(
            packs = listOf(Pack("p", "s", t(8, day = 1), 1, 0)),
            visits = (2..4).map { Visit("v$it", "s", t(10, day = it), t(10, day = it), 1, VisitKind.MANUAL) },
        )
        val r = reportText(d, sveta)
        assertTrue("Последняя оплата: 1 занятие" in r, r)
        assertTrue("1. Сб 03.10" in r && "2. Вс 04.10" in r, r)
        assertTrue("Сверх оплаты: 2 занятия, нужна оплата" in r, r)
    }

    @Test fun familyCountsEachMember() {
        val fam = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), lat = 3.0, lng = 3.0, sessionMin = 120, minMin = 90)
        val d0 = base.copy(clients = base.clients + fam)
        val d = visit(d0, "f", t(10), t(12)).data
        val v = d.visits.single()
        assertEquals(2, v.count)
        assertEquals(listOf("Ерлан", "Рита"), v.members)
        assertEquals(1, perPerson(v, fam))
        val onlyRita = withMembers(v, fam, listOf("Рита"), 1)
        assertEquals(1, onlyRita.count)
        // Без раздельного счёта: одно занятие на всю семью.
        val joint = fam.copy(perMember = false)
        val d2 = visit(d0.copy(clients = base.clients + joint), "f", t(10), t(12)).data
        assertEquals(1, d2.visits.single().count)
    }

    @Test fun plurals() {
        assertEquals("1 тренировка", trainings(1))
        assertEquals("3 тренировки", trainings(3))
        assertEquals("11 тренировок", trainings(11))
        assertEquals("22 тренировки", trainings(22))
    }

    // ---------- Общее место (кафе) ----------

    private val cafe = Place("cafe", "Кафе", 0.0005, 0.0, radius = 80)

    private fun withCafe(vararg slots: Pair<String, List<Slot>>): AppData {
        val online = slots.map { (name, sl) -> Client(name, name, placeId = "cafe", slots = sl) }
        return base.copy(clients = base.clients + online, places = listOf(cafe))
    }

    private fun cafeStay(d: AppData, from: Long, to: Long): Step {
        val a = onPlaceEnter(d, "cafe", from)
        val b = onPlaceExit(a.data, "cafe", to)
        return Step(b.data, a.effects + b.effects)
    }

    @Test fun cafeBySchedule() {
        val d = withCafe("Аня" to listOf(Slot(3, 18 * 60)), "Борис" to listOf(Slot(3, 19 * 60)), "Вера" to listOf(Slot(5, 18 * 60)))
        val s = cafeStay(d, t(17, 55), t(20, 5))
        val vs = s.data.visits.sortedBy { it.start }
        assertEquals(listOf("Аня", "Борис"), vs.map { it.clientId })
        assertEquals(t(18), vs[0].start)
        assertEquals(t(19), vs[1].start)
        assertTrue(s.data.pending.isEmpty())
        assertTrue(s.effects.single() is Effect.PlaceAuto)
    }

    @Test fun cafeWithoutScheduleAsks() {
        val d = withCafe("Аня" to emptyList(), "Борис" to emptyList())
        val s = cafeStay(d, t(18), t(19, 50))
        assertTrue(s.data.visits.isEmpty())
        val p = s.data.pending.single()
        assertEquals(2, p.count)
        assertTrue(p.suggested.isEmpty())
        val r = resolvePending(s.data, p.id, listOf("Аня" to 1, "Борис" to 1))
        assertTrue(r.pending.isEmpty())
        assertEquals(listOf(1, 1), r.visits.map { it.count })
        assertEquals(t(18), r.visits[0].start)
        assertEquals(r.visits[0].end, r.visits[1].start)
        assertEquals(t(19, 50), r.visits[1].end)
    }

    @Test fun cafeScheduleMismatchSuggests() {
        // По расписанию одна Аня, а сидели на два занятия: спросить, подставив Аню.
        val d = withCafe("Аня" to listOf(Slot(3, 18 * 60)), "Борис" to emptyList())
        val p = cafeStay(d, t(18), t(19, 50)).data.pending.single()
        assertEquals(listOf("Аня"), p.suggested)
        assertEquals(2, p.count)
    }

    @Test fun cafeShortStayIgnoredAndDismiss() {
        val d = withCafe("Аня" to emptyList())
        assertTrue(cafeStay(d, t(18), t(18, 15)).data.pending.isEmpty())
        val p = cafeStay(d, t(18), t(19)).data
        assertTrue(dismissPending(p, p.pending.single().id).pending.isEmpty())
    }

    @Test fun cafeNextToHomeIgnoresHomeGeofence() {
        // Кафе в 55 м от дома: зона дома срабатывает внутри кафе, это не возвращение домой.
        val d = withCafe("Аня" to listOf(Slot(3, 18 * 60)))
        var x = onPlaceEnter(d, "cafe", t(17, 58)).data
        x = onHomeArrive(x, t(18, 30), byGeofence = true).data
        assertEquals(setOf("p:cafe"), x.open.keys)
        // Wi‑Fi дома: это точно дом, занятие закрывается.
        val y = onHomeArrive(x, t(19, 5))
        assertTrue(y.data.open.isEmpty())
        assertTrue(y.data.pending.isNotEmpty())
    }

    @Test fun distance() {
        val m = distanceM(55.0, 37.0, 55.001, 37.0)
        assertTrue(m in 110.0..112.0, "$m")
    }

    // ---------- Страховочная проверка ----------

    @Test fun pollCatchesMissedVisit() {
        // Геозоны молчали: проверки в 9:50 (у дома нет, вышли), 10:05 и 10:50 у Светланы, 11:05 ушли.
        var s = onPoll(base, 0.003, 0.0, 20f, t(9, 50))
        assertTrue(s.data.openTrip != null)
        s = onPoll(s.data, 1.0, 1.0, 20f, t(10, 5))
        assertEquals(t(9, 57) + 30_000, s.data.open["s"])
        s = onPoll(s.data, 1.0, 1.0, 20f, t(10, 50))
        s = onPoll(s.data, 1.01, 1.0, 20f, t(11, 5))
        val v = s.data.visits.single()
        assertEquals("s", v.clientId)
        assertEquals(1, v.count)
        assertTrue(s.effects.single() is Effect.VisitAdded)
        // Дома: выход закрывается.
        s = onPoll(s.data, 0.0, 0.0, 20f, t(11, 35))
        assertNull(s.data.openTrip)
    }

    @Test fun pollKeepsGeofenceTimes() {
        // Вход пришёл от геозоны в 10:00, проверка не должна сдвинуть время.
        var d = onClientEnter(base, "s", t(10)).data
        d = onPoll(d, 1.0, 1.0, 20f, t(10, 10)).data
        assertEquals(t(10), d.open["s"])
    }

    @Test fun pollIgnoresInaccurateFix() {
        // Погрешность 400 м рядом с краем зоны: не выходим.
        var d = onClientEnter(base, "s", t(10)).data
        d = onPoll(d, 1.0025, 1.0, 400f, t(10, 30)).data
        assertEquals(setOf("s"), d.open.keys)
    }

    @Test fun nearest() {
        val (z, edge) = nearestZone(base, 1.0, 1.001)!!
        assertEquals("s", z.key)
        assertTrue(edge < 0)
    }
}
