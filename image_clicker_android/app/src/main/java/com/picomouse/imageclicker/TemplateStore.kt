package com.picomouse.imageclicker

import android.content.Context
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.io.File
import java.util.UUID

class TemplateStore(private val context: Context) {
    private val gson = Gson()
    private val configFile = File(context.filesDir, "templates.json")
    private val templateDir = File(context.filesDir, "templates")

    fun loadTemplates(): MutableList<TemplateItem> {
        if (!configFile.exists()) {
            return mutableListOf()
        }
        return try {
            val json = configFile.readText()
            val type = object : TypeToken<MutableList<TemplateItem>>() {}.type
            gson.fromJson(json, type) ?: mutableListOf()
        } catch (ex: Exception) {
            mutableListOf()
        }
    }

    fun saveTemplates(templates: List<TemplateItem>) {
        if (!templateDir.exists()) {
            templateDir.mkdirs()
        }
        val json = gson.toJson(templates)
        configFile.writeText(json)
    }

    fun createTemplateFile(): File {
        if (!templateDir.exists()) {
            templateDir.mkdirs()
        }
        val id = UUID.randomUUID().toString()
        return File(templateDir, "template_$id.png")
    }

    fun deleteTemplateFile(path: String) {
        if (path.isBlank()) {
            return
        }
        try {
            File(path).delete()
        } catch (ex: Exception) {
            // ignore
        }
    }
}
