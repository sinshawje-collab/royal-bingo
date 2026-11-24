package com.royalbingo.paymentreader

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.widget.*
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var prefs: android.content.SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("rb_reader", MODE_PRIVATE)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 28, 28, 24)
        }
        root.addView(TextView(this).apply {
            text = "Royal Bingo Payment Reader"
            textSize = 24f
            setPadding(0, 0, 0, 8)
        })
        root.addView(TextView(this).apply {
            text = "TeleBirr እና CBE Birr የገቢ ክፍያ ማሳወቂያዎችን ብቻ ይመረምራል።"
            textSize = 16f
            setPadding(0, 0, 0, 16)
        })

        statusView = TextView(this).apply {
            textSize = 15f
            setPadding(14, 14, 14, 14)
        }
        root.addView(statusView)

        val code = EditText(this).apply {
            hint = "8-character pairing code"
            gravity = Gravity.CENTER
            textSize = 18f
            setSingleLine(true)
        }
        root.addView(code)

        root.addView(Button(this).apply {
            text = "Connect to Royal Bingo"
            setOnClickListener {
                val c = code.text.toString().trim().uppercase(Locale.ROOT)
                if (!c.matches(Regex("[A-Z0-9]{8}"))) {
                    toast("Enter the 8-character pairing code")
                    return@setOnClickListener
                }
                isEnabled = false
                Thread {
                    try {
                        val conn = (java.net.URL("https://royal-bingo-9u3a.onrender.com/api/payment-device/enroll").openConnection() as java.net.HttpURLConnection)
                        conn.requestMethod = "POST"
                        conn.doOutput = true
                        conn.connectTimeout = 15000
                        conn.readTimeout = 15000
                        conn.setRequestProperty("Content-Type", "application/json")
                        conn.outputStream.use {
                            it.write("""{"pairing_code":"$c","label":"Samsung S22 Ultra"}""".toByteArray())
                        }
                        val status = conn.responseCode
                        val response = (if (status in 200..299) conn.inputStream else conn.errorStream)
                            ?.bufferedReader()?.use { it.readText() }.orEmpty()
                        val token = Regex("\"device_token\"\\s*:\\s*\"([^\"]+)\"")
                            .find(response)?.groupValues?.get(1)
                        if (token != null) {
                            prefs.edit().putString("device_token", token).apply()
                            runOnUiThread { toast("Phone connected successfully") ; refreshStatus() }
                        } else {
                            runOnUiThread { toast("Pairing failed (HTTP $status): $response") }
                        }
                        conn.disconnect()
                    } catch (e: Exception) {
                        runOnUiThread { toast("Connection error: ${e.message}") }
                    } finally {
                        runOnUiThread { isEnabled = true }
                    }
                }.start()
            }
        })

        root.addView(Button(this).apply {
            text = "Open Notification Access Settings"
            setOnClickListener { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
        })
        root.addView(Button(this).apply {
            text = "Refresh connection status"
            setOnClickListener { refreshStatus() }
        })
        root.addView(TextView(this).apply {
            text = "ማስታወሻ፦ በዚህ አፕ Notifications ገጽ ላይ “All notifications from this app are blocked” ቢልም የSMS ማንበብን አይከለክልም። የሚያስፈልገው Notification Access ብቻ ነው። የSMS አፕህ የክፍያ ማሳወቂያውን ማሳየት አለበት።"
            textSize = 14f
            setPadding(0, 16, 0, 0)
        })
        setContentView(root)
        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        if (::statusView.isInitialized) refreshStatus()
    }

    private fun refreshStatus() {
        val component = ComponentName(this, PaymentNotificationListener::class.java)
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
            ?.split(":")?.contains(component.flattenToString()) == true
        val paired = !prefs.getString("device_token", null).isNullOrBlank()
        val connected = prefs.getBoolean("listener_connected", false)
        val lastSeen = prefs.getLong("last_notification_at", 0L)
        val lastPayment = prefs.getString("last_payment_status", "No payment notification received yet")
        val lastServer = prefs.getString("last_server_status", "No payment sent yet")
        val seenText = if (lastSeen > 0) formatTime(lastSeen) else "Not yet"
        statusView.text = buildString {
            append(if (paired) "Royal Bingo: PAIRED ✓" else "Royal Bingo: NOT PAIRED")
            append("\n")
            append(if (enabled) "Notification Access: ON ✓" else "Notification Access: OFF — enable it below")
            append("\n")
            append(if (connected) "Listener service: CONNECTED ✓" else "Listener service: waiting for Android")
            append("\nLast notification seen: $seenText")
            append("\nPayment parser: $lastPayment")
            append("\nServer result: $lastServer")
        }
    }

    private fun formatTime(seconds: Long): String =
        SimpleDateFormat("MMM d, HH:mm:ss", Locale.getDefault()).format(Date(seconds * 1000))

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
