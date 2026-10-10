package com.rifat.trainercount.logic

data class Client(
    val id: String,
    val name: String,
    val members: List<String> = emptyList(),
    val address: String = "",
    val lat: Double? = null,
    val lng: Double? = null,
    val radius: Int = 120,
    val sessionMin: Int = 60,
    val minMin: Int = 30,
    val price: Int = 0,
    val archived: Boolean = false,
    val createdAt: Long = 0,
) {
    val hasPlace get() = lat != null && lng != null
}

data class Pack(
    val id: String,
    val clientId: String,
    val paidAt: Long,
    val sessions: Int,
    val amount: Int,
)

enum class VisitKind { AUTO, MANUAL, BURNED }

data class Visit(
    val id: String,
    val clientId: String,
    val start: Long,
    val end: Long,
    val count: Int,
    val kind: VisitKind = VisitKind.AUTO,
    val members: List<String> = emptyList(),
    /** Выход из зоны не пришёл, визит закрыт по другому событию: стоит проверить. */
    val check: Boolean = false,
)

data class Trip(
    val id: String,
    val start: Long,
    val end: Long? = null,
    /** null: решает автоматика (были тренировки = работа). */
    val work: Boolean? = null,
)

data class Settings(
    val homeLat: Double? = null,
    val homeLng: Double? = null,
    val homeRadius: Int = 150,
    val homeSsid: String = "",
    val startMin: Int = 6 * 60,
    val endMin: Int = 23 * 60,
    /** ISO: 1 = понедельник … 7 = воскресенье. */
    val days: Set<Int> = (1..7).toSet(),
    val setupDone: Boolean = false,
) {
    val hasHome get() = homeLat != null && homeLng != null
}

data class AppData(
    val clients: List<Client> = emptyList(),
    val packs: List<Pack> = emptyList(),
    val visits: List<Visit> = emptyList(),
    val trips: List<Trip> = emptyList(),
    val settings: Settings = Settings(),
    /** clientId → время входа в зону. */
    val open: Map<String, Long> = emptyMap(),
) {
    fun client(id: String) = clients.firstOrNull { it.id == id }
    val openTrip get() = trips.lastOrNull { it.end == null }
}
