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
    /** Чат клиента с ботом в Telegram (подключился по своей ссылке-приглашению). */
    val tgChatId: String = "",
    /** Платит за каждое занятие отдельно, а не пакетом: считаем, сколько проведено и не оплачено. */
    val perVisit: Boolean = false,
    /** Bybit UID, e-mail или телефон, с которого клиент переводит USDT. */
    val payId: String = "",
    /** Оплату подтверждать автоматически по переводам в Bybit. */
    val autoPay: Boolean = false,
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
    /** Вечерняя сводка на завтра, минуты от полуночи. */
    val eveningMin: Int = 21 * 60 + 30,
    val eveningOn: Boolean = true,
    /** Будильник за alarmLead минут до первой тренировки, если она раньше alarmBefore. */
    val alarmOn: Boolean = true,
    val alarmLead: Int = 45,
    val alarmBefore: Int = 12 * 60,
    /** Тихое напоминание за soonLead минут до тренировки (кроме той, перед которой звонит будильник). */
    val soonOn: Boolean = true,
    val soonLead: Int = 30,
    /** Запас на дорогу между занятиями при поиске окна. */
    val travelMin: Int = 30,
    /** Сверх маршрута на метро и пешком: на опоздание транспорта и подъём. */
    val travelExtra: Int = 10,
    /** Громкость будильника 5..100 % от громкости будильников телефона, плавное нарастание, мелодия, вибрация. */
    val alarmVolume: Int = 50,
    val alarmFade: Boolean = true,
    val alarmTone: String = "",
    val alarmVibrate: Boolean = true,
    /** Telegram: бот отправляет план на завтра в чат tgChatId в tgMin. */
    val tgOn: Boolean = false,
    val tgToken: String = "",
    val tgChatId: String = "",
    val tgChatName: String = "",
    val tgMin: Int = 21 * 60 + 30,
    val tgAddress: Boolean = true,
    /** Бот отвечает на сообщения: план дня, личные дела, где сейчас. */
    val tgBotOn: Boolean = false,
    /** Чаты, которым бот отвечает, кроме tgChatId. */
    val tgAllowed: List<String> = emptyList(),
    /** Ключ Gemini (бесплатный, из Google AI Studio): помогает боту понять свободные фразы. */
    val aiKey: String = "",
    /** Модель Gemini, выбранная при проверке ключа. */
    val aiModel: String = "",
    /** Имя бота (@…) для ссылок-приглашений клиентам. */
    val tgBotName: String = "",
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
    /** Расписание: постоянные занятия (выученные по визитам и заданные вручную). */
    val rules: List<Rule> = emptyList(),
    /** Разовые изменения расписания: отмены, переносы, дополнительные занятия. */
    val changes: List<Change> = emptyList(),
    /** Где стоим сейчас вне дома и зон клиентов: после часа спросим, было ли занятие. */
    val stay: Stay? = null,
    /** Места, где не спрашивать (магазины, торговые центры). */
    val ignored: List<Spot> = emptyList(),
    /** Личные дела в общем расписании: стоматолог, встреча. Добавляются и через Telegram. */
    val events: List<Personal> = emptyList(),
) {
    fun client(id: String) = clients.firstOrNull { it.id == id }
    fun place(id: String) = places.firstOrNull { it.id == id }
    val openTrip get() = trips.lastOrNull { it.end == null }
}

/**
 * Постоянное занятие: день недели и время начала (кратно 30 минутам).
 * parity: null каждую неделю, 0 или 1: только чётные или нечётные недели (раз в две недели).
 * manual: задано вручную, автоматика его не трогает. off: выученное занятие, которое убрали руками.
 */
data class Rule(
    val id: String,
    val clientId: String,
    val day: Int,
    val min: Int,
    val parity: Int? = null,
    val manual: Boolean = false,
    val off: Boolean = false,
)

enum class ChangeKind { CANCEL, MOVE, EXTRA }

/**
 * Разовое изменение. CANCEL и MOVE относятся к занятию date/min по расписанию; MOVE ставит его на toDate/toMin.
 * EXTRA: дополнительное занятие date/min. date и toDate: номер дня (LocalDate.toEpochDay).
 */
data class Change(
    val id: String,
    val clientId: String,
    val kind: ChangeKind,
    val date: Long,
    val min: Int,
    val toDate: Long? = null,
    val toMin: Int? = null,
)

data class Stay(
    val lat: Double,
    val lng: Double,
    val start: Long,
    val last: Long,
    val asked: Boolean = false,
    /** Ответили на вопрос: больше не спрашивать, пока не уйдём. */
    val done: Boolean = false,
)

data class Spot(val lat: Double, val lng: Double, val radius: Int = 150)

/** Личное дело: date — день (epochDay), min — начало в минутах от полуночи. went: был ли (null: ещё не спрашивали). */
data class Personal(val id: String, val date: Long, val min: Int, val duration: Int, val title: String, val went: Boolean? = null)
