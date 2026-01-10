package com.picomouse.imageclicker

import android.graphics.Bitmap
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.Rect
import org.opencv.imgproc.Imgproc
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

object TemplateMatcher {
    data class MatchResult(val score: Float, val x: Int, val y: Int)

    data class IntRect(val left: Int, val top: Int, val right: Int, val bottom: Int) {
        val width: Int = right - left
        val height: Int = bottom - top
    }

    fun bitmapToGray(bitmap: Bitmap): IntArray {
        val width = bitmap.width
        val height = bitmap.height
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        val gray = IntArray(width * height)
        for (i in pixels.indices) {
            val c = pixels[i]
            val r = (c shr 16) and 0xFF
            val g = (c shr 8) and 0xFF
            val b = c and 0xFF
            gray[i] = (0.299f * r + 0.587f * g + 0.114f * b).toInt()
        }
        return gray
    }

    fun bitmapToGrayMat(bitmap: Bitmap): Mat {
        val rgba = Mat(bitmap.height, bitmap.width, CvType.CV_8UC4)
        org.opencv.android.Utils.bitmapToMat(bitmap, rgba)
        val gray = Mat()
        Imgproc.cvtColor(rgba, gray, Imgproc.COLOR_RGBA2GRAY)
        rgba.release()
        return gray
    }

    fun matchTemplateOpenCv(
        screenGray: Mat,
        templateGray: Mat,
        roi: IntRect?
    ): MatchResult {
        val searchMat = if (roi != null) {
            if (roi.width <= 0 || roi.height <= 0) {
                return MatchResult(0f, 0, 0)
            }
            Mat(screenGray, Rect(roi.left, roi.top, roi.width, roi.height))
        } else {
            screenGray
        }

        val result = Mat()
        Imgproc.matchTemplate(searchMat, templateGray, result, Imgproc.TM_CCOEFF_NORMED)
        val mmr = Core.minMaxLoc(result)
        result.release()
        if (searchMat !== screenGray) {
            searchMat.release()
        }

        val x = (mmr.maxLoc.x + (roi?.left ?: 0)).toInt()
        val y = (mmr.maxLoc.y + (roi?.top ?: 0)).toInt()
        return MatchResult(mmr.maxVal.toFloat().coerceIn(0f, 1f), x, y)
    }

    fun matchTemplateSad(
        screenGray: IntArray,
        screenW: Int,
        screenH: Int,
        templateGray: IntArray,
        templateW: Int,
        templateH: Int,
        roi: IntRect?
    ): MatchResult {
        if (templateW <= 0 || templateH <= 0 || screenW < templateW || screenH < templateH) {
            return MatchResult(0f, 0, 0)
        }

        val maxSad = 255f * templateW * templateH
        var bestScore = 0f
        var bestX = 0
        var bestY = 0

        val left = roi?.left ?: 0
        val top = roi?.top ?: 0
        val right = min(screenW - templateW, (roi?.right ?: screenW) - templateW)
        val bottom = min(screenH - templateH, (roi?.bottom ?: screenH) - templateH)

        for (y in top..bottom) {
            val rowOffset = y * screenW
            for (x in left..right) {
                var sad = 0
                var tIndex = 0
                var sIndex = rowOffset + x
                for (ty in 0 until templateH) {
                    val sRow = sIndex
                    for (tx in 0 until templateW) {
                        sad += abs(screenGray[sRow + tx] - templateGray[tIndex])
                        tIndex++
                    }
                    sIndex += screenW
                }
                val score = 1f - (sad / maxSad)
                if (score > bestScore) {
                    bestScore = score
                    bestX = x
                    bestY = y
                }
            }
        }
        return MatchResult(bestScore.coerceIn(0f, 1f), bestX, bestY)
    }
}
