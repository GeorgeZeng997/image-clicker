package com.picomouse.imageclicker

import android.content.Context
import java.io.File

class LogStore(context: Context) {
    private val logFile = File(context.filesDir, "match_log.txt")
    private val maxLines = 200

    fun append(line: String) {
        val timestamped = "${System.currentTimeMillis()} $line"
        val lines = if (logFile.exists()) logFile.readLines().toMutableList() else mutableListOf()
        lines.add(timestamped)
        if (lines.size > maxLines) {
            val start = lines.size - maxLines
            val trimmed = lines.subList(start, lines.size)
            logFile.writeText(trimmed.joinToString("\n"))
        } else {
            logFile.writeText(lines.joinToString("\n"))
        }
    }

    fun readAll(): String {
        return if (logFile.exists()) logFile.readText() else ""
    }

    fun clear() {
        if (logFile.exists()) {
            logFile.delete()
        }
    }
}
