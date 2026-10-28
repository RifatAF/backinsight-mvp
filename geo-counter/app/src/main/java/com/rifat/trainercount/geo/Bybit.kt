package com.rifat.trainercount.geo

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.Deposit
import com.rifat.trainercount.logic.PayEvent
import com.rifat.trainercount.logic.applyDeposits
import com.rifat.trainercount.logic.fCoin
import com.rifat.trainercount.logic.lessons
import com.rifat.trainercount.logic.paidClientText
import com.rifat.trainercount.logic.payerText
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Автоподтверждение оплат: раз в 15 минут читаем входящие переводы внутри Bybit (только чтение)
 * и записываем оплату клиенту, у которого указан UID или e-mail отправителя.
 * Ключ хранится отдельно от данных и не попадает в выгрузку.
 */
object Bybit {
    private const val HOST = "https://api.bybit.com"
    private const val RECV = "20000"
    private const val PREFS = "bybit"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun key(ctx: Context) = prefs(ctx).getString("key", "").orEmpty()
    fun secret(ctx: Context) = prefs(ctx).getString("secret", "").orEmpty()
    fun on(ctx: Context) = key(ctx).isNotBlank() && secret(ctx).isNotBlank()
    /** Последняя проверка: время и итог (для экрана настроек). */
    fun lastStatus(ctx: Context) = prefs(ctx).getString("status", "").orEmpty()

    fun save(ctx: Context, key: String, secret: String) {
        val p = prefs(ctx)
        // Переводы до подключения не трогаем: старые оплаты уже внесены вручную.
        val since = if (p.getLong("since", 0) == 0L || key != key(ctx)) System.currentTimeMillis() else p.getLong("since", 0)
        p.edit().putString("key", key).putString("secret", secret).putLong("since", since).apply()
        schedule(ctx)
    }

    fun clear(ctx: Context) {
        prefs(ctx).edit().clear().apply()
        WorkManager.getInstance(ctx).cancelUniqueWork("bybit")
    }

