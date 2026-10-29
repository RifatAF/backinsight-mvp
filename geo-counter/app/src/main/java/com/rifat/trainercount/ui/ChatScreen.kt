@file:OptIn(ExperimentalMaterial3Api::class)

package com.rifat.trainercount.ui

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

/** Переписка с помощником живёт, пока открыто приложение. */
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
    val scope = rememberCoroutineScope()
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val list = rememberLazyListState()
    val msgs = ChatLog.msgs
    LaunchedEffect(msgs.size, busy) { if (msgs.isNotEmpty()) list.animateScrollToItem(msgs.size - 1 + if (busy) 1 else 0) }

    fun send(text: String) {
        val t = text.trim()
        if (t.isEmpty() || busy) return
        input = ""
        val history = ChatLog.history()
        msgs += ChatLog.Msg(true, t)
        busy = true
        scope.launch {
            val p = withContext(Dispatchers.IO) {
                runCatching { Assistant.think(ctx, t, history, if (com.rifat.trainercount.geo.Wifi.isHome(ctx, data)) "дома" else "не дома") }.getOrNull()
            }
            msgs += when {
                p == null -> ChatLog.Msg(false, "Не получилось связаться с ИИ. Проверьте интернет и ключ Gemini в настройках.")
                p.plan.hasChanges -> ChatLog.Msg(false, p.plan.text(), p, ChatLog.St.ASK)
                else -> ChatLog.Msg(false, p.plan.text())
            }
            busy = false
        }
    }

    fun update(i: Int, f: (ChatLog.Msg) -> ChatLog.Msg) { msgs[i] = f(msgs[i]) }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Помощник") },
            navigationIcon = { IconButton(onClick = { nav.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
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
                            modifier = Modifier.widthIn(max = 330.dp),
                        ) {
                            Column(Modifier.padding(10.dp)) {
                                Text(m.text)
                                when (m.st) {
                                    ChatLog.St.ASK -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                                        Button(onClick = {
                                            val (before, done) = Assistant.apply(ctx, m.proposal!!)
                                            update(i) { it.copy(st = ChatLog.St.DONE, before = before) }
                                            msgs += ChatLog.Msg(false, done)
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
