package com.rifat.trainercount.data

import android.content.Context
import com.rifat.trainercount.logic.perPersonMigrate
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Change
import com.rifat.trainercount.logic.ChangeKind
import com.rifat.trainercount.logic.Client
import com.rifat.trainercount.logic.Rule
import com.rifat.trainercount.logic.Pack
import com.rifat.trainercount.logic.Pending
import com.rifat.trainercount.logic.Place
import com.rifat.trainercount.logic.Slot
import com.rifat.trainercount.logic.Personal
import com.rifat.trainercount.logic.Spot
import com.rifat.trainercount.logic.Stay
import com.rifat.trainercount.logic.Settings
import com.rifat.trainercount.logic.Trip
import com.rifat.trainercount.logic.Visit
import com.rifat.trainercount.logic.VisitKind
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Все данные в одном JSON-файле внутри приложения. Google автоматически бэкапит его на Диск. */
object Store {
    private lateinit var file: File
    private val _data = MutableStateFlow(AppData())
    val data: StateFlow<AppData> get() = _data
    private var loaded = false

    @Synchronized
    fun init(ctx: Context) {
        if (loaded) return
        file = File(ctx.filesDir, "data.json")
        _data.value = runCatching { fromJson(JSONObject(file.readText())) }.getOrDefault(AppData())
        loaded = true
    }

    @Synchronized
    fun update(f: (AppData) -> AppData): AppData {
        val nd = f(_data.value)
        if (nd != _data.value) {
            val tmp = File(file.parentFile, "data.json.tmp")
            tmp.writeText(toJson(nd).toString())
            tmp.renameTo(file)
            _data.value = nd
        }
        return nd
    }

    fun exportJson(): String = toJson(_data.value).toString(2)

    fun importJson(text: String) {
        val d = fromJson(JSONObject(text))
        update { d }
    }

    // ---------- JSON ----------

    private fun strings(a: JSONArray?) = (0 until (a?.length() ?: 0)).map { a!!.getString(it) }
    private fun arr(l: List<String>) = JSONArray().apply { l.forEach { put(it) } }
    private fun JSONObject.dOpt(k: String) = if (has(k) && !isNull(k)) getDouble(k) else null
    private fun JSONObject.lOpt(k: String) = if (has(k) && !isNull(k)) getLong(k) else null
    private fun JSONObject.bOpt(k: String) = if (has(k) && !isNull(k)) getBoolean(k) else null
    private inline fun <T> JSONArray?.list(f: (JSONObject) -> T) =
        (0 until (this?.length() ?: 0)).map { f(this!!.getJSONObject(it)) }

