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
        assertTrue("Осталось: 9 тренировок" in r, r)
        assertTrue("1. 07.10" in r, r)
    }

    @Test fun plurals() {
        assertEquals("1 тренировка", trainings(1))
        assertEquals("3 тренировки", trainings(3))
        assertEquals("11 тренировок", trainings(11))
        assertEquals("22 тренировки", trainings(22))
    }
}
