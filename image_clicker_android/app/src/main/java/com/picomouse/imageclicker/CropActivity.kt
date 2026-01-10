package com.picomouse.imageclicker

import android.app.Activity
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.Bitmap
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

class CropActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_crop)

        val imagePath = intent.getStringExtra(EXTRA_IMAGE_PATH) ?: run {
            finish()
            return
        }

        val bitmap = BitmapFactory.decodeFile(imagePath) ?: run {
            finish()
            return
        }

        val imageView = findViewById<ImageView>(R.id.cropImageView)
        val overlay = findViewById<CropOverlayView>(R.id.cropOverlay)
        val confirmBtn = findViewById<Button>(R.id.confirmCropBtn)

        imageView.setImageBitmap(bitmap)

        confirmBtn.setOnClickListener {
            val rect = overlay.getSelectionRect()
            if (rect.width() < 5f || rect.height() < 5f) {
                Toast.makeText(this, "Selection too small", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val values = FloatArray(9)
            imageView.imageMatrix.getValues(values)
            val scaleX = values[Matrix.MSCALE_X]
            val scaleY = values[Matrix.MSCALE_Y]
            val transX = values[Matrix.MTRANS_X]
            val transY = values[Matrix.MTRANS_Y]

            if (scaleX == 0f || scaleY == 0f) {
                Toast.makeText(this, "Invalid scale", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val left = ((rect.left - transX) / scaleX).coerceIn(0f, bitmap.width.toFloat())
            val top = ((rect.top - transY) / scaleY).coerceIn(0f, bitmap.height.toFloat())
            val right = ((rect.right - transX) / scaleX).coerceIn(0f, bitmap.width.toFloat())
            val bottom = ((rect.bottom - transY) / scaleY).coerceIn(0f, bitmap.height.toFloat())

            if (right <= left || bottom <= top) {
                Toast.makeText(this, "Invalid selection", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val crop = Bitmap.createBitmap(
                bitmap,
                left.toInt(),
                top.toInt(),
                (right - left).toInt(),
                (bottom - top).toInt()
            )

            val store = TemplateStore(this)
            val output = store.createTemplateFile()
            output.outputStream().use { out ->
                crop.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
            }

            promptForName { name ->
                val resultIntent = intent
                resultIntent.putExtra(EXTRA_TEMPLATE_PATH, output.absolutePath)
                resultIntent.putExtra(EXTRA_TEMPLATE_NAME, name)
                setResult(Activity.RESULT_OK, resultIntent)
                finish()
            }
        }
    }

    private fun promptForName(onDone: (String) -> Unit) {
        val input = EditText(this)
        AlertDialog.Builder(this)
            .setTitle("Template Name")
            .setView(input)
            .setPositiveButton("OK") { _, _ ->
                val name = input.text.toString().trim().ifEmpty { "Template" }
                onDone(name)
            }
            .setNegativeButton("Cancel") { _, _ ->
                onDone("Template")
            }
            .show()
    }

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
        const val EXTRA_TEMPLATE_PATH = "extra_template_path"
        const val EXTRA_TEMPLATE_NAME = "extra_template_name"
    }
}
