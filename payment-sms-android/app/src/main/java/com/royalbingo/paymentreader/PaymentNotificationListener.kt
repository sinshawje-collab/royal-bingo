package com.royalbingo.paymentreader

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.os.Bundle
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class PaymentNotificationListener : NotificationListenerService() {
    override fun onListenerConnected() {
        super.onListenerConnected()
        getSharedPreferences("rb_reader", MODE_PRIVATE).edit()
            .putBoolean("listener_connected", true)
            .putLong("listener_connected_at", System.currentTimeMillis() / 1000)
            .apply()
    }

    override fun onListenerDisconnected() {
        getSharedPreferences("rb_reader", MODE_PRIVATE).edit()
            .putBoolean("listener_connected", false)
            .apply()
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val prefs = getSharedPreferences("rb_reader", MODE_PRIVATE)
        prefs.edit()
            .putLong("last_notification_at", System.currentTimeMillis() / 1000)
            .putString("last_notification_app", sbn.packageName)
            .apply()

        val extras = sbn.notification.extras ?: return
        val parts = mutableListOf<String>()
        listOf("android.title", "android.bigTitle", "android.text", "android.bigText", "android.subText", "android.summaryText")
            .forEach { key -> extras.getCharSequence(key)?.toString()?.takeIf { it.isNotBlank() }?.let(parts::add) }
        extras.getCharSequenceArray("android.textLines")?.forEach { it?.toString()?.let(parts::add) }
        @Suppress("DEPRECATION")
        val messages = extras.getParcelableArray("android.messages")
        messages?.forEach { item ->
            if (item is Bundle) item.getCharSequence("text")?.toString()?.let(parts::add)
        }

        val parsed = parsePayment(parts.joinToString(" ")) ?: return
        prefs.edit().putString("last_payment_status", "Recognized ${parsed.optString("provider")} payment (${parsed.optString("amount")} ETB)").apply()
        val token = prefs.getString("device_token", null) ?: run {
            prefs.edit().putString("last_server_status", "Not paired").apply()
            return
        }
        Thread { send(parsed, token) }.start()
    }

    private fun parsePayment(text: String): JSONObject? {
        val lower = text.lowercase(Locale.ROOT)
        val provider = when {
            lower.contains("telebirr") || lower.contains("ethio telecom") -> "TeleBirr"
            lower.contains("cbe birr") || lower.contains("commercial bank of ethiopia") -> "CBE Birr"
            else -> return null
        }
        if (lower.contains("airtime") || lower.contains("top-up") || lower.contains("top up")) return null

        // Accept common incoming-payment wording from both providers, including ETB/Birr/Br formats.
        // A sender-side "sent to" alert is also parsed; the server only matches it to an exact
        // pending deposit by transaction ID, amount and selected provider before admin approval.
        val amountMatch =
            Regex("(?i)you have received\\s+(?:ETB\\s*)?([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)?").find(text)
                ?: Regex("(?i)you received\\s+([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)").find(text)
                ?: Regex("(?i)received\\s+(?:ETB|Birr|Br\\.?)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)").find(text)
                ?: Regex("(?i)you have sent\\s+([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)\\s+to\\s+").find(text)
                ?: Regex("(?i)you sent\\s+([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)\\s+to\\s+").find(text)
                ?: return null
        val amount = amountMatch.groupValues[1].replace(",", "").toDoubleOrNull() ?: return null

        val tx = Regex("(?i)(?:transaction\\s*(?:number|id|no\\.?|#)?|txn\\s*(?:id|no\\.?)?)\\s*(?:is|:|#)?\\s*([A-Z0-9-]{6,32})").find(text)
            ?: Regex("(?i)\\bTID=([A-Z0-9-]{6,32})").find(text)
            ?: Regex("(?i)transaction[^A-Z0-9]{0,20}([A-Z0-9]{6,32})").find(text)
            ?: return null
        val txid = tx.groupValues[1].uppercase(Locale.ROOT)
        if (amount <= 0 || txid.length < 6) return null
        return JSONObject().put("provider", provider).put("amount", amount)
            .put("transaction_id", txid).put("received_at", System.currentTimeMillis() / 1000)
    }

    private fun send(data: JSONObject, token: String) {
        var result = "Send failed"
        try {
            val c = (URL("https://royal-bingo-9u3a.onrender.com/api/payment-device/notification").openConnection() as HttpURLConnection)
            c.requestMethod = "POST"
            c.connectTimeout = 12000
            c.readTimeout = 12000
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setRequestProperty("Authorization", "Bearer $token")
            c.outputStream.use { it.write(data.toString().toByteArray(Charsets.UTF_8)) }
            val code = c.responseCode
            val responseText = (if (code in 200..299) c.inputStream else c.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            result = if (code in 200..299) {
                val payload = try { JSONObject(responseText) } catch (_: Exception) { JSONObject() }
                val match = payload.optString("match_status", "")
                when (match) {
                    "matched" -> "HTTP $code · PAYMENT MATCHED"
                    "details_mismatch" -> "HTTP $code · DETAILS MISMATCH"
                    "no_pending_transaction" -> "HTTP $code · NO PENDING REQUEST"
                    "duplicate" -> "HTTP $code · DUPLICATE EVENT"
                    else -> "HTTP $code · " + payload.optString("message", "RECEIVED")
                }
            } else {
                "HTTP $code · " + responseText.take(140)
            }
            c.disconnect()
        } catch (e: Exception) {
            result = "Network error: ${e.javaClass.simpleName}"
        }
        getSharedPreferences("rb_reader", MODE_PRIVATE).edit()
            .putString("last_server_status", result)
            .putLong("last_server_attempt_at", System.currentTimeMillis() / 1000)
            .apply()
    }
}
