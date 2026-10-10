@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Client
import com.rifat.trainercount.logic.MAX_PER_VISIT
import com.rifat.trainercount.logic.Pack
import com.rifat.trainercount.logic.Visit
import com.rifat.trainercount.logic.VisitKind
import com.rifat.trainercount.logic.balance
import com.rifat.trainercount.logic.fDate
import com.rifat.trainercount.logic.fDateY
import com.rifat.trainercount.logic.fDow
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fMoney
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.newId
import com.rifat.trainercount.logic.pricePerSession
import com.rifat.trainercount.logic.reportText
import com.rifat.trainercount.logic.trainings

private sealed interface Dlg {
    data object AddVisit : Dlg
    data object Pay : Dlg
    data class EditVisit(val v: Visit) : Dlg
    data class DelPack(val p: Pack) : Dlg
}

@Composable
fun ClientScreen(data: AppData, id: String, nav: Nav) {
    val c = data.client(id)
    if (c == null) {
        LaunchedEffect(id) { nav.back() }
        return
    }
    val ctx = LocalContext.current
    var dlg by remember { mutableStateOf<Dlg?>(null) }
    val b = balance(data, id)
    val visits = data.visits.filter { it.clientId == id }.sortedByDescending { it.start }
    val packs = data.packs.filter { it.clientId == id }.sortedByDescending { it.paidAt }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(c.name) },
            navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
            actions = { IconButton(onClick = { nav.go(Screen.Edit(id)) }) { Icon(Icons.Default.Edit, "Изменить") } },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
        ) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        val (big, small) = when {
                            b.left < 0 -> "${-b.left}" to "сверх оплаты, нужно получить оплату"
                            b.current != null -> "${b.leftInCurrent}" to
                                "осталось из ${b.current.sessions} · пакет от ${fDate(b.current.paidAt)}" +
                                if (b.left > b.leftInCurrent) " (+${b.left - b.leftInCurrent} в следующем)" else ""
                            b.paid > 0 -> "0" to "пакет закончился"
                            else -> "—" to "оплат пока нет"
                        }
                        Text(
                            big, style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.SemiBold,
                            color = if (b.left <= 1) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                        )
                        Text(small, style = MaterialTheme.typography.bodyMedium)
                        if (c.members.size > 1) {
                            Text("Семья: ${c.members.joinToString(", ")}", style = MaterialTheme.typography.bodySmall)
                        }
                        Spacer(Modifier.height(12.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilledTonalButton(onClick = { dlg = Dlg.AddVisit }) { Text("+ Тренировка") }
                            FilledTonalButton(onClick = { dlg = Dlg.Pay }) { Text("Оплата") }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = {
                                val send = Intent(Intent.ACTION_SEND).setType("text/plain")
                                    .putExtra(Intent.EXTRA_TEXT, reportText(data, c))
                                ctx.startActivity(Intent.createChooser(send, "Отправить отчёт"))
                            }) { Text("Отчёт клиенту") }
                        }
                    }
                }
            }
            if (!c.hasPlace) item {
                Spacer(Modifier.height(10.dp))
                WarnCard("Адрес не задан: тренировки считаются только вручную.") { nav.go(Screen.Edit(id)) }
            }
            item { SectionTitle("История") }
            if (visits.isEmpty()) item { Text("Пока пусто", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(visits, key = { it.id }) { v ->
                ListItem(
                    headlineContent = { Text("${fDow(v.start)}, ${fDate(v.start)}" + if (v.kind == VisitKind.AUTO) "  ${fTime(v.start)}–${fTime(v.end)}" else "") },
                    supportingContent = {
                        val tags = buildList {
                            when (v.kind) {
                                VisitKind.AUTO -> add("авто, ${fDur((v.end - v.start) / 60_000)}")
                                VisitKind.MANUAL -> add("вручную")
                                VisitKind.BURNED -> add("отмена, списано")
                            }
                            if (v.members.isNotEmpty()) add(v.members.joinToString(", "))
                            if (v.check) add("проверить")
                        }
                        Text(
                            tags.joinToString(" · "),
                            color = if (v.check) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    },
                    trailingContent = { Text("×${v.count}", style = MaterialTheme.typography.titleMedium) },
                    modifier = Modifier.clickable { dlg = Dlg.EditVisit(v) },
                )
                HorizontalDivider()
            }
            item { SectionTitle("Оплаты") }
            if (packs.isEmpty()) item { Text("Оплат нет. Нажмите «Оплата», когда клиент заплатит.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(packs, key = { it.id }) { p ->
                ListItem(
                    headlineContent = { Text("${fDateY(p.paidAt)} · ${trainings(p.sessions)}") },
                    supportingContent = { if (p.amount > 0) Text(fMoney(p.amount)) },
                    trailingContent = { TextButton(onClick = { dlg = Dlg.DelPack(p) }) { Text("Удалить") } },
                )
            }
        }
    }

    when (val d = dlg) {
        Dlg.AddVisit -> AddVisitDialog(c) { dlg = null }
        Dlg.Pay -> PayDialog(data, c) { dlg = null }
        is Dlg.EditVisit -> EditVisitDialog(c, d.v) { dlg = null }
        is Dlg.DelPack -> AlertDialog(
            onDismissRequest = { dlg = null },
            title = { Text("Удалить оплату?") },
            text = { Text("${fDateY(d.p.paidAt)} · ${trainings(d.p.sessions)}") },
            confirmButton = {
                TextButton(onClick = {
                    Store.update { it.copy(packs = it.packs.filter { p -> p.id != d.p.id }) }
                    dlg = null
                }) { Text("Удалить") }
            },
            dismissButton = { TextButton(onClick = { dlg = null }) { Text("Отмена") } },
        )
        null -> {}
    }
}

@Composable
private fun MembersPicker(c: Client, selected: List<String>, onChange: (List<String>) -> Unit) {
    if (c.members.size < 2) return
    Text("Кто занимался", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 12.dp))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        c.members.forEach { m ->
            FilterChip(
                selected = m in selected,
                onClick = { onChange(if (m in selected) selected - m else selected + m) },
                label = { Text(m) },
            )
        }
    }
}

