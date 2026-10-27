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
import com.rifat.trainercount.logic.ClientCmd
import com.rifat.trainercount.logic.clientAiPrompt
import com.rifat.trainercount.logic.clientAnswer
import com.rifat.trainercount.logic.clientHelp
import com.rifat.trainercount.logic.inviteClientId
import com.rifat.trainercount.logic.parseClientAi
import com.rifat.trainercount.logic.parseClientBot
import com.rifat.trainercount.logic.rifatStatus
import com.rifat.trainercount.logic.aiPrompt
import com.rifat.trainercount.logic.aiChatPrompt
import com.rifat.trainercount.logic.aiContext
import com.rifat.trainercount.logic.parseAiReply
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
        // Клиент открыл свою ссылку-приглашение: запомнить его чат.
        inviteClientId(m.text)?.let { cid ->
            val c = d0.client(cid)
            if (c == null || c.archived) {
                Telegram.send(token, m.chat.id, "Ссылка устарела. Попросите Рифата прислать новую.")
                return
            }
            Store.update { d -> d.copy(clients = d.clients.map { when {
                it.id == cid -> it.copy(tgChatId = m.chat.id)
                it.tgChatId == m.chat.id -> it.copy(tgChatId = "")
                else -> it
            } }) }
            Notifier.clientInfo(this, c, "${c.name} подключился к боту", "Теперь может спрашивать про занятия и баланс")
            Telegram.send(token, m.chat.id, clientHelp(c))
            return
        }
        val linked = d0.clients.firstOrNull { it.tgChatId == m.chat.id && !it.archived }
        if (!allowed(d0, m.chat.id) && linked != null) {
            handleClient(token, linked, m)
            return
        }
        if (!allowed(d0, m.chat.id)) {
            Diag.add(this, "Бот: сообщение от ${m.chat.name} (нет доступа)")
            Notifier.askAllow(this, m.chat)
            Telegram.send(token, m.chat.id, "Этот чат пока не подключён. На телефоне Рифата появилось уведомление «Разрешить». После нажатия напишите ещё раз.")
            return
        }
        val now = System.currentTimeMillis()
        var cmd = parseBot(m.text, dayOf(now))
        // С ключом Gemini на вопросы отвечает ИИ; записи, удаления, балансы и сводки приложение считает само, точно.
        val exact = cmd is BotCmd.Add || cmd is BotCmd.Remove || cmd is BotCmd.Help ||
            cmd is BotCmd.Summary || cmd is BotCmd.Report || cmd is BotCmd.Balances
        if (d0.settings.aiKey.isNotBlank() && !exact) {
            val smart = smartReply(m.chat.id, m.text, now)
            if (smart != null) {
                Diag.add(this, "Бот (ИИ): «${m.text.take(40)}» от ${m.chat.name}")
                Telegram.send(token, m.chat.id, smart)
                return
            }
        }
        if (cmd is BotCmd.Unknown && d0.settings.aiKey.isNotBlank()) ai(m.text, now)?.let { cmd = it }
        // Место ищем до записи в данные: это может занять до 20 секунд.
        val where = if (cmd is BotCmd.Where) whereText() else ""
        var reply = ""
        Store.update { d -> botAnswer(d, cmd, now, { where }).also { reply = it.second }.first }
        if (Store.data.value != d0) Planner.refresh(this)
        Diag.add(this, "Бот: «${m.text.take(40)}» от ${m.chat.name}")
        Telegram.send(token, m.chat.id, reply)
    }

    /** Чат клиента: только его баланс, занятия и примерно, где Рифат. Остальное передаётся Рифату. */
    private fun handleClient(token: String, c: com.rifat.trainercount.logic.Client, m: Telegram.Message) {
        val now = System.currentTimeMillis()
        val d = Store.data.value
        val status = rifatStatus(d, c, now, Wifi.isHome(this, d))
        var reply: String? = null
        var forward = false
        if (d.settings.aiKey.isNotBlank()) {
            val past = history[m.chat.id]?.toList().orEmpty()
            gemini(clientAiPrompt(d, c, now, status, past, m.text))?.let(::parseClientAi)?.let { (r, f) -> reply = r; forward = f }
        }
        if (reply == null) {
            val cmd = parseClientBot(m.text)
            reply = clientAnswer(d, c, cmd, now, status)
            forward = cmd is ClientCmd.Message
        }
        if (forward) Notifier.clientInfo(this, c, "${c.name} пишет в бот", m.text)
        val text = reply!!
        history.getOrPut(m.chat.id) { ArrayDeque() }.apply { addLast(m.text to text); while (size > 4) removeFirst() }
        Diag.add(this, "Бот: клиент ${c.name}: «${m.text.take(40)}»")
        Telegram.send(token, m.chat.id, text)
    }

    /** Последние реплики по чатам: чтобы ИИ понимал «а где он закончит?» после предыдущего вопроса. */
    private val history = mutableMapOf<String, ArrayDeque<Pair<String, String>>>()

    /** Живой ответ ИИ с расписанием и местом. null: ИИ недоступен, ответим по-простому. */
    private fun smartReply(chatId: String, text: String, now: Long): String? {
        val d = Store.data.value
        val context = aiContext(d, now, whereText().replace("\n", "; "))
        val past = history[chatId]?.toList().orEmpty()
        val raw = gemini(aiChatPrompt(context, past, text)) ?: return null
        val ai = parseAiReply(raw, dayOf(now)) ?: return null
        var extra = ""
        // Запись и удаление выполняет приложение: так видно, что именно сделано, и есть проверка пересечений.
        listOfNotNull(ai.add, ai.remove).forEach { c ->
            Store.update { dd -> botAnswer(dd, c, now, { "" }).also { extra += "\n" + it.second }.first }
        }
        if (extra.isNotEmpty()) Planner.refresh(this)
        val reply = (ai.reply + extra).trim()
        history.getOrPut(chatId) { ArrayDeque() }.apply { addLast(text to reply); while (size > 4) removeFirst() }
        return reply
    }

    /** Запрос к Gemini с подбором модели, если старую переименовали. */
    private fun gemini(prompt: String): String? {
        val s = Store.data.value.settings
        fun save(m: String) = Store.update { it.copy(settings = it.settings.copy(aiModel = m)) }
        return try {
            Gemini.ask(s.aiKey, s.aiModel.ifBlank { Gemini.pickModel(s.aiKey).also(::save) }, prompt)
        } catch (e: Exception) {
            runCatching { Gemini.ask(s.aiKey, Gemini.pickModel(s.aiKey).also(::save), prompt) }
                .onFailure { Diag.add(this, "✗ Gemini: ${it.message}") }.getOrNull()
        }
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
