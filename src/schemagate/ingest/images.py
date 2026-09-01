import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from schemagate.errors import MalformedDocumentError

# Anthropic reads images at up to roughly 1568 pixels on the long edge and
# downscales anything larger before looking at it. Sending more costs tokens for
# pixels no model sees, so the resizing happens here where it is visible.
MAX_EDGE = 1568

# JPEG for photographs, PNG for anything with flat colour or text on a plain
# ground, which is what a screenshot or an exported page usually is.
JPEG_QUALITY = 88

MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}


@dataclass(frozen=True, slots=True)
class NormalisedImage:
    """An image in a form a provider will accept."""

    data: bytes
    media_type: str
    width: int
    height: int


def normalise(data: bytes) -> NormalisedImage:
    """Turn an uploaded image into something worth sending.

    Three things go wrong with photographs of documents, and all three are
    silent. A phone writes the rotation into EXIF and leaves the pixels
    sideways, so every viewer shows it upright and a model reads it on its side.
    A transparent PNG flattened carelessly turns white text black. And a 12
    megapixel photo is billed in full and then thrown away by the provider.
    """
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            fmt = (opened.format or "PNG").upper()
            image = ImageOps.exif_transpose(opened) or opened

            if image.mode in {"RGBA", "LA", "P"}:
                # Compose onto white rather than dropping the alpha channel,
                # which would render anything transparent as black.
                background = Image.new("RGB", image.size, (255, 255, 255))
                converted = image.convert("RGBA")
                background.paste(converted, mask=converted.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            if max(image.size) > MAX_EDGE:
                image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

            return _encode(image, "JPEG" if fmt in {"JPEG", "MPO"} else "PNG")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise MalformedDocumentError(f"The file is not a readable image: {error}") from error


def _encode(image: Image.Image, fmt: str) -> NormalisedImage:
    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    else:
        image.save(buffer, format="PNG", optimize=True)
    return NormalisedImage(
        data=buffer.getvalue(),
        media_type=MEDIA_TYPES[fmt],
        width=image.width,
        height=image.height,
    )


def register_heif() -> bool:
    """Teach Pillow to open HEIC, which is what an iPhone produces by default.

    Optional: without it a HEIC upload is refused with a readable message rather
    than crashing, and every other format still works.
    """
    try:
        import pillow_heif
    except ImportError:
        return False
    pillow_heif.register_heif_opener()
    return True


register_heif()
