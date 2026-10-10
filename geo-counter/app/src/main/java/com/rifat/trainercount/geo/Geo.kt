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
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Step
import com.rifat.trainercount.logic.onClientEnter
import com.rifat.trainercount.logic.onClientExit
import com.rifat.trainercount.logic.onHomeArrive
import com.rifat.trainercount.logic.onHomeLeave
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
        step?.effects?.forEach { Notifier.onEffect(ctx, it) }
    }

    /** При открытии приложения и по таймеру: если уже дома по Wi‑Fi, закрыть выход. */
    fun checkWifiNow(ctx: Context) {
        Store.init(ctx)
        val d = Store.data.value
        if ((d.openTrip != null || d.open.isNotEmpty()) && Wifi.isHome(ctx, d)) {
            handle(ctx) { onHomeArrive(it, System.currentTimeMillis()) }
        }
    }
}

object GeoSync {
    private const val HOME = "home"
    private const val CLIENT = "c:"

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
        if (!Perms.background(ctx)) return
        val d = Store.data.value
        val s = d.settings
        val list = buildList {
            if (s.homeLat != null && s.homeLng != null) add(fence(HOME, s.homeLat, s.homeLng, s.homeRadius))
            d.clients.filter { !it.archived && it.hasPlace }.forEach { add(fence(CLIENT + it.id, it.lat!!, it.lng!!, it.radius)) }
        }
        val gc = LocationServices.getGeofencingClient(ctx)
        val pi = geofencePi(ctx)
        gc.removeGeofences(pi).addOnCompleteListener {
            if (list.isEmpty()) return@addOnCompleteListener
            val req = GeofencingRequest.Builder()
                .setInitialTrigger(GeofencingRequest.INITIAL_TRIGGER_ENTER)
                .addGeofences(list)
                .build()
            runCatching { gc.addGeofences(req, pi) }
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
        val req = PeriodicWorkRequestBuilder<RefreshWorker>(6, TimeUnit.HOURS).build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("refresh", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun dispatch(ctx: Context, id: String, enter: Boolean, t: Long) {
        Tracker.handle(ctx) { d ->
            when {
                id == HOME && enter -> onHomeArrive(d, t)
                id == HOME -> onHomeLeave(d, t)
                id.startsWith(CLIENT) && enter -> onClientEnter(d, id.removePrefix(CLIENT), t)
                id.startsWith(CLIENT) -> onClientExit(d, id.removePrefix(CLIENT), t)
                else -> Step(d)
            }
        }
    }
}

class GeofenceReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val ev = GeofencingEvent.fromIntent(intent) ?: return
        if (ev.hasError()) return
        val now = System.currentTimeMillis()
        val t = ev.triggeringLocation?.time?.takeIf { it in (now - 30 * 60_000)..(now + 60_000) } ?: now
        val enter = when (ev.geofenceTransition) {
            Geofence.GEOFENCE_TRANSITION_ENTER -> true
            Geofence.GEOFENCE_TRANSITION_EXIT -> false
            else -> return
        }
        ev.triggeringGeofences?.forEach { GeoSync.dispatch(ctx, it.requestId, enter, t) }
    }
}

class WifiReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
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