    fun toJson(d: AppData): JSONObject = JSONObject().apply {
        put("v", 1)
        put("clients", JSONArray().apply {
            d.clients.forEach { c ->
                put(JSONObject().apply {
                    put("id", c.id); put("name", c.name); put("members", arr(c.members))
                    put("address", c.address); put("lat", c.lat ?: JSONObject.NULL); put("lng", c.lng ?: JSONObject.NULL)
                    put("radius", c.radius); put("sessionMin", c.sessionMin); put("minMin", c.minMin); put("pp2", true)
                    put("price", c.price); put("archived", c.archived); put("createdAt", c.createdAt)
                    put("placeId", c.placeId ?: JSONObject.NULL)
                    put("perMember", c.perMember)
                    put("slots", JSONArray().apply {
                        c.slots.forEach { sl -> put(JSONObject().apply { put("day", sl.day); put("min", sl.min) }) }
                    })
                })
            }
        })
        put("places", JSONArray().apply {
            d.places.forEach { p ->
                put(JSONObject().apply {
                    put("id", p.id); put("name", p.name); put("lat", p.lat); put("lng", p.lng)
                    put("radius", p.radius); put("sessionMin", p.sessionMin); put("minMin", p.minMin)
                })
            }
        })
        put("pending", JSONArray().apply {
            d.pending.forEach { p ->
                put(JSONObject().apply {
                    put("id", p.id); put("placeId", p.placeId); put("start", p.start); put("end", p.end)
                    put("count", p.count); put("suggested", arr(p.suggested))
                })
            }
        })
        put("packs", JSONArray().apply {
            d.packs.forEach { p ->
                put(JSONObject().apply {
                    put("id", p.id); put("clientId", p.clientId); put("paidAt", p.paidAt)
                    put("sessions", p.sessions); put("amount", p.amount)
                })
            }
        })
        put("visits", JSONArray().apply {
            d.visits.forEach { v ->
                put(JSONObject().apply {
                    put("id", v.id); put("clientId", v.clientId); put("start", v.start); put("end", v.end)
                    put("count", v.count); put("kind", v.kind.name); put("members", arr(v.members)); put("check", v.check)
                })
            }
        })
        put("trips", JSONArray().apply {
            d.trips.forEach { t ->
                put(JSONObject().apply {
                    put("id", t.id); put("start", t.start); put("end", t.end ?: JSONObject.NULL)
                    put("work", t.work ?: JSONObject.NULL)
                })
            }
        })
        val s = d.settings
        put("settings", JSONObject().apply {
            put("homeLat", s.homeLat ?: JSONObject.NULL); put("homeLng", s.homeLng ?: JSONObject.NULL)
            put("homeRadius", s.homeRadius); put("homeSsid", s.homeSsid)
            put("startMin", s.startMin); put("endMin", s.endMin)
            put("days", JSONArray().apply { s.days.sorted().forEach { put(it) } })
            put("setupDone", s.setupDone)
            put("eveningMin", s.eveningMin); put("eveningOn", s.eveningOn)
            put("alarmOn", s.alarmOn); put("alarmLead", s.alarmLead); put("alarmBefore", s.alarmBefore)
            put("soonOn", s.soonOn); put("soonLead", s.soonLead); put("travelMin", s.travelMin)
            put("alarmVolume", s.alarmVolume); put("alarmFade", s.alarmFade); put("alarmTone", s.alarmTone)
            put("alarmVibrate", s.alarmVibrate)
            put("tgOn", s.tgOn); put("tgToken", s.tgToken); put("tgChatId", s.tgChatId)
            put("tgChatName", s.tgChatName); put("tgMin", s.tgMin); put("tgAddress", s.tgAddress)
            put("tgBotOn", s.tgBotOn); put("tgAllowed", JSONArray(s.tgAllowed))
            put("aiKey", s.aiKey); put("aiModel", s.aiModel)
        })
        put("open", JSONObject().apply { d.open.forEach { (k, v) -> put(k, v) } })
        put("lastPoll", d.lastPoll ?: JSONObject.NULL)
        put("rules", JSONArray().apply {
            d.rules.forEach { r ->
                put(JSONObject().apply {
                    put("id", r.id); put("clientId", r.clientId); put("day", r.day); put("min", r.min)
                    put("parity", r.parity ?: JSONObject.NULL); put("manual", r.manual); put("off", r.off)
                })
            }
        })
        put("changes", JSONArray().apply {
            d.changes.forEach { c ->
                put(JSONObject().apply {
                    put("id", c.id); put("clientId", c.clientId); put("kind", c.kind.name)
                    put("date", c.date); put("min", c.min)
                    put("toDate", c.toDate ?: JSONObject.NULL); put("toMin", c.toMin ?: JSONObject.NULL)
                })
            }
        })
        d.stay?.let { s ->
            put("stay", JSONObject().apply {
                put("lat", s.lat); put("lng", s.lng); put("start", s.start); put("last", s.last)
                put("asked", s.asked); put("done", s.done)
            })
        }
        put("events", JSONArray().apply {
            d.events.forEach { e ->
                put(JSONObject().apply {
                    put("id", e.id); put("date", e.date); put("min", e.min); put("duration", e.duration); put("title", e.title)
                })
            }
        })
        put("ignored", JSONArray().apply {
            d.ignored.forEach { p -> put(JSONObject().apply { put("lat", p.lat); put("lng", p.lng); put("radius", p.radius) }) }
        })
    }

