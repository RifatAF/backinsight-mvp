@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.rifat.trainercount.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.Planner
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Occ
import com.rifat.trainercount.logic.PlanItem
import com.rifat.trainercount.logic.Rule
import com.rifat.trainercount.logic.Status
import com.rifat.trainercount.logic.addExtra
import com.rifat.trainercount.logic.addRule
import com.rifat.trainercount.logic.cancelOnce
import com.rifat.trainercount.logic.dayPlan
import com.rifat.trainercount.logic.editRule
import com.rifat.trainercount.logic.epochMs
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.freeSlots
import com.rifat.trainercount.logic.hhmm
import com.rifat.trainercount.logic.minuteOf
import com.rifat.trainercount.logic.lessonMin
import com.rifat.trainercount.logic.moveOnce
import com.rifat.trainercount.logic.occurrences
import com.rifat.trainercount.logic.parityOf
import com.rifat.trainercount.logic.readyInDays
import com.rifat.trainercount.logic.removeRule
import com.rifat.trainercount.logic.roundSlot
import com.rifat.trainercount.logic.trainings
import java.time.LocalDate
import java.time.format.TextStyle
import java.util.Locale

private val RU = Locale("ru")
private val PALETTE = listOf(
    Color(0xFF3B7BE8), Color(0xFFE8765A), Color(0xFF2E9E6A), Color(0xFF9B5DE5),
    Color(0xFFEE7B30), Color(0xFF00A6A6), Color(0xFFD6457A), Color(0xFF8A7F3A),
)
private val WINDOW = Color(0xFF2E9E6A)

fun clientColor(id: String) = PALETTE[Math.floorMod(id.hashCode(), PALETTE.size)]

fun dayTitle(date: LocalDate): String =
    date.dayOfWeek.getDisplayName(TextStyle.FULL, RU).replaceFirstChar { it.uppercase() } +
        ", ${date.dayOfMonth} " + date.month.getDisplayName(TextStyle.FULL, RU).let { genitive(it) }

private fun genitive(m: String) = when {
    m.endsWith("ь") || m.endsWith("й") -> m.dropLast(1) + "я"
    m == "март" || m == "август" -> m + "а"
    else -> m
}

private fun shortDow(date: LocalDate) = DAY_NAMES[date.dayOfWeek.value - 1]

private fun ruleText(r: Rule): String {
    val day = date0(r.day)
    return if (r.parity == null) "постоянно, каждый $day" else "через неделю, $day"
}

private fun date0(day: Int) = listOf("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")[day - 1]

private sealed interface SDlg {
    data class Lesson(val occ: Occ, val status: Status) : SDlg
    data class Move(val occ: Occ) : SDlg
    data class Cancel(val occ: Occ) : SDlg
    data class EditRule(val rule: Rule) : SDlg
    data class Add(val min: Int?) : SDlg
    /** Личное дело: ev = null для нового. */
    data class Event(val ev: com.rifat.trainercount.logic.Personal?, val min: Int?) : SDlg
}

