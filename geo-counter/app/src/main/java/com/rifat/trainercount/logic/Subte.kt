package com.rifat.trainercount.logic

/*
 * Метро Буэнос-Айреса без интернета: станции линий A–E и H (открытые данные города, порядок по линии).
 * Маршрут: пешком до станции, поездка с пересадками, пешком до места. Сравнивается с дорогой пешком.
 */

private data class Stop(val name: String, val lat: Double, val lng: Double)

private val SUBTE = listOf(
    "A" to listOf(
        Stop("Plaza De Mayo", -34.608810, -58.370968),
        Stop("Peru", -34.608559, -58.374268),
        Stop("Piedras", -34.608882, -58.379085),
        Stop("Lima", -34.609100, -58.382232),
        Stop("Saenz Peña", -34.609413, -58.386777),
        Stop("Congreso", -34.609226, -58.392669),
        Stop("Pasco", -34.609646, -58.398427),
        Stop("Alberti", -34.609834, -58.401208),
        Stop("Plaza De Miserere", -34.609817, -58.406707),
        Stop("Loria", -34.610782, -58.415186),
        Stop("Castro Barros", -34.611770, -58.421816),
        Stop("Rio De Janeiro", -34.615206, -58.429500),
        Stop("Acoyte", -34.618280, -58.436429),
        Stop("Primera Junta", -34.620405, -58.441178),
        Stop("Puan", -34.623529, -58.448648),
        Stop("Carabobo", -34.626667, -58.456710),
        Stop("San José De Flores", -34.629087, -58.463541),
        Stop("San Pedrito", -34.630707, -58.469640),
    ),
    "B" to listOf(
        Stop("Leandro N. Alem", -34.602989, -58.369930),
        Stop("Florida", -34.603297, -58.375072),
        Stop("C. Pellegrini", -34.603637, -58.380715),
        Stop("Uruguay", -34.604094, -58.387296),
        Stop("Callao - Maestro Alfredo Bravo", -34.604420, -58.392314),
        Stop("Pasteur - Amia", -34.604643, -58.399474),
        Stop("Pueyrredon", -34.604581, -58.405399),
        Stop("Carlos Gardel", -34.604080, -58.411763),
        Stop("Almagro - Medrano", -34.603165, -58.420962),
        Stop("Angel Gallardo", -34.602162, -58.431274),
        Stop("Malabia - Osvaldo Pugliese", -34.598967, -58.439771),
        Stop("Dorrego", -34.591718, -58.447573),
        Stop("Federico Lacroze", -34.587198, -58.455029),
        Stop("Tronador - Villa Ortúzar", -34.584095, -58.466227),
        Stop("De Los Incas -Pque. Chas", -34.581249, -58.474241),
        Stop("Echeverría", -34.577797, -58.481014),
        Stop("Juan Manuel De Rosas - Villa Urquiza", -34.574319, -58.486385),
    ),
    "C" to listOf(
        Stop("Retiro", -34.591194, -58.374018),
        Stop("San Martin", -34.595057, -58.377819),
        Stop("Lavalle", -34.601770, -58.378156),
        Stop("Diagonal Norte", -34.604844, -58.379530),
        Stop("Av. De Mayo", -34.608983, -58.380611),
        Stop("Moreno", -34.612617, -58.380444),
        Stop("Independencia", -34.618126, -58.380174),
        Stop("San Juan", -34.621917, -58.379921),
        Stop("Constitucion", -34.627619, -58.381434),
    ),
    "D" to listOf(
        Stop("Catedral", -34.607802, -58.373956),
        Stop("9 De Julio", -34.604245, -58.380574),
        Stop("Tribunales - Teatro Colón", -34.601587, -58.385142),
        Stop("Callao", -34.599640, -58.393125),
        Stop("Facultad De Medicina", -34.599757, -58.397924),
        Stop("Pueyrredon", -34.594426, -58.402395),
        Stop("Agüero", -34.591628, -58.407161),
        Stop("Bulnes", -34.588237, -58.411294),
        Stop("R.Scalabrini Ortiz", -34.585156, -58.415955),
        Stop("Plaza Italia", -34.581411, -58.421196),
        Stop("Palermo", -34.578422, -58.425711),
        Stop("Ministro Carranza - Miguel Abuelo", -34.575178, -58.435014),
        Stop("Olleros", -34.570012, -58.444668),
        Stop("Jose Hernandez", -34.566215, -58.452126),
        Stop("Juramento", -34.562309, -58.456489),
        Stop("Congreso De Tucuman", -34.555642, -58.462378),
    ),
    "E" to listOf(
        Stop("Plaza De Los Virreyes - Eva Peron", -34.643312, -58.461652),
        Stop("Varela", -34.640137, -58.457892),
        Stop("Medalla Milagrosa", -34.636389, -58.450278),
        Stop("Emilio Mitre", -34.631042, -58.442171),
        Stop("Jose Maria Moreno", -34.628018, -58.433816),
        Stop("Av. La Plata", -34.627015, -58.426789),
        Stop("Boedo", -34.625366, -58.415533),
        Stop("Urquiza", -34.624654, -58.409391),
        Stop("Jujuy", -34.623866, -58.402937),
        Stop("Pichincha", -34.623110, -58.397068),
        Stop("Entre Rios - Rodolfo Walsh", -34.622720, -58.391512),
        Stop("San Jose", -34.622339, -58.385149),
        Stop("Independencia", -34.617937, -58.381535),
        Stop("Belgrano", -34.612849, -58.377581),
        Stop("Bolivar", -34.609242, -58.373684),
        Stop("Correo Central", -34.603014, -58.370413),
        Stop("Catalinas", -34.596597, -58.371700),
        Stop("Retiro", -34.592114, -58.375850),
    ),
    "H" to listOf(
        Stop("Hospitales", -34.641269, -58.412385),
        Stop("Parque Patricios", -34.638406, -58.405795),
        Stop("Caseros", -34.635750, -58.398928),
        Stop("Inclan - Mezquita Al Ahmad", -34.629376, -58.400970),
        Stop("Humberto 1°", -34.623092, -58.402323),
        Stop("Venezuela", -34.615242, -58.404732),
        Stop("Once - 30 De Diciembre", -34.608935, -58.406036),
        Stop("Corrientes", -34.604490, -58.405450),
        Stop("Córdoba", -34.598455, -58.403722),
        Stop("Santa Fe - Carlos Jauregui", -34.594525, -58.402376),
        Stop("Las Heras", -34.587462, -58.397216),
        Stop("Facultad De Derecho - Julieta Lanteri", -34.583036, -58.391019),
    ),
)

