package com.royalbingo.paymentreader

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

object PaymentStore {
    private const val PREFS = "rb_reader"
    private const val HISTORY = "payment_history"
    private const val LIMIT = 50

    @Synchronized
    fun recordDetected(context: Context, data: JSONObject) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val tx = data.optString("transaction_id")
        val items = read(prefs.getString(HISTORY, "[]"))
        val existing = JSONArray()
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            if (item.optString("transaction_id") != tx) existing.put(item)
        }
        val event = JSONObject(data.toString())
            .put("local_status", "SENDING")
            .put("server_status", "Waiting for server")
        existing.put(event)
        prefs.edit().putString(HISTORY, trim(existing).toString()).apply()
    }

    @Synchronized
    fun updateResult(context: Context, transactionId: String, result: String, matchStatus: String) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val items = read(prefs.getString(HISTORY, "[]"))
        val updated = JSONArray()
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            if (item.optString("transaction_id") == transactionId) {
                item.put("server_status", result)
                item.put("local_status", matchStatus.ifBlank { "SENT" })
            }
            updated.put(item)
        }
        prefs.edit().putString(HISTORY, updated.toString()).apply()
    }

    fun events(context: Context): JSONArray {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return read(prefs.getString(HISTORY, "[]"))
    }

    @Synchronized
    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .remove(HISTORY).apply()
    }

    private fun read(raw: String?): JSONArray = try { JSONArray(raw ?: "[]") } catch (_: Exception) { JSONArray() }

    private fun trim(items: JSONArray): JSONArray {
        val result = JSONArray()
        val start = (items.length() - LIMIT).coerceAtLeast(0)
        for (i in start until items.length()) result.put(items.getJSONObject(i))
        return result
    }
}