@Composable
fun ScheduleScreen(data: AppData, nav: Nav) {
    val now = rememberNow()
    val today = LocalDate.now()
    var selected by remember { mutableStateOf(nav.scheduleDate ?: today) }
    androidx.compose.runtime.LaunchedEffect(Unit) { nav.scheduleDate = null }
    var dlg by remember { mutableStateOf<SDlg?>(null) }
    val weekStart = selected.minusDays((selected.dayOfWeek.value - 1).toLong())
    val plan = dayPlan(data, selected, now)
    val ready = readyInDays(data, now)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Расписание") },
                navigationIcon = { IconButton(onClick = nav::back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") } },
                actions = { TextButton(onClick = { nav.go(Screen.FindWindow) }) { Text("Найти окно") } },
            )
        },
        floatingActionButton = {
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                androidx.compose.material3.SmallFloatingActionButton(onClick = { dlg = SDlg.Event(null, null) }) {
                    Text("+ Дело", modifier = Modifier.padding(horizontal = 12.dp))
                }
                ExtendedFloatingActionButton(
                    onClick = { dlg = SDlg.Add(null) },
                    icon = { Icon(Icons.Default.Add, null) },
                    text = { Text("Занятие") },
                )
            }
        },
    ) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 96.dp),
        ) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                    IconButton(onClick = { selected = selected.minusWeeks(1) }) {
                        Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Прошлая неделя")
                    }
                    val end = weekStart.plusDays(6)
                    Text(
                        "${weekStart.dayOfMonth} ${genitive(weekStart.month.getDisplayName(TextStyle.FULL, RU))} – " +
                            "${end.dayOfMonth} ${genitive(end.month.getDisplayName(TextStyle.FULL, RU))}",
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = { selected = selected.plusWeeks(1) }) {
                        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Следующая неделя")
                    }
                }
                WeekStrip(data, weekStart, selected, today) { selected = it }
                if (selected != today) {
                    TextButton(onClick = { selected = today }) { Text("К сегодняшнему дню") }
                }
            }
            if (ready > 0) item {
                Card(Modifier.fillMaxWidth().padding(top = 8.dp)) {
                    Text(
                        "Расписание собирается само по вашим визитам: время округляется до получаса. " +
                            "Вечерние напоминания и будильник включатся через $ready дн. " +
                            "Постоянные занятия можно добавить и вручную: «+ Занятие».",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(12.dp),
                    )
                }
            }
            item {
                val lessons = plan.filterIsInstance<PlanItem.Lesson>()
                val wins = plan.filterIsInstance<PlanItem.Window>()
                Column(Modifier.padding(top = 12.dp, bottom = 8.dp)) {
                    Text(dayTitle(selected), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text(
                        when {
                            selected.dayOfWeek.value !in data.settings.days -> "Выходной по настройкам"
                            lessons.isEmpty() -> "Занятий нет"
                            else -> trainings(lessons.size) +
                                if (wins.isNotEmpty()) " · свободно ${fDur(wins.sumOf { it.to - it.from }.toLong())}" else ""
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            items(plan, key = { it.javaClass.simpleName + it.from + ((it as? PlanItem.Lesson)?.occ?.clientId ?: (it as? PlanItem.Unplanned)?.visit?.id ?: "") }) { item ->
                TimelineRow(data, item) {
                    dlg = when (item) {
                        is PlanItem.Lesson -> SDlg.Lesson(item.occ, item.status)
                        is PlanItem.Window -> SDlg.Add(item.from)
                        is PlanItem.Unplanned -> SDlg.Add(item.from)
                        is PlanItem.Event -> SDlg.Event(item.ev, null)
                    }
                }
            }
        }
    }

    when (val d = dlg) {
        is SDlg.Lesson -> LessonDialog(data, d.occ, d.status, selected, nav, { dlg = it }) { dlg = null }
        is SDlg.Move -> MoveDialog(data, d.occ, selected, now) { dlg = null }
        is SDlg.Cancel -> CancelDialog(data, d.occ, selected) { dlg = null }
        is SDlg.EditRule -> RuleDialog(data, d.rule, null, selected) { dlg = null }
        is SDlg.Add -> RuleDialog(data, null, d.min, selected) { dlg = null }
        is SDlg.Event -> EventDialog(d.ev, d.min, selected) { dlg = null }
        null -> {}
    }
}

@Composable
private fun WeekStrip(data: AppData, weekStart: LocalDate, selected: LocalDate, today: LocalDate, onPick: (LocalDate) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
        for (i in 0..6) {
            val date = weekStart.plusDays(i.toLong())
            val n = occurrences(data, date).size
            val sel = date == selected
            val off = date.dayOfWeek.value !in data.settings.days
            val bg = if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
            val fg = if (sel) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.weight(1f).clip(RoundedCornerShape(12.dp)).background(bg)
                    .then(if (date == today && !sel) Modifier.border(2.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(12.dp)) else Modifier)
                    .clickable { onPick(date) }.padding(vertical = 8.dp),
            ) {
                Text(shortDow(date), style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = if (off) 0.5f else 1f))
                Text("${date.dayOfMonth}", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = fg.copy(alpha = if (off) 0.5f else 1f))
                Text(if (n > 0) "$n" else "·", style = MaterialTheme.typography.labelSmall, color = fg)
            }
        }
    }
}