private class Node(val line: String, val stop: Stop)

private val NODES: List<Node> = SUBTE.flatMap { (line, stops) -> stops.map { Node(line, it) } }

/** Пешком по городу: по прямой × 1,3 со скоростью 75 м/мин (4,5 км/ч). */
fun walkMin(a: LatLng, b: LatLng): Int = (distanceM(a.first, a.second, b.first, b.second) * 1.3 / 75).toInt().coerceAtLeast(1)

private fun at(n: Node): LatLng = n.stop.lat to n.stop.lng

/** Поездка между соседними станциями: разгон, стоянка и сам перегон (~30 км/ч). */
private fun hop(a: Node, b: Node) = 1.0 + distanceM(a.stop.lat, a.stop.lng, b.stop.lat, b.stop.lng) / 500

private const val WAIT = 4 // ожидание поезда
private const val TRANSFER = 4 // переход между линиями, сверх ходьбы

/**
 * Лучший путь на метро или пешком. walk: сколько идти пешком между точками; по умолчанию оценка по прямой,
 * геослой подставляет настоящий пеший маршрут по улицам для первого и последнего участка и для пути целиком.
 */
fun subteLeg(from: LatLng, to: LatLng, walk: (LatLng, LatLng) -> Int = ::walkMin): Leg {
    val direct = walk(from, to)
    val n = NODES.size
    val dist = DoubleArray(n) { Double.MAX_VALUE }
    val prev = IntArray(n) { -1 }
    val done = BooleanArray(n)
    // Старт: дойти до станций в пределах ~20 минут.
    for (i in 0 until n) {
        val w = walkMin(from, at(NODES[i]))
        if (w <= 20) dist[i] = (w + WAIT).toDouble()
    }
    repeat(n) {
        var u = -1
        for (i in 0 until n) if (!done[i] && dist[i] < Double.MAX_VALUE && (u < 0 || dist[i] < dist[u])) u = i
        if (u < 0) return@repeat
        done[u] = true
        val a = NODES[u]
        for (v in 0 until n) {
            if (done[v]) continue
            val b = NODES[v]
            val cost = when {
                b.line == a.line -> {
                    val stops = SUBTE.first { it.first == a.line }.second
                    if (kotlin.math.abs(stops.indexOf(a.stop) - stops.indexOf(b.stop)) == 1) hop(a, b) else continue
                }
                else -> {
                    val m = distanceM(a.stop.lat, a.stop.lng, b.stop.lat, b.stop.lng)
                    if (m > 450) continue
                    m * 1.3 / 75 + TRANSFER
                }
            }
            if (dist[u] + cost < dist[v]) { dist[v] = dist[u] + cost; prev[v] = u }
        }
    }
    var best = -1
    var bestTotal = Double.MAX_VALUE
    for (i in 0 until n) {
        if (dist[i] == Double.MAX_VALUE) continue
        val w = walkMin(at(NODES[i]), to)
        if (w <= 20 && dist[i] + w < bestTotal) { bestTotal = dist[i] + w; best = i }
    }
    if (best < 0) return Leg(direct, direct, "пешком $direct мин", false)
    // Восстановить путь и описать по линиям; пешие концы пересчитать точнее.
    val path = generateSequence(best) { prev[it].takeIf { p -> p >= 0 } }.toList().reversed().map { NODES[it] }
    val walkIn = walk(from, at(path.first()))
    val walkOut = walk(at(path.last()), to)
    bestTotal += (walkIn - walkMin(from, at(path.first()))) + (walkOut - walkMin(at(path.last()), to))
    if (bestTotal >= direct) return Leg(direct, direct, "пешком $direct мин", false)
    val legs = mutableListOf<String>()
    var i = 0
    var walkTransfers = 0
    while (i < path.size) {
        var j = i
        while (j + 1 < path.size && path[j + 1].line == path[i].line) j++
        val stops = j - i
        if (stops > 0) legs += "линия ${path[i].line}: ${path[i].stop.name} → ${path[j].stop.name}, ${stops} ст."
        if (j + 1 < path.size) walkTransfers += walkMin(at(path[j]), at(path[j + 1]))
        i = j + 1
    }
    val total = bestTotal.toInt() + 1
    val walkAll = walkIn + walkOut + walkTransfers
    return Leg(total, walkAll, "$total мин: пешком $walkIn мин → метро " + legs.joinToString(" → пересадка → ") + " → пешком $walkOut мин, пешком всего $walkAll мин", false)
}
