@file:OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)

package com.rifat.trainercount.ui

import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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

    fun send(ctx: android.content.Context, text: String) {
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
                    Assistant.think(app, t, history, if (com.rifat.trainercount.geo.Wifi.isHome(app, com.rifat.trainercount.data.Store.data.value)) "дома" else "не дома")
                }.getOrNull()
            }
            add(app, when {
                p == null -> Msg(false, "Не получилось связаться с ИИ. Проверьте интернет и ключ Gemini в настройках.")
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

    fun update(i: Int, f: (ChatLog.Msg) -> ChatLog.Msg) { msgs[i] = f(msgs[i]) }

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
                    val m = msgs[i]
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (m.mine) Arrangement.End else Arrangement.Start) {
                        Card(
                            colors = CardDefaults.cardColors(
                                containerColor = if (m.mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                            ),
                            modifier = Modifier.widthIn(max = 330.dp).combinedClickable(
                                onClick = { if (picked.isNotEmpty()) picked = if (i in picked) picked - i else picked + i },
                                onLongClick = { picked = picked + i },
                            ),
                            border = if (i in picked) androidx.compose.foundation.BorderStroke(2.dp, MaterialTheme.colorScheme.primary) else null,
                        ) {
                            Column(Modifier.padding(10.dp)) {
                                Text(m.text)
                                when (m.st) {
                                    ChatLog.St.ASK -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                                        Button(onClick = {
                                            val (before, done) = Assistant.apply(ctx, m.proposal!!)
                                            update(i) { it.copy(st = ChatLog.St.DONE, before = before) }
                                            ChatLog.add(ctx, ChatLog.Msg(false, done))
                                        }) { Text("Сделать") }
                                        OutlinedButton(onClick = { update(i) { it.copy(st = ChatLog.St.SKIPPED) } }) { Text("Не надо") }
                                    }
                                    ChatLog.St.DONE -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                                        Text("✓ Сделано", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                                        OutlinedButton(onClick = {
                                            m.before?.let { Assistant.undo(ctx, it) }
                                            update(i) { it.copy(st = ChatLog.St.UNDONE) }
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