@Composable
private fun TimelineRow(data: AppData, item: PlanItem, onClick: () -> Unit) {
    val dur = item.to - item.from
    val h = (dur * 1.1f).dp.coerceAtLeast(56.dp).coerceAtMost(220.dp)
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(
            hhmm(item.from),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(48.dp).padding(top = 6.dp),
        )
        when (item) {
            is PlanItem.Window -> Box(
                Modifier.weight(1f).height(h).clip(RoundedCornerShape(12.dp))
                    .background(WINDOW.copy(alpha = 0.10f))
                    .border(BorderStroke(1.dp, WINDOW.copy(alpha = 0.6f)), RoundedCornerShape(12.dp))
                    .clickable(onClick = onClick).padding(12.dp),
            ) {
                Column {
                    Text("Окно ${hhmm(item.from)}–${hhmm(item.to)}", color = WINDOW, fontWeight = FontWeight.SemiBold)
                    Text(
                        "свободно ${fDur(dur.toLong())}, дорога ${data.settings.travelMin} мин учтена · нажмите, чтобы поставить занятие",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            is PlanItem.Lesson -> {
                val c = data.client(item.occ.clientId)
                LessonBlock(
                    color = clientColor(item.occ.clientId),
                    title = c?.name ?: "?",
                    time = "${hhmm(item.from)}–${hhmm(item.to)}",
                    tags = buildList {
                        when (item.status) {
                            Status.DONE -> add("✓ было" + (item.visit?.let { v -> if (v.kind == com.rifat.trainercount.logic.VisitKind.AUTO) ", пришли ${fTime(v.start)}" else "" } ?: ""))
                            Status.MISSED -> add("✗ не было")
                            Status.PLANNED -> {}
                        }
                        item.occ.movedFrom?.let { (d, m) -> add("перенос с ${shortDow(d)} ${d.dayOfMonth}.${"%02d".format(d.monthValue)} ${hhmm(m)}") }
                        if (item.occ.extra) add("разово")
                        if (item.occ.rule?.parity != null) add("через неделю")
                    },
                    faded = item.status == Status.MISSED,
                    height = h,
                    onClick = onClick,
                )
            }
            is PlanItem.Event -> LessonBlock(
                color = Color(0xFF8E8E93),
                title = item.ev.title,
                time = "${hhmm(item.from)}–${hhmm(item.to)}",
                tags = listOf("личное") + when (item.ev.went) { true -> listOf("был ✓"); false -> listOf("не был"); null -> emptyList() },
                faded = item.ev.went == false,
                height = h,
                onClick = onClick,
            )
            is PlanItem.Unplanned -> {
                val c = data.client(item.visit.clientId)
                LessonBlock(
                    color = Color.Gray,
                    title = c?.name ?: "?",
                    time = "${hhmm(item.from)}–${hhmm(item.to)}",
                    tags = listOf("✓ было, вне расписания"),
                    faded = false,
                    height = h,
                    onClick = onClick,
                )
            }
        }
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.LessonBlock(
    color: Color, title: String, time: String, tags: List<String>, faded: Boolean, height: androidx.compose.ui.unit.Dp, onClick: () -> Unit,
) {
    Row(
        Modifier.weight(1f).height(height).clip(RoundedCornerShape(12.dp))
            .background(color.copy(alpha = if (faded) 0.08f else 0.18f))
            .clickable(onClick = onClick),
    ) {
        Box(Modifier.width(5.dp).fillMaxHeight().background(color.copy(alpha = if (faded) 0.4f else 1f)))
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(time, style = MaterialTheme.typography.bodySmall)
            if (tags.isNotEmpty()) Text(
                tags.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

// ---------- Диалоги ----------

@Composable
private fun LessonDialog(
    data: AppData, occ: Occ, status: Status, date: LocalDate, nav: Nav, open: (SDlg) -> Unit, close: () -> Unit,
) {
    val ctx = LocalContext.current
    val c = data.client(occ.clientId)
    AlertDialog(
        onDismissRequest = close,
        title = { Text(c?.name ?: "Занятие") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("${dayTitle(date)}, ${hhmm(occ.min)}–${hhmm(occ.min + occ.duration)}")
                Text(
                    when {
                        occ.extra -> "Разовое занятие"
                        occ.movedFrom != null -> "Перенесено с ${dayTitle(occ.movedFrom.first)}, ${hhmm(occ.movedFrom.second)}"
                        occ.rule != null -> ruleText(occ.rule).replaceFirstChar { it.uppercase() } + if (occ.rule.manual) "" else " (выучено по визитам)"
                        else -> ""
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (status == Status.DONE) Text("✓ Занятие засчитано", color = MaterialTheme.colorScheme.primary)
                FilledTonalButton(onClick = { open(SDlg.Move(occ)) }, modifier = Modifier.fillMaxWidth()) { Text("Перенести") }
                OutlinedButton(onClick = { open(SDlg.Cancel(occ)) }, modifier = Modifier.fillMaxWidth()) { Text("Отменить этот раз") }
                if (occ.rule != null) {
                    OutlinedButton(onClick = { open(SDlg.EditRule(occ.rule)) }, modifier = Modifier.fillMaxWidth()) {
                        Text("Изменить постоянное время")
                    }
                    TextButton(onClick = {
                        Store.update { removeRule(it, occ.rule) }
                        Planner.reschedule(ctx)
                        close()
                    }) { Text("Убрать из расписания", color = MaterialTheme.colorScheme.error) }
                }
                if (c != null) TextButton(onClick = { close(); nav.go(Screen.ClientView(c.id)) }) { Text("Карточка клиента") }
            }
        },
        confirmButton = { TextButton(onClick = close) { Text("Закрыть") } },
    )
}

@Composable
private fun MoveDialog(data: AppData, occ: Occ, date: LocalDate, now: Long, close: () -> Unit) {
    val ctx = LocalContext.current
    val c = data.client(occ.clientId)
    val from = maxOf(date.minusDays(2), LocalDate.now())
    val slots = remember(occ, date) { freeSlots(data, from, 8, occ.duration, now).take(14) }
    var manualDate by remember { mutableStateOf(date) }

    fun moveTo(toDate: LocalDate, toMin: Int) {
        Store.update { moveOnce(it, occ, date, toDate, toMin) }
        Planner.reschedule(ctx)
        close()
    }

    AlertDialog(
        onDismissRequest = close,
        title = { Text("Перенести: ${c?.name}") },
        text = {
            Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState())) {
                Text("Свободные окна:", style = MaterialTheme.typography.labelLarge)
                if (slots.isEmpty()) Text("Свободных окон в ближайшие дни нет", style = MaterialTheme.typography.bodySmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    slots.forEach { (d, m) ->
                        FilterChip(selected = false, onClick = { moveTo(d, m) }, label = { Text("${shortDow(d)} ${d.dayOfMonth} · ${hhmm(m)}") })
                    }
                }
                Spacer(Modifier.height(12.dp))
                Text("Или выбрать самому:", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = {
                        pickDate(ctx, epochNoon(manualDate)) { t -> manualDate = com.rifat.trainercount.logic.dayOf(t) }
                    }) { Text("${shortDow(manualDate)} ${manualDate.dayOfMonth}.${"%02d".format(manualDate.monthValue)}") }
                    OutlinedButton(onClick = {
                        pickTime(ctx, occ.min) { m -> moveTo(manualDate, roundSlot(m)) }
                    }) { Text("Время…") }
                }
                Text(
                    "Время округляется до получаса.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = { TextButton(onClick = close) { Text("Отмена") } },
    )
}

private fun epochNoon(d: LocalDate) = com.rifat.trainercount.logic.epochMs(d, 12 * 60)

@Composable
private fun CancelDialog(data: AppData, occ: Occ, date: LocalDate, close: () -> Unit) {
    val ctx = LocalContext.current
    var burn by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = close,
        title = { Text("Отменить занятие?") },
        text = {
            Column {
                Text("${data.client(occ.clientId)?.name}, ${dayTitle(date)}, ${hhmm(occ.min)}. Время станет свободным окном.")
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Checkbox(checked = burn, onCheckedChange = { burn = it })
                    Text("Поздняя отмена: списать занятие")
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                Store.update { cancelOnce(it, occ, date, burn) }
                Planner.reschedule(ctx)
                close()
            }) { Text("Отменить занятие") }
        },
        dismissButton = { TextButton(onClick = close) { Text("Назад") } },
    )
}

/** Добавить занятие (разово или постоянно) или изменить постоянное. */
@Composable
private fun RuleDialog(data: AppData, rule: Rule?, startMin: Int?, date: LocalDate, close: () -> Unit) {
    val ctx = LocalContext.current
    val clients = data.clients.filter { !it.archived }.sortedBy { it.name.lowercase() }
    var clientId by remember { mutableStateOf(rule?.clientId) }
    var day by remember { mutableIntStateOf(rule?.day ?: date.dayOfWeek.value) }
    var min by remember { mutableIntStateOf(rule?.min ?: startMin ?: 10 * 60) }
    // 0: разово, 1: каждую неделю, 2: через неделю
    var mode by remember { mutableIntStateOf(if (rule == null) 0 else if (rule.parity == null) 1 else 2) }

    AlertDialog(
        onDismissRequest = close,
        title = { Text(if (rule == null) "Новое занятие" else "Постоянное время") },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState())) {
                if (rule == null) {
                    Text("Клиент", style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        clients.forEach { c ->
                            FilterChip(
                                selected = clientId == c.id, onClick = { clientId = c.id },
                                label = { Text(c.name) },
                                leadingIcon = { Box(Modifier.size(10.dp).clip(CircleShape).background(clientColor(c.id))) },
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Text("Как часто", style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        FilterChip(selected = mode == 0, onClick = { mode = 0 }, label = { Text("Разово") })
                        FilterChip(selected = mode == 1, onClick = { mode = 1 }, label = { Text("Каждую неделю") })
                        FilterChip(selected = mode == 2, onClick = { mode = 2 }, label = { Text("Через неделю") })
                    }
                } else {
                    Text(data.client(rule.clientId)?.name ?: "", fontWeight = FontWeight.SemiBold)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        FilterChip(selected = mode == 1, onClick = { mode = 1 }, label = { Text("Каждую неделю") })
                        FilterChip(selected = mode == 2, onClick = { mode = 2 }, label = { Text("Через неделю") })
                    }
                }
                if (mode != 0) {
                    Spacer(Modifier.height(8.dp))
                    Text("День", style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        DAY_NAMES.forEachIndexed { i, n -> FilterChip(selected = day == i + 1, onClick = { day = i + 1 }, label = { Text(n) }) }
                    }
                } else {
                    Text("${dayTitle(date)}", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp))
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { pickTime(ctx, min) { min = roundSlot(it) } }) { Text("Время: ${hhmm(min)}") }
                if (mode == 2) Text(
                    "Через неделю, начиная с недели ${date.dayOfMonth}.${"%02d".format(date.monthValue)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                val dur = clientId?.let { id -> data.client(id)?.let { lessonMin(data, it) } }
                if (dur != null) Text("Длительность ${fDur(dur.toLong())}", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(enabled = clientId != null, onClick = {
                val id = clientId ?: return@TextButton
                val parity = if (mode == 2) parityOf(date) else null
                Store.update {
                    when {
                        rule != null -> editRule(it, rule, day, min, parity)
                        mode == 0 -> addExtra(it, id, date, min)
                        else -> addRule(it, id, day, min, parity)
                    }
                }
                Planner.reschedule(ctx)
                close()
            }) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = close) { Text("Отмена") } },
    )
}

/** Карточка на главном экране: ближайшие занятия сегодня или завтра. */
@Composable
fun NextLessonsCard(data: AppData, now: Long, onClick: () -> Unit) {
    val today = LocalDate.now()
    val nowMin = minuteOf(now)
    val todayPlan = dayPlan(data, today, now)
    val todayLeft = todayPlan.filter { it is PlanItem.Lesson && it.status == Status.PLANNED }
    // Тот же план, что в расписании: занятия «с–до» и окна с учётом дороги.
    val (label, list) = if (todayLeft.isNotEmpty()) "Сегодня" to todayPlan.filter {
        (it is PlanItem.Lesson && it.status == Status.PLANNED) || ((it is PlanItem.Window || it is PlanItem.Event) && it.to > nowMin)
    } else "Завтра" to dayPlan(data, today.plusDays(1), now).filter { it !is PlanItem.Unplanned }
    val shown = list.dropWhile { it is PlanItem.Window }.dropLastWhile { it is PlanItem.Window }
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text("Расписание · $label", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
            if (shown.isEmpty()) {
                Text("Занятий нет", style = MaterialTheme.typography.bodyMedium)
            } else shown.take(8).forEach { item ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                    val time = "${hhmm(item.from)}–${hhmm(item.to)}"
                    if (item is PlanItem.Lesson) {
                        Box(Modifier.size(10.dp).clip(CircleShape).background(clientColor(item.occ.clientId)))
                        Text("  $time  ${data.client(item.occ.clientId)?.name}", style = MaterialTheme.typography.bodyMedium)
                    } else if (item is PlanItem.Event) {
                        Box(Modifier.size(10.dp).clip(CircleShape).background(Color(0xFF8E8E93)))
                        Text("  $time  ${item.ev.title}", style = MaterialTheme.typography.bodyMedium)
                    } else {
                        Box(Modifier.size(10.dp).clip(CircleShape).border(1.dp, WINDOW, CircleShape))
                        Text("  $time  окно", style = MaterialTheme.typography.bodyMedium, color = WINDOW)
                    }
                }
            }
            if (shown.size > 8) Text("ещё ${shown.size - 8}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** Личное дело в расписании: стоматолог, встреча. Его же можно добавить, написав боту в Telegram. */
@Composable
private fun EventDialog(ev: com.rifat.trainercount.logic.Personal?, min: Int?, date: LocalDate, close: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var title by remember { mutableStateOf(ev?.title ?: "") }
    var start by remember { mutableIntStateOf(ev?.min ?: min ?: 12 * 60) }
    var dur by remember { mutableIntStateOf(ev?.duration ?: 60) }
    var day by remember { mutableStateOf(ev?.let { LocalDate.ofEpochDay(it.date) } ?: date) }
    AlertDialog(
        onDismissRequest = close,
        title = { Text(if (ev == null) "Личное дело" else "Изменить дело") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                androidx.compose.material3.OutlinedTextField(
                    value = title, onValueChange = { title = it }, label = { Text("Что: стоматолог, встреча…") },
                    singleLine = true, modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = {
                        pickDate(ctx, epochMs(day, 12 * 60)) { t -> day = com.rifat.trainercount.logic.dayOf(t) }
                    }) { Text(dayTitle(day)) }
                    OutlinedButton(onClick = { pickTime(ctx, start) { start = it } }) { Text(hhmm(start)) }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Длится", modifier = Modifier.weight(1f))
                    Stepper(dur, { dur = it }, 15..600, 15) { fDur(it.toLong()) }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = title.isNotBlank(), onClick = {
                val ne = com.rifat.trainercount.logic.Personal(ev?.id ?: com.rifat.trainercount.logic.newId(), day.toEpochDay(), start, dur, title.trim())
                Store.update { d -> d.copy(events = d.events.filter { it.id != ne.id } + ne) }
                com.rifat.trainercount.geo.Planner.refresh(ctx)
                close()
            }) { Text("Сохранить") }
        },
        dismissButton = {
            if (ev != null) TextButton(onClick = {
                Store.update { d -> d.copy(events = d.events.filter { it.id != ev.id }) }
                com.rifat.trainercount.geo.Planner.refresh(ctx)
                close()
            }) { Text("Удалить") } else TextButton(onClick = close) { Text("Отмена") }
        },
    )
}
