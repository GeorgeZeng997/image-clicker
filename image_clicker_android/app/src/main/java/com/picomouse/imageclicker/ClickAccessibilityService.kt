package com.picomouse.imageclicker

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent

class ClickAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // No-op
    }

    override fun onInterrupt() {
        // No-op
    }

    companion object {
        private var instance: ClickAccessibilityService? = null
        private val handler = Handler(Looper.getMainLooper())

        fun performClick(x: Int, y: Int, clickType: String, button: String, afterClick: (() -> Unit)? = null) {
            val service = instance ?: return
            val duration = if (button == "right") 500L else 50L
            if (clickType == "double") {
                service.dispatchTap(x, y, duration) {
                    handler.postDelayed({
                        service.dispatchTap(x, y, duration, afterClick)
                    }, 120L)
                }
            } else {
                service.dispatchTap(x, y, duration, afterClick)
            }
        }
    }

    private fun dispatchTap(x: Int, y: Int, durationMs: Long, onDone: (() -> Unit)? = null) {
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                onDone?.invoke()
            }
        }, null)
    }
}
