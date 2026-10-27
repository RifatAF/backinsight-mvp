package com.rifat.trainercount.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.Notifier
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.distanceM
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.stayNo
import com.rifat.trainercount.logic.stayVisit

/** «Больше часа на одном месте: было занятие?» Отметить клиента, добавить нового или ответить «нет». */
@Composable
fun StayDialog(data: AppData, nav: Nav, close: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val s = data.stay ?: return close()
    // Сначала те, у кого адреса нет или он рядом: скорее всего это они.
    val clients = data.clients.filter { !it.archived && it.placeId == null }.sortedBy { c ->
        if (c.lat == null || c.lng == null) 0.0 else distanceM(s.lat, s.lng, c.lat, c.lng)
    }
    fun done(f: (AppData) -> AppData) {
        Store.update(f)
        Notifier.cancelId(ctx, Notifier.STAY_NID)
        close()
    }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("Было занятие?") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "Вы на одном месте с ${fTime(s.start)}, ${fDur((s.last - s.start) / 60_000)}. Здесь нет зоны клиента.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedButton(onClick = { close(); nav.go(Screen.Edit(null, fromStay = true)) }, modifier = Modifier.fillMaxWidth()) {
                    Text("Новый клиент здесь")
                }
                clients.forEach { c ->
                    OutlinedButton(onClick = { done { stayVisit(it, c.id) } }, modifier = Modifier.fillMaxWidth()) {
                        Text(c.name + if (c.lat == null) " · поставить адрес сюда" else "")
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { done { stayNo(it, ignore = false) } }) { Text("Не было") } },
        dismissButton = { TextButton(onClick = { done { stayNo(it, ignore = true) } }) { Text("Не спрашивать здесь") } },
    )
}
