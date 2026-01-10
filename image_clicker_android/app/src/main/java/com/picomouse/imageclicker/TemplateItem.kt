package com.picomouse.imageclicker

data class TemplateItem(
    val id: String,
    var name: String,
    var imagePath: String,
    var threshold: Float,
    var clickMode: String,
    var manualX: Int,
    var manualY: Int,
    var button: String,
    var clickType: String,
    var jitterEnabled: Boolean,
    var jitterRadius: Int,
    var clickDelayMinMs: Int,
    var clickDelayMaxMs: Int,
    var clickDelayRandom: Boolean,
    var enabled: Boolean,
    var stopAllOnMatch: Boolean,
    var afterClickMode: String,
    var afterClickX: Int,
    var afterClickY: Int
) {
    fun resolveClickPosition(matchX: Int, matchY: Int, templateWidth: Int, templateHeight: Int): Pair<Int, Int> {
        return if (clickMode == "manual") {
            manualX to manualY
        } else {
            (matchX + templateWidth / 2) to (matchY + templateHeight / 2)
        }
    }
}
