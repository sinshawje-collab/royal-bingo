package com.royalbingo.paymentreader

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class PaymentNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val extras = sbn.notification.extras ?: return
        val title = extras.getCharSequence("android.title")?.toString().orEmpty()
        val body = listOf("android.text", "android.bigText", "android.subText")
            .mapNotNull { extras.getCharSequence(it)?.toString() }.joinToString(" ")
        val parsed = parsePayment("$title $body") ?: return
        val token = getSharedPreferences("rb_reader", MODE_PRIVATE).getString("device_token", null) ?: return
        Thread { send(parsed, token) }.start()
    }

    private fun parsePayment(text: String): JSONObject? {
        val lower = text.lowercase(Locale.ROOT)
        val provider = when {
            lower.contains("telebirr") || lower.contains("ethio telecom") -> "TeleBirr"
            lower.contains("cbe birr") || lower.contains("commercial bank of ethiopia") -> "CBE Birr"
            else -> return null
        }
        // The supplied CBE sample is an AIRTIME transfer, not a cash deposit.
        // Never send airtime, outgoing transfers, or top-ups to the deposit matcher.
        if (lower.contains("airtime") || lower.contains("top-up") || lower.contains("top up")) return null
        if (provider == "TeleBirr" && !(lower.contains("you have received") || lower.contains("received etb") || lower.contains("received birr"))) return null
        if (provider == "CBE Birr" && !(lower.contains("you have received") || lower.contains("received etb") || lower.contains("credited"))) return null
        val amountMatch = Regex("(?i)(?:ETB|Birr|Br\\.?)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)").find(text)
            ?: Regex("(?i)([0-9][0-9,]*(?:\\.[0-9]{1,2})?)\\s*(?:ETB|Birr|Br\\.?)").find(text)
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
        try {
            val c = (URL("https://royal-bingo-9u3a.onrender.com/api/payment-device/notification").openConnection() as HttpURLConnection)
            c.requestMethod = "POST"; c.connectTimeout = 12000; c.readTimeout = 12000; c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setRequestProperty("Authorization", "Bearer $token")
            c.outputStream.use { it.write(data.toString().toByteArray(Charsets.UTF_8)) }
            if (c.responseCode in 200..299) c.inputStream.close()
            c.disconnect()
        } catch (_: Exception) { }
    }
}
