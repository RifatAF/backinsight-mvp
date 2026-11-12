@file:OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)

package com.rifat.trainercount.ui

import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.geo.Assistant
import com.rifat.trainercount.logic.AppData
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Переписка с помощником. Запрос к ИИ идёт в фоне и не обрывается, если выйти с экрана;
 * текст переписки сохраняется и после перезапуска приложения.
 */
object ChatLog {
    enum class St { NONE, ASK, DONE, SKIPPED, UNDONE }
    data class Msg(
        val mine: Boolean,
        val text: String,
        val proposal: Assistant.Proposal? = null,
        val st: St = St.NONE,
        val before: AppData? = null,
    )
    val msgs = mutableStateListOf<Msg>()
    var busy by mutableStateOf(false)
    private val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + Dispatchers.Main)
    private var loaded = false

    fun load(ctx: android.content.Context) {
        if (loaded) return
        loaded = true
        val arr = runCatching { org.json.JSONArray(ctx.getSharedPreferences("chat", 0).getString("msgs", "[]")) }.getOrNull() ?: return
        for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { msgs += Msg(it.optBoolean("mine"), it.optString("text")) }
    }

    private fun save(ctx: android.content.Context) {
        val arr = org.json.JSONArray()
        msgs.takeLast(40).forEach { arr.put(org.json.JSONObject().put("mine", it.mine).put("text", it.text)) }
        ctx.getSharedPreferences("chat", 0).edit().putString("msgs", arr.toString()).apply()
    }

    fun add(ctx: android.content.Context, m: Msg) { msgs += m; save(ctx) }

    fun clear(ctx: android.content.Context) { msgs.clear(); save(ctx) }

    /** day: день, открытый на экране расписания, чтобы «сюда», «этот день» понимались правильно. */
    fun send(ctx: android.content.Context, text: String, day: java.time.LocalDate? = null) {
        val t = text.trim()
        if (t.isEmpty() || busy) return
        val app = ctx.applicationContext
        val history = history()
        add(app, Msg(true, t))
        // Простой вопрос: ответ сразу, без ИИ.
        com.rifat.trainercount.logic.quickAnswer(com.rifat.trainercount.data.Store.data.value, t, System.currentTimeMillis())?.let {
            add(app, Msg(false, it)); return
        }
        busy = true
        scope.launch {
            val p = withContext(Dispatchers.IO) {
                runCatching {
                    val where = if (com.rifat.trainercount.geo.Wifi.isHome(app, com.rifat.trainercount.data.Store.data.value)) "дома" else "не дома"
                    Assistant.think(app, t, history, where + (day?.let { "\nНа экране открыто расписание на ${dayTitle(it)} ($it); «этот день», «сюда» значит он, если день не назван" } ?: ""))
                }.getOrNull()
            }
            add(app, when {
                p == null -> Msg(false, "Не получилось связаться с ИИ. Проверьте интернет и ключ Gemini в настройках.")
                // Личные дела записываются сразу; кнопка «Отменить» остаётся.
                p.plan.hasChanges && com.rifat.trainercount.logic.autoApply(p.actions) ->
                    Msg(false, com.rifat.trainercount.logic.doneText(p.plan.steps).substringBefore("\nЕсли не так"), p, St.DONE, Assistant.apply(app, p).first)
                p.plan.hasChanges -> Msg(false, p.plan.text(), p, St.ASK)
                else -> Msg(false, p.plan.text())
            })
            busy = false
        }
    }

    fun history() = msgs.zipWithNext().filter { (q, a) -> q.mine && !a.mine }.map { (q, a) -> q.text to a.text }.takeLast(6)
}

private val EXAMPLES = listOf(
    "Сегодня будет только Петр в 16:30",
    "Перенеси Анну с пятницы на субботу 11:00",
    "Новый клиент Виталий у метро Карранса, подбери дни после 14:00",
    "Когда я свободен на этой неделе?",
)