@Composable
private fun AddVisitDialog(c: Client, close: () -> Unit) {
    val ctx = LocalContext.current
    var date by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var count by remember { mutableIntStateOf(1) }
    var burned by remember { mutableStateOf(false) }
    var members by remember { mutableStateOf(listOf<String>()) }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("Добавить вручную") },
        text = {
            Column {
                OutlinedButton(onClick = { pickDate(ctx, date) { date = it } }) { Text("Дата: ${fDow(date)}, ${fDateY(date)}") }
                Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(selected = !burned, onClick = { burned = false }, label = { Text("Тренировка") })
                    FilterChip(selected = burned, onClick = { burned = true }, label = { Text("Отмена, списать") })
                }
                Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Сколько", modifier = Modifier.weight(1f))
                    Stepper(count, { count = it }, 1..MAX_PER_VISIT)
                }
                MembersPicker(c, members) { members = it }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val v = Visit(newId(), c.id, date, date, count, if (burned) VisitKind.BURNED else VisitKind.MANUAL, members)
                Store.update { it.copy(visits = it.visits + v) }
                close()
            }) { Text("Добавить") }
        },
        dismissButton = { TextButton(onClick = close) { Text("Отмена") } },
    )
}

@Composable
private fun EditVisitDialog(c: Client, v: Visit, close: () -> Unit) {
    var count by remember { mutableIntStateOf(v.count) }
    var members by remember { mutableStateOf(v.members) }
    var confirmDelete by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("${fDow(v.start)}, ${fDateY(v.start)}") },
        text = {
            Column {
                if (v.kind == VisitKind.AUTO) Text("${fTime(v.start)}–${fTime(v.end)} · ${fDur((v.end - v.start) / 60_000)}")
                if (v.check) Text(
                    "Выход от клиента не зафиксирован, поставлена 1 тренировка. Проверьте и сохраните.",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
                Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Тренировок", modifier = Modifier.weight(1f))
                    Stepper(count, { count = it }, 1..MAX_PER_VISIT)
                }
                MembersPicker(c, members) { members = it }
                TextButton(onClick = { confirmDelete = true }, modifier = Modifier.padding(top = 8.dp)) {
                    Text(if (confirmDelete) "Нажмите «Удалить» ниже" else "Удалить визит", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                Store.update { d ->
                    if (confirmDelete) d.copy(visits = d.visits.filter { it.id != v.id })
                    else d.copy(visits = d.visits.map { if (it.id == v.id) it.copy(count = count, members = members, check = false) else it })
                }
                close()
            }) { Text(if (confirmDelete) "Удалить" else "Сохранить") }
        },
        dismissButton = { TextButton(onClick = close) { Text("Отмена") } },
    )
}

@Composable
private fun PayDialog(data: AppData, c: Client, close: () -> Unit) {
    val ctx = LocalContext.current
    val last = data.packs.filter { it.clientId == c.id }.maxByOrNull { it.paidAt }
    var date by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var sessions by remember { mutableIntStateOf(last?.sessions ?: 10) }
    var amount by remember {
        mutableStateOf((last?.amount ?: (pricePerSession(data, c) * (last?.sessions ?: 10))).takeIf { it > 0 }?.toString() ?: "")
    }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("Новая оплата") },
        text = {
            Column {
                OutlinedButton(onClick = { pickDate(ctx, date) { date = it } }) { Text("Дата: ${fDateY(date)}") }
                Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Тренировок", modifier = Modifier.weight(1f))
                    Stepper(sessions, { sessions = it }, 1..100)
                }
                OutlinedTextField(
                    value = amount,
                    onValueChange = { amount = it.filter(Char::isDigit).take(9) },
                    label = { Text("Сумма, ₽ (необязательно)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.padding(top = 12.dp),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val p = Pack(newId(), c.id, date, sessions, amount.toIntOrNull() ?: 0)
                Store.update { it.copy(packs = it.packs + p) }
                close()
            }) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = close) { Text("Отмена") } },
    )
}
