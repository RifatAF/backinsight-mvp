package com.rifat.trainercount.logic

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class BotTest {
    private val today = LocalDate.of(2026, 10, 27) // вторник
    private fun d(day: Int, mon: Int = 10) = LocalDate.of(2026, mon, day)

    @Test fun plans() {
        assertEquals(BotCmd.Plan(d(30)), parseBot("А что в пятницу?", today))
        assertEquals(BotCmd.Plan(d(30)), parseBot("какое расписание 30", today))
        assertEquals(BotCmd.Plan(d(30)), parseBot("расписание на 30", today))
        assertEquals(BotCmd.Plan(d(30)), parseBot("Пятница 30 какое расписание", today))
        assertEquals(BotCmd.Plan(d(28)), parseBot("завтра", today))
        assertEquals(BotCmd.Plan(d(2, 11)), parseBot("что 2 ноября", today))
        assertEquals(BotCmd.Plan(d(5, 11)), parseBot("расписание на 5", today))
        assertEquals(BotCmd.Week(today), parseBot("расписание на неделю", today))
    }

    @Test fun addEvents() {
        assertEquals(BotCmd.Add(d(30), 17 * 60, 60, "Стоматолог Лера"), parseBot("30.10 17:00 стоматолог Лера", today))
        assertEquals(BotCmd.Add(d(30), 17 * 60, 60, "Стоматолог у Леры"), parseBot("тридцатого десятого 17:00 стоматолог у Леры", today))
        assertEquals(BotCmd.Add(d(30), 17 * 60, 120, "Стоматолог"), parseBot("запиши тридцатого в 17 стоматолог на 2 часа", today))
        assertEquals(BotCmd.Add(d(28), 17 * 60, 30, "Родительское собрание"), parseBot("завтра в 5 вечера родительское собрание на 30 минут", today))
        assertEquals(BotCmd.Add(d(29), 9 * 60 + 30, 90, "Лера врач"), parseBot("в четверг с 9:30 до 11:00 Лера врач", today))
        assertTrue(parseBot("30.10 17:00", today) is BotCmd.Unknown)
    }

    @Test fun otherCommands() {
        assertEquals(BotCmd.Where, parseBot("Где Рифат?", today))
        assertEquals(BotCmd.Help, parseBot("/start", today))
        assertEquals(BotCmd.Free(d(29)), parseBot("свободно в четверг?", today))
        assertEquals(BotCmd.Free(null), parseBot("когда есть окна", today))
        assertEquals(BotCmd.Remove(d(30), "стоматолог"), parseBot("удали 30.10 стоматолог", today))
    }

    @Test fun answerAddsToScheduleAndWarns() {
        val z = ZoneId.systemDefault()
        val now = LocalDateTime.of(2026, 10, 27, 20, 0).atZone(z).toInstant().toEpochMilli()
        var data = AppData(clients = listOf(Client("er", "Ерлан и Рита", lat = 1.0, lng = 1.0)))
        data = addRule(data, "er", 5, 16 * 60 + 30, null)
        val (nd, txt) = botAnswer(data, parseBot("30.10 17:00 стоматолог Лера", today), now, { "" })
        assertEquals(1, nd.events.size)
        assertTrue("Пересекается: 16:30–17:30 Ерлан и Рита" in txt, txt)
        val plan = botAnswer(nd, parseBot("что в пятницу", today), now, { "" }).second
        assertTrue("17:00–18:00 Стоматолог Лера (личное)" in plan, plan)
        // Личное дело занимает время в поиске окон.
        assertTrue(findWindows(nd, d(30), 1, 60, 30, now).none { it.earliest in 16 * 60..18 * 60 })
        val (rd, rt) = botAnswer(nd, parseBot("удали 30.10 стоматолог", today), now, { "" })
        assertTrue(rd.events.isEmpty(), rt)
    }

    @Test fun aiAnswerToCommand() {
        val add = parseAi("""{"cmd": "add", "date": "2026-10-29", "time": "14:00", "minutes": null, "title": "стоматолог Лера"}""", today)
        assertEquals(BotCmd.Add(d(29), 14 * 60, 60, "Стоматолог Лера"), add)
        assertEquals(BotCmd.Plan(d(30)), parseAi("""{"cmd":"plan","date":"2026-10-30","time":null}""", today))
        assertEquals(BotCmd.Where, parseAi("""{"cmd":"where"}""", today))
        assertEquals(null, parseAi("""{"cmd":"add","date":"2026-10-29","time":null,"title":"врач"}""", today))
        assertEquals(null, parseAi("не JSON", today))
        assertTrue("2026-10-27, вторник" in aiPrompt("привет", today))
    }

    @Test fun smartContextAndReply() {
        val z = ZoneId.systemDefault()
        val now = LocalDateTime.of(2026, 10, 27, 16, 40).atZone(z).toInstant().toEpochMilli()
        var data = AppData(clients = listOf(Client("er", "Ерлан и Рита", address = "Tamborini 2626", lat = 1.0, lng = 1.0)))
        data = addRule(data, "er", 2, 16 * 60, null)
        val ctx = aiContext(data, now, "в дороге")
        assertTrue("Последнее занятие сегодня: 16:00–17:00 Ерлан и Рита, адрес: Tamborini 2626" in ctx, ctx)
        assertTrue("Где Рифат: в дороге" in ctx, ctx)
        val prompt = aiChatPrompt(ctx, listOf("где Рифат?" to "В дороге"), "куда мне приехать?")
        assertTrue("куда мне приехать?" in prompt && "Недавняя переписка" in prompt)
        val r = parseAiReply("""{"reply": "Он закончит в 17:00 на Tamborini 2626.\nПодъезжай туда.", "add": null, "remove": null}""", today)!!
        assertEquals("Он закончит в 17:00 на Tamborini 2626.\nПодъезжай туда.", r.reply)
        assertEquals(null, r.add)
        val r2 = parseAiReply("""{"reply":"Записал","add":{"date":"2026-10-29","time":"14:00","minutes":90,"title":"зубной Лера"},"remove":null}""", today)!!
        assertEquals(BotCmd.Add(d(29), 14 * 60, 90, "Зубной Лера"), r2.add)
    }

    @Test fun clientBot() {
        val z = ZoneId.systemDefault()
        val now = LocalDateTime.of(2026, 10, 27, 14, 0).atZone(z).toInstant().toEpochMilli()
        val ira = Client("ira", "Ира", lat = 1.0, lng = 1.0)
        val stas = Client("st", "Стас", lat = 2.0, lng = 2.0)
        var data = AppData(clients = listOf(ira, stas), packs = listOf(Pack("p", "ira", now - 86_400_000L, 4, 120)))
        data = addRule(addRule(data, "ira", 2, 16 * 60, null), "st", 2, 13 * 60 + 30, null)
        assertEquals("ira", inviteClientId("/start c_ira"))
        assertEquals("https://t.me/RifatRasp_bot?start=c_ira", inviteLink("@RifatRasp_bot", ira))
        assertEquals(ClientCmd.Balance, parseClientBot("Сколько занятий осталось?"))
        assertEquals(ClientCmd.Lessons, parseClientBot("Когда следующее занятие?"))
        assertEquals(ClientCmd.Where, parseClientBot("Рифат скоро будет?"))
        assertTrue(parseClientBot("Можно перенести на четверг") is ClientCmd.Message)
        assertEquals("Осталось 4 занятия из 4.", clientBalanceText(data, ira))
        // Рифат у Стаса: Ире не называем, у кого он, и не даём карту.
        val busy = data.copy(open = mapOf("st" to now - 30 * 60_000L))
        val st = rifatStatus(busy, ira, now, home = false)
        val where = clientWhereText(busy, ira, now, st)
        assertEquals("Рифат сейчас на тренировке, освободится примерно в 14:30. Ваше занятие сегодня в 16:00.", where)
        assertTrue("Стас" !in clientAiPrompt(busy, ira, now, st, emptyList(), "где он?"))
        assertTrue("16:00–17:00" in clientLessonsText(data, ira, now))
        assertEquals("Перенесите" to true, parseClientAi("""{"reply": "Перенесите", "forward": true}"""))
    }
}
