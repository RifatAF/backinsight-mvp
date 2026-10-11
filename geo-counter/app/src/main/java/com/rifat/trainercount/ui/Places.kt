@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import android.location.Address
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
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
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Notifier
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.MAX_PER_VISIT
import com.rifat.trainercount.logic.Pending
import com.rifat.trainercount.logic.Place
import com.rifat.trainercount.logic.Slot
import com.rifat.trainercount.logic.dismissPending
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fDow
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.newId
import com.rifat.trainercount.logic.resolvePending
import com.rifat.trainercount.logic.thresholdFor
import com.rifat.trainercount.logic.trainings
import kotlinx.coroutines.launch

val DAY_NAMES = listOf("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

fun slotText(s: Slot) = "${DAY_NAMES[(s.day - 1).coerceIn(0, 6)]} ${hm(s.min)}"

/** Выбор места: «Я здесь», поиск по адресу, координаты или ссылка с карты. */
@Composable
fun LocationPicker(
    lat: Double?,
    lng: Double?,
    address: String,
    radius: Float,
    emptyHint: String,
    onPlace: (Double, Double, String?) -> Unit,
    onAutoAddress: (String) -> Unit,
    onAddress: (String) -> Unit,
    onRadius: (Float) -> Unit,
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<Address>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }

    fun set(la: Double, lo: Double, addr: String?) {
        onPlace(la, lo, addr)
        if (addr.isNullOrBlank()) scope.launch { reverseGeocode(ctx, la, lo)?.let(onAutoAddress) }
    }

    Column {
        Text(
            when {
                lat != null && address.isNotBlank() -> "✓ $address"
                lat != null -> "✓ Место задано: %.5f, %.5f".format(lat, lng)
                else -> emptyHint
            },
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(8.dp))
        Button(enabled = !busy, onClick = {
            busy = true
            currentLocation(ctx) { l ->
                busy = false
                if (l == null) Toast.makeText(ctx, "Не удалось определить место", Toast.LENGTH_SHORT).show()
                else set(l.latitude, l.longitude, null)
            }
        }) {
            Icon(Icons.Default.LocationOn, null)
            Text(if (busy) "  Определяю…" else "  Я здесь")
        }
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = query,
            onValueChange = { q ->
                query = q
                parseCoords(q)?.let { (la, lo) -> set(la, lo, null); results = emptyList() }
            },
            label = { Text("Или адрес / координаты / ссылка на карту") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            trailingIcon = {
                IconButton(onClick = {
                    scope.launch {
                        results = geocode(ctx, query)
                        if (results.isEmpty()) Toast.makeText(ctx, "Ничего не нашлось", Toast.LENGTH_SHORT).show()
                    }
                }) { Icon(Icons.Default.Search, "Найти") }
            },
        )
        results.forEach { a ->
            TextButton(onClick = {
                set(a.latitude, a.longitude, shortAddress(a))
                results = emptyList(); query = ""
            }) { Text(a.getAddressLine(0) ?: shortAddress(a)) }
        }
        if (lat != null) {
            OutlinedTextField(
                value = address, onValueChange = onAddress, label = { Text("Подпись адреса") },
                singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            )
            Text("Радиус зоны: ${radius.toInt()} м", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            Slider(value = radius, onValueChange = onRadius, valueRange = 60f..300f, steps = 23)
            Text(
                "Больше радиус: надёжнее срабатывает. Меньше: не путает с соседними домами.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** Расписание клиента в общем месте. */
@Composable
fun SlotsEditor(slots: List<Slot>, onChange: (List<Slot>) -> Unit) {
    val ctx = LocalContext.current
    var day by remember { mutableIntStateOf(1) }
    Column {
        Text(
            "Если занятия в постоянное время, укажите его: приложение само поймёт, с кем было занятие, и ничего не спросит.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (slots.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(top = 8.dp)) {
            slots.sortedWith(compareBy({ it.day }, { it.min })).forEach { s ->
                InputChip(
                    selected = false,
                    onClick = { onChange(slots - s) },
                    label = { Text(slotText(s)) },
                    trailingIcon = { Icon(Icons.Default.Close, "Убрать") },
                )
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(top = 8.dp)) {
            DAY_NAMES.forEachIndexed { i, n ->
                FilterChip(selected = day == i + 1, onClick = { day = i + 1 }, label = { Text(n) })
            }
        }
        OutlinedButton(onClick = {
            pickTime(ctx, 18 * 60) { m ->
                val s = Slot(day, m)
                if (s !in slots) onChange(slots + s)
            }
        }) { Text("+ Время в ${DAY_NAMES[day - 1]}") }
    }
}

@Composable
fun EditPlaceScreen(data: AppData, id: String?, nav: Nav) {
    val ctx = LocalContext.current
    val orig = id?.let { data.place(it) }
    var name by remember { mutableStateOf(orig?.name ?: "") }
    var lat by remember { mutableStateOf(orig?.lat) }
    var lng by remember { mutableStateOf(orig?.lng) }
    var address by remember { mutableStateOf("") }
    var radius by remember { mutableFloatStateOf((orig?.radius ?: 80).toFloat()) }
    var sessionMin by remember { mutableIntStateOf(orig?.sessionMin ?: 60) }
    var minMin by remember { mutableIntStateOf(orig?.minMin ?: 30) }
    var askDelete by remember { mutableStateOf(false) }
    val pid = remember { orig?.id ?: newId() }

    fun save() {
        val la = lat
        val lo = lng
        if (name.isBlank() || la == null || lo == null) {
            Toast.makeText(ctx, "Нужны название и место", Toast.LENGTH_SHORT).show(); return
        }
        val p = Place(pid, name.trim(), la, lo, radius.toInt(), sessionMin, minMin)
        Store.update { d ->
            if (d.places.any { it.id == pid }) d.copy(places = d.places.map { if (it.id == pid) p else it })
            else d.copy(places = d.places + p)
        }
        GeoSync.sync(ctx)
        nav.back()
    }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(if (orig == null) "Общее место" else orig.name) },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = { TextButton(onClick = { save() }) { Text("Сохранить") } },
        )
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
            Text(
                "Место, где бывают разные клиенты: кафе для онлайн-занятий, зал. Клиентов к нему привязывают в их карточке.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedTextField(
                value = name, onValueChange = { name = it }, label = { Text("Название, например «Кафе»") },
                singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            )
            SectionTitle("Где")
            Card {
                Column(Modifier.padding(12.dp)) {
                    LocationPicker(
                        lat, lng, address, radius, "Место не задано. Проще всего нажать «Я здесь», сидя в кафе.",
                        onPlace = { la, lo, a -> lat = la; lng = lo; if (a != null) address = a },
                        onAutoAddress = { if (address.isBlank()) address = it },
                        onAddress = { address = it },
                        onRadius = { radius = it },
                    )
                }
            }
            SectionTitle("Как считать")
            Card {
                Column(Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Длина занятия", modifier = Modifier.weight(1f))
                        Stepper(sessionMin, { sessionMin = it }, 30..180, 15) { fDur(it.toLong()) }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Засчитывать от", modifier = Modifier.weight(1f))
                        Stepper(minMin, { minMin = it }, 10..90, 5) { "$it мин" }
                    }
                    Text(
                        "1 занятие от ${fDur(thresholdFor(1, sessionMin, minMin).toLong())}, " +
                            "2 от ${fDur(thresholdFor(2, sessionMin, minMin).toLong())}, " +
                            "3 от ${fDur(thresholdFor(3, sessionMin, minMin).toLong())}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (orig != null) {
                OutlinedButton(onClick = { askDelete = true }, modifier = Modifier.padding(top = 16.dp)) {
                    Text("Удалить место", color = MaterialTheme.colorScheme.error)
                }
            }
            Spacer(Modifier.height(16.dp))
            Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                Button(onClick = { save() }) { Text("Сохранить") }
            }
            Spacer(Modifier.height(32.dp))
        }
    }

    if (askDelete && orig != null) AlertDialog(
        onDismissRequest = { askDelete = false },
        title = { Text("Удалить «${orig.name}»?") },
        text = { Text("История занятий останется. Клиенты этого места станут без адреса.") },
        confirmButton = {
            TextButton(onClick = {
                Store.update { d ->
                    d.copy(
                        places = d.places.filter { it.id != orig.id },
                        clients = d.clients.map { if (it.placeId == orig.id) it.copy(placeId = null) else it },
                        pending = d.pending.filter { it.placeId != orig.id },
                        open = d.open - ("p:" + orig.id),
                    )
                }
                GeoSync.sync(ctx)
                nav.back()
            }) { Text("Удалить", color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = { askDelete = false }) { Text("Отмена") } },
    )
}

/** Блок в настройках: список общих мест. */
@Composable
fun PlacesBlock(data: AppData, nav: Nav) {
    Column {
        Text(
            "Кафе для онлайн-занятий, зал: места, где бывают разные клиенты.",
            style = MaterialTheme.typography.bodyMedium,
        )
        data.places.forEach { p ->
            val n = data.clients.count { it.placeId == p.id && !it.archived }
            TextButton(onClick = { nav.go(Screen.EditPlace(p.id)) }) {
                Text("${p.name} · клиентов: $n")
            }
        }
        OutlinedButton(onClick = { nav.go(Screen.EditPlace(null)) }, modifier = Modifier.padding(top = 4.dp)) {
            Text("+ Общее место")
        }
    }
}

@Composable
fun PendingCard(data: AppData, p: Pending, onClick: () -> Unit) {
    val name = data.place(p.placeId)?.name ?: "Общее место"
    WarnCard("$name, ${fDow(p.start)} ${fDate(p.start)}, ${fTime(p.start)}–${fTime(p.end)}: ${trainings(p.count)}. С кем?", onClick)
}

/** Отметить, с кем были занятия в общем месте. */
@Composable
fun PendingDialog(data: AppData, p: Pending, close: () -> Unit) {
    val place = data.place(p.placeId)
    val here = data.clients.filter { it.placeId == p.placeId && !it.archived }.sortedBy { it.name.lowercase() }
    val others = data.clients.filter { it.placeId != p.placeId && !it.archived }.sortedBy { it.name.lowercase() }
    val picks = remember(p.id) { mutableStateMapOf<String, Int>().apply { p.suggested.forEach { put(it, 1) } } }
    var showOthers by remember { mutableStateOf(here.isEmpty()) }
    val total = picks.values.sum()

    val ctx = LocalContext.current
    val done: () -> Unit = {
        Notifier.cancelId(ctx, Notifier.pendingNid(p))
        close()
    }

    AlertDialog(
        onDismissRequest = close,
        title = { Text("${place?.name ?: "Общее место"}: с кем?") },
        text = {
            Column(Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState())) {
                Text("${fDow(p.start)}, ${fDate(p.start)} · ${fTime(p.start)}–${fTime(p.end)} · по времени ${trainings(p.count)}")
                Text(
                    "Отмечено: ${trainings(total)}",
                    fontWeight = FontWeight.SemiBold,
                    color = if (total == p.count) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
                here.forEach { c -> PickRow(c.name, picks[c.id] ?: 0) { picks[c.id] = it } }
                if (others.isNotEmpty()) {
                    if (here.isNotEmpty()) HorizontalDivider(Modifier.padding(vertical = 6.dp))
                    if (showOthers) others.forEach { c -> PickRow(c.name, picks[c.id] ?: 0) { picks[c.id] = it } }
                    else TextButton(onClick = { showOthers = true }) { Text("Другие клиенты") }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = total > 0, onClick = {
                val list = (here + others).mapNotNull { c -> picks[c.id]?.takeIf { it > 0 }?.let { c.id to it } }
                Store.update { resolvePending(it, p.id, list) }
                done()
            }) { Text("Сохранить") }
        },
        dismissButton = {
            Row {
                TextButton(onClick = {
                    Store.update { dismissPending(it, p.id) }
                    done()
                }) { Text("Не было") }
                TextButton(onClick = close) { Text("Позже") }
            }
        },
    )
}

@Composable
private fun PickRow(name: String, value: Int, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(name, modifier = Modifier.weight(1f))
        Stepper(value, onChange, 0..MAX_PER_VISIT)
    }
}
