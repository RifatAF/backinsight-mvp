package com.rifat.trainercount.data

import android.content.Context
import com.rifat.trainercount.logic.AppData
import com.rifat.trainercount.logic.Client
import com.rifat.trainercount.logic.Pack
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
                    put("radius", c.radius); put("sessionMin", c.sessionMin); put("minMin", c.minMin)
                    put("price", c.price); put("archived", c.archived); put("createdAt", c.createdAt)
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
        })
        put("open", JSONObject().apply { d.open.forEach { (k, v) -> put(k, v) } })
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
            ),
            open = open?.keys()?.asSequence()?.associateWith { open.getLong(it) } ?: emptyMap(),
        )
    }
}
