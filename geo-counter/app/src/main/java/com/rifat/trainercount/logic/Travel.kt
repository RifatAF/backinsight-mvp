package com.rifat.trainercount.logic

/**
 * Дорога между двумя точками: сколько ехать всего и сколько из этого пешком.
 * exact: посчитано по настоящему маршруту (Google или OpenStreetMap), иначе оценка по расстоянию.
 */
data class Leg(val min: Int, val walkMin: Int, val how: String, val exact: Boolean)

typealias LatLng = Pair<Double, Double>

/** Маршрутизатор: from → to с отправлением в depart (мс). null: не знает. */
fun interface Router {
    fun leg(from: LatLng, to: LatLng, depart: Long): Leg?
}

/** Без интернета: схема метро и пешком по прямой. */
fun estimateLeg(from: LatLng, to: LatLng): Leg = subteLeg(from, to)

fun Leg.text() = if (exact) how else "$how (примерно)"

/** Между занятиями закладывается дорога из настроек (по умолчанию 30 мин). Маршрут только для проверки. */
fun needMin(s: Settings, @Suppress("UNUSED_PARAMETER") leg: Leg?) = s.travelMin

/** Предупреждение, если по маршруту с запасом не успеть за gap минут. null: успевает или маршрута нет. */
fun routeWarn(s: Settings, leg: Leg?, gap: Int): String? =
    if (leg != null && leg.min + s.travelExtra > gap) "по маршруту ${leg.text()}, с запасом ${s.travelExtra} мин нужно ${leg.min + s.travelExtra} мин, а есть $gap" else null
