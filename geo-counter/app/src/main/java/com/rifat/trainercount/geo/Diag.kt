package com.rifat.trainercount.geo

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Журнал работы: что и когда пришло от системы. Последние 1500 строк, только в телефоне. */
object Diag {
    private const val MAX = 1500
    private val fmt = DateTimeFormatter.ofPattern("dd.MM HH:mm:ss").withZone(ZoneId.systemDefault())

    private fun file(ctx: Context) = File(ctx.filesDir, "log.txt")

    @Synchronized
    fun add(ctx: Context, msg: String) {
        val f = file(ctx)
        val lines = (if (f.exists()) f.readLines() else emptyList()).takeLast(MAX - 1) + "${fmt.format(Instant.now())}  $msg"
        f.writeText(lines.joinToString("\n"))
    }

    @Synchronized
    fun read(ctx: Context): List<String> = file(ctx).let { if (it.exists()) it.readLines().reversed() else emptyList() }

    // ---------- Состояние геозон ----------

    data class GeoState(val ok: Boolean, val text: String, val at: Long)

    private val _state = MutableStateFlow<GeoState?>(null)
    val state: StateFlow<GeoState?> get() = _state

    fun loadState(ctx: Context) {
        if (_state.value != null) return
        val p = ctx.getSharedPreferences("diag", Context.MODE_PRIVATE)
        if (!p.contains("text")) return
        _state.value = GeoState(p.getBoolean("ok", true), p.getString("text", "") ?: "", p.getLong("at", 0))
    }

    fun setState(ctx: Context, ok: Boolean, text: String) {
        val s = GeoState(ok, text, System.currentTimeMillis())
        _state.value = s
        ctx.getSharedPreferences("diag", Context.MODE_PRIVATE).edit()
            .putBoolean("ok", ok).putString("text", text).putLong("at", s.at).apply()
    }

    fun geofenceError(code: Int): String = when (code) {
        1000 ->
            "Геозоны недоступны: включите «Точность определения местоположения» (Настройки → Местоположение → Службы геолокации → Точность определения местоположения Google)"
        1001 -> "Слишком много зон (больше 100)"
        1002 -> "Система отказала: слишком много запросов"
        1004 ->
            "Нет разрешения на геолокацию «Всегда»"
        1005 -> "Слишком частые запросы, повторю позже"
        else -> "Ошибка геозон, код $code"
    }
}
