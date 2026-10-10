@file:OptIn(ExperimentalMaterial3Api::class)

package com.rifat.trainercount.ui

import android.annotation.SuppressLint
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import android.location.Address
import android.location.Geocoder
import android.location.Location
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.rifat.trainercount.geo.Perms
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val ctx = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= 31 && dark -> dynamicDarkColorScheme(ctx)
        Build.VERSION.SDK_INT >= 31 -> dynamicLightColorScheme(ctx)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
fun rememberNow(periodMs: Long = 30_000): Long {
    val now by produceState(System.currentTimeMillis()) {
        while (true) {
            delay(periodMs)
            value = System.currentTimeMillis()
        }
    }
    return now
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(top = 20.dp, bottom = 8.dp),
    )
}

@Composable
fun StatTile(label: String, value: String, modifier: Modifier = Modifier) {
    Card(modifier = modifier) {
        Column(Modifier.padding(14.dp)) {
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
fun WarnCard(text: String, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.Warning, null, tint = MaterialTheme.colorScheme.onErrorContainer)
            Text(
                text,
                color = MaterialTheme.colorScheme.onErrorContainer,
                modifier = Modifier.padding(start = 12.dp),
            )
        }
    }
}

@Composable
fun Stepper(value: Int, onChange: (Int) -> Unit, range: IntRange, step: Int = 1, label: (Int) -> String = { "$it" }) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        FilledTonalIconButton(onClick = { onChange((value - step).coerceIn(range)) }, enabled = value > range.first) {
            Text("−", style = MaterialTheme.typography.titleLarge)
        }
        Text(
            label(value),
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(horizontal = 12.dp),
        )
        FilledTonalIconButton(onClick = { onChange((value + step).coerceIn(range)) }, enabled = value < range.last) {
            Icon(Icons.Default.Add, null)
        }
    }
}

@Composable
fun CheckRow(ok: Boolean, title: String, hint: String, action: String, onAction: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Icon(
            if (ok) Icons.Default.CheckCircle else Icons.Default.Warning, null,
            tint = if (ok) Color(0xFF2E9E6A) else MaterialTheme.colorScheme.error,
            modifier = Modifier.size(24.dp),
        )
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (!ok) Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (!ok) FilledTonalButton(onClick = onAction) { Text(action) }
    }
}

// ---------- Диалоги даты и времени ----------

fun pickDate(ctx: Context, initial: Long, onPick: (Long) -> Unit) {
    val z = ZoneId.systemDefault()
    val d = Instant.ofEpochMilli(initial).atZone(z)
    DatePickerDialog(ctx, { _, y, m, day ->
        val picked = LocalDate.of(y, m + 1, day).atTime(d.toLocalTime()).atZone(z).toInstant().toEpochMilli()
        onPick(picked)
    }, d.year, d.monthValue - 1, d.dayOfMonth).show()
}

fun pickTime(ctx: Context, minutes: Int, onPick: (Int) -> Unit) {
    TimePickerDialog(ctx, { _, h, m -> onPick(h * 60 + m) }, minutes / 60, minutes % 60, true).show()
}

fun hm(min: Int) = "%02d:%02d".format(min / 60, min % 60)

// ---------- Геолокация ----------

@SuppressLint("MissingPermission")
fun currentLocation(ctx: Context, cb: (Location?) -> Unit) {
    if (!Perms.location(ctx)) return cb(null)
    LocationServices.getFusedLocationProviderClient(ctx)
        .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, CancellationTokenSource().token)
        .addOnSuccessListener { cb(it) }
        .addOnFailureListener { cb(null) }
}

@Suppress("DEPRECATION")
suspend fun geocode(ctx: Context, q: String): List<Address> = withContext(Dispatchers.IO) {
    runCatching { Geocoder(ctx, Locale("ru")).getFromLocationName(q, 5) ?: emptyList() }.getOrDefault(emptyList())
}

@Suppress("DEPRECATION")
suspend fun reverseGeocode(ctx: Context, lat: Double, lng: Double): String? = withContext(Dispatchers.IO) {
    runCatching { Geocoder(ctx, Locale("ru")).getFromLocation(lat, lng, 1)?.firstOrNull()?.let(::shortAddress) }.getOrNull()
}

fun shortAddress(a: Address): String =
    listOfNotNull(a.thoroughfare, a.subThoroughfare).joinToString(", ").ifBlank { a.getAddressLine(0) ?: "" }

private val coordRe = Regex("""(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})""")

/** Координаты из текста: «55.7558, 37.6173» или ссылка Google Maps с @55.75,37.61. */
fun parseCoords(text: String): Pair<Double, Double>? {
    val m = coordRe.find(text) ?: return null
    val lat = m.groupValues[1].toDoubleOrNull() ?: return null
    val lng = m.groupValues[2].toDoubleOrNull() ?: return null
    if (lat !in -90.0..90.0 || lng !in -180.0..180.0) return null
    return lat to lng
}
