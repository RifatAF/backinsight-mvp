package com.rifat.trainercount.logic

import kotlin.test.Test
import kotlin.test.assertTrue

class SubteTest {
    @Test fun routes() {
        // Обелиск → метро Ministro Carranza: линия D напрямую.
        val l = subteLeg(-34.6037 to -58.3816, -34.5757 to -58.4354)
        println(l.how)
        assertTrue("линия D" in l.how && l.min in 20..40, l.how)
        // Пересадка: Plaza de Mayo (A) → Ministro Carranza (D).
        val t = subteLeg(-34.6085 to -58.3717, -34.5757 to -58.4354)
        println(t.how)
        assertTrue("линия D" in t.how && t.min in 20..45, t.how)
        // Рядом: только пешком.
        val w = subteLeg(-34.5757 to -58.4354, -34.5800 to -58.4300)
        assertTrue(w.how.startsWith("пешком") && w.min < 15, w.how)
        // Палермо → Боэдо: с пересадкой.
        val x = subteLeg(-34.5850 to -58.4200, -34.6300 to -58.4180)
        println(x.how)
        assertTrue("пересадка" in x.how || x.how.startsWith("пешком"), x.how)
    }
}
