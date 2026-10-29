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

/** Оценка без сервиса маршрутов: пешком до 1,5 км, дальше метро или автобус с подходом и ожиданием. */
fun estimateLeg(from: LatLng, to: LatLng): Leg {
    val m = distanceM(from.first, from.second, to.first, to.second) * 1.3
    if (m < 1500) return (m / 75).toInt().coerceAtLeast(1).let { Leg(it, it, "пешком ${it} мин", false) }
    // Подход к станции и от неё, ожидание и поездка ~22 км/ч.
    val ride = (m / (22_000.0 / 60)).toInt()
    return Leg(ride + 14, 10, "метро или автобус примерно, пешком ~10 мин", false)
}

fun Leg.text() = if (exact) how else "$how (оценка)"

/** Сколько минут закладывать между занятиями: дорога + запас. Без координат: дорога из настроек. */
fun needMin(s: Settings, leg: Leg?) = if (leg == null) s.travelMin else leg.min + s.travelExtra
