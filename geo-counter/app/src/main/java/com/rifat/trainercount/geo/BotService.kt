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
import com.rifat.trainercount.logic.RifatStatus
import com.rifat.trainercount.logic.trackLesson
import com.rifat.trainercount.logic.clientTrackText
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
import com.rifat.trainercount.logic.isYes
import com.rifat.trainercount.logic.isNo
import com.rifat.trainercount.logic.quickAnswer
import com.rifat.trainercount.logic.BOT_HELP
import com.rifat.trainercount.logic.CLIENT_BUTTONS
import com.rifat.trainercount.logic.OWNER_BUTTONS
import com.rifat.trainercount.logic.OWNER_MORE
import com.rifat.trainercount.logic.BTN_MORE
import com.rifat.trainercount.logic.BTN_BACK
import com.rifat.trainercount.logic.autoApply
import com.rifat.trainercount.logic.doneText
import com.rifat.trainercount.logic.isUndo
import com.rifat.trainercount.logic.isButton
import com.rifat.trainercount.logic.keyboardJson
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
        val OWNER_KB = keyboardJson(OWNER_BUTTONS)
        val CLIENT_KB = keyboardJson(CLIENT_BUTTONS)
        val MORE_KB = keyboardJson(OWNER_MORE)

        /** Чат только что разрешили: прислать приветствие с кнопками. */
        fun welcome(ctx: Context, chatId: String) {
            Store.init(ctx)
            val token = Store.data.value.settings.tgToken
            if (token.isBlank()) return
            Thread { Telegram.send(token, chatId, "Готово, чат подключён ✓\n\n$BOT_HELP", OWNER_KB) }.start()
        }

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
                    // Ошибка в одном сообщении не должна останавливать бота и выглядеть как «нет связи».
                    // Throwable, а не Exception: любая ошибка иначе молча останавливала бота до перезапуска.
                    try { handle(s.tgToken, m) } catch (e: InterruptedException) { throw e } catch (e: Throwable) {
                        Diag.add(this, "✗ Бот: ошибка при ответе (${e.javaClass.simpleName}: ${e.message})")
                        runCatching { Telegram.send(s.tgToken, m.chat.id, "Не получилось ответить, попробуйте ещё раз.") }
                    }
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
            Telegram.send(token, m.chat.id, clientHelp(c), CLIENT_KB)
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
            Telegram.send(token, m.chat.id, "Здравствуйте! Это помощник Рифата. Чтобы пользоваться ботом, Рифат должен подтвердить этот чат: ему пришло уведомление. Как только он нажмёт «Разрешить», я пришлю приветствие и подсказки.")
            return
        }
        val now = System.currentTimeMillis()
        // Кнопки сворачиваются: основная строка и «Ещё» присылаются только по запросу.
        when (m.text.trim()) {
            BTN_MORE -> { Telegram.send(token, m.chat.id, "Ещё вопросы на кнопках ниже.", MORE_KB); return }
            BTN_BACK -> { Telegram.send(token, m.chat.id, "Основные кнопки ниже.", OWNER_KB); return }
        }
        // «отмени» сразу после изменения: вернуть как было.
        if (isUndo(m.text)) lastDone.remove(m.chat.id)?.let { before ->
            Assistant.undo(this, before)
            proposals.remove(m.chat.id)
            send(token, m.chat.id, "Вернул как было.")
            return
        }
        // Ответ на предложенный план: «да» выполняет, «нет» отменяет.
        proposals.remove(m.chat.id)?.let { p ->
            if (isYes(m.text)) {
                val (before, done) = Assistant.apply(this, p)
                lastDone[m.chat.id] = before
                remember(m.chat.id, m.text, done)
                send(token, m.chat.id, "$done\nЕсли не так, напишите «отмени».")
                return
            }
            if (isNo(m.text)) { send(token, m.chat.id, "Хорошо, ничего не меняю."); return }
        }
        var cmd = parseBot(m.text, dayOf(now))
        // С ключом Gemini сообщение разбирает помощник; балансы, сводки и кнопки приложение считает само, точно.
        val exact = cmd is BotCmd.Help || cmd is BotCmd.Summary || cmd is BotCmd.Report || cmd is BotCmd.Balances ||
            isButton(m.text, OWNER_BUTTONS) || isButton(m.text, OWNER_MORE) || quickAnswer(d0, m.text, now) != null
        if (d0.settings.aiKey.isNotBlank() && !exact) {
            val status = whereText(map = Regex("где|куда|адрес|встрет|подъех|забрать", RegexOption.IGNORE_CASE).containsMatchIn(m.text))
            Telegram.typing(token, m.chat.id)
            val p = thinkWithin(m, status)
            if (p != null) {
                Diag.add(this, "Бот (помощник): «${m.text.take(40)}» от ${m.chat.name}")
                val text = when {
                    // Личные дела записываются сразу, без «да».
                    p.plan.hasChanges && autoApply(p.actions) -> {
                        val (before, _) = Assistant.apply(this, p)
                        lastDone[m.chat.id] = before
                        doneText(p.plan.steps)
                    }
                    p.plan.hasChanges -> { proposals[m.chat.id] = p; p.plan.text() + "\n\nСделать? Ответьте «да» или «нет»." }
                    else -> p.plan.text()
                }
                remember(m.chat.id, m.text, text)
                send(token, m.chat.id, text)
                return
            }
        }
        // Помощник уже пробовал и не ответил: второй запрос к ИИ только затянет ответ.
        val triedAi = d0.settings.aiKey.isNotBlank() && !exact
        if (cmd is BotCmd.Unknown && d0.settings.aiKey.isNotBlank() && !triedAi) ai(m.text, now)?.let { cmd = it }
        // Место ищем до записи в данные: это может занять до 20 секунд.
        val where = if (cmd is BotCmd.Where) whereText() else ""
        var reply = ""
        Store.update { d -> botAnswer(d, cmd, now, { where }).also { reply = it.second }.first }
        if (Store.data.value != d0) Planner.refresh(this)
        Diag.add(this, "Бот: «${m.text.take(40)}» от ${m.chat.name}")
        send(token, m.chat.id, reply, if (cmd is BotCmd.Help) OWNER_KB else null)
    }

    private val pool = java.util.concurrent.Executors.newCachedThreadPool { r -> Thread(r, "tg-ai").apply { isDaemon = true } }

    /**
     * Помощник с ограничением по времени: если ИИ не ответил за 60 секунд, бот отвечает сам, по расписанию,
     * а не молчит. Пока ждём, в чате видно «печатает…».
     */
    private fun thinkWithin(m: Telegram.Message, status: String): Assistant.Proposal? {
        val token = Store.data.value.settings.tgToken
        val f = pool.submit<Assistant.Proposal?> {
            Assistant.think(this, m.text, history[m.chat.id]?.toList().orEmpty(), status.replace("\n", "; "))
        }
        val until = System.currentTimeMillis() + 60_000
        while (true) {
            try {
                return f.get(4, TimeUnit.SECONDS)
            } catch (_: java.util.concurrent.TimeoutException) {
                if (System.currentTimeMillis() > until) {
                    f.cancel(true)
                    Diag.add(this, "✗ Бот: ИИ не ответил за минуту, отвечаю без него")
                    return null
                }
                Telegram.typing(token, m.chat.id)
            } catch (e: java.util.concurrent.ExecutionException) {
                Diag.add(this, "✗ Бот: ошибка ИИ (${e.cause?.javaClass?.simpleName}: ${e.cause?.message})")
                return null
            }
        }
    }

    /** Ответ без кнопок (их не раскрываем заново) и никогда не пустой: пустое сообщение Telegram не доставит. */
    private fun send(token: String, chat: String, text: String, kb: String? = null) {
        Telegram.send(token, chat, text.ifBlank { "Не понял, напишите подробнее." }, kb)
            .onFailure { Diag.add(this, "✗ Бот: не отправилось (${it.message})") }
    }

    /** Данные до последнего изменения из бота, для «отмени». */
    private val lastDone = mutableMapOf<String, AppData>()

    /** Чат клиента: только его баланс, занятия и примерно, где Рифат. Остальное передаётся Рифату. */
    private fun handleClient(token: String, c: com.rifat.trainercount.logic.Client, m: Telegram.Message) {
        val now = System.currentTimeMillis()
        val d = Store.data.value
        val status = rifatStatus(d, c, now, Wifi.isHome(this, d))
        var reply: String? = null
        var forward = false
        if (composing.remove(m.chat.id) && parseClientBot(m.text) !is ClientCmd.Compose && !isButton(m.text, CLIENT_BUTTONS)) {
            reply = "Передал Рифату, он ответит вам сам."
            forward = true
        }
        // За 30 минут до занятия «где Рифат» показывает дорогу к клиенту: сколько ехать и успевает ли.
        if (reply == null && parseClientBot(m.text) is ClientCmd.Where && status != RifatStatus.WithYou) {
            trackLesson(d, c, now)?.let { l ->
                val fix = currentFix()?.let { Triple(it.latitude, it.longitude, it.time) }
                reply = clientTrackText(d, c, l, fix, now)
            }
        }
        if (reply == null && d.settings.aiKey.isNotBlank() && !isButton(m.text, CLIENT_BUTTONS)) {
            val past = history[m.chat.id]?.toList().orEmpty()
            gemini(clientAiPrompt(d, c, now, status, past, m.text))?.let(::parseClientAi)?.let { (r, f) -> reply = r; forward = f }
        }
        if (reply == null) {
            val cmd = parseClientBot(m.text)
            reply = clientAnswer(d, c, cmd, now, status)
            forward = cmd is ClientCmd.Message
            if (cmd is ClientCmd.Compose) composing += m.chat.id
        }
        if (forward) Notifier.clientInfo(this, c, "${c.name} пишет в бот", m.text)
        val text = reply!!
        history.getOrPut(m.chat.id) { ArrayDeque() }.apply { addLast(m.text to text); while (size > 4) removeFirst() }
        Diag.add(this, "Бот: клиент ${c.name}: «${m.text.take(40)}»")
        send(token, m.chat.id, text, if (parseClientBot(m.text) is ClientCmd.Help) CLIENT_KB else null)
    }

    /** Клиенты, нажавшие «Написать Рифату»: их следующее сообщение передаётся как есть. */
    private val composing = mutableSetOf<String>()

    /** Планы помощника, ждущие «да» в чате. */
    private val proposals = mutableMapOf<String, Assistant.Proposal>()

    private fun remember(chat: String, q: String, a: String) =
        history.getOrPut(chat) { ArrayDeque() }.apply { addLast(q to a); while (size > 6) removeFirst() }

    /** Последние реплики по чатам: чтобы ИИ понимал «а где он закончит?» после предыдущего вопроса. */
    private val history = mutableMapOf<String, ArrayDeque<Pair<String, String>>>()

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
    /** Текущее место телефона (до 25 секунд) или последнее известное. */
    private fun currentFix(): android.location.Location? {
        if (!Perms.location(this)) return null
        val fused = LocationServices.getFusedLocationProviderClient(this)
        return runCatching {
            Tasks.await(fused.getCurrentLocation(Priority.PRIORITY_BALANCED_POWER_ACCURACY, CancellationTokenSource().token), 20, TimeUnit.SECONDS)
        }.getOrNull() ?: runCatching { Tasks.await(fused.lastLocation, 5, TimeUnit.SECONDS) }.getOrNull()
    }

    private fun whereText(map: Boolean = true): String {
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
        if (!home && map) {
            val l = currentFix()
            if (l != null) lines += "На карте (±${l.accuracy.toInt()} м, ${fTime(l.time)}): https://maps.google.com/?q=${l.latitude},${l.longitude}"
        }
        return lines.joinToString("\n")
    }
}
