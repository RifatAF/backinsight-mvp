@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
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
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Client
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.newId
import com.rifat.trainercount.logic.thresholdFor

@Composable
fun EditClientScreen(data: AppData, id: String?, nav: Nav) {
    val ctx = LocalContext.current
    val orig = id?.let { data.client(it) }
    val c0 = remember { orig ?: Client(newId(), "", createdAt = System.currentTimeMillis()) }

    var name by remember { mutableStateOf(c0.name) }
    var members by remember { mutableStateOf(c0.members.joinToString(", ")) }
    var address by remember { mutableStateOf(c0.address) }
    var lat by remember { mutableStateOf(c0.lat) }
    var lng by remember { mutableStateOf(c0.lng) }
    var radius by remember { mutableFloatStateOf(c0.radius.toFloat()) }
    var sessionMin by remember { mutableIntStateOf(c0.sessionMin) }
    var minMin by remember { mutableIntStateOf(c0.minMin) }
    var price by remember { mutableStateOf(if (c0.price > 0) c0.price.toString() else "") }
    var archived by remember { mutableStateOf(c0.archived) }
    var placeId by remember { mutableStateOf(c0.placeId) }
    var slots by remember { mutableStateOf(c0.slots) }

    var askDelete by remember { mutableStateOf(false) }

    fun current() = c0.copy(
        name = name.trim(),
        members = members.split(',').map { it.trim() }.filter { it.isNotEmpty() },
        address = address.trim(), lat = lat, lng = lng, radius = radius.toInt(),
        sessionMin = sessionMin, minMin = minMin, price = price.toIntOrNull() ?: 0, archived = archived,
        placeId = placeId, slots = slots,
    )

    fun save() {
        if (name.isBlank()) {
            Toast.makeText(ctx, "Введите имя", Toast.LENGTH_SHORT).show(); return
        }
        val c = current()
        Store.update { d ->
            if (d.clients.any { it.id == c.id }) d.copy(clients = d.clients.map { if (it.id == c.id) c else it })
            else d.copy(clients = d.clients + c)
        }
        GeoSync.sync(ctx)
        nav.back()
        if (orig == null) nav.go(Screen.ClientView(c.id))
    }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(if (orig == null) "Новый клиент" else "Клиент") },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = { TextButton(onClick = { save() }) { Text("Сохранить") } },
        )
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
            OutlinedTextField(
                value = name, onValueChange = { name = it }, label = { Text("Имя или «Семья Ивановых»") },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = members, onValueChange = { members = it },
                label = { Text("Если семья: имена через запятую") },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words),
            )

            SectionTitle("Где занятия")
            if (data.places.isNotEmpty()) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(selected = placeId == null, onClick = { placeId = null }, label = { Text("У клиента") })
                    data.places.forEach { p ->
                        FilterChip(selected = placeId == p.id, onClick = { placeId = p.id }, label = { Text(p.name) })
                    }
                }
                Spacer(Modifier.height(8.dp))
            }
            Card {
                Column(Modifier.padding(12.dp)) {
                    if (placeId == null) {
                        LocationPicker(
                            lat, lng, address, radius, "Место не задано. Проще всего нажать «Я здесь», стоя у дома клиента.",
                            onPlace = { la, lo, a -> lat = la; lng = lo; if (a != null) address = a },
                            onAutoAddress = { if (address.isBlank()) address = it },
                            onAddress = { address = it },
                            onRadius = { radius = it },
                        )
                        if (data.places.isEmpty()) TextButton(onClick = { nav.go(Screen.EditPlace(null)) }) {
                            Text("Занимаетесь в кафе или зале с разными клиентами? Добавить общее место")
                        }
                    } else {
                        Text("Расписание", style = MaterialTheme.typography.titleSmall)
                        SlotsEditor(slots) { slots = it }
                    }
                }
            }

            SectionTitle("Как считать")
            Card {
                Column(Modifier.padding(12.dp)) {
                    if (placeId == null) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Длина тренировки", modifier = Modifier.weight(1f))
                            Stepper(sessionMin, { sessionMin = it }, 30..180, 15) { fDur(it.toLong()) }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Засчитывать от", modifier = Modifier.weight(1f))
                            Stepper(minMin, { minMin = it }, 10..90, 5) { "$it мин" }
                        }
                        val preview = current()
                        Text(
                            "1 тренировка от ${fDur(thresholdFor(1, preview).toLong())}, " +
                                "2 от ${fDur(thresholdFor(2, preview).toLong())}, 3 от ${fDur(thresholdFor(3, preview).toLong())}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                    OutlinedTextField(
                        value = price, onValueChange = { price = it.filter(Char::isDigit).take(7) },
                        label = { Text("Цена тренировки, ₽ (для статистики)") },
                        supportingText = { Text("Пусто: возьмётся из последней оплаты") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    )
                }
            }

            if (orig != null) {
                SectionTitle("Прочее")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("В архиве")
                        Text("Закончил заниматься: учёт выключен, история хранится", style = MaterialTheme.typography.bodySmall)
                    }
                    Switch(checked = archived, onCheckedChange = { archived = it })
                }
                OutlinedButton(onClick = { askDelete = true }, modifier = Modifier.padding(top = 12.dp)) {
                    Text("Удалить клиента", color = MaterialTheme.colorScheme.error)
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
        text = { Text("Вся история и оплаты этого клиента будут удалены. Если он просто закончил, лучше отправить в архив.") },
        confirmButton = {
            TextButton(onClick = {
                Store.update { d ->
                    d.copy(
                        clients = d.clients.filter { it.id != orig.id },
                        visits = d.visits.filter { it.clientId != orig.id },
                        packs = d.packs.filter { it.clientId != orig.id },
                        open = d.open - orig.id,
                    )
                }
                GeoSync.sync(ctx)
                nav.reset(Screen.Home)
            }) { Text("Удалить", color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = { askDelete = false }) { Text("Отмена") } },
    )
}
