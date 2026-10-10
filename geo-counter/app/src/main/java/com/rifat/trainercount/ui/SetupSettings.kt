@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings as AndroidSettings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Perms
import com.rifat.trainercount.geo.Wifi
import com.rifat.trainercount.logic.AppData
import kotlinx.coroutines.launch

/** Проверка разрешений, без которых фоновый учёт не работает. */
@Composable
fun PermissionChecklist() {
    val ctx = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { tick++ }
    val refresh: (Any?) -> Unit = { tick++ }
    val loc = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions(), refresh)
    val bg = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission(), refresh)
    val notif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission(), refresh)

    key(tick) {
    val hasLoc = Perms.location(ctx)
    Column {
        CheckRow(hasLoc, "Геолокация", "Нужна, чтобы узнавать адреса клиентов", "Разрешить") {
            loc.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
        }
        CheckRow(
            Perms.background(ctx), "Геолокация «Всегда»",
            "В открывшемся окне выберите «Разрешить в любом режиме»", "Открыть",
        ) {
            if (!hasLoc) Toast.makeText(ctx, "Сначала разрешите геолокацию", Toast.LENGTH_SHORT).show()
            else bg.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        }
        CheckRow(Perms.notifications(ctx), "Уведомления", "Тихие сообщения о засчитанных тренировках", "Разрешить") {
            if (Build.VERSION.SDK_INT >= 33) notif.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        CheckRow(Perms.battery(ctx), "Батарея без ограничений", "Чтобы система не усыпляла учёт", "Открыть") {
            ctx.startActivity(
                Intent(AndroidSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
            )
        }
    }
    }
}

/** Блок «Дом»: место + Wi‑Fi. */
@Composable
fun HomeBlock(data: AppData) {
    val ctx = LocalContext.current
    val s = data.settings
    var busy by remember { mutableStateOf(false) }
    var ssid by remember(s.homeSsid) { mutableStateOf(s.homeSsid) }
    Column {
        Text(
            if (s.hasHome) "Дом сохранён. Учёт рабочего времени: от выхода из дома до возвращения."
            else "Встаньте дома и нажмите кнопку: приложение запомнит место и домашний Wi‑Fi.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(8.dp))
        Button(enabled = !busy, onClick = {
            busy = true
            currentLocation(ctx) { l ->
                busy = false
                if (l == null) {
                    Toast.makeText(ctx, "Не удалось определить место. Включена ли геолокация?", Toast.LENGTH_LONG).show()
                    return@currentLocation
                }
                val cur = Wifi.currentSsid(ctx)
                Store.update { d ->
                    val names = d.settings.homeSsid.split(',').map { it.trim() }.filter { it.isNotEmpty() }
                    val newSsid = if (cur != null && names.none { it.equals(cur, true) }) (names + cur).joinToString(", ")
                    else d.settings.homeSsid
                    d.copy(settings = d.settings.copy(homeLat = l.latitude, homeLng = l.longitude, homeSsid = newSsid))
                }
                GeoSync.sync(ctx)
                Toast.makeText(ctx, "Дом сохранён" + (cur?.let { ", Wi‑Fi «$it»" } ?: ""), Toast.LENGTH_SHORT).show()
            }
        }) {
            Icon(Icons.Default.Home, null)
            Text(if (busy) "  Определяю…" else if (s.hasHome) "  Я дома: обновить" else "  Я сейчас дома")
        }
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = ssid,
            onValueChange = { ssid = it },
            label = { Text("Домашний Wi‑Fi (можно несколько через запятую)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        if (ssid != s.homeSsid) {
            TextButton(onClick = { Store.update { it.copy(settings = it.settings.copy(homeSsid = ssid.trim())) } }) {
                Text("Сохранить Wi‑Fi")
            }
        }
        if (s.hasHome) {
            Text("Радиус дома: ${s.homeRadius} м", style = MaterialTheme.typography.bodySmall)
            var r by remember(s.homeRadius) { mutableStateOf(s.homeRadius.toFloat()) }
            Slider(
                value = r, onValueChange = { r = it }, valueRange = 100f..300f, steps = 7,
                onValueChangeFinished = {
                    Store.update { it.copy(settings = it.settings.copy(homeRadius = r.toInt())) }
                    GeoSync.sync(ctx)
                },
            )
        }
    }
}

private val DAYS = listOf("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

@Composable
fun HoursBlock(data: AppData) {
    val ctx = LocalContext.current
    val s = data.settings
    Column {
        Text("Вне этих часов приложение ничего не считает.", style = MaterialTheme.typography.bodyMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            OutlinedButton(onClick = {
                pickTime(ctx, s.startMin) { m -> Store.update { it.copy(settings = it.settings.copy(startMin = m)) } }
            }) { Text("с ${hm(s.startMin)}") }
            OutlinedButton(onClick = {
                pickTime(ctx, s.endMin) { m -> Store.update { it.copy(settings = it.settings.copy(endMin = m)) } }
            }) { Text("до ${hm(s.endMin)}") }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            DAYS.forEachIndexed { i, name ->
                val day = i + 1
                FilterChip(
                    selected = day in s.days,
                    onClick = {
                        Store.update {
                            val days = if (day in it.settings.days) it.settings.days - day else it.settings.days + day
                            it.copy(settings = it.settings.copy(days = days))
                        }
                    },
                    label = { Text(name) },
                )
            }
        }
    }
}

@Composable
fun SetupScreen(data: AppData, nav: Nav) {
    val ctx = LocalContext.current
    Scaffold(topBar = { TopAppBar(title = { Text("Настройка за минуту") }) }) { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)
        ) {
            Text(
                "Приложение само считает тренировки по геопозиции: пробыли у клиента 30 минут и больше, значит тренировка была. " +
                    "Интернет не нужен, батарея почти не тратится.",
                style = MaterialTheme.typography.bodyMedium,
            )
            SectionTitle("1. Разрешения")
            Card { Column(Modifier.padding(12.dp)) { PermissionChecklist() } }
            SectionTitle("2. Дом")
            Card { Column(Modifier.padding(12.dp)) { HomeBlock(data) } }
            SectionTitle("3. Рабочие часы")
            Card { Column(Modifier.padding(12.dp)) { HoursBlock(data) } }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    Store.update { it.copy(settings = it.settings.copy(setupDone = true)) }
                    GeoSync.sync(ctx)
                    nav.reset(Screen.Home)
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Готово") }
            if (!Perms.background(ctx)) {
                Text(
                    "Без геолокации «Всегда» учёт будет только ручным.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 6.dp).align(Alignment.CenterHorizontally),
                )
            }
            Spacer(Modifier.height(32.dp))
        }
    }
}

@Composable
fun SettingsScreen(data: AppData, nav: Nav) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        runCatching { ctx.contentResolver.openOutputStream(uri)?.use { it.write(Store.exportJson().toByteArray()) } }
            .onSuccess { Toast.makeText(ctx, "Сохранено", Toast.LENGTH_SHORT).show() }
            .onFailure { Toast.makeText(ctx, "Ошибка: ${it.message}", Toast.LENGTH_LONG).show() }
    }
    val importer = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val text = ctx.contentResolver.openInputStream(uri)!!.bufferedReader().use { it.readText() }
                Store.importJson(text)
                GeoSync.sync(ctx)
            }.onSuccess { Toast.makeText(ctx, "Данные восстановлены", Toast.LENGTH_SHORT).show() }
                .onFailure { Toast.makeText(ctx, "Не тот файл: ${it.message}", Toast.LENGTH_LONG).show() }
        }
    }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Настройки") },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
        )
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
            SectionTitle("Проверка работы")
            Card { Column(Modifier.padding(12.dp)) { PermissionChecklist() } }
            SectionTitle("Дом")
            Card { Column(Modifier.padding(12.dp)) { HomeBlock(data) } }
            SectionTitle("Рабочие часы")
            Card { Column(Modifier.padding(12.dp)) { HoursBlock(data) } }
            SectionTitle("Данные")
            Card {
                Column(Modifier.padding(12.dp)) {
                    Text(
                        "Данные хранятся только в телефоне и автоматически попадают в резервную копию Google. " +
                            "Можно сохранить файл вручную.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                        OutlinedButton(onClick = { export.launch("trenirovki-${java.time.LocalDate.now()}.json") }) { Text("Сохранить файл") }
                        OutlinedButton(onClick = { importer.launch(arrayOf("application/json", "application/octet-stream", "*/*")) }) {
                            Text("Восстановить")
                        }
                    }
                }
            }
            SectionTitle("Как считается")
            Text(
                "• Пробыли у клиента от 30 минут: 1 тренировка. Каждая следующая через длительность тренировки (+10 минут запаса). " +
                    "Пороги меняются в карточке клиента.\n" +
                    "• После выхода приходит тихое уведомление с кнопками «Отменить» и «+1».\n" +
                    "• Рабочее время: от выхода из дома до возвращения (по домашнему Wi‑Fi или по месту). " +
                    "Выходы без тренировок считаются личными, это можно поменять в статистике.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(32.dp))
        }
    }
}
