package com.rifat.trainercount.geo

import android.content.Context
import com.rifat.trainercount.logic.LatLng
import com.rifat.trainercount.logic.Leg
import com.rifat.trainercount.logic.Router
import com.rifat.trainercount.logic.distanceM
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant

/**
 * Маршруты между клиентами. С ключом Google Maps: настоящий маршрут на метро, автобусе и пешком.
 * Без ключа: пешком по улицам (OpenStreetMap, бесплатно) для близких точек, дальше оценка.
 * Результаты кешируются на 30 дней: одни и те же адреса не запрашиваются заново.
 */
object Routes {
    private const val PREFS = "routes"
    private const val TTL = 30L * 24 * 3600_000

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun googleKey(ctx: Context) = prefs(ctx).getString("google", "").orEmpty()
    fun setGoogleKey(ctx: Context, key: String) =
        prefs(ctx).edit().putString("google", key.trim()).apply { if (key.isBlank()) remove("google") }.commit().also { clearCache(ctx) }

    fun clearCache(ctx: Context) {
        val p = prefs(ctx)
        p.edit().apply { p.all.keys.filter { it.startsWith("r:") }.forEach { remove(it) } }.apply()
    }

    private fun key(a: LatLng, b: LatLng, hour: Int) =
        "r:%.4f,%.4f;%.4f,%.4f;%d".format(java.util.Locale.US, a.first, a.second, b.first, b.second, hour / 3)

    private fun load(ctx: Context, k: String): Leg? {
        val v = prefs(ctx).getString(k, null) ?: return null
        val o = runCatching { JSONObject(v) }.getOrNull() ?: return null
        if (System.currentTimeMillis() - o.optLong("t") > TTL) return null
        return Leg(o.getInt("min"), o.getInt("walk"), o.getString("how"), o.getBoolean("exact"))
    }

    private fun save(ctx: Context, k: String, l: Leg) = prefs(ctx).edit().putString(
        k, JSONObject().put("min", l.min).put("walk", l.walkMin).put("how", l.how).put("exact", l.exact).put("t", System.currentTimeMillis()).toString(),
    ).apply()

    /** offline: только из кеша, без сети (для экранов на главном потоке). */
    fun router(ctx: Context, offline: Boolean = false): Router = Router { a, b, depart ->
        val hour = Instant.ofEpochMilli(depart).atZone(java.time.ZoneId.systemDefault()).hour
        val k = key(a, b, hour)
        load(ctx, k) ?: if (offline) null else fetch(ctx, a, b, depart)?.also { save(ctx, k, it) }
    }

    private fun fetch(ctx: Context, a: LatLng, b: LatLng, depart: Long): Leg? {
        val g = googleKey(ctx)
        if (g.isNotBlank()) runCatching { google(g, a, b, depart) }.getOrNull()?.let { return it }
        val straight = distanceM(a.first, a.second, b.first, b.second)
        return if (straight < 2500) runCatching { osmWalk(a, b) }.getOrNull() else null
    }

    private fun http(url: String, body: String? = null, headers: Map<String, String> = emptyMap()): JSONObject {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 10_000
        c.readTimeout = 15_000
        headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
        if (body != null) {
            c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            c.outputStream.use { it.write(body.toByteArray()) }
        }
        val ok = c.responseCode in 200..299
        val text = (if (ok) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
        c.disconnect()
        val o = JSONObject(text)
        if (!ok) error(o.optJSONObject("error")?.optString("message") ?: "ошибка ${c.responseCode}")
        return o
    }

    private fun secs(s: String?) = s?.removeSuffix("s")?.toDoubleOrNull()?.let { (it / 60).toInt() } ?: 0

    /** Пешком по улицам: бесплатный сервер OpenStreetMap. */
    private fun osmWalk(a: LatLng, b: LatLng): Leg? {
        val o = http("https://routing.openstreetmap.de/routed-foot/route/v1/foot/${a.second},${a.first};${b.second},${b.first}?overview=false")
        val r = o.optJSONArray("routes")?.optJSONObject(0) ?: return null
        val min = (r.getDouble("duration") / 60).toInt().coerceAtLeast(1)
        return Leg(min, min, "пешком $min мин (${"%.1f".format(r.getDouble("distance") / 1000)} км)", true)
    }

    private fun place(p: LatLng) = JSONObject().put("location", JSONObject().put("latLng", JSONObject().put("latitude", p.first).put("longitude", p.second)))

    /** Google Routes: общественный транспорт с пешими участками. Время отправления не в прошлом. */
    fun google(key: String, a: LatLng, b: LatLng, depart: Long): Leg? {
        val t = Instant.ofEpochMilli(maxOf(depart, System.currentTimeMillis() + 60_000))
        val body = JSONObject().put("origin", place(a)).put("destination", place(b)).put("travelMode", "TRANSIT")
            .put("departureTime", t.toString()).put("languageCode", "ru").toString()
        val o = http(
            "https://routes.googleapis.com/directions/v2:computeRoutes", body,
            mapOf(
                "X-Goog-Api-Key" to key,
                "X-Goog-FieldMask" to "routes.duration,routes.legs.steps.travelMode,routes.legs.steps.staticDuration,routes.legs.steps.transitDetails",
            ),
        )
        val route = o.optJSONArray("routes")?.optJSONObject(0) ?: return null
        val steps = route.optJSONArray("legs")?.optJSONObject(0)?.optJSONArray("steps") ?: JSONArray()
        // Склеиваем подряд идущие пешие шаги: «пешком 6 мин → метро D 18 мин → пешком 9 мин».
        val parts = mutableListOf<Pair<String, Int>>()
        var walk = 0
        for (i in 0 until steps.length()) {
            val st = steps.getJSONObject(i)
            val m = secs(st.optString("staticDuration"))
            if (st.optString("travelMode") == "WALK") {
                walk += m
                if (parts.lastOrNull()?.first == "пешком") parts[parts.lastIndex] = "пешком" to parts.last().second + m else parts += "пешком" to m
            } else {
                val line = st.optJSONObject("transitDetails")?.optJSONObject("transitLine")
                val type = when (line?.optJSONObject("vehicle")?.optString("type")) {
                    "SUBWAY", "METRO_RAIL" -> "метро"; "BUS", "INTERCITY_BUS", "TROLLEYBUS" -> "автобус"
                    "RAIL", "HEAVY_RAIL", "COMMUTER_TRAIN", "TRAIN" -> "поезд"; "TRAM", "LIGHT_RAIL" -> "трамвай"; else -> "транспорт"
                }
                parts += ("$type ${line?.optString("nameShort").orEmpty().ifBlank { line?.optString("name").orEmpty() }}".trim()) to m
            }
        }
        val total = secs(route.optString("duration"))
        if (total == 0) return null
        val how = "$total мин: " + parts.filter { it.second > 0 }.joinToString(" → ") { "${it.first} ${it.second} мин" } +
            if (parts.size > 1) ", пешком всего $walk мин" else ""
        return Leg(total, walk, how, true)
    }
}
