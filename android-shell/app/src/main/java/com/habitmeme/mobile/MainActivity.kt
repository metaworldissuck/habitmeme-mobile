package com.habitmeme.mobile

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import com.habitmeme.mobile.databinding.ActivityMainBinding
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding

    private val localBaseUrl = "http://127.0.0.1:8787"
    private val devBaseUrl = "http://10.0.2.2:8787"
    private val startCommand = "cd ~/storage/shared/hackathon/habitmeme-mobile && uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        bindActions()
        setupWebView()
        checkBackend()
    }

    private fun bindActions() {
        binding.retryButton.setOnClickListener { checkBackend() }
        binding.copyCommandButton.setOnClickListener { copyStartCommand() }
        binding.useDevButton.setOnClickListener { loadUrl(devBaseUrl) }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        binding.webView.settings.javaScriptEnabled = true
        binding.webView.settings.domStorageEnabled = true
        binding.webView.webChromeClient = WebChromeClient()
        binding.webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                if (url.startsWith(localBaseUrl) || url.startsWith(devBaseUrl)) {
                    return false
                }
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                return true
            }
        }
    }

    private fun checkBackend() {
        showHint(getString(R.string.status_checking))
        thread {
            val localOk = isHealthy("$localBaseUrl/health")
            runOnUiThread {
                if (localOk) {
                    loadUrl(localBaseUrl)
                } else {
                    showHint(getString(R.string.status_missing))
                }
            }
        }
    }

    private fun isHealthy(url: String): Boolean {
        return runCatching {
            val connection = URL(url).openConnection() as HttpURLConnection
            connection.connectTimeout = 3000
            connection.readTimeout = 3000
            connection.requestMethod = "GET"
            connection.connect()
            connection.responseCode in 200..299
        }.getOrDefault(false)
    }

    private fun loadUrl(baseUrl: String) {
        binding.hintContainer.isVisible = false
        binding.webView.isVisible = true
        binding.webView.loadUrl("$baseUrl/app")
    }

    private fun showHint(message: String) {
        binding.webView.isVisible = false
        binding.hintContainer.isVisible = true
        binding.statusText.text = message
        binding.commandText.text = startCommand
    }

    private fun copyStartCommand() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("Termux start command", startCommand))
    }
}

