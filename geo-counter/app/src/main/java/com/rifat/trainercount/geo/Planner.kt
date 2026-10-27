package com.rifat.trainercount.geo

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.rifat.trainercount.R
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.hhmm
import com.rifat.trainercount.logic.learnRules
import com.rifat.trainercount.logic.nextAlarm
import com.rifat.trainercount.logic.nextEvening
import com.rifat.trainercount.logic.readyInDays
import com.rifat.trainercount.logic.tomorrowText
import com.rifat.trainercount.ui.MainActivity

/** Вечерняя сводка на завтра и утренний будильник перед первой тренировкой. */
object Planner {
    const val CH_EVENING = "evening"
    const val CH_ALARM = "alarm"
    const val EXTRA_SCHEDULE = "openSchedule"
    private const val NID_EVENING = 7001
    const val NID_ALARM = 7002

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_EVENING, "Расписание на завтра", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "Вечерняя сводка: кто и во сколько завтра, свободные окна" }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_ALARM, "Будильник перед тренировкой", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Звонит за 45 минут до первой утренней тренировки"
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
                )
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 800, 600, 800, 600, 800)
                setBypassDnd(true)
            }
        )
    }

    private fun pi(ctx: Context, action: String, req: Int) = PendingIntent.getBroadcast(
        ctx, req, Intent(ctx, ScheduleReceiver::class.java).setAction(action),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    fun openSchedule(ctx: Context, req: Int) = PendingIntent.getActivity(
        ctx, req,
        Intent(ctx, MainActivity::class.java).putExtra(EXTRA_SCHEDULE, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    /** Пересобрать расписание по визитам и переставить напоминания. */
    fun refresh(ctx: Context) {
        Store.init(ctx)
        val now = System.currentTimeMillis()
        Store.update { learnRules(it, now) }
        reschedule(ctx)
    }

    fun canExact(ctx: Context): Boolean {
        val am = ctx.getSystemService(AlarmManager::class.java)
        return Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
    }

    @SuppressLint("MissingPermission")
    fun reschedule(ctx: Context) {
        val d = Store.data.value
        val s = d.settings
        val now = System.currentTimeMillis()
        val am = ctx.getSystemService(AlarmManager::class.java)

        val evening = pi(ctx, ScheduleReceiver.EVENING, 11)
        am.cancel(evening)
        if (s.eveningOn) am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, nextEvening(s, now), evening)

        val alarm = pi(ctx, ScheduleReceiver.ALARM, 12)
        am.cancel(alarm)
        val next = nextAlarm(d, now) ?: return
        if (canExact(ctx)) {
            am.setAlarmClock(AlarmManager.AlarmClockInfo(next.first, openSchedule(ctx, 13)), alarm)
        } else {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next.first, alarm)
        }
    }

    @SuppressLint("MissingPermission")
    fun showEvening(ctx: Context) {
        val d = Store.data.value
        val now = System.currentTimeMillis()
        if (!Perms.notifications(ctx) || readyInDays(d, now) > 0) return
        val (title, body) = tomorrowText(d, now) ?: return
        val alarm = nextAlarm(d, now)
        val text = body + (alarm?.let { "\nБудильник: ${java.time.Instant.ofEpochMilli(it.first).atZone(java.time.ZoneId.systemDefault()).let { z -> hhmm(z.hour * 60 + z.minute) }}" } ?: "")
        val n = NotificationCompat.Builder(ctx, CH_EVENING)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text.lineSequence().first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(openSchedule(ctx, 14))
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(ctx).notify(NID_EVENING, n)
        Diag.add(ctx, "Сводка на завтра: $title")
    }

    @SuppressLint("MissingPermission")
    fun ring(ctx: Context, text: String) {
        val full = PendingIntent.getActivity(
            ctx, 15, Intent(ctx, AlarmActivity::class.java).putExtra("text", text)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(ctx, CH_ALARM)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Пора собираться")
            .setContentText(text)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .setOngoing(true)
            .addAction(0, "Выключить", pi(ctx, ScheduleReceiver.STOP, 16))
            .addAction(0, "Ещё 10 минут", pi(ctx, ScheduleReceiver.SNOOZE, 17))
            .build()
        n.flags = n.flags or android.app.Notification.FLAG_INSISTENT
        NotificationManagerCompat.from(ctx).notify(NID_ALARM, n)
    }

    @SuppressLint("MissingPermission")
    fun snooze(ctx: Context) {
        val am = ctx.getSystemService(AlarmManager::class.java)
        val at = System.currentTimeMillis() + 10 * 60_000
        val alarm = pi(ctx, ScheduleReceiver.ALARM, 12)
        if (canExact(ctx)) am.setAlarmClock(AlarmManager.AlarmClockInfo(at, openSchedule(ctx, 13)), alarm)
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, alarm)
    }
}

class ScheduleReceiver : BroadcastReceiver() {
    companion object {
        const val EVENING = "com.rifat.trainercount.EVENING"
        const val ALARM = "com.rifat.trainercount.ALARM"
        const val STOP = "com.rifat.trainercount.ALARM_STOP"
        const val SNOOZE = "com.rifat.trainercount.ALARM_SNOOZE"
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        Store.init(ctx)
        when (intent.action) {
            EVENING -> {
                Planner.refresh(ctx)
                Planner.showEvening(ctx)
            }
            ALARM -> {
                val d = Store.data.value
                val now = System.currentTimeMillis()
                // Первое занятие в ближайшие 2 часа: его и пишем на будильнике.
                val first = com.rifat.trainercount.logic.dayPlan(d, com.rifat.trainercount.logic.dayOf(now), now)
                    .filterIsInstance<com.rifat.trainercount.logic.PlanItem.Lesson>()
                    .firstOrNull { it.status == com.rifat.trainercount.logic.Status.PLANNED }
                val text = first?.let { "${hhmm(it.occ.min)} ${d.client(it.occ.clientId)?.name}" } ?: "Тренировка по расписанию"
                Diag.add(ctx, "Будильник: $text")
                Planner.ring(ctx, text)
                Planner.reschedule(ctx)
            }
            STOP -> NotificationManagerCompat.from(ctx).cancel(Planner.NID_ALARM)
            SNOOZE -> {
                NotificationManagerCompat.from(ctx).cancel(Planner.NID_ALARM)
                Planner.snooze(ctx)
            }
        }
    }
}

/** Экран будильника поверх блокировки. */
class AlarmActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        val text = intent.getStringExtra("text") ?: ""
        val pad = (24 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(pad, pad, pad, pad)
            setBackgroundColor(0xFF1E2533.toInt())
        }
        root.addView(TextView(this).apply {
            setText("Пора собираться")
            textSize = 30f
            setTextColor(0xFFC6E84B.toInt())
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            setText(text)
            textSize = 22f
            setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(0, pad, 0, pad * 2)
        })
        fun button(label: String, action: String) = Button(this).apply {
            setText(label)
            textSize = 20f
            setOnClickListener {
                sendBroadcast(Intent(this@AlarmActivity, ScheduleReceiver::class.java).setAction(action))
                finish()
            }
        }
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        root.addView(button("Выключить", ScheduleReceiver.STOP), lp)
        root.addView(button("Ещё 10 минут", ScheduleReceiver.SNOOZE), lp)
        setContentView(root)
    }
}