    fun fromJson(o: JSONObject): AppData {
        val s = o.optJSONObject("settings") ?: JSONObject()
        val def = Settings()
        val days = s.optJSONArray("days")
        val open = o.optJSONObject("open")
        return AppData(
            clients = o.optJSONArray("clients").list { c ->
                Client(
                    id = c.getString("id"), name = c.getString("name"), members = strings(c.optJSONArray("members")),
                    address = c.optString("address"), lat = c.dOpt("lat"), lng = c.dOpt("lng"),
                    radius = c.optInt("radius", 120), sessionMin = c.optInt("sessionMin", 60),
                    minMin = c.optInt("minMin", 30), price = c.optInt("price", 0),
                    archived = c.optBoolean("archived"), createdAt = c.optLong("createdAt"),
                    perMember = c.optBoolean("perMember", true),
                    placeId = if (c.has("placeId") && !c.isNull("placeId")) c.getString("placeId") else null,
                    slots = c.optJSONArray("slots").list { sl -> Slot(sl.getInt("day"), sl.getInt("min")) },
                ).let { if (c.has("pp2")) it else perPersonMigrate(it) }
            },
            places = o.optJSONArray("places").list { p ->
                Place(
                    id = p.getString("id"), name = p.getString("name"), lat = p.getDouble("lat"), lng = p.getDouble("lng"),
                    radius = p.optInt("radius", 100), sessionMin = p.optInt("sessionMin", 60), minMin = p.optInt("minMin", 30),
                )
            },
            pending = o.optJSONArray("pending").list { p ->
                Pending(
                    p.getString("id"), p.getString("placeId"), p.getLong("start"), p.getLong("end"),
                    p.getInt("count"), strings(p.optJSONArray("suggested")),
                )
            },
            packs = o.optJSONArray("packs").list { p ->
                Pack(p.getString("id"), p.getString("clientId"), p.getLong("paidAt"), p.getInt("sessions"), p.optInt("amount"))
            },
            visits = o.optJSONArray("visits").list { v ->
                Visit(
                    id = v.getString("id"), clientId = v.getString("clientId"), start = v.getLong("start"),
                    end = v.getLong("end"), count = v.getInt("count"),
                    kind = runCatching { VisitKind.valueOf(v.optString("kind")) }.getOrDefault(VisitKind.AUTO),
                    members = strings(v.optJSONArray("members")), check = v.optBoolean("check"),
                )
            },
            trips = o.optJSONArray("trips").list { t -> Trip(t.getString("id"), t.getLong("start"), t.lOpt("end"), t.bOpt("work")) },
            settings = Settings(
                homeLat = s.dOpt("homeLat"), homeLng = s.dOpt("homeLng"),
                homeRadius = s.optInt("homeRadius", def.homeRadius), homeSsid = s.optString("homeSsid"),
                startMin = s.optInt("startMin", def.startMin), endMin = s.optInt("endMin", def.endMin),
                days = if (days == null) def.days else (0 until days.length()).map { days.getInt(it) }.toSet(),
                setupDone = s.optBoolean("setupDone"),
                eveningMin = s.optInt("eveningMin", def.eveningMin), eveningOn = s.optBoolean("eveningOn", def.eveningOn),
                alarmOn = s.optBoolean("alarmOn", def.alarmOn), alarmLead = s.optInt("alarmLead", def.alarmLead),
                alarmBefore = s.optInt("alarmBefore", def.alarmBefore),
                soonOn = s.optBoolean("soonOn", def.soonOn), soonLead = s.optInt("soonLead", def.soonLead),
                travelMin = s.optInt("travelMin", def.travelMin),
                alarmVolume = s.optInt("alarmVolume", def.alarmVolume), alarmFade = s.optBoolean("alarmFade", def.alarmFade),
                alarmTone = s.optString("alarmTone"), alarmVibrate = s.optBoolean("alarmVibrate", def.alarmVibrate),
                tgOn = s.optBoolean("tgOn"), tgToken = s.optString("tgToken"), tgChatId = s.optString("tgChatId"),
                tgChatName = s.optString("tgChatName"), tgMin = s.optInt("tgMin", def.tgMin),
                tgAddress = s.optBoolean("tgAddress", def.tgAddress),
                tgBotOn = s.optBoolean("tgBotOn"), tgAllowed = strings(s.optJSONArray("tgAllowed")),
                aiKey = s.optString("aiKey"), aiModel = s.optString("aiModel"),
            ),
            open = open?.keys()?.asSequence()?.associateWith { open.getLong(it) } ?: emptyMap(),
            lastPoll = o.lOpt("lastPoll"),
            rules = o.optJSONArray("rules").list { r ->
                Rule(
                    r.getString("id"), r.getString("clientId"), r.getInt("day"), r.getInt("min"),
                    if (r.has("parity") && !r.isNull("parity")) r.getInt("parity") else null,
                    r.optBoolean("manual"), r.optBoolean("off"),
                )
            },
            changes = o.optJSONArray("changes").list { c ->
                Change(
                    c.getString("id"), c.getString("clientId"),
                    runCatching { ChangeKind.valueOf(c.getString("kind")) }.getOrDefault(ChangeKind.CANCEL),
                    c.getLong("date"), c.getInt("min"), c.lOpt("toDate"),
                    if (c.has("toMin") && !c.isNull("toMin")) c.getInt("toMin") else null,
                )
            },
            stay = o.optJSONObject("stay")?.let { s ->
                Stay(s.getDouble("lat"), s.getDouble("lng"), s.getLong("start"), s.getLong("last"), s.optBoolean("asked"), s.optBoolean("done"))
            },
            events = o.optJSONArray("events").list { e ->
                Personal(e.getString("id"), e.getLong("date"), e.getInt("min"), e.optInt("duration", 60), e.optString("title"))
            },
            ignored = o.optJSONArray("ignored").list { p -> Spot(p.getDouble("lat"), p.getDouble("lng"), p.optInt("radius", 150)) },
        )
    }
}
