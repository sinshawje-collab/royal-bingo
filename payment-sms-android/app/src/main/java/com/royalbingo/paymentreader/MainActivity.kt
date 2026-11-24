package com.royalbingo.paymentreader

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.*
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    private lateinit var pairedView: TextView
    private lateinit var accessView: TextView
    private lateinit var listenerView: TextView
    private lateinit var lastSeenView: TextView
    private lateinit var paymentView: TextView
    private lateinit var serverView: TextView
    private lateinit var codeInput: EditText
    private lateinit var connectButton: Button
    private lateinit var prefs: android.content.SharedPreferences

    private val navy = Color.rgb(17, 14, 42)
    private val purple = Color.rgb(45, 25, 86)
    private val gold = Color.rgb(255, 190, 55)
    private val muted = Color.rgb(190, 184, 211)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("rb_reader", MODE_PRIVATE)
        window.statusBarColor = navy
        window.navigationBarColor = navy
        window.decorView.systemUiVisibility = 0

        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(16), dp(18), dp(24))
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(navy, Color.rgb(29, 19, 57))).apply { cornerRadius = 0f }
        }
        val scroll = ScrollView(this).apply { isFillViewport = true; addView(page) }

        val brand = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(18), dp(16), dp(18))
            background = rounded(purple, 22)
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.royal_bingo_logo)
            scaleType = ImageView.ScaleType.FIT_CENTER
            background = rounded(Color.rgb(65, 39, 104), 18)
            setPadding(dp(3), dp(3), dp(3), dp(3))
            contentDescription = "Royal Bingo official logo"
        }
        brand.addView(logo, LinearLayout.LayoutParams(dp(88), dp(88)))
        val brandText = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, 0, 0) }
        brandText.addView(label("ROYAL BINGO", 22f, Color.WHITE, true))
        brandText.addView(label("PAYMENT READER", 12f, gold, true))
        brandText.addView(label("SECURE DEVICE LINK", 10f, muted, false))
        brand.addView(brandText)
        page.addView(brand)

        page.addView(label("Welcome back", 23f, Color.WHITE, true).apply { setPadding(dp(2), dp(22), 0, dp(4)) })
        page.addView(label("Monitor incoming TeleBirr and CBE Birr payment alerts.", 13f, muted, false).apply { setPadding(dp(2), 0, 0, dp(16)) })

        val connectionCard = card()
        connectionCard.addView(sectionTitle("DEVICE CONNECTION"))
        pairedView = statusLine()
        accessView = statusLine()
        listenerView = statusLine()
        connectionCard.addView(pairedView)
        connectionCard.addView(accessView)
        connectionCard.addView(listenerView)
        page.addView(connectionCard)

        val pairingCard = card()
        pairingCard.addView(sectionTitle("PAIR YOUR DEVICE"))
        pairingCard.addView(label("Enter the 8-character code generated inside the Royal Bingo admin panel.", 12f, muted, false).apply { setPadding(0, 0, 0, dp(10)) })
        codeInput = EditText(this).apply {
            hint = "ENTER PAIRING CODE"
            setHintTextColor(Color.rgb(130, 120, 155))
            setTextColor(Color.WHITE)
            textSize = 17f
            gravity = Gravity.CENTER
            letterSpacing = 0.16f
            isSingleLine = true
            inputType = 0x00000001 or 0x00001000
            background = rounded(Color.rgb(35, 27, 61), 13, Color.rgb(91, 69, 132))
            setPadding(dp(12), dp(13), dp(12), dp(13))
        }
        pairingCard.addView(codeInput, LinearLayout.LayoutParams(-1, dp(52)))
        connectButton = actionButton("CONNECT TO ROYAL BINGO", true)
        connectButton.setOnClickListener { pairDevice() }
        pairingCard.addView(connectButton, LinearLayout.LayoutParams(-1, dp(50)).apply { topMargin = dp(12) })
        page.addView(pairingCard)

        val activityCard = card()
        activityCard.addView(sectionTitle("PAYMENT ACTIVITY"))
        lastSeenView = detailLine("Last notification", "Not yet")
        paymentView = detailLine("Payment detected", "Waiting for a new payment alert")
        serverView = detailLine("Server response", "No payment sent yet")
        activityCard.addView(lastSeenView)
        activityCard.addView(paymentView)
        activityCard.addView(serverView)
        page.addView(activityCard)

        val settingsButton = actionButton("OPEN NOTIFICATION ACCESS SETTINGS", false)
        settingsButton.setOnClickListener { startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
        page.addView(settingsButton, LinearLayout.LayoutParams(-1, dp(50)).apply { topMargin = dp(4) })
        val refreshButton = TextView(this).apply {
            text = "↻   Refresh status"
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(gold)
            setPadding(0, dp(15), 0, dp(12))
            setOnClickListener { refreshStatus() }
        }
        page.addView(refreshButton)
        page.addView(label("PRIVACY & SECURITY", 11f, gold, true).apply { setPadding(dp(2), dp(14), 0, dp(6)) })
        page.addView(label("Only payment provider, received amount, transaction ID and time are forwarded securely. Full message text and OTP codes are not uploaded. Wallet deposits still require admin approval.", 12f, muted, false))
        page.addView(label("Important: your SMS app must show new payment notifications. This app cannot recover notifications received before it was installed or enabled.", 11f, muted, false).apply { setPadding(0, dp(12), 0, 0) })

        setContentView(scroll)
        refreshStatus()
    }

    private fun pairDevice() {
        val code = codeInput.text.toString().trim().uppercase(Locale.ROOT)
        if (!code.matches(Regex("[A-Z0-9]{8}"))) { toast("Enter a valid 8-character pairing code"); return }
        connectButton.isEnabled = false
        connectButton.text = "CONNECTING..."
        Thread {
            try {
                val conn = java.net.URL("https://royal-bingo-9u3a.onrender.com/api/payment-device/enroll").openConnection() as java.net.HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 15000
                conn.readTimeout = 15000
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write("""{"pairing_code":"$code","label":"Samsung S22 Ultra"}""".toByteArray()) }
                val status = conn.responseCode
                val response = (if (status in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
                val token = Regex("\"device_token\"\\s*:\\s*\"([^\"]+)\"").find(response)?.groupValues?.get(1)
                if (token != null) {
                    prefs.edit().putString("device_token", token).apply()
                    runOnUiThread { toast("Device connected successfully"); refreshStatus() }
                } else runOnUiThread { toast("Pairing failed (HTTP $status): $response") }
                conn.disconnect()
            } catch (e: Exception) { runOnUiThread { toast("Connection error: ${e.message}") } }
            finally { runOnUiThread { connectButton.isEnabled = true; connectButton.text = "CONNECT TO ROYAL BINGO" } }
        }.start()
    }

    override fun onResume() { super.onResume(); if (::prefs.isInitialized) refreshStatus() }

    private fun refreshStatus() {
        val component = ComponentName(this, PaymentNotificationListener::class.java)
        val enabled = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")?.split(":")?.contains(component.flattenToString()) == true
        val paired = !prefs.getString("device_token", null).isNullOrBlank()
        val connected = prefs.getBoolean("listener_connected", false)
        val seen = prefs.getLong("last_notification_at", 0L)
        pairedView.text = (if (paired) "●  PAIRED" else "●  NOT PAIRED")
        pairedView.setTextColor(if (paired) Color.rgb(112, 230, 163) else Color.rgb(255, 125, 125))
        accessView.text = if (enabled) "●  NOTIFICATION ACCESS  ·  ON" else "●  NOTIFICATION ACCESS  ·  OFF"
        accessView.setTextColor(if (enabled) Color.rgb(112, 230, 163) else Color.rgb(255, 125, 125))
        listenerView.text = if (connected) "●  LISTENER SERVICE  ·  CONNECTED" else "●  LISTENER SERVICE  ·  WAITING"
        listenerView.setTextColor(if (connected) Color.rgb(112, 230, 163) else gold)
        lastSeenView.text = "Last notification\n" + if (seen > 0) formatTime(seen) else "Not yet"
        paymentView.text = "Payment detected\n" + prefs.getString("last_payment_status", "Waiting for a new payment alert")
        serverView.text = "Server response\n" + prefs.getString("last_server_status", "No payment sent yet")
    }

    private fun card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(16), dp(16), dp(16))
        background = rounded(Color.rgb(37, 28, 65), 19)
        val p = LinearLayout.LayoutParams(-1, -2); p.bottomMargin = dp(14); layoutParams = p
    }
    private fun sectionTitle(s: String) = label(s, 11f, gold, true).apply { setPadding(0, 0, 0, dp(12)) }
    private fun statusLine() = label("", 13f, Color.WHITE, true).apply { setPadding(0, dp(5), 0, dp(5)) }
    private fun detailLine(title: String, value: String) = label("$title\n$value", 12f, muted, false).apply { setPadding(0, dp(7), 0, dp(7)) }
    private fun label(s: String, size: Float, color: Int, bold: Boolean) = TextView(this).apply {
        text = s; textSize = size; setTextColor(color)
        if (bold) typeface = Typeface.create("sans-serif", Typeface.BOLD)
    }
    private fun actionButton(s: String, primary: Boolean) = Button(this).apply {
        text = s; textSize = 12f; isAllCaps = false
        setTextColor(if (primary) navy else Color.WHITE)
        background = rounded(if (primary) gold else Color.rgb(67, 48, 101), 14)
        typeface = Typeface.create("sans-serif", Typeface.BOLD)
        stateListAnimator = null
    }
    private fun rounded(color: Int, radius: Int, stroke: Int? = null) = GradientDrawable().apply {
        setColor(color); cornerRadius = dp(radius).toFloat()
        if (stroke != null) setStroke(dp(1), stroke)
    }
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun formatTime(seconds: Long) = SimpleDateFormat("MMM d, HH:mm:ss", Locale.getDefault()).format(Date(seconds * 1000))
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
