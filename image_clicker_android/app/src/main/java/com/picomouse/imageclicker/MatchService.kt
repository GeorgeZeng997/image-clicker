package com.picomouse.imageclicker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.IntentCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.opencv.android.OpenCVLoader
import org.opencv.core.Mat
import java.util.Random
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

class MatchService : Service() {

    private val scope = CoroutineScope(Job() + Dispatchers.Default)
    private var projection: MediaProjection? = null
    private var captureHelper: ScreenCaptureHelper? = null
    private var matchingJob: Job? = null
    private val templateCache = mutableMapOf<String, TemplateBitmap>()
    private val lastMatchMap = mutableMapOf<String, MatchPoint>()
    private val random = Random()
    private lateinit var logStore: LogStore
    private var openCvReady = false

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
        logStore = LogStore(this)
        openCvReady = OpenCVLoader.initLocal()
        sendLog("Service created. OpenCV=$openCvReady")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                sendLog("Stop requested")
                stopSelf()
                return START_NOT_STICKY
            }
        }

        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, -1) ?: -1
        val data = intent?.let { IntentCompat.getParcelableExtra(it, EXTRA_DATA, Intent::class.java) }
        if (resultCode == -1 || data == null) {
            sendLog("Missing projection data")
            stopSelf()
            return START_NOT_STICKY
        }

        if (projection == null) {
            val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            projection = manager.getMediaProjection(resultCode, data)
            captureHelper = projection?.let { ScreenCaptureHelper(this, it) }
        }

        startForeground(NOTIFICATION_ID, buildNotification("Matching running"))
        startMatchingLoop()
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        projection?.stop()
        projection = null
        for (entry in templateCache.values) {
            entry.grayMat?.release()
        }
        templateCache.clear()
        matchingJob?.cancel()
        scope.cancel()
        sendLog("Service destroyed")
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startMatchingLoop() {
        matchingJob?.cancel()
        matchingJob = scope.launch {
            val store = TemplateStore(this@MatchService)
            while (isActive) {
                val helper = captureHelper
                if (helper == null) {
                    delay(SCAN_INTERVAL_MS)
                    continue
                }

                val templates = store.loadTemplates()
                val templateIds = templates.map { it.id }.toSet()
                pruneCache(templateIds)
                lastMatchMap.keys.retainAll(templateIds)

                if (templates.isEmpty()) {
                    sendStatus("No templates")
                    delay(SCAN_INTERVAL_MS)
                    continue
                }

                val screenshot = helper.captureOnce()
                if (screenshot == null) {
                    delay(SCAN_INTERVAL_MS)
                    continue
                }

                val scale = SCALE_FACTOR
                val scaledBitmap = if (scale < 1f) {
                    val scaledW = max(1, (screenshot.width * scale).roundToInt())
                    val scaledH = max(1, (screenshot.height * scale).roundToInt())
                    Bitmap.createScaledBitmap(screenshot, scaledW, scaledH, true)
                } else {
                    screenshot
                }

                val screenW = scaledBitmap.width
                val screenH = scaledBitmap.height
                val screenGrayArray = TemplateMatcher.bitmapToGray(scaledBitmap)
                val screenGrayMat = if (openCvReady) TemplateMatcher.bitmapToGrayMat(scaledBitmap) else null

                var matchCount = 0
                var stopAll = false

                for (item in templates) {
                    if (!item.enabled) {
                        continue
                    }
                    val templateBitmap = loadTemplateBitmap(item, scale)
                    if (templateBitmap == null) {
                        continue
                    }

                    val roi = buildRoi(lastMatchMap[item.id], screenW, screenH, templateBitmap.width, templateBitmap.height)

                    val result = try {
                        if (openCvReady && screenGrayMat != null && templateBitmap.grayMat != null) {
                            TemplateMatcher.matchTemplateOpenCv(screenGrayMat, templateBitmap.grayMat, roi)
                        } else {
                            TemplateMatcher.matchTemplateSad(
                                screenGrayArray,
                                screenW,
                                screenH,
                                templateBitmap.grayArray,
                                templateBitmap.width,
                                templateBitmap.height,
                                roi
                            )
                        }
                    } catch (ex: Exception) {
                        TemplateMatcher.matchTemplateSad(
                            screenGrayArray,
                            screenW,
                            screenH,
                            templateBitmap.grayArray,
                            templateBitmap.width,
                            templateBitmap.height,
                            roi
                        )
                    }

                    if (result.score >= item.threshold) {
                        matchCount++
                        lastMatchMap[item.id] = MatchPoint(result.x, result.y)

                        val matchX = if (scale < 1f) (result.x / scale).toInt() else result.x
                        val matchY = if (scale < 1f) (result.y / scale).toInt() else result.y

                        performClickForTemplate(
                            item,
                            matchX,
                            matchY,
                            templateBitmap.originalWidth,
                            templateBitmap.originalHeight
                        )
                        sendStatus("Last match: ${item.name} (${String.format("%.3f", result.score)})")
                        sendLog("Match ${item.name} score=${String.format("%.3f", result.score)}")
                        if (item.stopAllOnMatch) {
                            stopAll = true
                            break
                        }
                    }
                }

                screenGrayMat?.release()

                sendStatus("Matches: $matchCount")

                if (stopAll) {
                    sendLog("Stop all on match triggered")
                    stopSelf()
                    return@launch
                }

                delay(SCAN_INTERVAL_MS)
            }
        }
    }

    private suspend fun performClickForTemplate(
        item: TemplateItem,
        matchX: Int,
        matchY: Int,
        templateW: Int,
        templateH: Int
    ) {
        var (x, y) = item.resolveClickPosition(matchX, matchY, templateW, templateH)
        if (item.jitterEnabled && item.jitterRadius > 0) {
            x += random.nextInt(item.jitterRadius * 2 + 1) - item.jitterRadius
            y += random.nextInt(item.jitterRadius * 2 + 1) - item.jitterRadius
        }

        val delayMs = if (item.clickDelayRandom) {
            val min = minOf(item.clickDelayMinMs, item.clickDelayMaxMs)
            val max = maxOf(item.clickDelayMinMs, item.clickDelayMaxMs)
            if (max <= 0) 0 else random.nextInt(max - min + 1) + min
        } else {
            item.clickDelayMinMs
        }

        if (delayMs > 0) {
            delay(delayMs.toLong())
        }

        ClickAccessibilityService.performClick(
            x,
            y,
            item.clickType,
            item.button
        ) {
            if (item.afterClickMode == "manual") {
                ClickAccessibilityService.performClick(
                    item.afterClickX,
                    item.afterClickY,
                    "single",
                    "left",
                    null
                )
            }
        }
    }

    private fun loadTemplateBitmap(item: TemplateItem, scale: Float): TemplateBitmap? {
        val cached = templateCache[item.id]
        if (cached != null && cached.scale == scale) {
            return cached
        }
        cached?.grayMat?.release()
        val bitmap = BitmapFactory.decodeFile(item.imagePath) ?: return null
        val scaledBitmap = if (scale < 1f) {
            val scaledW = max(1, (bitmap.width * scale).roundToInt())
            val scaledH = max(1, (bitmap.height * scale).roundToInt())
            Bitmap.createScaledBitmap(bitmap, scaledW, scaledH, true)
        } else {
            bitmap
        }
        val grayArray = TemplateMatcher.bitmapToGray(scaledBitmap)
        val grayMat = if (openCvReady) TemplateMatcher.bitmapToGrayMat(scaledBitmap) else null
        val template = TemplateBitmap(
            grayArray = grayArray,
            grayMat = grayMat,
            width = scaledBitmap.width,
            height = scaledBitmap.height,
            scale = scale,
            originalWidth = bitmap.width,
            originalHeight = bitmap.height
        )
        templateCache[item.id] = template
        return template
    }

    private fun buildRoi(
        lastMatch: MatchPoint?,
        screenW: Int,
        screenH: Int,
        templateW: Int,
        templateH: Int
    ): TemplateMatcher.IntRect? {
        if (lastMatch == null) {
            return null
        }
        val padding = ROI_PADDING
        val left = max(0, lastMatch.x - padding)
        val top = max(0, lastMatch.y - padding)
        val right = min(screenW, lastMatch.x + templateW + padding)
        val bottom = min(screenH, lastMatch.y + templateH + padding)
        if (right - left < templateW || bottom - top < templateH) {
            return null
        }
        return TemplateMatcher.IntRect(left, top, right, bottom)
    }

    private fun pruneCache(templateIds: Set<String>) {
        val toRemove = templateCache.keys.filter { it !in templateIds }
        for (id in toRemove) {
            templateCache[id]?.grayMat?.release()
            templateCache.remove(id)
        }
    }

    private fun sendStatus(message: String) {
        val intent = Intent(ACTION_STATUS).putExtra(EXTRA_STATUS, message)
        sendBroadcast(intent)
        val notification = buildNotification(message)
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun sendLog(message: String) {
        logStore.append(message)
        val intent = Intent(ACTION_LOG).putExtra(EXTRA_LOG, message)
        sendBroadcast(intent)
    }

    private fun buildNotification(text: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Image Clicker")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) {
            return
        }
        val channel = NotificationChannel(CHANNEL_ID, "Image Clicker", NotificationManager.IMPORTANCE_LOW)
        manager.createNotificationChannel(channel)
    }

    data class MatchPoint(val x: Int, val y: Int)

    data class TemplateBitmap(
        val grayArray: IntArray,
        val grayMat: Mat?,
        val width: Int,
        val height: Int,
        val scale: Float,
        val originalWidth: Int,
        val originalHeight: Int
    )

    companion object {
        private const val CHANNEL_ID = "image_clicker"
        private const val NOTIFICATION_ID = 1001
        private const val SCAN_INTERVAL_MS = 2000L
        private const val SCALE_FACTOR = 0.5f
        private const val ROI_PADDING = 80

        const val ACTION_START = "com.picomouse.imageclicker.action.START"
        const val ACTION_STOP = "com.picomouse.imageclicker.action.STOP"
        const val ACTION_STATUS = "com.picomouse.imageclicker.action.STATUS"
        const val ACTION_LOG = "com.picomouse.imageclicker.action.LOG"
        const val EXTRA_STATUS = "extra_status"
        const val EXTRA_LOG = "extra_log"
        const val EXTRA_RESULT_CODE = "extra_result_code"
        const val EXTRA_DATA = "extra_data"
    }
}
