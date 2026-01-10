package com.picomouse.imageclicker

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View

class CropOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private val rect = RectF()
    private val paint = Paint().apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        color = 0xFFFF0000.toInt()
    }

    private var startX = 0f
    private var startY = 0f

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (rect.width() > 0 && rect.height() > 0) {
            canvas.drawRect(rect, paint)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                startX = event.x
                startY = event.y
                rect.set(startX, startY, startX, startY)
                invalidate()
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                rect.set(
                    minOf(startX, event.x),
                    minOf(startY, event.y),
                    maxOf(startX, event.x),
                    maxOf(startY, event.y)
                )
                invalidate()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    fun getSelectionRect(): RectF {
        return RectF(rect)
    }
}
