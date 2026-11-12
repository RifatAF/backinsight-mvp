package com.rifat.trainercount.geo

import android.content.Context
import android.location.Geocoder
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AgentPlan
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.agentContext
import com.rifat.trainercount.logic.agentPrompt
import com.rifat.trainercount.logic.applyActions
import com.rifat.trainercount.logic.findSlotsText
import com.rifat.trainercount.logic.parseAgent
import com.rifat.trainercount.logic.routesText
import com.rifat.trainercount.logic.undoPlan
import java.util.Locale

/**
 * ИИ-помощник по расписанию для чата в приложении и для бота. Думает в два шага: если нужно подобрать
 * время новому клиенту, приложение само считает окна и дорогу и отдаёт их ИИ. Изменения выполняются
 * только после подтверждения.
 */
object Assistant {
    /** План, ждущий подтверждения: действия ИИ (выполняются заново на свежих данных). */
    class Proposal(val plan: AgentPlan, val actions: List<Any?>)

    @Volatile private var smart: List<String>? = null
    // Для журнала: сколько ждали ИИ и какая модель ответила.
    @Volatile private var lastMs = 0L
    @Volatile private var lastModel = ""
    @Volatile private var lastErr = ""

    private fun ask(prompt: String, think: Int = 0): String? {
        val s = Store.data.value.settings
        if (s.aiKey.isBlank()) return null
        // Flash с коротким «обдумыванием»: отвечает за секунды. Pro медленный (до минуты), его не ждём:
        // если Flash не ответил, сразу облегчённая модель.
        val models = smart ?: runCatching { Gemini.pickSmart(s.aiKey) }.getOrNull()?.filter { "pro" !in it }?.also { smart = it }.orEmpty()
        for (m in (models + s.aiModel.ifBlank { null }.let { listOfNotNull(it) }).distinct()) {
            val t0 = System.currentTimeMillis()
            runCatching { Gemini.ask(s.aiKey, m, prompt, think) }
                .onFailure { lastErr = "$m: ${it.message?.take(80)}" }
                .getOrNull()?.takeIf { it.isNotBlank() }?.let { lastModel = m; lastMs += System.currentTimeMillis() - t0; return it }
            lastMs += System.currentTimeMillis() - t0
        }
        val t0 = System.currentTimeMillis()
        return runCatching { Gemini.ask(s.aiKey, Gemini.pickModel(s.aiKey).also { lastModel = it }, prompt) }.getOrNull()
            .also { lastMs += System.currentTimeMillis() - t0 }
    }

    private fun geocode(ctx: Context, place: String): Pair<Double, Double>? {
        if (place.isBlank() || !Geocoder.isPresent()) return null
        return runCatching {
            @Suppress("DEPRECATION")
            Geocoder(ctx, Locale("es", "AR")).getFromLocationName(place, 1)?.firstOrNull()?.let { it.latitude to it.longitude }
        }.getOrNull()
    }

    /** Уточнить координаты нового клиента по адресу: карта точнее, чем догадка ИИ. */
    private fun refine(ctx: Context, actions: List<Any?>): List<Any?> = actions.map { a ->
        val m = a as? Map<*, *> ?: return@map a
        if (m["type"] != "add_client") return@map a
        val at = geocode(ctx, (m["address"] as? String).orEmpty()) ?: return@map a
        m.toMutableMap().apply { put("lat", at.first); put("lng", at.second) }
    }

    /** Подумать над сообщением. null: ИИ недоступен. status: где Рифат (для вопросов «где он»). */
    fun think(ctx: Context, text: String, history: List<Pair<String, String>>, status: String): Proposal? {
        val now = System.currentTimeMillis()
        lastMs = 0; lastModel = ""; lastErr = ""
        val d = Store.data.value
        val context = agentContext(d, now, status)
        return thinkInner(ctx, d, now, context, text, history).also {
            val total = System.currentTimeMillis() - now
            Diag.add(ctx, "Помощник: ${total / 1000.0} с (ИИ ${lastMs / 1000.0} с, $lastModel)" + if (lastErr.isNotBlank()) "; сбой $lastErr" else "")
        }
    }

    private fun thinkInner(ctx: Context, d: com.rifat.trainercount.logic.AppData, now: Long, context: String, text: String, history: List<Pair<String, String>>): Proposal? {
        var r = parseAgent(ask(agentPrompt(context, history, text), think = 512) ?: return null) ?: return null
        // Дороги для проверки «успеет ли»: из сети не дольше 8 секунд, дальше только сохранённые.
        val online = Routes.router(ctx)
        val offline = Routes.router(ctx, offline = true)
        var until = 0L
        val router = com.rifat.trainercount.logic.Router { a, b, t ->
            val n = System.currentTimeMillis()
            if (until == 0L) until = n + 8_000
            if (n < until) online.leg(a, b, t) else offline.leg(a, b, t)
        }
        // ИИ попросил окна или маршруты: считаем сами и отдаём ему.
        if (r.query != null || r.routes.isNotEmpty()) {
            val info = buildList {
                r.query?.let { q0 ->
                    val at = geocode(ctx, q0.place)
                    val q = if (at != null) q0.copy(lat = at.first, lng = at.second) else q0
                    add("Окна:\n" + findSlotsText(d, q, now, router = router))
                }
                if (r.routes.isNotEmpty()) add("Маршруты:\n" + routesText(d, r.routes, now, router))
            }.joinToString("\n\n")
            r = parseAgent(ask(agentPrompt(context, history, text, info), think = 1024) ?: return null) ?: r
        }
        var actions = refine(ctx, r.actions)
        var (nd, steps) = applyActions(d, actions, now, router = router)
        // Проверка не пропустила часть действий: один раз просим ИИ поправиться с объяснением причин.
        val rejected = steps.filter { !it.ok }
        if (rejected.isNotEmpty()) {
            val fb = rejected.joinToString("\n") { it.text }
            parseAgent(ask(agentPrompt(context, history, text, null, fb)) ?: "")?.let { r2 ->
                val a2 = refine(ctx, r2.actions)
                val (nd2, s2) = applyActions(d, a2, now, router = router)
                if (s2.count { !it.ok } < rejected.size) { r = r2; actions = a2; nd = nd2; steps = s2 }
            }
        }
        return Proposal(AgentPlan(r.reply.ifBlank { if (steps.isEmpty()) "Не понял, напишите подробнее." else "" }, steps, nd), actions)
    }

    /** Выполнить план. Возвращает данные до изменения (для отмены) и итог. */
    fun apply(ctx: Context, p: Proposal): Pair<AppData, String> {
        val before = Store.data.value
        var steps = emptyList<com.rifat.trainercount.logic.AgentStep>()
        val router = Routes.router(ctx, offline = true)
        Store.update { d -> applyActions(d, p.actions, System.currentTimeMillis(), router = router).also { steps = it.second }.first }
        Planner.refresh(ctx)
        Diag.add(ctx, "Помощник: выполнено ${steps.count { it.ok }} действий")
        val done = steps.filter { it.ok }.joinToString("\n") { "✓ " + it.text.substringBefore("\n   ⚠") }
        val fail = steps.filter { !it.ok }.joinToString("\n") { "✗ " + it.text }
        return before to listOf("Готово.", done, fail).filter { it.isNotBlank() }.joinToString("\n")
    }

    fun undo(ctx: Context, before: AppData) {
        Store.update { undoPlan(it, before) }
        Planner.refresh(ctx)
    }
}
