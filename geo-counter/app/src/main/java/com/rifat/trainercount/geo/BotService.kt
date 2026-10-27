package com.rifat.trainercount.geo

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.android.gms.tasks.Tasks
import com.rifat.trainercount.R
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.BotCmd
import com.rifat.trainercount.logic.aiPrompt
import com.rifat.trainercount.logic.parseAi
import com.rifat.trainercount.logic.PlanItem
import com.rifat.trainercount.logic.Status
import com.rifat.trainercount.logic.botAnswer
import com.rifat.trainercount.logic.dayOf
import com.rifat.trainercount.logic.dayPlan
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.hhmm
import com.rifat.trainercount.logic.minuteOf
import com.rifat.trainercount.logic.parseBot
import com.rifat.trainercount.ui.MainActivity
import java.util.concurrent.TimeUnit

/**
 * Бот в Telegram отвечает на сообщения: план дня, личные дела, «где Рифат».
 * Держит одно соединение с Telegram и ждёт сообщений (длинный опрос), поэтому почти не тратит батарею.
 */
class BotService : Service() {
    companion object {
        private const val CH = "bot"
        private const val NID = 5501

        /** Включить или выключить по настройкам. */
        fun sync(ctx: Context) {
            Store.init(ctx)
            val s = Store.data.value.settings
            val i = Intent(ctx, BotService::class.java)
            if (s.tgBotOn && s.tgToken.isNotBlank()) runCatching { ContextCompat.startForegroundService(ctx, i) }
                .onFailure { Diag.add(ctx, "✗ Бот: система не дала запуститься (${it.javaClass.simpleName})") }
            else ctx.stopService(i)
        }

        fun allowed(d: AppData, chatId: String) = chatId == d.settings.tgChatId || chatId in d.settings.tgAllowed
    }

    @Volatile private var running = false
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH, "Бот Telegram", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Значок, пока бот отвечает на сообщения"
                setShowBadge(false)
            }
        )
        val open = PendingIntent.getActivity(
            this, NID, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(this, CH)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Бот на связи")
            .setContentText("Отвечает в Telegram о расписании")
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        ServiceCompat.startForeground(
            this, NID, n, if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0,
        )
        if (worker?.isAlive != true) {
            running = true
            worker = Thread(::loop, "tg-bot").apply { isDaemon = true; start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        super.onDestroy()
    }

    private fun loop() {
        Store.init(this)
        val prefs = getSharedPreferences("tg", MODE_PRIVATE)
        var offset = prefs.getLong("offset", 0)
        var fails = 0
        while (running) {
            val s = Store.data.value.settings
            if (!s.tgBotOn || s.tgToken.isBlank()) break
            try {
                val msgs = Telegram.updates(s.tgToken, offset, 50)
                fails = 0
                for (m in msgs) {
                    offset = m.updateId + 1
                    prefs.edit().putLong("offset", offset).apply()
                    if (m.chat.id.isBlank() || m.text.isBlank()) continue
                    Telegram.remember(this, m.chat)
                    handle(s.tgToken, m)
                }
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                // Нет сети или Telegram недоступен: подождать и повторить, реже при долгих сбоях.
                fails++
                if (fails == 3) Diag.add(this, "✗ Бот: нет связи с Telegram (${e.message})")
                try { Thread.sleep((15_000L * fails).coerceAtMost(300_000L)) } catch (_: InterruptedException) { break }
            }
        }
        stopSelf()
    }

    private fun handle(token: String, m: Telegram.Message) {
        val d0 = Store.data.value
        if (!allowed(d0, m.chat.id)) {
            Diag.add(this, "Бот: сообщение от ${m.chat.name} (нет доступа)")
            Notifier.askAllow(this, m.chat)
            Telegram.send(token, m.chat.id, "Этот чат пока не подключён. На телефоне Рифата появилось уведомление «Разрешить». После нажатия напишите ещё раз.")
            return
        }
        val now = System.currentTimeMillis()
        var cmd = parseBot(m.text, dayOf(now))
        if (cmd is BotCmd.Unknown && d0.settings.aiKey.isNotBlank()) ai(m.text, now)?.let { cmd = it }
        // Место ищем до записи в данные: это может занять до 20 секунд.
        val where = if (cmd is BotCmd.Where) whereText() else ""
        var reply = ""
        Store.update { d -> botAnswer(d, cmd, now, { where }).also { reply = it.second }.first }
        if (Store.data.value != d0) Planner.refresh(this)
        Diag.add(this, "Бот: «${m.text.take(40)}» от ${m.chat.name}")
        Telegram.send(token, m.chat.id, reply)
    }

    /** Фразу не понял сам: спросить Gemini. В ИИ уходит только текст сообщения и дата. */
    private fun ai(text: String, now: Long): BotCmd? {
        val s = Store.data.value.settings
        fun run(model: String) = parseAi(Gemini.ask(s.aiKey, model, aiPrompt(text, dayOf(now))), dayOf(now))
        return try {
            run(s.aiModel.ifBlank { Gemini.pickModel(s.aiKey).also { m -> Store.update { it.copy(settings = it.settings.copy(aiModel = m)) } } })
        } catch (e: Exception) {
            // Модель могли переименовать: подобрать заново и повторить один раз.
            runCatching {
                val m = Gemini.pickModel(s.aiKey)
                Store.update { it.copy(settings = it.settings.copy(aiModel = m)) }
                run(m)
            }.onFailure { Diag.add(this, "✗ Gemini: ${it.message}") }.getOrNull()
        }
    }

    /** Где сейчас: у кого, с какого времени, что дальше, точка на карте. */
    @SuppressLint("MissingPermission")
    private fun whereText(): String {
        val d = Store.data.value
        val now = System.currentTimeMillis()
        val lines = mutableListOf<String>()
        val open = d.open.entries.maxByOrNull { it.value }
        val home = Wifi.isHome(this, d)
        when {
            open != null -> {
                val name = d.client(open.key)?.name ?: d.place(open.key.removePrefix("p:"))?.name ?: "клиента"
                lines += "Сейчас на занятии: $name, с ${fTime(open.value)}"
            }
            home -> lines += "Дома"
            d.openTrip != null -> lines += "В работе, вышел из дома в ${fTime(d.openTrip!!.start)}, сейчас в дороге"
            else -> lines += "Не дома"
        }
        val nowMin = minuteOf(now)
        dayPlan(d, dayOf(now), now).firstOrNull {
            (it is PlanItem.Lesson && it.status == Status.PLANNED && it.from > nowMin) || (it is PlanItem.Event && it.from > nowMin)
        }?.let { next ->
            val what = when (next) {
                is PlanItem.Lesson -> d.client(next.occ.clientId)?.name
                is PlanItem.Event -> next.ev.title
                else -> null
            }
            lines += "Дальше: ${hhmm(next.from)} $what"
        }
        if (!home && Perms.location(this)) {
            val fused = LocationServices.getFusedLocationProviderClient(this)
            val l = runCatching {
                Tasks.await(fused.getCurrentLocation(Priority.PRIORITY_BALANCED_POWER_ACCURACY, CancellationTokenSource().token), 20, TimeUnit.SECONDS)
            }.getOrNull() ?: runCatching { Tasks.await(fused.lastLocation, 5, TimeUnit.SECONDS) }.getOrNull()
            if (l != null) lines += "На карте (±${l.accuracy.toInt()} м, ${fTime(l.time)}): https://maps.google.com/?q=${l.latitude},${l.longitude}"
        }
        return lines.joinToString("\n")
    }
}
