package com.rifat.trainercount.geo

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import androidx.core.content.ContextCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.google.android.gms.common.api.ApiException
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.android.gms.tasks.Tasks
import com.rifat.trainercount.logic.Effect
import com.rifat.trainercount.logic.HOME_KEY
import com.rifat.trainercount.logic.fDur
import com.rifat.trainercount.logic.inHours
import com.rifat.trainercount.logic.nearestZone
import com.rifat.trainercount.logic.onPoll
import com.rifat.trainercount.logic.trainings
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Step
import com.rifat.trainercount.logic.zones
import com.rifat.trainercount.logic.onClientEnter
import com.rifat.trainercount.logic.onClientExit
import com.rifat.trainercount.logic.onHomeArrive
import com.rifat.trainercount.logic.onHomeLeave
import com.rifat.trainercount.logic.onPlaceEnter
import com.rifat.trainercount.logic.onPlaceExit
import java.util.concurrent.TimeUnit

object Perms {
    private fun has(ctx: Context, p: String) = ContextCompat.checkSelfPermission(ctx, p) == PackageManager.PERMISSION_GRANTED
    fun location(ctx: Context) = has(ctx, Manifest.permission.ACCESS_FINE_LOCATION)
    fun background(ctx: Context) = location(ctx) && has(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    fun notifications(ctx: Context) =
        Build.VERSION.SDK_INT < 33 || has(ctx, Manifest.permission.POST_NOTIFICATIONS)
    fun battery(ctx: Context) =
        ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)
    fun allOk(ctx: Context) = background(ctx) && notifications(ctx) && battery(ctx)
}

object Wifi {
    @Suppress("DEPRECATION")
    fun currentSsid(ctx: Context): String? {
        val wm = ctx.applicationContext.getSystemService(WifiManager::class.java) ?: return null
        val raw = wm.connectionInfo?.ssid ?: return null
        val s = raw.trim().removeSurrounding("\"")
        return if (s.isBlank() || s == "<unknown ssid>") null else s
    }

    fun isHome(ctx: Context, d: AppData): Boolean {
        val names = d.settings.homeSsid.split(',').map { it.trim() }.filter { it.isNotEmpty() }
        if (names.isEmpty()) return false
        val cur = currentSsid(ctx) ?: return false
        return names.any { it.equals(cur, ignoreCase = true) }
    }
}

/** Применяет событие к данным и показывает уведомления. */
object Tracker {
    fun handle(ctx: Context, f: (AppData) -> Step) {
        Store.init(ctx)
        var step: Step? = null
        Store.update { d -> f(d).also { step = it }.data }
        step?.effects?.forEach {
            logEffect(ctx, it)
            Notifier.onEffect(ctx, it)
        }
    }

    private fun logEffect(ctx: Context, e: Effect) {
        val d = Store.data.value
        when (e) {
            is Effect.VisitAdded -> Diag.add(
                ctx,
                "✓ Засчитано: ${d.client(e.visit.clientId)?.name} +${trainings(e.visit.count)} " +
                    "(${fDur((e.visit.end - e.visit.start) / 60_000)}${if (e.visit.check) ", выход не пришёл" else ""})",
            )
            is Effect.PlaceAuto -> Diag.add(ctx, "✓ ${e.place.name}: по расписанию ${trainings(e.visits.size)}")
            is Effect.PendingAdded -> Diag.add(ctx, "? ${d.place(e.pending.placeId)?.name}: ${trainings(e.pending.count)}, спрошу с кем")
        }
    }

    /** При открытии приложения и по таймеру: если уже дома по Wi‑Fi, закрыть выход. */
    fun checkWifiNow(ctx: Context) {
        Store.init(ctx)
        val d = Store.data.value
        if ((d.openTrip != null || d.open.isNotEmpty()) && Wifi.isHome(ctx, d)) {
            Diag.add(ctx, "Дома по Wi‑Fi: закрываю выход")
            handle(ctx) { onHomeArrive(it, System.currentTimeMillis()) }
        }
    }
}

object GeoSync {
    private const val HOME = "home"
    private const val CLIENT = "c:"
    private const val PLACE = "p:"

    private fun flags() = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE

    private fun geofencePi(ctx: Context) =
        PendingIntent.getBroadcast(ctx, 1, Intent(ctx, GeofenceReceiver::class.java), flags())

    private fun wifiPi(ctx: Context) =
        PendingIntent.getBroadcast(ctx, 2, Intent(ctx, WifiReceiver::class.java), flags())

