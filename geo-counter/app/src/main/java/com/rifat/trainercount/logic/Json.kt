package com.rifat.trainercount.logic

/** Небольшой разбор JSON для ответов ИИ: объекты → Map, массивы → List, числа → Double. */
object Json {
    fun parse(text: String): Any? {
        // ИИ иногда оборачивает ответ в ```json … ``` или добавляет текст вокруг.
        val a = text.indexOf('{')
        val b = text.lastIndexOf('}')
        if (a < 0 || b < a) return null
        return runCatching { P(text.substring(a, b + 1)).value() }.getOrNull()
    }

    private class P(val s: String) {
        var i = 0
        fun ws() { while (i < s.length && s[i].isWhitespace()) i++ }
        fun value(): Any? {
            ws()
            return when (val c = s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> str()
                't' -> { i += 4; true }
                'f' -> { i += 5; false }
                'n' -> { i += 4; null }
                else -> if (c == '-' || c.isDigit()) num() else error("json")
            }
        }
        fun obj(): Map<String, Any?> {
            val m = LinkedHashMap<String, Any?>()
            i++; ws()
            if (s[i] == '}') { i++; return m }
            while (true) {
                ws(); val k = str(); ws(); i++ // ':'
                m[k] = value(); ws()
                if (s[i] == ',') { i++; continue }
                i++; return m
            }
        }
        fun arr(): List<Any?> {
            val l = mutableListOf<Any?>()
            i++; ws()
            if (s[i] == ']') { i++; return l }
            while (true) {
                l += value(); ws()
                if (s[i] == ',') { i++; continue }
                i++; return l
            }
        }
        fun str(): String {
            val sb = StringBuilder()
            i++
            while (s[i] != '"') {
                if (s[i] == '\\') {
                    i++
                    when (s[i]) {
                        'n' -> sb.append('\n'); 't' -> sb.append('\t'); 'r' -> {}
                        'u' -> { sb.append(s.substring(i + 1, i + 5).toInt(16).toChar()); i += 4 }
                        else -> sb.append(s[i])
                    }
                } else sb.append(s[i])
                i++
            }
            i++
            return sb.toString()
        }
        fun num(): Double {
            val st = i
            while (i < s.length && (s[i].isDigit() || s[i] in "+-.eE")) i++
            return s.substring(st, i).toDouble()
        }
    }
}

fun Any?.str(key: String): String? = ((this as? Map<*, *>)?.get(key) as? String)?.trim()?.takeIf { it.isNotEmpty() }
fun Any?.num(key: String): Double? = (this as? Map<*, *>)?.get(key).let { it as? Double ?: (it as? String)?.toDoubleOrNull() }
fun Any?.bool(key: String): Boolean = (this as? Map<*, *>)?.get(key) == true
fun Any?.list(key: String): List<Any?> = ((this as? Map<*, *>)?.get(key) as? List<*>).orEmpty()
fun Any?.obj(key: String): Any? = (this as? Map<*, *>)?.get(key) as? Map<*, *>

/** Строка для JSON внутри запроса. */
fun jsonStr(s: String) = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