    fun schedule(ctx: Context) {
        if (!on(ctx)) return
        val net = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            "bybit", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<BybitWorker>(15, TimeUnit.MINUTES).setConstraints(net).build(),
        )
    }

    /** Проверить сейчас (при открытии приложения). */
    fun checkSoon(ctx: Context) {
        if (!on(ctx)) return
        val net = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            "bybit-now", ExistingWorkPolicy.KEEP, OneTimeWorkRequestBuilder<BybitWorker>().setConstraints(net).build(),
        )
    }

    private fun hmac(secret: String, s: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(), "HmacSHA256"))
        return mac.doFinal(s.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    private fun http(url: String, headers: Map<String, String> = emptyMap()): JSONObject {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 15_000
        c.readTimeout = 20_000
        headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
        val stream = if (c.responseCode in 200..299) c.inputStream else c.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
        c.disconnect()
        return JSONObject(text)
    }

    /** Часы телефона могут спешить: подпись действительна только в окне времени сервера. */
    private fun serverTime(): Long = runCatching { http("$HOST/v5/market/time").getLong("time") }.getOrDefault(System.currentTimeMillis())

    private fun get(key: String, secret: String, path: String, query: String): JSONObject {
        val ts = serverTime().toString()
        val o = http(
            "$HOST$path?$query",
            mapOf(
                "X-BAPI-API-KEY" to key, "X-BAPI-TIMESTAMP" to ts, "X-BAPI-RECV-WINDOW" to RECV,
                "X-BAPI-SIGN" to hmac(secret, ts + key + RECV + query),
            ),
        )
        if (o.optInt("retCode", -1) != 0) error(o.optString("retMsg", "ошибка Bybit") + " (код ${o.optInt("retCode")})")
        return o.getJSONObject("result")
    }

    /** Все входящие: внутри Bybit и по сети (TRC20 и др.). */
    fun deposits(key: String, secret: String, start: Long): List<Deposit> = internal(key, secret, start) + onChain(key, secret, start)

    /** Переводы с кошельков по сети. fromAddress Bybit даёт, только если адрес отправителя однозначен. */
    private fun onChain(key: String, secret: String, start: Long): List<Deposit> {
        val now = System.currentTimeMillis()
        val from = maxOf(start, now - 29L * 24 * 3600_000)
        val out = mutableListOf<Deposit>()
        var cursor = ""
        repeat(10) {
            val q = "limit=50&startTime=$from&endTime=$now" + if (cursor.isNotEmpty()) "&cursor=" + java.net.URLEncoder.encode(cursor, "UTF-8") else ""
            val r = get(key, secret, "/v5/asset/deposit/query-record", q)
            val rows = r.optJSONArray("rows")
            for (i in 0 until (rows?.length() ?: 0)) {
                val x = rows!!.getJSONObject(i)
                val st = x.optInt("status")
                out += Deposit(
                    id = "c" + x.optString("id"), coin = x.optString("coin"),
                    amount = x.optString("amount").toDoubleOrNull() ?: 0.0,
                    // 3: зачислен, 70012: зачислен и после отката сети. Остальное не считаем.
                    status = if (st == 3 || st == 70012) 2 else 1,
                    address = x.optString("fromAddress"), fromUid = "",
                    time = x.optString("successAt").toLongOrNull()?.let { if (it < 10_000_000_000L) it * 1000 else it } ?: now,
                    chain = x.optString("chain").ifBlank { "сеть" },
                )
            }
            cursor = r.optString("nextPageCursor")
            if (cursor.isEmpty() || (rows?.length() ?: 0) == 0) return out
        }
        return out
    }

    private fun internal(key: String, secret: String, start: Long): List<Deposit> {
        val now = System.currentTimeMillis()
        val from = maxOf(start, now - 29L * 24 * 3600_000)
        val out = mutableListOf<Deposit>()
        var cursor = ""
        repeat(10) {
            val q = "limit=50&startTime=$from&endTime=$now" + if (cursor.isNotEmpty()) "&cursor=" + java.net.URLEncoder.encode(cursor, "UTF-8") else ""
            val r = get(key, secret, "/v5/asset/deposit/query-internal-record", q)
            val rows = r.optJSONArray("rows")
            for (i in 0 until (rows?.length() ?: 0)) {
                val x = rows!!.getJSONObject(i)
                out += Deposit(
                    id = x.optString("id"), coin = x.optString("coin"),
                    amount = x.optString("amount").toDoubleOrNull() ?: 0.0, status = x.optInt("status"),
                    address = x.optString("address"), fromUid = x.optString("fromMemberId"),
                    time = x.optString("createdTime").toLongOrNull()?.let { if (it < 10_000_000_000L) it * 1000 else it } ?: now,
                )
            }
            cursor = r.optString("nextPageCursor")
            if (cursor.isEmpty() || (rows?.length() ?: 0) == 0) return out
        }
        return out
    }

    /** Проверить переводы и записать оплаты. Возвращает текст итога. */
    fun check(ctx: Context): String {
        Store.init(ctx)
        val p = prefs(ctx)
        val since = p.getLong("since", System.currentTimeMillis())
        val deps = deposits(key(ctx), secret(ctx), since)
        var events: List<PayEvent> = emptyList()
        Store.update { d -> applyDeposits(d, deps, since).also { events = it.second }.first }
        val seen = p.getStringSet("seen", emptySet())!!.toMutableSet()
        val d = Store.data.value
        val s = d.settings
        for (e in events) {
            when (e) {
                is PayEvent.Paid -> {
                    Diag.add(ctx, "✓ Bybit: ${e.client.name} ${fCoin(e.dep)} → ${lessons(e.sessions)}")
                    Notifier.clientInfo(ctx, e.client, "Оплата через Bybit ✓", "${e.client.name}: ${fCoin(e.dep)}, записал ${lessons(e.sessions)}")
                    if (e.client.tgChatId.isNotBlank() && s.tgToken.isNotBlank() && s.tgBotOn)
                        Telegram.send(s.tgToken, e.client.tgChatId, paidClientText(d, e), BotService.CLIENT_KB)
                }
                // О непонятных переводах говорим один раз.
                is PayEvent.Unclear -> if (seen.add(e.dep.id)) Notifier.clientInfo(
                    ctx, e.client, "Перевод в Bybit: проверьте",
                    "${e.client.name}: ${fCoin(e.dep)}. Не понял, сколько это занятий: укажите цену занятия у клиента или внесите оплату вручную.",
                )
                is PayEvent.Unknown -> if (seen.add(e.dep.id)) Notifier.info(
                    ctx, "Пришло ${fCoin(e.dep)}: от кого?",
                    if (e.dep.chain.isNotBlank() && e.dep.address.isBlank())
                        "Отправитель ${payerText(e.dep)}. Внесите оплату клиенту вручную."
                    else "От ${payerText(e.dep)}. Если это клиент, впишите это в его карточке: «Изменить» → «Оплата через Bybit», и следующие переводы запишутся сами.",
                )
            }
        }
        val paid = events.count { it is PayEvent.Paid }
        val status = "${com.rifat.trainercount.logic.fTime(System.currentTimeMillis())}: " +
            if (paid > 0) "записано оплат: $paid" else "новых оплат нет"
        p.edit().putStringSet("seen", seen.toList().takeLast(300).toSet()).putString("status", "✓ $status").apply()
        return status
    }

    fun fail(ctx: Context, e: Throwable) {
        val msg = "✗ ${com.rifat.trainercount.logic.fTime(System.currentTimeMillis())}: ${e.message}"
        if (prefs(ctx).getString("status", "") != msg) Diag.add(ctx, "✗ Bybit: ${e.message}")
        prefs(ctx).edit().putString("status", msg).apply()
    }
}

class BybitWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        if (!Bybit.on(applicationContext)) return Result.success()
        return try {
            Bybit.check(applicationContext); Result.success()
        } catch (e: Exception) {
            Bybit.fail(applicationContext, e); Result.success()
        }
    }
}
