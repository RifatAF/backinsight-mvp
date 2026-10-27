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
        // 1 человек = 1 час: долгий визит всё равно одна тренировка.
        val s = visit(paid, "s", t(10), t(11, 45))
        assertEquals(1, s.data.visits.single().count)
        val b = balance(s.data, "s")
        assertEquals(9, b.left)
        assertEquals(9, b.leftInCurrent)
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
        assertEquals(2, m.sessions)
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
        assertEquals(t(10), d.visits.single().start)
        assertEquals(1, d.visits.single().count)
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
        val fam = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), lat = 3.0, lng = 3.0)
        val d0 = base.copy(clients = base.clients + fam)
        // Час: был один человек.
        assertEquals(1, visit(d0, "f", t(10), t(11)).data.visits.single().count)
        assertEquals(120, lessonMin(d0, fam))
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

    // ---------- Расписание ----------

    private fun date(day: Int) = java.time.LocalDate.of(2026, 10, day)

    /** Визит с геозоны: пришёл в h:m, ушёл через час. */
    private fun auto(id: String, client: String, day: Int, h: Int, m: Int) =
        Visit(id, client, t(h, m, day), t(h, m, day) + 3_600_000, 1)

    /** Визит семьи на 2 часа. */
    private fun auto2(id: String, client: String, day: Int, h: Int, m: Int) =
        Visit(id, client, t(h, m, day), t(h, m, day) + 7_200_000, 2)

    @Test fun rounding() {
        assertEquals(16 * 60, roundSlot(16 * 60 + 5))
        assertEquals(11 * 60 + 30, roundSlot(11 * 60 + 25))
        assertEquals(11 * 60 + 30, roundSlot(11 * 60 + 44))
        assertEquals(12 * 60, roundSlot(11 * 60 + 45))
    }

    @Test fun learnsWeeklyFromOneWeek() {
        // Ср 07.10 в 16:05 у Светланы → каждую среду в 16:00.
        val d = learnRules(base.copy(visits = listOf(auto("v", "s", 7, 16, 5))), t(20, day = 8))
        val r = d.rules.single()
        assertEquals(3, r.day)
        assertEquals(16 * 60, r.min)
        assertNull(r.parity)
        val next = dayPlan(d, date(14), t(9, day = 13)).filterIsInstance<PlanItem.Lesson>().single()
        assertEquals(Status.PLANNED, next.status)
        assertEquals("s", next.occ.clientId)
    }

    @Test fun learnsBiweekly() {
        // Егор: пн 05.10 и 19.10 в 10:00, пн 12.10 и 26.10 в 18:00 → два занятия через неделю.
        val vs = listOf(auto("a", "e", 5, 10, 0), auto("b", "e", 19, 9, 55), auto("c", "e", 12, 18, 10), auto("x", "e", 26, 18, 0))
        val d = learnRules(base.copy(visits = vs), t(20, day = 26))
        val morning = d.rules.single { it.min == 10 * 60 }
        assertEquals(parityOf(date(5)), morning.parity)
        assertEquals(parityOf(date(12)), d.rules.single { it.min == 18 * 60 }.parity)
        // 26.10 утра нет, 02.11 утро есть.
        assertTrue(occurrences(d, date(26)).none { it.min == 10 * 60 })
        assertTrue(occurrences(d, java.time.LocalDate.of(2026, 11, 2)).any { it.min == 10 * 60 })
    }

    @Test fun familyMovesDoNotDoubleSchedule() {
        // Ерлан и Рита по четвергам: две недели в 15:00, потом две недели в 16:00 (переносы).
        val fam = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), lat = 3.0, lng = 3.0)
        val vs = listOf(auto2("a", "f", 1, 15, 0), auto2("b", "f", 8, 15, 5), auto2("c", "f", 15, 16, 0), auto2("d", "f", 22, 15, 55))
        val d = learnRules(base.copy(clients = base.clients + fam, visits = vs), t(20, day = 22))
        assertEquals(listOf(16 * 60), d.rules.filter { it.clientId == "f" }.map { it.min })
        assertEquals(listOf(16 * 60 to 18 * 60), occurrences(d, date(29)).filter { it.clientId == "f" }.map { it.min to it.min + it.duration })
    }

    @Test fun alternatingWeeksLikeIgor() {
        // Неделя A: вт, чт 13:30. Неделя B: пн, ср, пт 11:30. Сегодня вт 27.10, неделя A.
        val a = listOf(13, 15, 27).map { auto("a$it", "e", it, 13, 35) }
        val b = listOf(5, 7, 9, 19, 21, 23).map { auto("b$it", "e", it, 11, 30) }
        val d = learnRules(base.copy(visits = a + b), t(20, day = 27))
        fun plan(day: java.time.LocalDate) = occurrences(d, day).filter { it.clientId == "e" }.map { it.min }
        assertEquals(listOf(13 * 60 + 30), plan(date(29)))      // чт 29.10
        assertEquals(emptyList(), plan(date(30)))                  // пт 30.10: недели A без пятницы
        assertEquals(listOf(11 * 60 + 30), plan(java.time.LocalDate.of(2026, 11, 2)))
        assertEquals(emptyList(), plan(java.time.LocalDate.of(2026, 11, 3)))
    }

    @Test fun oneLessonPerDay() {
        // Стас по пятницам 14:30, один раз пришёл в 17:00 (перенос): в пятницу одно занятие.
        val vs = listOf(auto("a", "s", 2, 14, 30), auto("b", "s", 9, 14, 30), auto("c", "s", 16, 17, 0), auto("d", "s", 23, 14, 35))
        val d = learnRules(base.copy(visits = vs), t(20, day = 27))
        assertEquals(listOf(14 * 60 + 30), occurrences(d, date(30)).map { it.min })
    }

    @Test fun fridayWithShiftingTimes() {
        // Ерлан и Рита по пятницам, время из-за переносов разное: 13:00, 15:00, 16:00, 15:00.
        val fam = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), lat = 3.0, lng = 3.0)
        val vs = listOf(auto2("a", "f", 2, 13, 0), auto2("b", "f", 9, 15, 0), auto2("c", "f", 16, 16, 0), auto2("d", "f", 23, 15, 5))
        val d = learnRules(base.copy(clients = base.clients + fam, visits = vs), t(20, day = 27))
        assertEquals(listOf(15 * 60 to null), d.rules.filter { it.clientId == "f" }.map { it.min to it.parity })
        // Пришли в 17:00 вместо 15:00: занятие отмечено как было в 17:00, без второго блока.
        val dd = d.copy(visits = d.visits + Visit("x", "f", t(17, 0, day = 30), t(19, 0, day = 30), 2))
        val plan = dayPlan(dd, date(30), t(20, day = 30))
        assertEquals(1, plan.count { it !is PlanItem.Window })
        val l = plan.filterIsInstance<PlanItem.Lesson>().single()
        assertEquals(Status.DONE, l.status)
        assertEquals(17 * 60 to 19 * 60, l.from to l.to)
    }

    @Test fun cancelSurvivesRelearnedTime() {
        var d = addRule(base, "s", 5, 15 * 60, null)
        val occ = occurrences(d, date(30)).single()
        d = cancelOnce(d, occ, date(30), burn = false)
        // Правило переучилось на 16:00: отмена на 30.10 остаётся.
        d = d.copy(rules = d.rules.map { it.copy(min = 16 * 60) })
        assertTrue(occurrences(d, date(30)).isEmpty())
    }

    @Test fun manualVisitCountsDayNotTime() {
        // Пятницы отмечены вручную вечером: день учится, время берётся из автовизитов.
        val vs = listOf(auto("a", "s", 2, 14, 30), Visit("b", "s", t(23, 6, day = 9), t(23, 6, day = 9), 1, VisitKind.MANUAL),
            auto("c", "s", 16, 14, 30), Visit("d", "s", t(23, 0, day = 23), t(23, 0, day = 23), 1, VisitKind.MANUAL))
        val d = learnRules(base.copy(visits = vs), t(20, day = 27))
        assertEquals(listOf(5 to 14 * 60 + 30), d.rules.map { it.day to it.min })
        assertNull(d.rules.single().parity)
    }

    @Test fun familyLengthFromVisitsAndSlots() {
        // Трое детей, а занятие обычно 2 часа: в расписании 2 часа, а не 3.
        val fam = Client("f", "Анна", members = listOf("Анна", "Макар", "Федя"), lat = 3.0, lng = 3.0)
        val d0 = base.copy(clients = base.clients + fam)
        assertEquals(180, lessonMin(d0, fam))
        assertEquals(120, lessonMin(d0.copy(visits = listOf(auto2("a", "f", 4, 17, 30), auto2("b", "f", 11, 17, 30))), fam))
        // Время из карточки клиента главнее выученного; ночная ручная запись ничему не учит.
        val c = sveta.copy(slots = listOf(Slot(3, 10 * 60)))
        val vs = listOf(auto("a", "s", 14, 11, 0), auto("b", "s", 21, 11, 0), Visit("n", "s", t(23, 6, day = 23), t(23, 6, day = 23), 1, VisitKind.MANUAL))
        val d = learnRules(base.copy(clients = listOf(c, egor), visits = vs), t(20, day = 27))
        assertEquals(listOf(3 to 10 * 60), d.rules.map { it.day to it.min })
    }

    @Test fun longStayAsksAboutLesson() {
        // Вне дома и зон клиентов (точка 0.1, 0.1), опрос каждые 15 минут.
        val d0 = onHomeLeave(base, t(13, 45)).data
        var d = d0
        val asks = mutableListOf<Effect>()
        for (m in listOf(0, 15, 30, 45, 60, 75)) {
            val st = onPoll(d, 0.1, 0.1, 20f, t(14, 0) + m * 60_000L)
            d = st.data; asks += st.effects.filterIsInstance<Effect.StayAsk>()
        }
        assertEquals(1, asks.size)  // спросили один раз, через час
        val withVisit = stayVisit(d, "e")
        val v = withVisit.visits.single()
        assertEquals("e", v.clientId)
        assertEquals(1, v.count)
        assertTrue(withVisit.stay!!.done)
        // «Не спрашивать здесь»: на следующий день в этом месте вопроса нет.
        var n = stayNo(d, ignore = true).copy(stay = null)
        val again = (0..5).flatMap { m -> onPoll(n, 0.1, 0.1, 20f, t(14, 0, day = 8) + m * 15 * 60_000L).also { n = it.data }.effects }
        assertTrue(again.none { it is Effect.StayAsk })
    }

    @Test fun homeArrivalOnlyByWifiWhenSsidSet() {
        val d = onHomeLeave(base.copy(settings = home.copy(homeSsid = "Totoli")), t(13, 21)).data
        // Неточное место «у дома» (кафе рядом): поездка продолжается.
        assertTrue(onHomeArrive(d, t(16, 40), byGeofence = true).data.openTrip != null)
        assertNull(onHomeArrive(d, t(19, 0)).data.openTrip)
    }

    @Test fun oldFamilyDurationMigrated() {
        val old = Client("f", "Ерлан и Рита", members = listOf("Ерлан", "Рита"), sessionMin = 120, minMin = 90)
        val m = perPersonMigrate(old)
        assertEquals(60, m.sessionMin)
        assertEquals(30, m.minMin)
        assertEquals(sveta, perPersonMigrate(sveta))
    }

    @Test fun staleRuleDropped() {
        val d = learnRules(base.copy(visits = listOf(auto("v", "s", 1, 16, 0))), t(12, day = 29))
        assertTrue(d.rules.isEmpty())
    }

    @Test fun planDoneMissedAndWindows() {
        val s = home.copy(startMin = 9 * 60, endMin = 20 * 60)
        var d = base.copy(settings = s)
        d = addRule(d, "s", 3, 10 * 60, null)
        d = addRule(d, "e", 3, 16 * 60, null)
        d = d.copy(visits = listOf(auto("v", "s", 7, 10, 10)))
        val plan = dayPlan(d, date(7), t(19, day = 7))
        val lessons = plan.filterIsInstance<PlanItem.Lesson>()
        assertEquals(Status.DONE, lessons.first { it.occ.clientId == "s" }.status)
        assertEquals(Status.MISSED, lessons.first { it.occ.clientId == "e" }.status)
        val w = plan.filterIsInstance<PlanItem.Window>().map { hhmm(it.from) + "-" + hhmm(it.to) }
        // До 10:00 только 30 минут на дорогу: окна нет. После занятий +30 минут, перед занятием −30.
        assertEquals(listOf("11:30-15:30", "17:30-20:00"), w)
    }

    @Test fun moveCancelExtra() {
        var d = addRule(base, "s", 3, 10 * 60, null)
        val occ = occurrences(d, date(7)).single()
        d = moveOnce(d, occ, date(7), date(8), 15 * 60)
        assertTrue(occurrences(d, date(7)).isEmpty())
        val moved = occurrences(d, date(8)).single()
        assertEquals(15 * 60, moved.min)
        assertEquals(date(7) to 10 * 60, moved.movedFrom)
        // Перенос переносим ещё раз, затем отменяем со списанием.
        d = moveOnce(d, moved, date(8), date(9), 11 * 60)
        val again = occurrences(d, date(9)).single()
        d = cancelOnce(d, again, date(9), burn = true)
        assertTrue(occurrences(d, date(9)).isEmpty())
        assertEquals(VisitKind.BURNED, d.visits.single().kind)
        // Следующая неделя по расписанию не тронута.
        assertEquals(10 * 60, occurrences(d, date(14)).single().min)
        d = addExtra(d, "e", date(14), 18 * 60)
        assertTrue(occurrences(d, date(14)).single { it.clientId == "e" }.extra)
    }

    @Test fun editAndRemoveLearnedRuleStick() {
        var d = learnRules(base.copy(visits = listOf(auto("v", "s", 7, 16, 0))), t(20, day = 8))
        val r = d.rules.single()
        d = editRule(d, r, 3, 17 * 60, null)
        d = learnRules(d, t(20, day = 9))
        assertEquals(listOf(17 * 60), d.rules.filter { !it.off }.map { it.min })
        d = removeRule(d, d.rules.single { !it.off })
        d = learnRules(d, t(20, day = 9))
        assertTrue(occurrences(d, date(14)).isEmpty())
    }

    @Test fun reminders() {
        var d = base.copy(visits = listOf(auto("v", "s", 1, 9, 0)))
        assertEquals(0, readyInDays(d, t(20, day = 8)))
        assertEquals(6, readyInDays(d, t(20, day = 2)))
        d = addRule(d, "s", 4, 9 * 60, null) // чт 08.10 9:00
        d = addRule(d, "e", 4, 14 * 60, null)
        val (at, occ) = nextAlarm(d, t(20, day = 7))!!
        assertEquals(t(8, 15, day = 8), at)
        assertEquals("s", occ.clientId)
        val (title, body) = tomorrowText(d, t(21, day = 7))!!
        assertTrue("2 тренировки" in title, title)
        assertTrue("9:00–10:00 Светлана" in body && "14:00–15:00 Егор" in body, body)
        // Первое занятие после 12:00: будильник не нужен.
        val late = removeRule(d, d.rules.single { it.clientId == "s" })
        assertNull(nextAlarm(late, t(20, day = 7)))
        assertEquals(t(21, 30, day = 7), nextEvening(home, t(20, day = 7)))
        assertEquals(t(21, 30, day = 8), nextEvening(home, t(22, day = 7)))
    }

    @Test fun cafeUsesLearnedSchedule() {
        val d = withCafe("Аня" to emptyList(), "Борис" to emptyList())
        val withRules = addRule(addRule(d, "Аня", 3, 18 * 60, null), "Борис", 3, 19 * 60, null)
        val s = cafeStay(withRules, t(17, 55), t(20, 5))
        assertEquals(listOf("Аня", "Борис"), s.data.visits.sortedBy { it.start }.map { it.clientId })
    }

    @Test fun findWindowWithTravel() {
        val s = home.copy(startMin = 9 * 60, endMin = 20 * 60)
        var d = base.copy(settings = s)
        d = addRule(d, "s", 3, 10 * 60, null) // 10:00–11:00
        d = addRule(d, "e", 3, 14 * 60, null) // 14:00–15:00
        val ws = findWindows(d, date(7), 1, 60, 30, t(7, day = 7))
        // 9:00 до 10:00 нужно ехать к 10:00 → нет; 11:30–12:30 … 12:30–13:30; после 15:30 до 20:00.
        assertEquals(listOf("11:30-12:30", "15:30-19:00"), ws.map { hhmm(it.earliest) + "-" + hhmm(it.latest) })
        val txt = windowsText(ws, 60)
        assertTrue("Ср 07.10: с 11:30 до 12:30, с 15:30 до 19:00" in txt, txt)
        // Сегодня после 16:10 окно начинается не раньше 17:00 (дорога 30 мин, округление).
        val later = findWindows(d, date(7), 1, 60, 30, t(16, 10, day = 7))
        assertEquals(17 * 60, later.single().earliest)
    }

    @Test fun splitVisitDoesNotShiftWindow() {
        val s = home.copy(startMin = 9 * 60, endMin = 20 * 60)
        // Занятие 16:00–17:00, телефон на минуту «вышел» из зоны: два куска визита.
        val v1 = Visit("v1", "s", t(16, 5, day = 7), t(16, 40, day = 7), 1)
        val v2 = Visit("v2", "s", t(16, 45, day = 7), t(17, 5, day = 7), 0)
        var d = base.copy(settings = s, visits = listOf(v1, v2))
        d = addRule(d, "s", 3, 16 * 60, null)
        assertTrue(dayPlan(d, date(7), t(18, day = 7)).none { it is PlanItem.Unplanned })
        val ws = findWindows(d, date(7), 1, 60, 30, t(7, day = 7))
        assertEquals(17 * 60 + 30, ws.last().earliest)
    }

    @Test fun soonReminderSkipsAlarmedLesson() {
        var d = base.copy(visits = listOf(auto("v", "s", 1, 9, 0)))
        d = addRule(d, "s", 4, 9 * 60, null)   // чт 9:00: будильник
        d = addRule(d, "e", 4, 14 * 60, null)  // чт 14:00: напоминание 13:30
        val (at, occ) = nextSoon(d, t(20, day = 7))!!
        assertEquals(t(13, 30, day = 8), at)
        assertEquals("e", occ.clientId)
        assertNull(nextSoon(d.copy(settings = d.settings.copy(soonOn = false)), t(20, day = 7)))
    }

    @Test fun familyMessage() {
        val s = home.copy(startMin = 9 * 60, endMin = 20 * 60)
        var d = base.copy(settings = s, clients = listOf(sveta.copy(address = "Beruti, 3393"), egor))
        d = addRule(d, "s", 4, 10 * 60, null)
        d = addRule(d, "e", 4, 14 * 60, null)
        val txt = familyText(d, t(21, day = 7), withAddress = true)
        assertTrue(txt.startsWith("План на завтра, чт 08.10:"), txt)
        assertTrue("10:00–11:00 Светлана (Beruti, 3393)" in txt, txt)
        assertTrue("14:00–15:00 Егор\n" in txt, txt)
        assertTrue("Начало 10:00, конец 15:00" in txt, txt)
        assertTrue("Перерывы: 11:30–13:30" in txt, txt)
        assertEquals("План на завтра, пт 09.10:\nЗанятий нет", familyText(d, t(21, day = 8), true))
    }
}
