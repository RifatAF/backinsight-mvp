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
    /** Общее место (кафе, зал), где проходят занятия с этим клиентом. */
    val placeId: String? = null,
    /** Расписание в общем месте: по нему приложение само понимает, с кем было занятие. */
    val slots: List<Slot> = emptyList(),
    /** Семья: каждый пришедший считается отдельным занятием. */
    val perMember: Boolean = true,
) {
    val hasPlace get() = lat != null && lng != null
    val isFamily get() = members.size > 1
    val countsEach get() = isFamily && perMember
}

/** День недели ISO (1 = пн) и время начала в минутах от полуночи. */
data class Slot(val day: Int, val min: Int)

/** Место, где бывают разные клиенты: кафе для онлайн-занятий, зал. */
data class Place(
    val id: String,
    val name: String,
    val lat: Double,
    val lng: Double,
    val radius: Int = 100,
    val sessionMin: Int = 60,
    val minMin: Int = 30,
)

/** Пробыли в общем месте, но непонятно с кем: ждёт ответа. */
data class Pending(
    val id: String,
    val placeId: String,
    val start: Long,
    val end: Long,
    val count: Int,
    /** Кого подставить заранее (совпали по расписанию). */
    val suggested: List<String> = emptyList(),
)

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
    val places: List<Place> = emptyList(),
    val pending: List<Pending> = emptyList(),
    val packs: List<Pack> = emptyList(),
    val visits: List<Visit> = emptyList(),
    val trips: List<Trip> = emptyList(),
    val settings: Settings = Settings(),
    /** clientId или «p:»+placeId → время входа в зону. */
    val open: Map<String, Long> = emptyMap(),
    /** Время последней фоновой проверки места (страховка, если геозона не сработала). */
    val lastPoll: Long? = null,
) {
    fun client(id: String) = clients.firstOrNull { it.id == id }
    fun place(id: String) = places.firstOrNull { it.id == id }
    val openTrip get() = trips.lastOrNull { it.end == null }
}
