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

/** Сколько минут закладывать между занятиями: дорога + запас. Без координат: дорога из настроек. */
fun needMin(s: Settings, leg: Leg?) = if (leg == null) s.travelMin else leg.min + s.travelExtra
