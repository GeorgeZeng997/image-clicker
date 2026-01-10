package com.picomouse.imageclicker

import android.Manifest
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import com.picomouse.imageclicker.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val store by lazy { TemplateStore(this) }
    private val logStore by lazy { LogStore(this) }
    private val templates = mutableListOf<TemplateItem>()
    private val logLines = ArrayDeque<String>()
    private var selectedIndex = -1

    private var lastResultCode = -1
    private var lastProjectionData: Intent? = null
    private var pendingAction: PendingAction = PendingAction.NONE
    private var isMatching = false

    private val autoSaveHandler = Handler(Looper.getMainLooper())
    private val autoSaveRunnable = Runnable { saveTemplateSettings(silent = true) }
    private var suppressAutoSave = false

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK || result.data == null) {
            Toast.makeText(this, "Screen capture permission denied", Toast.LENGTH_SHORT).show()
            pendingAction = PendingAction.NONE
            return@registerForActivityResult
        }
        lastResultCode = result.resultCode
        lastProjectionData = result.data
        when (pendingAction) {
            PendingAction.CAPTURE_TEMPLATE -> captureTemplateOnce()
            PendingAction.START_MATCH -> startMatching()
            else -> Unit
        }
        pendingAction = PendingAction.NONE
    }

    private val cropLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK || result.data == null) {
            return@registerForActivityResult
        }
        val path = result.data?.getStringExtra(CropActivity.EXTRA_TEMPLATE_PATH) ?: return@registerForActivityResult
        val name = result.data?.getStringExtra(CropActivity.EXTRA_TEMPLATE_NAME) ?: "Template"
        val item = TemplateItem(
            id = UUID.randomUUID().toString(),
            name = name,
            imagePath = path,
            threshold = 0.8f,
            clickMode = "match",
            manualX = 0,
            manualY = 0,
            button = "left",
            clickType = "single",
            jitterEnabled = false,
            jitterRadius = 0,
            clickDelayMinMs = 0,
            clickDelayMaxMs = 0,
            clickDelayRandom = false,
            enabled = true,
            stopAllOnMatch = false,
            afterClickMode = "none",
            afterClickX = 0,
            afterClickY = 0
        )
        templates.add(item)
        store.saveTemplates(templates)
        refreshTemplateList(selectLast = true)
        Toast.makeText(this, "Template added", Toast.LENGTH_SHORT).show()
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!granted) {
            Toast.makeText(this, "Notification permission required for matching", Toast.LENGTH_LONG).show()
        }
    }

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                MatchService.ACTION_STATUS -> {
                    val message = intent.getStringExtra(MatchService.EXTRA_STATUS) ?: return
                    if (message.startsWith("Last match:")) {
                        binding.lastMatchText.text = message
                    } else if (message.startsWith("Matches:")) {
                        binding.matchCountText.text = message
                    } else {
                        binding.statusText.text = message
                    }
                }
                MatchService.ACTION_LOG -> {
                    val line = intent.getStringExtra(MatchService.EXTRA_LOG) ?: return
                    appendLogLine(line)
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        templates.addAll(store.loadTemplates())
        setupSpinners()
        setupAutoSave()
        setupButtons()
        refreshTemplateList(selectLast = false)
        updateUiForSelection()
        loadLogView()
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter().apply {
            addAction(MatchService.ACTION_STATUS)
            addAction(MatchService.ACTION_LOG)
        }
        registerReceiver(statusReceiver, filter)
    }

    override fun onStop() {
        super.onStop()
        unregisterReceiver(statusReceiver)
    }

    private fun setupSpinners() {
        val buttonAdapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_item,
            listOf("left", "right", "middle")
        )
        buttonAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        binding.buttonSpinner.adapter = buttonAdapter

        val clickAdapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_item,
            listOf("single", "double")
        )
        clickAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        binding.clickTypeSpinner.adapter = clickAdapter

        binding.templateSpinner.onItemSelectedListener = SimpleItemSelectedListener { position ->
            selectedIndex = position
            updateUiForSelection()
        }

        binding.buttonSpinner.onItemSelectedListener = SimpleItemSelectedListener {
            scheduleAutoSave()
        }
        binding.clickTypeSpinner.onItemSelectedListener = SimpleItemSelectedListener {
            scheduleAutoSave()
        }
    }

    private fun setupAutoSave() {
        binding.thresholdInput.doAfterTextChanged { scheduleAutoSave() }
        binding.manualXInput.doAfterTextChanged { scheduleAutoSave() }
        binding.manualYInput.doAfterTextChanged { scheduleAutoSave() }
        binding.jitterRadiusInput.doAfterTextChanged { scheduleAutoSave() }
        binding.delayMinInput.doAfterTextChanged { scheduleAutoSave() }
        binding.delayMaxInput.doAfterTextChanged { scheduleAutoSave() }
        binding.afterXInput.doAfterTextChanged { scheduleAutoSave() }
        binding.afterYInput.doAfterTextChanged { scheduleAutoSave() }

        binding.templateEnabledSwitch.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }
        binding.stopAllSwitch.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }
        binding.jitterSwitch.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }
        binding.delayRandomSwitch.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }

        binding.clickModeGroup.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }
        binding.afterClickGroup.setOnCheckedChangeListener { _, _ -> scheduleAutoSave() }
    }

    private fun setupButtons() {
        binding.captureTemplateBtn.setOnClickListener {
            requestScreenPermission(PendingAction.CAPTURE_TEMPLATE)
        }
        binding.removeTemplateBtn.setOnClickListener {
            removeSelectedTemplate()
        }
        binding.saveTemplateBtn.setOnClickListener {
            saveTemplateSettings(silent = false)
        }
        binding.enableAllBtn.setOnClickListener {
            setAllEnabled(true)
        }
        binding.disableAllBtn.setOnClickListener {
            setAllEnabled(false)
        }
        binding.removeAllBtn.setOnClickListener {
            confirmRemoveAll()
        }
        binding.accessibilityBtn.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        binding.toggleMatchBtn.setOnClickListener {
            if (isMatching) {
                stopMatching()
            } else {
                requestScreenPermission(PendingAction.START_MATCH)
            }
        }
        binding.clearLogBtn.setOnClickListener {
            logStore.clear()
            loadLogView()
        }
    }

    private fun requestScreenPermission(action: PendingAction) {
        if (action == PendingAction.START_MATCH && !ensureNotificationsAllowed()) {
            return
        }
        pendingAction = action
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(manager.createScreenCaptureIntent())
    }

    private fun captureTemplateOnce() {
        val data = lastProjectionData ?: return
        val resultCode = lastResultCode
        lifecycleScope.launch {
            val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            val projection = manager.getMediaProjection(resultCode, data)
            val helper = ScreenCaptureHelper(this@MainActivity, projection)
            val bitmap = helper.captureOnce()
            projection.stop()
            if (bitmap == null) {
                Toast.makeText(this@MainActivity, "Capture failed", Toast.LENGTH_SHORT).show()
                return@launch
            }
            val tempFile = withContext(Dispatchers.IO) {
                val file = File(cacheDir, "capture_${System.currentTimeMillis()}.png")
                file.outputStream().use { out ->
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
                }
                file
            }
            val intent = Intent(this@MainActivity, CropActivity::class.java)
            intent.putExtra(CropActivity.EXTRA_IMAGE_PATH, tempFile.absolutePath)
            cropLauncher.launch(intent)
        }
    }

    private fun startMatching() {
        val data = lastProjectionData ?: return
        val intent = Intent(this, MatchService::class.java)
        intent.action = MatchService.ACTION_START
        intent.putExtra(MatchService.EXTRA_RESULT_CODE, lastResultCode)
        intent.putExtra(MatchService.EXTRA_DATA, data)
        ContextCompat.startForegroundService(this, intent)
        isMatching = true
        binding.toggleMatchBtn.text = "Stop Matching"
        binding.statusText.text = "Status: Running"
    }

    private fun stopMatching() {
        val intent = Intent(this, MatchService::class.java)
        intent.action = MatchService.ACTION_STOP
        startService(intent)
        isMatching = false
        binding.toggleMatchBtn.text = "Start Matching"
        binding.statusText.text = "Status: Idle"
    }

    private fun removeSelectedTemplate() {
        if (selectedIndex !in templates.indices) {
            return
        }
        val item = templates.removeAt(selectedIndex)
        store.deleteTemplateFile(item.imagePath)
        store.saveTemplates(templates)
        refreshTemplateList(selectLast = false)
        updateUiForSelection()
    }

    private fun confirmRemoveAll() {
        if (templates.isEmpty()) {
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Remove All Templates")
            .setMessage("This will delete all templates and images. Continue?")
            .setPositiveButton("Remove") { _, _ ->
                for (item in templates) {
                    store.deleteTemplateFile(item.imagePath)
                }
                templates.clear()
                store.saveTemplates(templates)
                refreshTemplateList(selectLast = false)
                updateUiForSelection()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun setAllEnabled(enabled: Boolean) {
        for (item in templates) {
            item.enabled = enabled
        }
        store.saveTemplates(templates)
        updateUiForSelection()
    }

    private fun refreshTemplateList(selectLast: Boolean) {
        val names = templates.map { it.name }
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, names)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        binding.templateSpinner.adapter = adapter
        if (templates.isEmpty()) {
            selectedIndex = -1
            return
        }
        selectedIndex = if (selectLast) templates.lastIndex else minOf(selectedIndex.coerceAtLeast(0), templates.lastIndex)
        binding.templateSpinner.setSelection(selectedIndex)
    }

    private fun updateUiForSelection() {
        suppressAutoSave = true
        try {
            if (selectedIndex !in templates.indices) {
                binding.thresholdInput.setText("")
                binding.manualXInput.setText("")
                binding.manualYInput.setText("")
                binding.jitterRadiusInput.setText("")
                binding.delayMinInput.setText("")
                binding.delayMaxInput.setText("")
                binding.afterXInput.setText("")
                binding.afterYInput.setText("")
                binding.templatePreview.setImageBitmap(null)
                return
            }
            val item = templates[selectedIndex]
            binding.thresholdInput.setText(item.threshold.toString())
            if (item.clickMode == "manual") {
                binding.radioManual.isChecked = true
            } else {
                binding.radioMatch.isChecked = true
            }
            binding.manualXInput.setText(item.manualX.toString())
            binding.manualYInput.setText(item.manualY.toString())
            binding.buttonSpinner.setSelection(buttonIndex(item.button))
            binding.clickTypeSpinner.setSelection(if (item.clickType == "double") 1 else 0)
            binding.templateEnabledSwitch.isChecked = item.enabled
            binding.stopAllSwitch.isChecked = item.stopAllOnMatch
            binding.jitterSwitch.isChecked = item.jitterEnabled
            binding.jitterRadiusInput.setText(item.jitterRadius.toString())
            binding.delayMinInput.setText(item.clickDelayMinMs.toString())
            binding.delayMaxInput.setText(item.clickDelayMaxMs.toString())
            binding.delayRandomSwitch.isChecked = item.clickDelayRandom

            when (item.afterClickMode) {
                "manual" -> binding.afterManual.isChecked = true
                "none" -> binding.afterNone.isChecked = true
                else -> binding.afterRestore.isChecked = true
            }
            binding.afterXInput.setText(item.afterClickX.toString())
            binding.afterYInput.setText(item.afterClickY.toString())

            val preview = BitmapFactory.decodeFile(item.imagePath)
            binding.templatePreview.setImageBitmap(preview)
        } finally {
            suppressAutoSave = false
        }
    }

    private fun saveTemplateSettings(silent: Boolean) {
        if (selectedIndex !in templates.indices) {
            return
        }
        val item = templates[selectedIndex]
        val threshold = binding.thresholdInput.text.toString().toFloatOrNull() ?: item.threshold
        val manualX = binding.manualXInput.text.toString().toIntOrNull() ?: item.manualX
        val manualY = binding.manualYInput.text.toString().toIntOrNull() ?: item.manualY
        val jitterRadius = binding.jitterRadiusInput.text.toString().toIntOrNull() ?: item.jitterRadius
        val delayMin = binding.delayMinInput.text.toString().toIntOrNull() ?: item.clickDelayMinMs
        val delayMax = binding.delayMaxInput.text.toString().toIntOrNull() ?: item.clickDelayMaxMs
        val afterX = binding.afterXInput.text.toString().toIntOrNull() ?: item.afterClickX
        val afterY = binding.afterYInput.text.toString().toIntOrNull() ?: item.afterClickY

        item.threshold = threshold.coerceIn(0f, 1f)
        item.clickMode = if (binding.radioManual.isChecked) "manual" else "match"
        item.manualX = manualX
        item.manualY = manualY
        item.button = binding.buttonSpinner.selectedItem.toString()
        item.clickType = binding.clickTypeSpinner.selectedItem.toString()
        item.enabled = binding.templateEnabledSwitch.isChecked
        item.stopAllOnMatch = binding.stopAllSwitch.isChecked
        item.jitterEnabled = binding.jitterSwitch.isChecked
        item.jitterRadius = jitterRadius.coerceAtLeast(0)
        item.clickDelayMinMs = delayMin.coerceAtLeast(0)
        item.clickDelayMaxMs = delayMax.coerceAtLeast(0)
        item.clickDelayRandom = binding.delayRandomSwitch.isChecked
        item.afterClickMode = when {
            binding.afterManual.isChecked -> "manual"
            binding.afterNone.isChecked -> "none"
            else -> "restore"
        }
        item.afterClickX = afterX
        item.afterClickY = afterY

        store.saveTemplates(templates)
        if (!silent) {
            Toast.makeText(this, "Template saved", Toast.LENGTH_SHORT).show()
        }
    }

    private fun scheduleAutoSave() {
        if (suppressAutoSave) {
            return
        }
        autoSaveHandler.removeCallbacks(autoSaveRunnable)
        autoSaveHandler.postDelayed(autoSaveRunnable, 350)
    }

    private fun buttonIndex(value: String): Int {
        return when (value) {
            "right" -> 1
            "middle" -> 2
            else -> 0
        }
    }

    private fun ensureNotificationsAllowed(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return true
        }
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
            return true
        }
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        return false
    }

    private fun loadLogView() {
        val content = logStore.readAll()
        logLines.clear()
        if (content.isNotBlank()) {
            content.lines().forEach { logLines.add(it) }
        }
        updateLogText()
    }

    private fun appendLogLine(line: String) {
        logLines.add(line)
        while (logLines.size > 200) {
            logLines.removeFirst()
        }
        updateLogText()
    }

    private fun updateLogText() {
        binding.logText.text = if (logLines.isEmpty()) "No logs yet" else logLines.joinToString("\n")
    }

    enum class PendingAction {
        NONE,
        CAPTURE_TEMPLATE,
        START_MATCH
    }
}
