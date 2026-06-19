from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings


class ImageOptimizationError(ValueError):
    pass


@dataclass(slots=True)
class OptimizedImage:
    data: bytes
    mime_type: str
    width: int | None
    height: int | None
    was_optimized: bool


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def optimize_for_ai(
    image_bytes: bytes, mime_type: str, settings: Settings
) -> OptimizedImage:
    if not settings.optimize_image:
        return OptimizedImage(image_bytes, mime_type, None, None, False)

    # HEIC/HEIF memerlukan codec tambahan. Tetap kirim file asli agar format
    # yang didukung provider AI tidak rusak bila codec tersebut tidak tersedia.
    if mime_type in {"image/heic", "image/heif"}:
        return OptimizedImage(image_bytes, mime_type, None, None, False)

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.width * source.height > settings.image_max_pixels:
                raise ImageOptimizationError(
                    "Resolusi gambar melampaui batas keamanan"
                )
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (settings.image_max_dimension, settings.image_max_dimension),
                Image.Resampling.LANCZOS,
            )
            image = _to_rgb(image)
            width, height = image.size

            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=settings.image_jpeg_quality,
                optimize=True,
                progressive=True,
            )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageOptimizationError("Konten file gambar tidak dapat dibaca") from exc
    optimized_bytes = output.getvalue()
    return OptimizedImage(
        data=optimized_bytes,
        mime_type="image/jpeg",
        width=width,
        height=height,
        was_optimized=True,
    )
