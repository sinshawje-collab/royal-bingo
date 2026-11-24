package com.royalbingo.paymentreader

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.widget.*

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("rb_reader", MODE_PRIVATE)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(28, 32, 28, 24) }
        root.addView(TextView(this).apply { text = "Royal Bingo Payment Reader"; textSize = 23f })
        root.addView(TextView(this).apply { text = "TeleBirr እና CBE Birr የክፍያ ማሳወቂያዎችን ብቻ ይመረምራል።"; textSize = 16f; setPadding(0,16,0,18) })
        val code = EditText(this).apply { hint = "8-character pairing code"; gravity = Gravity.CENTER; textSize = 18f }
        root.addView(code)
        val pair = Button(this).apply { text = "Connect to Royal Bingo" }
        root.addView(pair)
        pair.setOnClickListener {
            val c = code.text.toString().trim().uppercase()
            if (c.length != 8) { toast("Enter the 8-character code"); return@setOnClickListener }
            Thread {
                try {
                    val conn = (java.net.URL("https://royal-bingo-9u3a.onrender.com/api/payment-device/enroll").openConnection() as java.net.HttpURLConnection)
                    conn.requestMethod = "POST"; conn.doOutput = true; conn.connectTimeout = 15000; conn.readTimeout = 15000
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.outputStream.use { it.write("""{"pairing_code":"$c","label":"Samsung S22 Ultra"}""".toByteArray()) }
                    val response = (if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream).bufferedReader().use { it.readText() }
                    val token = Regex("\"device_token\"\\s*:\\s*\"([^\"]+)\"").find(response)?.groupValues?.get(1)
                    if (token != null) {
                        prefs.edit().putString("device_token", token).apply()
                        runOnUiThread { toast("Phone connected. Enable notification access below.") }
                    } else runOnUiThread { toast("Pairing failed: " + response) }
                    conn.disconnect()
                } catch (e: Exception) { runOnUiThread { toast("Connection error: " + e.message) } }
            }.start()
        }
        root.addView(Button(this).apply { text = "Enable Notification Access"; setOnClickListener { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) } })
        root.addView(TextView(this).apply { text = "Important: enable access for this app. Android notification access only sees notifications that the SMS app displays. Do not hide payment SMS notifications."; textSize = 14f; setPadding(0,18,0,0) })
        root.addView(TextView(this).apply { text = if (prefs.getString("device_token", null) != null) "Device paired ✓" else "Device not paired"; textSize = 16f; setPadding(0,20,0,0) })
        setContentView(root)
    }
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
