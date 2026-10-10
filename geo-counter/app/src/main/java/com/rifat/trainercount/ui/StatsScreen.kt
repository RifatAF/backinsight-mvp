@file:OptIn(ExperimentalMaterial3Api::class)

package com.rifat.trainercount.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.VisitKind
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fDow
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fMoney
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.isWork
import com.rifat.trainercount.logic.monthStats
import com.rifat.trainercount.logic.trainings
import java.time.YearMonth
import java.time.format.TextStyle
import java.util.Locale

@Composable
fun StatsScreen(data: AppData, nav: Nav) {
    val now = rememberNow(60_000)
    var ym by remember { mutableStateOf(YearMonth.now()) }
    val m = monthStats(data, ym, now)
    val monthName = ym.month.getDisplayName(TextStyle.FULL_STANDALONE, Locale("ru")).replaceFirstChar { it.uppercase() }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Статистика") },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
        ) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                    IconButton(onClick = { ym = ym.minusMonths(1) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Раньше") }
                    Text(
                        "$monthName ${ym.year}",
                        style = MaterialTheme.typography.titleMedium,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = { ym = ym.plusMonths(1) }, enabled = ym < YearMonth.now()) {
                        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Позже")
                    }
                }
            }
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StatTile("тренировок" + if (m.burned > 0) " (${m.burned} отмен)" else "", "${m.sessions}", Modifier.weight(1f))
                        StatTile("заработано", fMoney(m.earned), Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StatTile("в работе", fDur(m.workMin), Modifier.weight(1f))
                        StatTile("получено оплат", fMoney(m.received), Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StatTile("у клиентов", fDur(m.atClientsMin), Modifier.weight(1f))
                        StatTile("дорога и ожидание", fDur(m.roadMin), Modifier.weight(1f))
                    }
                    if (m.perHour > 0) StatTile("в час с учётом дороги", fMoney(m.perHour), Modifier.fillMaxWidth())
                }
            }
            if (m.perClient.isNotEmpty()) {
                item { SectionTitle("По клиентам") }
                items(m.perClient, key = { it.client.id }) { pc ->
                    ListItem(
                        headlineContent = { Text(pc.client.name) },
                        supportingContent = { if (pc.burned > 0) Text("из них отмен: ${pc.burned}") },
                        trailingContent = {
                            Column(horizontalAlignment = Alignment.End) {
                                Text(trainings(pc.sessions))
                                if (pc.earned > 0) Text(fMoney(pc.earned), style = MaterialTheme.typography.bodySmall)
                            }
                        },
                    )
                }
            }
            if (m.trips.isNotEmpty()) {
                item {
                    SectionTitle("Выходы из дома")
                    Text(
                        "Выключите переключатель, если выход был личным, и он не попадёт в рабочее время.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                items(m.trips, key = { it.id }) { t ->
                    val end = t.end ?: now
                    val work = isWork(data, t, now)
                    val n = data.visits.filter { it.kind != VisitKind.BURNED && it.start in t.start..end }.sumOf { it.count }
                    ListItem(
                        headlineContent = { Text("${fDow(t.start)}, ${fDate(t.start)}  ${fTime(t.start)}–${t.end?.let(::fTime) ?: "сейчас"}") },
                        supportingContent = { Text(fDur((end - t.start) / 60_000) + if (n > 0) " · ${trainings(n)}" else " · без тренировок") },
                        trailingContent = {
                            Switch(checked = work, onCheckedChange = { v ->
                                Store.update { d -> d.copy(trips = d.trips.map { if (it.id == t.id) it.copy(work = v) else it }) }
                            })
                        },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}