@Composable
fun ChatScreen(data: AppData, nav: Nav) {
    val ctx = LocalContext.current
    var input by remember { mutableStateOf("") }
    val busy = ChatLog.busy
    val list = rememberLazyListState()
    val msgs = ChatLog.msgs
    LaunchedEffect(Unit) { ChatLog.load(ctx) }
    LaunchedEffect(msgs.size, busy) { if (msgs.isNotEmpty()) list.animateScrollToItem(msgs.size - 1 + if (busy) 1 else 0) }

    fun send(text: String) {
        if (text.isBlank() || busy) return
        input = ""
        ChatLog.send(ctx, text)
    }

    // Выбор сообщений, как в Telegram: долгое нажатие начинает выбор, нажатия добавляют и убирают.
    var picked by remember { mutableStateOf(setOf<Int>()) }
    fun copy(idx: Collection<Int>) {
        val text = idx.sorted().mapNotNull { msgs.getOrNull(it) }.joinToString("\n\n") { (if (it.mine) "Я: " else "Помощник: ") + it.text }
        val cm = ctx.getSystemService(android.content.ClipboardManager::class.java)
        cm?.setPrimaryClip(android.content.ClipData.newPlainText("Помощник", text))
        android.widget.Toast.makeText(ctx, "Скопировано: ${idx.size}", android.widget.Toast.LENGTH_SHORT).show()
        picked = emptySet()
    }

    Scaffold(topBar = {
        if (picked.isNotEmpty()) TopAppBar(
            title = { Text("Выбрано: ${picked.size}") },
            navigationIcon = { IconButton(onClick = { picked = emptySet() }) { Text("✕") } },
            actions = {
                androidx.compose.material3.TextButton(onClick = { picked = msgs.indices.toSet() }) { Text("Все") }
                androidx.compose.material3.TextButton(onClick = { copy(picked) }) { Text("Копировать") }
            },
        ) else TopAppBar(
            title = { Text("Помощник") },
            navigationIcon = { IconButton(onClick = { nav.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = {
                if (msgs.isNotEmpty()) androidx.compose.material3.TextButton(onClick = { copy(msgs.indices.toList()) }) { Text("Копировать всё") }
                if (msgs.isNotEmpty() && !busy) androidx.compose.material3.TextButton(onClick = { ChatLog.clear(ctx) }) { Text("Очистить") }
            },
        )
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).imePadding()) {
            if (data.settings.aiKey.isBlank()) {
                WarnCard("Помощнику нужен бесплатный ключ Gemini: Настройки → Telegram → «Помощь ИИ». Нажмите, чтобы открыть.") { nav.go(Screen.Settings) }
            }
            LazyColumn(
                Modifier.weight(1f).fillMaxWidth(),
                state = list,
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (msgs.isEmpty()) item {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            "Напишите обычными словами, что поменять или что подобрать. Я покажу план, а сделаю после вашего «Сделать». Например:",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        EXAMPLES.forEach { e -> SuggestionChip(onClick = { input = e }, label = { Text(e) }) }
                    }
                }
                items(msgs.indices.toList()) { i ->
                    Bubble(i, i in picked, onClick = { if (picked.isNotEmpty()) picked = if (i in picked) picked - i else picked + i }, onLong = { picked = picked + i })
                }
                if (busy) item { Text("Думаю…", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = input, onValueChange = { input = it },
                    placeholder = { Text("Что поменять в расписании?") },
                    modifier = Modifier.weight(1f), maxLines = 5,
                )
                IconButton(enabled = !busy && input.isNotBlank(), onClick = { send(input) }) {
                    Icon(Icons.AutoMirrored.Filled.Send, "Отправить")
                }
            }
        }
    }
}

/** Одно сообщение переписки с кнопками «Сделать» / «Отменить». */
@Composable
private fun Bubble(i: Int, selected: Boolean = false, onClick: () -> Unit = {}, onLong: () -> Unit = {}) {
    val ctx = LocalContext.current
    val msgs = ChatLog.msgs
    val m = msgs.getOrNull(i) ?: return
    fun update(f: (ChatLog.Msg) -> ChatLog.Msg) { if (i < msgs.size) msgs[i] = f(msgs[i]) }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (m.mine) Arrangement.End else Arrangement.Start) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = if (m.mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
            ),
            modifier = Modifier.widthIn(max = 330.dp).combinedClickable(onClick = onClick, onLongClick = onLong),
            border = if (selected) androidx.compose.foundation.BorderStroke(2.dp, MaterialTheme.colorScheme.primary) else null,
        ) {
            Column(Modifier.padding(10.dp)) {
                Text(m.text)
                when (m.st) {
                    ChatLog.St.ASK -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                        Button(onClick = {
                            val (before, done) = Assistant.apply(ctx, m.proposal!!)
                            update { it.copy(st = ChatLog.St.DONE, before = before) }
                            ChatLog.add(ctx, ChatLog.Msg(false, done))
                        }) { Text("Сделать") }
                        OutlinedButton(onClick = { update { it.copy(st = ChatLog.St.SKIPPED) } }) { Text("Не надо") }
                    }
                    ChatLog.St.DONE -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                        Text("✓ Сделано", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                        OutlinedButton(onClick = {
                            m.before?.let { Assistant.undo(ctx, it) }
                            update { it.copy(st = ChatLog.St.UNDONE) }
                        }) { Text("Отменить") }
                    }
                    ChatLog.St.SKIPPED -> Text("Не стал менять", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 6.dp))
                    ChatLog.St.UNDONE -> Text("Изменения отменены", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 6.dp))
                    ChatLog.St.NONE -> {}
                }
            }
        }
    }
}

