package com.rifat.trainercount.geo

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.rifat.trainercount.data.Store
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/** Отправка плана на завтра через своего Telegram-бота. Сеть нужна только для этого. */
object Telegram {
    data class Chat(val id: String, val name: String)

    private fun call(token: String, method: String, params: Map<String, String> = emptyMap()): JSONObject {
        val c = URL("https://api.telegram.org/bot${token.trim()}/$method").openConnection() as HttpURLConnection
        c.connectTimeout = 15_000
        c.readTimeout = 20_000
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        val body = params.entries.joinToString("&") { (k, v) -> "$k=" + URLEncoder.encode(v, "UTF-8") }
        c.outputStream.use { it.write(body.toByteArray()) }
        val stream = if (c.responseCode in 200..299) c.inputStream else c.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
        c.disconnect()
        return JSONObject(text)
    }

    /** Имя бота или null, если токен неверный. Вызывать не на главном потоке. */
    fun botName(token: String): String? = runCatching {
        val o = call(token, "getMe")
        if (o.optBoolean("ok")) o.getJSONObject("result").optString("username") else null
    }.getOrNull()

    /** Чаты, которые написали боту (нажали «Старт»). */
    fun chats(token: String): List<Chat> = runCatching {
        val o = call(token, "getUpdates")
        val arr = o.optJSONArray("result") ?: return emptyList()
        (0 until arr.length()).mapNotNull { i ->
            val u = arr.getJSONObject(i)
            val chat = (u.optJSONObject("message") ?: u.optJSONObject("my_chat_member"))?.optJSONObject("chat") ?: return@mapNotNull null
            val name = listOf(chat.optString("first_name"), chat.optString("last_name"))
                .filter { it.isNotBlank() }.joinToString(" ")
                .ifBlank { chat.optString("title") }.ifBlank { chat.optString("username") }
            Chat(chat.get("id").toString(), name)
        }.distinctBy { it.id }
    }.getOrDefault(emptyList())

    fun send(token: String, chatId: String, text: String): Result<Unit> = runCatching {
        val o = call(token, "sendMessage", mapOf("chat_id" to chatId, "text" to text))
        if (!o.optBoolean("ok")) error(o.optString("description", "ошибка Telegram"))
    }

    /** Поставить отправку в очередь: дойдёт, как только появится интернет. */
    fun enqueue(ctx: Context, text: String) {
        val req = OneTimeWorkRequestBuilder<TgWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
            .setInputData(workDataOf("text" to text))
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork("telegram", ExistingWorkPolicy.REPLACE, req)
    }
}

class TgWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        Store.init(applicationContext)
        val s = Store.data.value.settings
        val text = inputData.getString("text") ?: return Result.failure()
        if (s.tgToken.isBlank() || s.tgChatId.isBlank()) return Result.failure()
        val r = Telegram.send(s.tgToken, s.tgChatId, text)
        return if (r.isSuccess) {
            Diag.add(applicationContext, "✓ Telegram: план отправлен (${s.tgChatName})")
            Result.success()
        } else {
            Diag.add(applicationContext, "✗ Telegram: ${r.exceptionOrNull()?.message}")
            if (runAttemptCount < 5) Result.retry() else Result.failure()
        }
    }
}
