@file:OptIn(ExperimentalMaterial3Api::class)

package com.rifat.trainercount.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.rifat.trainercount.geo.Perms
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Client
import com.rifat.trainercount.logic.VisitKind
import com.rifat.trainercount.logic.balance
import com.rifat.trainercount.logic.dayOf
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.inHours
import com.rifat.trainercount.logic.isWork
import com.rifat.trainercount.logic.trainings
import java.time.LocalDate

@Composable
fun HomeScreen(data: AppData, nav: Nav) {
    val ctx = LocalContext.current
    val now = rememberNow()
    var resumeTick by remember { mutableIntStateOf(0) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { resumeTick++ }
    val permsOk = remember(resumeTick) { Perms.allOk(ctx) }
    var showArchive by remember { mutableStateOf(false) }

    val active = data.clients.filter { !it.archived }.sortedBy { it.name.lowercase() }
    val archived = data.clients.filter { it.archived }
    val toCheck = data.visits.filter { it.check }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Тренировки") },
                actions = {
                    IconButton(onClick = { nav.go(Screen.Stats) }) { Icon(Icons.Default.DateRange, "Статистика") }
                    IconButton(onClick = { nav.go(Screen.Settings) }) { Icon(Icons.Default.Settings, "Настройки") }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { nav.go(Screen.Edit(null)) },
                icon = { Icon(Icons.Default.Add, null) },
                text = { Text("Клиент") },
            )
        },
    ) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { StatusCard(data, now) }
            if (!permsOk) item {
                WarnCard("Фоновый учёт выключен: не хватает разрешений. Нажмите, чтобы исправить.") { nav.go(Screen.Settings) }
            }
            if (!data.settings.hasHome) item {
                WarnCard("Дом не задан: рабочее время не считается.") { nav.go(Screen.Settings) }
            }
            if (toCheck.isNotEmpty()) item {
                WarnCard("Проверьте ${toCheck.size} ${if (toCheck.size == 1) "визит" else "визита"}: выход не был зафиксирован.") {
                    nav.go(Screen.ClientView(toCheck.first().clientId))
                }
            }
            if (active.isEmpty()) item {
                Card(Modifier.fillMaxWidth()) {
                    Text(
                        "Добавьте первого клиента. Проще всего прямо у его дома: «Клиент» → «Я здесь».",
                        modifier = Modifier.padding(16.dp),
                    )
                }
            }
            items(active, key = { it.id }) { c -> ClientCard(data, c) { nav.go(Screen.ClientView(c.id)) } }
            if (archived.isNotEmpty()) {
                item {
                    TextButton(onClick = { showArchive = !showArchive }) {
                        Text(if (showArchive) "Скрыть архив" else "Архив (${archived.size})")
                    }
                }
                if (showArchive) items(archived, key = { it.id }) { c -> ClientCard(data, c) { nav.go(Screen.ClientView(c.id)) } }
            }
        }
    }
}

@Composable
private fun StatusCard(data: AppData, now: Long) {
    val open = data.open.entries.maxByOrNull { it.value }
    val trip = data.openTrip
    val (title, sub) = when {
        open != null -> {
            val name = data.client(open.key)?.name ?: "клиента"
            "Сейчас у: $name" to "с ${fTime(open.value)} · ${fDur((now - open.value) / 60_000)}. Засчитается при выходе"
        }
        trip != null -> "В работе" to "вышли в ${fTime(trip.start)} · ${fDur((now - trip.start) / 60_000)}"
        !inHours(data.settings, now) -> "Отдых" to "вне рабочих часов учёт выключен"
        else -> "Дома" to "учёт включится, когда выйдете"
    }
    val today = LocalDate.now()
    val todayVisits = data.visits.filter { dayOf(it.start) == today && it.kind != VisitKind.BURNED }.sumOf { it.count }
    val todayWork = data.trips.filter { dayOf(it.start) == today && isWork(data, it, now) }
        .sumOf { ((it.end ?: now) - it.start) / 60_000 }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Text(sub, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(8.dp))
            Text(
                "Сегодня: ${trainings(todayVisits)}" + if (todayWork > 0) " · в работе ${fDur(todayWork)}" else "",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
        }
    }
}

@Composable
fun ClientCard(data: AppData, c: Client, onClick: () -> Unit) {
    val b = balance(data, c.id)
    val last = data.visits.filter { it.clientId == c.id }.maxByOrNull { it.start }
    val low = b.paid > 0 && b.left <= 1 || b.left < 0
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(c.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    val sub = buildList {
                        if (c.members.size > 1) add(c.members.joinToString(", "))
                        if (!c.hasPlace) add("адрес не задан")
                        add(last?.let { "был ${fDate(it.start)}" } ?: "ещё не был")
                    }.joinToString(" · ")
                    Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Column(horizontalAlignment = Alignment.End) {
                    val color = if (low) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
                    when {
                        b.left < 0 -> {
                            Text("${-b.left}", style = MaterialTheme.typography.headlineMedium, color = color)
                            Text("сверх оплаты", style = MaterialTheme.typography.bodySmall, color = color)
                        }
                        b.current != null -> {
                            Text("${b.leftInCurrent}", style = MaterialTheme.typography.headlineMedium, color = color)
                            Text("из ${b.current.sessions}", style = MaterialTheme.typography.bodySmall)
                        }
                        b.paid > 0 -> Text("пакет закончился", style = MaterialTheme.typography.bodySmall, color = color)
                        else -> Text("нет оплат", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            val cur = b.current
            if (cur != null) {
                Spacer(Modifier.height(10.dp))
                LinearProgressIndicator(
                    progress = { (cur.sessions - b.leftInCurrent).toFloat() / cur.sessions },
                    modifier = Modifier.fillMaxWidth(),
                    color = if (low) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}
