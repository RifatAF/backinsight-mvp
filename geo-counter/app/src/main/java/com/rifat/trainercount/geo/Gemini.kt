package com.rifat.trainercount.geo

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Бесплатный ИИ Google (ключ из AI Studio). Нужен только для фраз, которые бот не понял сам. */
object Gemini {
    private const val BASE = "https://generativelanguage.googleapis.com/v1beta/"

    private fun http(key: String, path: String, body: JSONObject?): JSONObject {
        val c = URL(BASE + path).openConnection() as HttpURLConnection
        c.connectTimeout = 15_000
        c.readTimeout = 30_000
        c.setRequestProperty("x-goog-api-key", key.trim())
        if (body != null) {
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        val ok = c.responseCode in 200..299
        val text = (if (ok) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
        c.disconnect()
        val o = JSONObject(text)
        if (!ok) error(o.optJSONObject("error")?.optString("message") ?: "ошибка ${c.responseCode}")
        return o
    }

    /** Подобрать быструю модель, доступную по ключу: имена моделей у Google меняются. Ошибка: ключ не подошёл. */
    fun pickModel(key: String): String {
        val models = http(key, "models?pageSize=200", null).optJSONArray("models") ?: JSONArray()
        val names = (0 until models.length()).map { models.getJSONObject(it) }
            .filter { m -> m.optJSONArray("supportedGenerationMethods")?.toString()?.contains("generateContent") == true }
            .map { it.getString("name").removePrefix("models/") }
            .filter { "flash" in it && listOf("image", "tts", "live", "audio", "thinking", "exp", "preview").none { w -> w in it } }
        // Сначала облегчённые (на бесплатном тарифе их лимиты больше), новые версии раньше старых.
        return names.sortedWith(compareByDescending<String> { "lite" in it }.thenByDescending { it })
            .firstOrNull() ?: error("нет подходящей модели")
    }

    /** Отправить запрос, получить текст ответа. */
    fun ask(key: String, model: String, prompt: String): String {
        val body = JSONObject()
            .put("contents", JSONArray().put(JSONObject().put("parts", JSONArray().put(JSONObject().put("text", prompt)))))
            .put("generationConfig", JSONObject().put("responseMimeType", "application/json").put("temperature", 0))
        val o = http(key, "models/$model:generateContent", body)
        return o.optJSONArray("candidates")?.optJSONObject(0)?.optJSONObject("content")?.optJSONArray("parts")
            ?.optJSONObject(0)?.optString("text").orEmpty()
    }
}
