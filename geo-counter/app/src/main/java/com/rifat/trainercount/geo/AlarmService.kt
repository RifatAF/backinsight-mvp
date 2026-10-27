package com.rifat.trainercount.geo

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.rifat.trainercount.R
import com.rifat.trainercount.data.Store

/**
 * Будильник сам проигрывает мелодию: так громкость настраивается отдельно от остального телефона
 * и может плавно нарастать. Работает до нажатия «Выключить» или 10 минут.
 */
class AlarmService : Service() {
    companion object {
        const val CH = "alarm_quiet"
        private const val EXTRA_TEXT = "text"
        private const val EXTRA_PREVIEW = "preview"
        private const val MAX_MS = 10 * 60_000L
        private const val PREVIEW_MS = 6_000L
        private const val FADE_MS = 30_000L

        fun channel(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CH, "Будильник", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Экран будильника. Звук и громкость настраиваются в приложении"
                    setSound(null, null)
                    enableVibration(false)
                    setBypassDnd(true)
                }
            )
        }

        /** Запустить будильник. false: система не дала запустить из фона. */
        fun start(ctx: Context, text: String, preview: Boolean = false): Boolean = runCatching {
            ContextCompat.startForegroundService(
                ctx, Intent(ctx, AlarmService::class.java).putExtra(EXTRA_TEXT, text).putExtra(EXTRA_PREVIEW, preview),
            )
        }.isSuccess

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, AlarmService::class.java))
        }

        /** Громкость по ощущениям: 50 % в ползунке примерно вдвое тише, чем 100 %. */
        fun gain(percent: Int): Float = (percent.coerceIn(1, 100) / 100f).let { it * it }

        fun toneUri(tone: String): Uri =
            tone.takeIf { it.isNotBlank() }?.let(Uri::parse) ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
    }

    private var player: MediaPlayer? = null
    private val handler = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Store.init(this)
        val s = Store.data.value.settings
        val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Тренировка по расписанию"
        val preview = intent?.getBooleanExtra(EXTRA_PREVIEW, false) == true
        ServiceCompat.startForeground(
            this, Planner.NID_ALARM, notification(text, preview),
            if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK else 0,
        )
        stopSound()
        play(toneUri(s.alarmTone), gain(s.alarmVolume), s.alarmFade && !preview)
        if (s.alarmVibrate) vibrate()
        handler.postDelayed({ stopSelf() }, if (preview) PREVIEW_MS else MAX_MS)
        return START_NOT_STICKY
    }

    private fun play(uri: Uri, target: Float, fade: Boolean) {
        val p = MediaPlayer()
        try {
            p.setAudioAttributes(
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
            )
            p.setDataSource(this, uri)
            p.isLooping = true
            p.prepare()
        } catch (e: Exception) {
            // Мелодию удалили: играем стандартную.
            p.reset()
            p.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build())
            p.setDataSource(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))
            p.isLooping = true
            p.prepare()
        }
        val start = if (fade) 0.02f else target
        p.setVolume(start, start)
        p.start()
        player = p
        if (fade) {
            val t0 = System.currentTimeMillis()
            handler.post(object : Runnable {
                override fun run() {
                    val k = ((System.currentTimeMillis() - t0).toFloat() / FADE_MS).coerceAtMost(1f)
                    val v = start + (target - start) * k
                    player?.setVolume(v, v)
                    if (k < 1f) handler.postDelayed(this, 500)
                }
            })
        }
    }

    private fun vibrator(): Vibrator? =
        if (Build.VERSION.SDK_INT >= 31) getSystemService(VibratorManager::class.java)?.defaultVibrator
        else getSystemService(Vibrator::class.java)

    private fun vibrate() {
        vibrator()?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 600, 900), 1))
    }

    private fun notification(text: String, preview: Boolean) = NotificationCompat.Builder(this, CH)
        .setSmallIcon(R.drawable.ic_stat)
        .setContentTitle(if (preview) "Проверка будильника" else "Пора собираться")
        .setContentText(text)
        .setCategory(NotificationCompat.CATEGORY_ALARM)
        .setPriority(NotificationCompat.PRIORITY_MAX)
        .setOngoing(true)
        .apply {
            val full = PendingIntent.getActivity(
                this@AlarmService, 15, Intent(this@AlarmService, AlarmActivity::class.java).putExtra("text", text)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            if (!preview) setFullScreenIntent(full, true)
            setContentIntent(full)
        }
        .addAction(0, "Выключить", action(ScheduleReceiver.STOP, 16))
        .apply { if (!preview) addAction(0, "Ещё 10 минут", action(ScheduleReceiver.SNOOZE, 17)) }
        .build()

    private fun action(act: String, req: Int) = PendingIntent.getBroadcast(
        this, req, Intent(this, ScheduleReceiver::class.java).setAction(act),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun stopSound() {
        handler.removeCallbacksAndMessages(null)
        player?.runCatching { stop(); release() }
        player = null
        vibrator()?.cancel()
    }

    override fun onDestroy() {
        stopSound()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
}
