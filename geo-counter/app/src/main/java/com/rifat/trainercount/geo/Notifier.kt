package com.rifat.trainercount.geo

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.rifat.trainercount.R
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.Effect
import com.rifat.trainercount.logic.MAX_PER_VISIT
import com.rifat.trainercount.logic.Pending
import com.rifat.trainercount.logic.Place
import com.rifat.trainercount.logic.dismissPending
import com.rifat.trainercount.logic.perPerson
import com.rifat.trainercount.logic.withMembers
import com.rifat.trainercount.logic.Visit
import com.rifat.trainercount.logic.balance
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.trainings
import com.rifat.trainercount.ui.MainActivity

object Notifier {
    private const val CH_VISITS = "visits"
    private const val CH_PAY = "pay"
    private const val CH_ASK = "ask"
    const val EXTRA_CLIENT = "clientId"
    const val EXTRA_PENDING = "pendingId"

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_VISITS, "Засчитанные тренировки", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Тихие уведомления после выхода от клиента" }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_PAY, "Пора напомнить об оплате", NotificationManager.IMPORTANCE_DEFAULT)
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_ASK, "С кем было занятие", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "Общее место без расписания: нужно отметить клиентов" }
        )
    }

    fun onEffect(ctx: Context, e: Effect) {
        when (e) {
            is Effect.VisitAdded -> show(ctx, e.visit)
            is Effect.PlaceAuto -> showAuto(ctx, e.place, e.visits)
            is Effect.PendingAdded -> showPending(ctx, e.pending)
        }
    }

    private fun openApp(ctx: Context, req: Int, extra: Pair<String, String>?) = PendingIntent.getActivity(
        ctx, req,
        Intent(ctx, MainActivity::class.java).apply { extra?.let { putExtra(it.first, it.second) } }
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    @SuppressLint("MissingPermission")
    private fun showAuto(ctx: Context, place: Place, visits: List<Visit>) {
        if (!Perms.notifications(ctx) || visits.isEmpty()) return
        val d = Store.data.value
        val names = visits.joinToString(", ") { v -> "${d.client(v.clientId)?.name ?: "?"} ${fTime(v.start)}" }
        val id = visits.first().id.hashCode()
        val n = NotificationCompat.Builder(ctx, CH_VISITS)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("${place.name}: +${trainings(visits.size)}")
            .setContentText("По расписанию: $names")
            .setStyle(NotificationCompat.BigTextStyle().bigText("По расписанию: $names"))
            .setContentIntent(openApp(ctx, id, null))
            .setAutoCancel(true)
            .addAction(
                0, "Отменить",
                PendingIntent.getBroadcast(
                    ctx, ("u" + id).hashCode(),
                    Intent(ctx, ActionReceiver::class.java).setAction(ActionReceiver.UNDO)
                        .putExtra("visitIds", visits.joinToString(",") { it.id }).putExtra("notifId", id),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                ),
            )
        NotificationManagerCompat.from(ctx).notify(id, n.build())
    }

    fun pendingNid(p: Pending) = p.id.hashCode()

    @SuppressLint("MissingPermission")
    private fun showPending(ctx: Context, p: Pending) {
        if (!Perms.notifications(ctx)) return
        val place = Store.data.value.place(p.placeId) ?: return
        val id = pendingNid(p)
        val n = NotificationCompat.Builder(ctx, CH_ASK)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("${place.name}: ${trainings(p.count)}. С кем?")
            .setContentText("${fTime(p.start)}–${fTime(p.end)} · нажмите, чтобы отметить")
            .setContentIntent(openApp(ctx, id, EXTRA_PENDING to p.id))
            .setAutoCancel(true)
            .addAction(
                0, "Занятий не было",
                PendingIntent.getBroadcast(
                    ctx, ("d" + p.id).hashCode(),
                    Intent(ctx, ActionReceiver::class.java).setAction(ActionReceiver.DISMISS)
                        .putExtra("pendingId", p.id).putExtra("notifId", id),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                ),
            )
        NotificationManagerCompat.from(ctx).notify(id, n.build())
    }

    fun cancelId(ctx: Context, id: Int) = NotificationManagerCompat.from(ctx).cancel(id)

    private fun nid(v: Visit) = v.id.hashCode()

    @SuppressLint("MissingPermission")
    fun show(ctx: Context, v: Visit) {
        if (!Perms.notifications(ctx)) return
        val d = Store.data.value
        val c = d.client(v.clientId) ?: return
        val b = balance(d, c.id)
        val left = when {
            b.current != null && b.left > 1 -> "Осталось ${b.left} из ${b.activeTotal}"
            b.left == 1 -> "Осталась 1, пора напомнить об оплате"
            b.left == 0 && b.paid > 0 -> "Пакет закончился, пора напомнить об оплате"
            b.left < 0 -> "Сверх оплаты: ${trainings(-b.left)}"
            else -> "Оплат пока нет"
        }
        val text = buildString {
            if (c.isFamily && v.members.isNotEmpty()) append("${v.members.joinToString(", ")}\n")
            append("${fTime(v.start)}–${fTime(v.end)} · $left")
            if (v.check) append("\nВыход не зафиксирован, проверьте время")
        }
        val open = PendingIntent.getActivity(
            ctx, nid(v),
            Intent(ctx, MainActivity::class.java).putExtra(EXTRA_CLIENT, c.id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(ctx, if (b.left <= 1) CH_PAY else CH_VISITS)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("${c.name}: +${trainings(v.count)}")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .addAction(0, "Отменить", action(ctx, ActionReceiver.UNDO, v))
        when {
            // Семья: одной кнопкой «был только один».
            c.countsEach && v.members.size == 2 -> v.members.forEach { m ->
                n.addAction(0, "Только $m", action(ctx, ActionReceiver.ONLY, v, m))
            }
            c.countsEach -> {}
            v.count < MAX_PER_VISIT -> n.addAction(0, "+1", action(ctx, ActionReceiver.PLUS, v))
        }
        NotificationManagerCompat.from(ctx).notify(nid(v), n.build())
    }

    fun cancel(ctx: Context, v: Visit) = NotificationManagerCompat.from(ctx).cancel(nid(v))

    private fun action(ctx: Context, act: String, v: Visit, member: String? = null) = PendingIntent.getBroadcast(
        ctx, (act + v.id + (member ?: "")).hashCode(),
        Intent(ctx, ActionReceiver::class.java).setAction(act).putExtra("visitId", v.id).putExtra("member", member),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}

class ActionReceiver : BroadcastReceiver() {
    companion object {
        const val UNDO = "com.rifat.trainercount.UNDO"
        const val PLUS = "com.rifat.trainercount.PLUS"
        const val DISMISS = "com.rifat.trainercount.DISMISS"
        const val ONLY = "com.rifat.trainercount.ONLY"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        Store.init(ctx)
        if (intent.action == DISMISS) {
            val pid = intent.getStringExtra("pendingId") ?: return
            Store.update { dismissPending(it, pid) }
            Notifier.cancelId(ctx, intent.getIntExtra("notifId", 0))
            return
        }
        intent.getStringExtra("visitIds")?.let { csv ->
            val ids = csv.split(',').toSet()
            Store.update { d -> d.copy(visits = d.visits.filter { it.id !in ids }) }
            Notifier.cancelId(ctx, intent.getIntExtra("notifId", 0))
            return
        }
        val id = intent.getStringExtra("visitId") ?: return
        val v = Store.data.value.visits.firstOrNull { it.id == id } ?: return
        when (intent.action) {
            UNDO -> {
                Store.update { d -> d.copy(visits = d.visits.filter { it.id != id }) }
                Notifier.cancel(ctx, v)
            }
            ONLY -> {
                val c = Store.data.value.client(v.clientId) ?: return
                val m = intent.getStringExtra("member") ?: return
                val nv = withMembers(v, c, listOf(m), perPerson(v, c))
                Store.update { d -> d.copy(visits = d.visits.map { if (it.id == id) nv else it }) }
                Notifier.show(ctx, nv)
            }
            PLUS -> {
                val nv = v.copy(count = (v.count + 1).coerceAtMost(MAX_PER_VISIT))
                Store.update { d -> d.copy(visits = d.visits.map { if (it.id == id) nv else it }) }
                Notifier.show(ctx, nv)
            }
        }
    }
}
