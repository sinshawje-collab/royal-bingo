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

        val notificationText = parts.joinToString(" ").replace(Regex("\\s+"), " ").trim()
        val parsed = parsePayment(notificationText, sbn.packageName) ?: run {
            // Keep a useful diagnostic without saving or uploading the notification's private text.
            prefs.edit().putString(
                "last_payment_status",
                "Notification seen, but not recognized as an incoming payment (" + sbn.packageName + ")"
            ).apply()
            return
        }
        prefs.edit().putString("last_payment_status", "Recognized ${parsed.optString("provider")} payment (${parsed.optString("amount")} ETB)").apply()
        PaymentStore.recordDetected(this, parsed)
        val token = prefs.getString("device_token", null) ?: run {
            prefs.edit().putString("last_server_status", "Not paired").apply()
            PaymentStore.updateResult(this, parsed.optString("transaction_id"), "Not paired", "NOT PAIRED")
            return
        }
        Thread { send(parsed, token) }.start()
    }

    private fun parsePayment(text: String, packageName: String): JSONObject? {
        val lower = text.lowercase(Locale.ROOT)
        val pkg = packageName.lowercase(Locale.ROOT)
        // Some TeleBirr notification layouts omit the brand from the visible message.
        // Trust the app identity only when it is explicitly the TeleBirr package.
        val provider = when {
            lower.contains("telebirr") || lower.contains("ethio telecom") || pkg.contains("telebirr") -> "TeleBirr"
            lower.contains("cbe birr") || lower.contains("commercial bank of ethiopia") -> "CBE Birr"
            else -> return null
        }
        if (lower.contains("airtime") || lower.contains("top-up") || lower.contains("top up")) return null
        if (lower.contains("debited") || lower.contains("withdrawn") || lower.contains("sent to") ||
            lower.contains("you sent") || lower.contains("transfer sent") || lower.contains("payment sent")) return null

        val amountPatterns = listOf(
            Regex("(?i)you\\s+have\\s+received\\s+(?:an\\s+amount\\s+of\\s*)?(?:ETB\\s*)?([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)?"),
            Regex("(?i)you\\s+received\\s+(?:ETB\\s*)?([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)?"),
            Regex("(?i)received\\s+(?:ETB|Birr|Br\\.?)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)"),
            Regex("(?i)(?:credited|credit)\\s+(?:with|to\\s+your\\s+account)\\s*(?:ETB|Birr|Br\\.?)?\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)"),
            Regex("(?i)(?:amount|value)\\s*[:=]?\\s*(?:ETB|Birr|Br\\.?)?\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)")
        )
        val amountMatch = amountPatterns.firstNotNullOfOrNull { it.find(text) } ?: return null
        val amount = amountMatch.groupValues[1].replace(",", "").toDoubleOrNull() ?: return null

        val txPatterns = listOf(
            Regex("(?i)transaction\\s*(?:number|no\\.?|id|#)\\s*(?:is|:|#)?\\s*([A-Z0-9-]{6,32})"),
            Regex("(?i)txn\\s*(?:id|no\\.?|number)?\\s*(?:is|:|#)?\\s*([A-Z0-9-]{6,32})"),
            Regex("(?i)\\bTID\\s*[:=#-]\\s*([A-Z0-9-]{6,32})"),
            Regex("(?i)(?:reference|ref)\\s*(?:number|no\\.?|id|#)?\\s*(?:is|:|#)?\\s*([A-Z0-9-]{6,32})")
        )
        val tx = txPatterns.firstNotNullOfOrNull { it.find(text) } ?: return null
        val txid = tx.groupValues[1].trim().trimEnd('.', ',', ';', ':').uppercase(Locale.ROOT)
        if (amount <= 0 || txid.length < 6) return null
        return JSONObject().put("provider", provider).put("amount", amount)
            .put("transaction_id", txid).put("received_at", System.currentTimeMillis() / 1000)
    }

    private fun send(data: JSONObject, token: String) {
        var result = "Send failed"
        var matchStatus = "FAILED"
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
                matchStatus = match.uppercase(Locale.ROOT).ifBlank { "RECEIVED" }
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
        PaymentStore.updateResult(this, data.optString("transaction_id"), result, matchStatus)
    }
}
