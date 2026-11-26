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
        getWindow().setStatusBarColor(Color.rgb(8, 3, 27));
        getWindow().setNavigationBarColor(Color.rgb(8, 3, 27));

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

        webView.setFocusable(true);
        webView.setFocusableInTouchMode(true);
        webView.requestFocus();
        webView.setOnTouchListener((v, event) -> {
            v.requestFocusFromTouch();
            return false;
        });

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
                try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
                catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                String script =
                    "(function(){if(window.__rbAdminShell)return;window.__rbAdminShell=true;" +
                    "var login=document.getElementById('apkLogin');if(login&&!localStorage.getItem('royal_bingo_apk_token')){" +
                    "var guard=document.getElementById('startupGuard');if(guard)guard.style.setProperty('display','none','important');" +
                    "login.style.setProperty('display','flex','important');login.style.setProperty('z-index','2147483000','important');" +
                    "login.style.setProperty('pointer-events','auto','important');login.style.setProperty('touch-action','manipulation','important');" +
                    "login.querySelectorAll('input,button').forEach(function(el){el.style.setProperty('pointer-events','auto','important');el.style.setProperty('touch-action','manipulation','important');el.removeAttribute('disabled');});}" +
                    "var css='#appRoot{visibility:hidden!important}'+ " +
                    "'body.rb-admin-only #appRoot{visibility:visible!important}'+ " +
                    "'body.rb-admin-only #appRoot> :not(#tab-bingo):not(#financeRotationBox):not(#adminBroadcast){display:none!important}'+ " +
                    "'body.rb-admin-only #appRoot>#tab-bingo{display:block!important;padding:12px 8px 90px!important}'+ " +
                    "'body.rb-admin-only #appRoot>#tab-bingo> :not(#adminDashboard){display:none!important}'+ " +
                    "'body.rb-admin-only #appRoot #adminDashboard{display:block!important}';" +
                    "var st=document.createElement('style');st.id='rbAdminShellStyle';st.textContent=css;document.head.appendChild(st);" +
                    "function brandLogin(){var box=document.getElementById('apkLogin');if(!box)return;" +
                    "var h=box.querySelector('h2');if(h)h.textContent='ROYAL BINGO ADMIN';" +
                    "var p=box.querySelector('p');if(p&&p.textContent.indexOf('Standalone APK')>=0)p.textContent='ADMIN CONTROL CENTER · SECURE LOGIN';" +
                    "var labels=box.querySelectorAll('label');if(labels.length)labels[0].textContent='Admin ስልክ ቁጥር';" +
                    "var btn=box.querySelector('button');if(btn)btn.textContent='🔐  ADMIN LOGIN';" +
                    "var help=box.querySelector('p:last-child');if(help)help.textContent='Password ከBot ውስጥ /setadminpassword በመጠቀም ያዘጋጁ።';}" +
                    "var verified=false,checking=false;" +
                    "async function verifyAdmin(){if(verified||checking)return;var login=document.getElementById('apkLogin');" +
                    "if(login&&getComputedStyle(login).display!=='none')return;" +
                    "var token=localStorage.getItem('royal_bingo_apk_token')||'';if(!token)return;" +
                    "if(typeof apiReq!=='function')return;checking=true;" +
                    "try{var d=await apiReq('/api/state?stake=10');if(!d||d.is_admin!==true){" +
                    "localStorage.removeItem('royal_bingo_apk_token');document.body.classList.remove('rb-admin-only');" +
                    "var root=document.getElementById('appRoot');if(root)root.style.display='none';" +
                    "var dash=document.getElementById('adminDashboard');if(dash)dash.style.display='none';" +
                    "var msg=document.getElementById('apkLoginMsg');if(msg){msg.style.color='#fca5a5';msg.textContent='❌ Admin ፈቃድ ያለው መለያ ብቻ ይጠቀሙ።';}" +
                    "if(typeof showApkLogin==='function')showApkLogin();return;}" +
                    "if(typeof renderState==='function')renderState(d);window.adminSessionConfirmed=true;" +
                    "var dash=document.getElementById('adminDashboard');if(dash)dash.style.display='block';" +
                    "document.body.classList.add('rb-admin-only');var tabs=document.querySelectorAll('#appRoot .tab');" +
                    "tabs.forEach(function(t){t.classList.remove('on');});var tab=document.getElementById('tab-bingo');if(tab)tab.classList.add('on');verified=true;" +
                    "}catch(e){console.warn('Admin verification failed',e);}finally{checking=false;}}" +
                    "var oldLogin=window.apkLogin;if(typeof oldLogin==='function'&&!window.__rbLoginWrapped){" +
                    "window.__rbLoginWrapped=true;window.apkLogin=async function(){await oldLogin();setTimeout(verifyAdmin,250);};}" +
                    "brandLogin();var mo=new MutationObserver(function(){brandLogin();});" +
                    "mo.observe(document.documentElement,{subtree:true,childList:true});" +
                    "setInterval(verifyAdmin,700);setInterval(brandLogin,1500);verifyAdmin();" +
                    "})();";
                view.evaluateJavascript(script, null);
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
