package com.rifat.trainercount.logic

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class BybitTest {
    private fun dep(id: String, amount: Double, uid: String = "", mail: String = "", t: Long = 2_000, coin: String = "USDT", status: Int = 2) =
        Deposit(id, coin, amount, status, mail, uid, t)

    @Test fun autoPay() {
        val data = AppData(
            clients = listOf(
                Client("a", "Анна", price = 30, payId = "123456", autoPay = true),
                Client("i", "Игорь", price = 40, perVisit = true, payId = "Igor@Mail.com", autoPay = true),
                Client("s", "Стас", price = 30, payId = "777"), // автоподтверждение выключено
            ),
        )
        val deps = listOf(
            dep("1", 300.0, uid = "123456"),
            dep("2", 80.0, mail = "igor@mail.com"),
            dep("3", 90.0, uid = "777"),
            dep("4", 300.0, uid = "123456", t = 500), // до подключения
            dep("5", 300.0, uid = "123456", status = 1), // ещё не зачислен
            dep("6", 10.0, uid = "123456"), // меньше занятия
            dep("7", 0.5, uid = "123456", coin = "BTC"),
        )
        val (d, ev) = applyDeposits(data, deps, since = 1_000)
        assertEquals(listOf(10, 2), d.packs.map { it.sessions })
        assertEquals(listOf("bb:1", "bb:2"), d.packs.map { it.id })
        assertTrue(ev[2] is PayEvent.Unknown && ev[2].dep.id == "3", ev.toString())
        assertTrue(ev[3] is PayEvent.Unclear, ev.toString())
        assertEquals(0, unpaid(d.copy(visits = listOf(Visit("v", "i", 0, 1, 2))), d.client("i")!!))
        // Повторная проверка ничего не дублирует.
        val (d2, ev2) = applyDeposits(d, deps, since = 1_000)
        assertEquals(2, d2.packs.size)
        assertTrue(ev2.none { it is PayEvent.Paid })
        assertTrue("Оплата получена ✓ 300 USDT, это 10 занятий" in paidClientText(d, ev[0] as PayEvent.Paid))
    }

    @Test fun trc20() {
        val data = AppData(clients = listOf(Client("a", "Анна", price = 30, payId = "123456, TXabcWallet1", autoPay = true)))
        val deps = listOf(
            Deposit("c1", "USDT", 150.0, 2, "TXabcWallet1", "", 2_000, chain = "TRX"),
            Deposit("c2", "USDT", 300.0, 2, "", "", 2_000, chain = "TRX"), // с биржи: отправитель неизвестен
        )
        val (d, ev) = applyDeposits(data, deps, since = 1_000)
        assertEquals(listOf(5), d.packs.map { it.sessions })
        assertTrue(ev[1] is PayEvent.Unknown)
        assertEquals("150 USDT (TRC20)", fCoin(deps[0]))
        assertTrue("другой биржи" in payerText(deps[1]))
    }

    @Test fun unpaidVisits() {
        val d = AppData(
            clients = listOf(Client("a", "Анна", price = 30)),
            packs = listOf(Pack("p", "a", 0, 2, 60)),
            visits = listOf(Visit("v1", "a", 10, 20, 1), Visit("v2", "a", 30, 40, 1), Visit("v3", "a", 50, 60, 1)),
        )
        assertEquals(setOf("v3"), unpaidVisitIds(d, "a"))
        assertEquals(emptySet(), unpaidVisitIds(d.copy(packs = d.packs + Pack("bb:1", "a", 70, 1, 30)), "a"))
    }
}