/**
 * Помощник прямо под расписанием: последние сообщения и поле ввода.
 * Свёрнут: видна строка ввода и последний ответ. Стрелка открывает всю переписку.
 */
@Composable
fun AssistantPanel(data: AppData, nav: Nav, day: java.time.LocalDate) {
    val ctx = LocalContext.current
    LaunchedEffect(Unit) { ChatLog.load(ctx) }
    var input by remember { mutableStateOf("") }
    var open by remember { mutableStateOf(false) }
    val msgs = ChatLog.msgs
    val busy = ChatLog.busy
    val list = rememberLazyListState()
    // Ответ пришёл: панель раскрывается, чтобы его было видно.
    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty() && !msgs.last().mine) open = true }
    LaunchedEffect(msgs.size, busy, open) { if (open && msgs.isNotEmpty()) list.scrollToItem(msgs.size - 1 + if (busy) 1 else 0) }
    androidx.compose.material3.Surface(
        tonalElevation = 3.dp,
        shadowElevation = 8.dp,
        modifier = Modifier.fillMaxWidth()
            .windowInsetsPadding(androidx.compose.foundation.layout.WindowInsets.ime.union(androidx.compose.foundation.layout.WindowInsets.navigationBars)),
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(start = 12.dp)) {
                Text(
                    if (busy) "Помощник думает…" else "Помощник",
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.weight(1f).combinedClickable(onClick = { open = !open }),
                )
                if (msgs.isNotEmpty()) androidx.compose.material3.TextButton(onClick = { open = !open }) { Text(if (open) "Свернуть" else "Показать") }
                androidx.compose.material3.TextButton(onClick = { nav.go(Screen.Chat) }) { Text("Весь чат") }
            }
            if (data.settings.aiKey.isBlank()) {
                Text(
                    "Нужен ключ Gemini: Настройки → Telegram → «Помощь ИИ».",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 12.dp),
                )
            }
            if (open && (msgs.isNotEmpty() || busy)) {
                LazyColumn(
                    Modifier.fillMaxWidth().heightIn(max = 260.dp),
                    state = list,
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    items(msgs.indices.toList()) { i -> Bubble(i) }
                    if (busy) item { Text("Думаю…", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = input, onValueChange = { input = it },
                    placeholder = { Text("Что поменять?") },
                    modifier = Modifier.weight(1f), maxLines = 4,
                )
                IconButton(enabled = !busy && input.isNotBlank(), onClick = {
                    ChatLog.send(ctx, input, day)
                    input = ""
                    open = true
                }) { Icon(Icons.AutoMirrored.Filled.Send, "Отправить") }
            }
        }
    }
}