    private fun fence(id: String, lat: Double, lng: Double, r: Int) = Geofence.Builder()
        .setRequestId(id)
        .setCircularRegion(lat, lng, r.toFloat())
        .setExpirationDuration(Geofence.NEVER_EXPIRE)
        .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER or Geofence.GEOFENCE_TRANSITION_EXIT)
        .setNotificationResponsiveness(60_000)
        .build()

    @SuppressLint("MissingPermission")
    fun sync(ctx: Context) {
        Store.init(ctx)
        if (!Perms.background(ctx)) {
            Diag.setState(ctx, false, "Нет разрешения на геолокацию «Всегда»: автоучёт выключен")
            return
        }
        val d = Store.data.value
        val s = d.settings
        val list = buildList {
            if (s.homeLat != null && s.homeLng != null) add(fence(HOME, s.homeLat, s.homeLng, s.homeRadius))
            d.clients.filter { !it.archived && it.hasPlace && it.placeId == null }
                .forEach { add(fence(CLIENT + it.id, it.lat!!, it.lng!!, it.radius)) }
            d.places.forEach { add(fence(PLACE + it.id, it.lat, it.lng, it.radius)) }
        }
        val gc = LocationServices.getGeofencingClient(ctx)
        val pi = geofencePi(ctx)
        val names = zones(d).joinToString(", ") { it.name }
        gc.removeGeofences(pi).addOnCompleteListener {
            if (list.isEmpty()) {
                Diag.setState(ctx, true, "Зон пока нет: добавьте дом и клиентов")
                return@addOnCompleteListener
            }
            val req = GeofencingRequest.Builder()
                .setInitialTrigger(GeofencingRequest.INITIAL_TRIGGER_ENTER)
                .addGeofences(list)
                .build()
            try {
                gc.addGeofences(req, pi)
                    .addOnSuccessListener {
                        val prev = Diag.state.value
                        val text = "Отслеживается зон: ${list.size}"
                        if (prev?.ok != true || prev.text != text) Diag.add(ctx, "Зоны включены: $names")
                        Diag.setState(ctx, true, text)
                    }
                    .addOnFailureListener { e ->
                        val msg = (e as? ApiException)?.statusCode?.let(Diag::geofenceError) ?: "Ошибка геозон: ${e.message}"
                        Diag.add(ctx, "✗ $msg")
                        Diag.setState(ctx, false, msg)
                    }
            } catch (e: SecurityException) {
                Diag.setState(ctx, false, "Нет разрешения на геолокацию «Всегда»")
            }
        }
        registerWifi(ctx)
    }

    private fun registerWifi(ctx: Context) {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return
        val pi = wifiPi(ctx)
        runCatching { cm.unregisterNetworkCallback(pi) }
        val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
        runCatching { cm.registerNetworkCallback(req, pi) }
    }

    fun schedule(ctx: Context) {
        val wm = WorkManager.getInstance(ctx)
        wm.enqueueUniquePeriodicWork(
            "refresh", ExistingPeriodicWorkPolicy.KEEP, PeriodicWorkRequestBuilder<RefreshWorker>(6, TimeUnit.HOURS).build()
        )
        wm.enqueueUniquePeriodicWork(
            "poll", ExistingPeriodicWorkPolicy.KEEP, PeriodicWorkRequestBuilder<PollWorker>(15, TimeUnit.MINUTES).build()
        )
    }

    fun zoneName(d: AppData, id: String): String = when {
        id == HOME -> "дом"
        id.startsWith(CLIENT) -> d.client(id.removePrefix(CLIENT))?.name ?: "клиент"
        id.startsWith(PLACE) -> d.place(id.removePrefix(PLACE))?.name ?: "место"
        else -> id
    }

    fun dispatch(ctx: Context, id: String, enter: Boolean, t: Long) {
        val d0 = Store.data.value
        if (id != HOME && enter && !inHours(d0.settings, t)) {
            Diag.add(ctx, "Нерабочее время по настройкам: «${zoneName(d0, id)}» не считаю")
            return
        }
        // Дома по Wi‑Fi: зона клиента или кафе по соседству сработала от неточного места.
        if (id != HOME && enter && Wifi.isHome(ctx, d0)) {
            Diag.add(ctx, "Дома по Wi‑Fi: вход в «${zoneName(d0, id)}» пропускаю")
            return
        }
        Tracker.handle(ctx) { d ->
            when {
                id == HOME && enter -> onHomeArrive(d, t, byGeofence = true)
                id == HOME -> onHomeLeave(d, t)
                id.startsWith(CLIENT) && enter -> onClientEnter(d, id.removePrefix(CLIENT), t)
                id.startsWith(CLIENT) -> onClientExit(d, id.removePrefix(CLIENT), t)
                id.startsWith(PLACE) && enter -> onPlaceEnter(d, id.removePrefix(PLACE), t)
                id.startsWith(PLACE) -> onPlaceExit(d, id.removePrefix(PLACE), t)
                else -> Step(d)
            }
        }
    }
}

class GeofenceReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val ev = GeofencingEvent.fromIntent(intent) ?: return
        if (ev.hasError()) {
            val msg = Diag.geofenceError(ev.errorCode)
            Diag.add(ctx, "✗ $msg")
            Diag.setState(ctx, false, msg)
            return
        }
        val now = System.currentTimeMillis()
        val t = ev.triggeringLocation?.time?.takeIf { it in (now - 30 * 60_000)..(now + 60_000) } ?: now
        val enter = when (ev.geofenceTransition) {
            Geofence.GEOFENCE_TRANSITION_ENTER -> true
            Geofence.GEOFENCE_TRANSITION_EXIT -> false
            else -> return
        }
        Store.init(ctx)
        val acc = ev.triggeringLocation?.accuracy?.toInt()
        ev.triggeringGeofences?.forEach {
            Diag.add(ctx, "${if (enter) "→ Вход" else "← Выход"}: ${GeoSync.zoneName(Store.data.value, it.requestId)}" + (acc?.let { a -> " (±$a м)" } ?: ""))
            GeoSync.dispatch(ctx, it.requestId, enter, t)
        }
    }
}

class WifiReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Diag.add(ctx, "Wi‑Fi: ${Wifi.currentSsid(ctx) ?: "без названия"}")
        Tracker.checkWifiNow(ctx)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        GeoSync.sync(ctx)
        GeoSync.schedule(ctx)
    }
}

class RefreshWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        GeoSync.sync(applicationContext)
        Tracker.checkWifiNow(applicationContext)
        return Result.success()
    }
}

/** Страховка: раз в 15 минут в рабочие часы смотрим, где телефон, и досчитываем то, что геозоны пропустили. */
object Poller {
    @SuppressLint("MissingPermission")
    fun run(ctx: Context, manual: Boolean) {
        Store.init(ctx)
        val now = System.currentTimeMillis()
        val d = Store.data.value
        if (!Perms.background(ctx)) {
            if (manual) Diag.add(ctx, "Проверка: нет разрешения «Всегда»")
            return
        }
        if (!manual && !inHours(d.settings, now)) return
        if (Wifi.isHome(ctx, d)) {
            Tracker.checkWifiNow(ctx)
            Store.update { it.copy(lastPoll = now) }
            if (manual) Diag.add(ctx, "Проверка: дома (Wi‑Fi)")
            return
        }
        val fused = LocationServices.getFusedLocationProviderClient(ctx)
        fun fix(priority: Int) = runCatching {
            Tasks.await(fused.getCurrentLocation(priority, CancellationTokenSource().token), 40, TimeUnit.SECONDS)
        }.getOrNull()
        val rough = fix(Priority.PRIORITY_BALANCED_POWER_ACCURACY)
        // Рядом с краем зоны и место неточное: уточняем по GPS.
        val edge = rough?.let { nearestZone(d, it.latitude, it.longitude)?.second }
        val precise = if (rough != null && rough.accuracy > 60 && edge != null && kotlin.math.abs(edge) < 250)
            fix(Priority.PRIORITY_HIGH_ACCURACY) else null
        val l = precise ?: rough ?: runCatching { Tasks.await(fused.lastLocation, 10, TimeUnit.SECONDS) }.getOrNull()
        if (l == null) {
            Diag.add(ctx, "Проверка: место не определено")
            return
        }
        val near = nearestZone(d, l.latitude, l.longitude)
        Diag.add(
            ctx,
            "Проверка (±${l.accuracy.toInt()} м): " + when {
                near == null -> "зон нет"
                near.second <= 0 -> "внутри зоны «${near.first.name}»"
                else -> "ближе всего «${near.first.name}», ${near.second.toInt()} м до края"
            },
        )
        val before = Store.data.value.open.keys
        Tracker.handle(ctx) { onPoll(it, l.latitude, l.longitude, l.accuracy, now) }
        val after = Store.data.value.open.keys
        (after - before).forEach { Diag.add(ctx, "→ Вход по проверке: ${zoneLabel(it)}") }
        (before - after).forEach { Diag.add(ctx, "← Выход по проверке: ${zoneLabel(it)}") }
    }

    private fun zoneLabel(key: String): String {
        val d = Store.data.value
        return if (key == HOME_KEY) "дом" else d.client(key)?.name ?: d.place(key.removePrefix("p:"))?.name ?: key
    }
}

class PollWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        Poller.run(applicationContext, manual = false)
        return Result.success()
    }
}
