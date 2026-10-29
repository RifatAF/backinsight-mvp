package com.rifat.trainercount.logic

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class AgentTest {
    private val z = ZoneId.systemDefault()
    private fun t(day: Int, h: Int, m: Int = 0) = LocalDateTime.of(2026, 10, day, h, m).atZone(z).toInstant().toEpochMilli()
    private val thu = LocalDate.of(2026, 10, 29)

    private fun data(): AppData {
        var d = AppData(
            clients = listOf(
                Client("ig", "Игорь", lat = -34.58, lng = -58.43),
                Client("pe", "Петр Мария", members = listOf("Петр", "Мария"), lat = -34.60, lng = -58.38),
                Client("an", "Анна", lat = -34.575, lng = -58.435),
            ),
            settings = Settings(homeLat = -34.62, homeLng = -58.44, startMin = 9 * 60, endMin = 21 * 60),
        )
        d = addRule(d, "ig", 4, 13 * 60 + 30, null)
        d = addRule(d, "pe", 4, 10 * 60, null)
        d = addRule(d, "an", 2, 14 * 60, null)
        return d
    }

    @Test fun onlyPetrToday() {
        val now = t(29, 8)
        val raw = """```json
{"reply": "Понял: сегодня только Петр в 16:30.",
 "actions": [{"type": "cancel", "client": "Игорь", "date": "2026-10-29"},
             {"type": "move", "client": "Петр", "date": "2026-10-29", "to_date": "2026-10-29", "to_time": "16:30"}],
 "query": null}
```"""
        val r = parseAgent(raw)!!
        val (nd, steps) = applyActions(data(), r.actions, now, z)
        assertTrue(steps.all { it.ok }, steps.toString())
        val plan = dayPlan(nd, thu, now, z).filterIsInstance<PlanItem.Lesson>()
        assertEquals(listOf("pe" to 16 * 60 + 30), plan.map { it.occ.clientId to it.from })
        assertEquals("Перенести: Петр Мария, чт 29.10 10:00 → 16:30", steps[1].text)
        // Отмена плана возвращает расписание.
        val back = undoPlan(nd, data())
        assertEquals(2, dayPlan(back, thu, now, z).filterIsInstance<PlanItem.Lesson>().size)
    }

    @Test fun newClientNearCarranza() {
        val now = t(29, 8)
        val q = parseAgent("""{"reply": "", "actions": [], "query": {"after": "14:00", "minutes": 60, "place": "Estación Ministro Carranza, Buenos Aires", "lat": -34.5752, "lng": -58.4349}}""")!!.query!!
        assertEquals(14 * 60, q.after)
        val slots = findSlotsText(data(), q, now, z)
        // Вторник после Анны (живёт рядом с Каррансой) лучше всего: почти без лишней дороги.
        val first = slots.lines().first().substringAfter(") ")
        assertTrue(first.startsWith("вт 03.11") && "до этого Анна до 15:00" in first && "с 15:30" in first, slots)
        val raw = """{"reply": "Лучше всего вторник сразу после Анны: она в 600 м.",
 "actions": [{"type": "add_client", "name": "Виталий", "address": "Estación Ministro Carranza, Buenos Aires", "lat": -34.5752, "lng": -58.4349},
             {"type": "add_rule", "client": "Виталий", "day": 2, "time": "15:30"}]}"""
        val (nd, steps) = applyActions(data(), parseAgent(raw)!!.actions, now, z)
        assertTrue(steps.all { it.ok }, steps.toString())
        val v = nd.clients.first { it.name == "Виталий" }
        assertTrue(v.hasPlace)
        assertTrue(nd.rules.any { it.clientId == v.id && it.day == 2 && it.min == 15 * 60 + 30 && it.manual })
        assertEquals("Постоянно: Виталий, вт 15:30, с вт 03.11", steps[1].text)
    }

    @Test fun contextAndConfirm() {
        val ctx = agentContext(data(), t(29, 8), "дома", z)
        assertTrue("Игорь: " in ctx && "постоянно чт 13:30" in ctx && "Дата сегодня: 2026-10-29" in ctx, ctx)
        assertTrue("13:30–14:30 Игорь" in ctx, ctx)
        assertTrue(isYes("Да") && isYes("ок!") && !isYes("да, но Игоря оставь"))
        assertTrue(isNo("нет"))
        val bad = applyActions(data(), parseAgent("""{"reply":"","actions":[{"type":"cancel","client":"Вася","date":"2026-10-29"}]}""")!!.actions, t(29, 8), z).second
        assertTrue(!bad.single().ok)
        // Длинная просьба без ИИ не записывается делом.
        assertTrue(parseBot("Смотри сегодня будет только Петр и в 16.30. Также у меня новый клиент Виталий после 14.00", thu) is BotCmd.Unknown)
    }

    @Test fun askAboutPastEvents() {
        val ev = Personal("e", LocalDate.of(2026, 10, 28).toEpochDay(), 14 * 60, 60, "Апто медико")
        val d = data().copy(events = listOf(ev))
        assertTrue(eventsToAsk(d, t(28, 14, 30), z).isEmpty())
        assertEquals(listOf("e"), eventsToAsk(d, t(29, 9), z).map { it.id })
        val no = setWent(d, "e", false)
        assertTrue(eventsToAsk(no, t(29, 9), z).isEmpty())
        assertTrue("Апто медико (личное дело) [не был]" in agentContext(no, t(28, 8), "дома", z))
    }

    @Test fun rejectsClashes() {
        val now = t(29, 8)
        val d = data().copy(clients = data().clients + Client("vi", "Виталий", lat = -34.575, lng = -58.435))
        // Игорь по четвергам 13:30–14:30: Виталию в 14:30 нужно ещё 30 мин на дорогу.
        assertTrue(clashText(d, "vi", thu, 14 * 60 + 30, now, z)!!.contains("Игорь"))
        assertEquals(null, clashText(d, "vi", thu, 15 * 60, now, z))
        val (_, steps) = applyActions(d, Json.parse("""{"actions":[{"type":"add_rule","client":"Виталий","day":4,"time":"13:00"}]}""").list("actions"), now, z)
        assertTrue(!steps.single().ok && "Игорь" in steps.single().text, steps.toString())
        val ctx = agentContext(d, now, "дома", z)
        assertTrue("Свободные окна на 14 дней" in ctx, ctx)
        println(ctx.substringAfter("Свободные окна").take(400))
    }

    @Test fun usesRoutes() {
        val now = t(29, 8)
        val d = data().copy(clients = data().clients + Client("vi", "Виталий", lat = -34.575, lng = -58.435))
        // Маршрут от Игоря 40 мин на метро: с запасом 10 мин Виталий в 15:00 уже не успевает, в 15:30 успевает.
        val slow = Router { _, _, _ -> Leg(40, 12, "40 мин: пешком 5 мин → метро D 28 мин → пешком 7 мин", true) }
        val why = clashText(d, "vi", thu, 15 * 60, now, z, slow)!!
        assertTrue("метро D" in why && "+ 10 мин запас" in why, why)
        assertEquals(null, clashText(d, "vi", thu, 15 * 60 + 30, now, z, slow))
        val r = parseAgent("""{"reply":"","actions":[],"routes":[{"from":"Игорь","to":"Анна"},{"from":"дом","to":"Вася"}]}""")!!
        val txt = routesText(d, r.routes, now, slow)
        assertTrue("Игорь → Анна: 40 мин" in txt && "с запасом 50 мин" in txt && "Вася: нет места" in txt, txt)
        // Слоты: в каждом рабочем дне есть вариант, воскресенье тоже.
        val q = SlotQuery(14 * 60, 21 * 60, 60, -34.5752, -58.4349, "")
        val slots = findSlotsText(d.copy(settings = d.settings.copy(days = (1..7).toSet())), q, now, z, slow)
        assertTrue("вс 01.11" in slots && "пт 30.10" in slots, slots)
    }

    @Test fun realEndsIdleAndPairs() {
        val now = t(29, 8)
        // Петр и Мария по четвергам по плану 10:00–12:00, а по факту последние разы уходил в 13:00.
        var d = data()
        d = d.copy(visits = listOf(15, 22).map { day -> Visit("v$day", "pe", t(day, 10), t(day, 13), 2, members = listOf("Петр", "Мария")) })
        val plan = dayPlan(d, LocalDate.of(2026, 11, 5), now, z).filterIsInstance<PlanItem.Lesson>().first { it.occ.clientId == "pe" }
        assertEquals(13 * 60, realEnd(d, plan, LocalDate.of(2026, 11, 5), z))
        assertTrue(clashText(d, "an", LocalDate.of(2026, 11, 5), 13 * 60, now, z)!!.contains("Петр Мария"))
        // Пары дней на 2 раза в неделю: не подряд.
        val q = SlotQuery(14 * 60, 21 * 60, 60, -34.5752, -58.4349, "", perWeek = 2)
        val slots = findSlotsText(d.copy(settings = d.settings.copy(days = (1..7).toSet())), q, now, z)
        assertTrue("Лучшие пары дней на 2 раза в неделю" in slots && "A) " in slots, slots)
        // Поправить время постоянного занятия.
        val (nd, steps) = applyActions(d, Json.parse("""{"actions":[{"type":"set_rule","client":"Петр","day":4,"time":"16:30"},{"type":"set_length","client":"Анна","minutes":120}]}""").list("actions"), now, z)
        assertTrue(steps.all { it.ok }, steps.toString())
        assertTrue(nd.rules.any { it.clientId == "pe" && it.day == 4 && it.min == 16 * 60 + 30 && !it.off })
        assertEquals(120, nd.client("an")!!.sessionMin)
    }
}
