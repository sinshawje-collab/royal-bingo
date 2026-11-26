package com.royalbingo.app;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.content.Intent;
import android.net.Uri;
import android.view.KeyEvent;
import android.view.Gravity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.widget.FrameLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private static final String HOME = "https://royal-bingo-9u3a.onrender.com/";
    private WebView webView;

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(android.graphics.Color.rgb(8, 3, 27));
        getWindow().setNavigationBarColor(android.graphics.Color.rgb(8, 3, 27));

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        TextView readerButton = new TextView(this);
        readerButton.setText("💳  PAYMENT READER");
        readerButton.setTextColor(Color.rgb(35, 20, 4));
        readerButton.setTextSize(12);
        readerButton.setTypeface(Typeface.create("sans-serif", Typeface.BOLD));
        readerButton.setGravity(Gravity.CENTER);
        readerButton.setPadding(dp(16), 0, dp(16), 0);
        GradientDrawable buttonBg = new GradientDrawable();
        buttonBg.setColor(Color.rgb(255, 194, 65));
        buttonBg.setCornerRadius(dp(18));
        readerButton.setBackground(buttonBg);
        readerButton.setElevation(dp(8));
        readerButton.setContentDescription("Open Royal Bingo Payment Reader");
        readerButton.setOnClickListener(v -> startActivity(
            new Intent(MainActivity.this, com.royalbingo.paymentreader.PaymentReaderActivity.class)));
        FrameLayout.LayoutParams readerParams = new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, dp(46), Gravity.BOTTOM | Gravity.END);
        readerParams.setMargins(0, 0, dp(14), dp(78));
        root.addView(readerButton, readerParams);

        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadsImagesAutomatically(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSafeBrowsingEnabled(true);

        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost() == null ? "" : uri.getHost();
                if ("royal-bingo-9u3a.onrender.com".equalsIgnoreCase(host)) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (Exception ignored) { }
                return true;
            }
        });
        webView.loadUrl(HOME);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
