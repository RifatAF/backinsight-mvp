@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.epochMs
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.findWindows
import com.rifat.trainercount.logic.hhmm
import com.rifat.trainercount.logic.windowsText
import java.time.LocalDate

/** Быстрый ответ клиенту «есть свободное окно?»: свободное время с учётом дороги. */
@Composable
fun FindWindowScreen(data: AppData, nav: Nav) {
    val ctx = LocalContext.current
    val now = rememberNow(60_000)
    var duration by remember { mutableIntStateOf(60) }
    var days by remember { mutableIntStateOf(7) }
    val travel = data.settings.travelMin
    val today = LocalDate.now()
    val ws = findWindows(data, today, days, duration, travel, now)
    val byDay = ws.groupBy { it.date }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Найти окно") },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = {
                IconButton(enabled = ws.isNotEmpty(), onClick = {
                    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, windowsText(ws, duration))
                    ctx.startActivity(Intent.createChooser(send, "Отправить клиенту"))
                }) { Icon(Icons.Default.Share, "Отправить клиенту") }
            },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item {
                Text("Длина занятия", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(45, 60, 90, 120).forEach { m ->
                        FilterChip(selected = duration == m, onClick = { duration = m }, label = { Text(fDur(m.toLong())) })
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                    Text("Дорога до и после", modifier = Modifier.weight(1f))
                    Stepper(travel, { v -> Store.update { it.copy(settings = it.settings.copy(travelMin = v)) } }, 0..90, 15) { "$it мин" }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(selected = days == 7, onClick = { days = 7 }, label = { Text("Неделя") })
                    FilterChip(selected = days == 14, onClick = { days = 14 }, label = { Text("2 недели") })
                }
                Text(
                    "Время «с … до …»: когда можно начать занятие, чтобы успеть доехать и уехать к следующему клиенту.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            if (ws.isEmpty()) item {
                Card(Modifier.fillMaxWidth()) { Text("Свободных окон нет", modifier = Modifier.padding(16.dp)) }
            }
            items(byDay.keys.toList(), key = { it.toEpochDay() }) { date ->
                val list = byDay.getValue(date)
                Card(onClick = { nav.scheduleDate = date; nav.go(Screen.Schedule) }, modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        Text(
                            dayTitle(date) + if (date == today) " (сегодня)" else if (date == today.plusDays(1)) " (завтра)" else "",
                            fontWeight = FontWeight.SemiBold,
                        )
                        list.forEach { w ->
                            Text(
                                if (w.earliest == w.latest) "в ${hhmm(w.earliest)}" else "начать с ${hhmm(w.earliest)} до ${hhmm(w.latest)}",
                                color = clientColorWindow(),
                                style = MaterialTheme.typography.bodyLarge,
                            )
                        }
                    }
                }
            }
            if (ws.isNotEmpty()) item {
                Button(onClick = {
                    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, windowsText(ws, duration))
                    ctx.startActivity(Intent.createChooser(send, "Отправить клиенту"))
                }, modifier = Modifier.fillMaxWidth()) { Text("Отправить клиенту") }
            }
        }
    }
}

private fun clientColorWindow() = androidx.compose.ui.graphics.Color(0xFF2E9E6A)
