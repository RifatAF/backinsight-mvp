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
import com.rifat.trainercount.logic.Visit
import com.rifat.trainercount.logic.balance
import com.rifat.trainercount.logic.fTime
import com.rifat.trainercount.logic.trainings
import com.rifat.trainercount.ui.MainActivity

object Notifier {
    private const val CH_VISITS = "visits"
    private const val CH_PAY = "pay"
    const val EXTRA_CLIENT = "clientId"

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_VISITS, "Засчитанные тренировки", NotificationManager.IMPORTANCE_LOW)
                .apply { description = "Тихие уведомления после выхода от клиента" }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_PAY, "Пора напомнить об оплате", NotificationManager.IMPORTANCE_DEFAULT)
        )
    }

    fun onEffect(ctx: Context, e: Effect) {
        when (e) {
            is Effect.VisitAdded -> show(ctx, e.visit)
        }
    }

    private fun nid(v: Visit) = v.id.hashCode()

    @SuppressLint("MissingPermission")
    fun show(ctx: Context, v: Visit) {
        if (!Perms.notifications(ctx)) return
        val d = Store.data.value
        val c = d.client(v.clientId) ?: return
        val b = balance(d, c.id)
        val left = when {
            b.current != null && b.left > 1 -> "Осталось ${b.leftInCurrent} из ${b.current.sessions}"
            b.left == 1 -> "Осталась 1, пора напомнить об оплате"
            b.left == 0 && b.paid > 0 -> "Пакет закончился, пора напомнить об оплате"
            b.left < 0 -> "Сверх оплаты: ${trainings(-b.left)}"
            else -> "Оплат пока нет"
        }
        val text = buildString {
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
        if (v.count < MAX_PER_VISIT) n.addAction(0, "+1", action(ctx, ActionReceiver.PLUS, v))
        NotificationManagerCompat.from(ctx).notify(nid(v), n.build())
    }

    fun cancel(ctx: Context, v: Visit) = NotificationManagerCompat.from(ctx).cancel(nid(v))

    private fun action(ctx: Context, act: String, v: Visit) = PendingIntent.getBroadcast(
        ctx, (act + v.id).hashCode(),
        Intent(ctx, ActionReceiver::class.java).setAction(act).putExtra("visitId", v.id),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}

class ActionReceiver : BroadcastReceiver() {
    companion object {
        const val UNDO = "com.rifat.trainercount.UNDO"
        const val PLUS = "com.rifat.trainercount.PLUS"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        Store.init(ctx)
        val id = intent.getStringExtra("visitId") ?: return
        val v = Store.data.value.visits.firstOrNull { it.id == id } ?: return
        when (intent.action) {
            UNDO -> {
                Store.update { d -> d.copy(visits = d.visits.filter { it.id != id }) }
                Notifier.cancel(ctx, v)
            }
            PLUS -> {
                val nv = v.copy(count = (v.count + 1).coerceAtMost(MAX_PER_VISIT))
                Store.update { d -> d.copy(visits = d.visits.map { if (it.id == id) nv else it }) }
                Notifier.show(ctx, nv)
            }
        }
    }
}
