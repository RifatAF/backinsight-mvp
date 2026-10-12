@file:OptIn(ExperimentalMaterial3Api::class)

package com.rifat.trainercount.ui

import android.content.Intent
import android.provider.Settings as AndroidSettings
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
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.rifat.trainercount.geo.Diag
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Poller
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fTime
import kotlin.concurrent.thread

/** Строка состояния геозон: зелёная или красная, с переходом к исправлению. */
@Composable
fun GeoStateCard(onFix: () -> Unit) {
    val st by Diag.state.collectAsStateWithLifecycle()
    val s = st ?: return
    Card(
        onClick = onFix,
        colors = CardDefaults.cardColors(
            containerColor = if (s.ok) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.errorContainer,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(if (s.ok) "✓ ${s.text}" else "✗ ${s.text}", style = MaterialTheme.typography.bodyMedium)
            Text("обновлено ${fDate(s.at)} ${fTime(s.at)}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
fun LogScreen(nav: Nav) {
    val ctx = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    val lines = remember(tick) { Diag.read(ctx) }
    val st by Diag.state.collectAsStateWithLifecycle()

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Журнал работы") },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = {
                IconButton(onClick = {
                    val send = Intent(Intent.ACTION_SEND).setType("text/plain")
                        .putExtra(Intent.EXTRA_TEXT, Diag.read(ctx).take(400).joinToString("\n"))
                    ctx.startActivity(Intent.createChooser(send, "Отправить журнал"))
                }) { Icon(Icons.Default.Share, "Отправить") }
            },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
        ) {
            item {
                Text(
                    "Здесь видно всё, что приложение получило от системы: входы и выходы из зон, Wi‑Fi, проверки места. " +
                        "Если тренировка не засчиталась, отправьте журнал.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            item {
                Column(Modifier.padding(vertical = 10.dp)) {
                    GeoStateCard {
                        ctx.startActivity(Intent(AndroidSettings.ACTION_LOCATION_SOURCE_SETTINGS))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                        Button(enabled = !busy, onClick = {
                            busy = true
                            thread {
                                Poller.run(ctx.applicationContext, manual = true)
                                busy = false
                                tick++
                            }
                        }) { Text(if (busy) "Проверяю…" else "Где я сейчас?") }
                        OutlinedButton(onClick = {
                            GeoSync.sync(ctx)
                            tick++
                        }) { Text("Перезапустить зоны") }
                    }
                    if (st?.ok == false) {
                        OutlinedButton(
                            onClick = { ctx.startActivity(Intent(AndroidSettings.ACTION_LOCATION_SOURCE_SETTINGS)) },
                            modifier = Modifier.padding(top = 4.dp),
                        ) { Text("Открыть настройки геолокации") }
                    }
                }
            }
            if (lines.isEmpty()) item { Text("Пока пусто") }
            items(lines) { line ->
                Text(
                    line,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 12.sp,
                    color = when {
                        "✗" in line -> MaterialTheme.colorScheme.error
                        "✓" in line -> MaterialTheme.colorScheme.primary
                        else -> MaterialTheme.colorScheme.onSurface
                    },
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
        }
    }
}
