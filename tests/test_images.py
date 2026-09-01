import io

import pytest
from PIL import Image

from schemagate.errors import MalformedDocumentError
from schemagate.ingest.images import MAX_EDGE, normalise


def photo(width: int = 800, height: int = 600, fmt: str = "JPEG", **save: object) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_a_photo_comes_back_ready_to_send() -> None:
    result = normalise(photo())

    assert result.media_type == "image/jpeg"
    assert opened(result.data).size == (800, 600)


def test_an_oversized_photo_is_scaled_down() -> None:
    result = normalise(photo(6000, 3000))

    width, height = opened(result.data).size
    assert max(width, height) == MAX_EDGE, (
        "a larger image costs tokens without being read any better, and the "
        "provider downscales it anyway"
    )
    assert width / height == pytest.approx(2.0), "aspect ratio is preserved"


def test_a_small_photo_is_not_enlarged() -> None:
    result = normalise(photo(300, 200))

    assert opened(result.data).size == (300, 200)


def test_a_sideways_photo_is_turned_upright() -> None:
    # EXIF orientation 6 means "rotate 90 degrees clockwise to display".
    image = Image.new("RGB", (400, 200), "white")
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    result = normalise(buffer.getvalue())

    assert opened(result.data).size == (200, 400), (
        "a phone writes the rotation into EXIF and leaves the pixels sideways; "
        "a model reads the pixels"
    )


def test_the_orientation_tag_is_not_left_behind() -> None:
    image = Image.new("RGB", (400, 200), "white")
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    result = normalise(buffer.getvalue())

    assert opened(result.data).getexif().get(274) in (None, 1), (
        "the pixels were rotated, so a tag saying to rotate again would undo it"
    )


def test_transparency_is_flattened_rather_than_turned_black() -> None:
    image = Image.new("RGBA", (100, 100), (255, 255, 255, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = normalise(buffer.getvalue())

    assert opened(result.data).mode == "RGB"


def test_a_png_is_kept_as_a_png() -> None:
    assert normalise(photo(fmt="PNG")).media_type == "image/png"


def test_an_unreadable_file_is_reported() -> None:
    with pytest.raises(MalformedDocumentError):
        normalise(b"not an image at all")


def test_the_result_is_smaller_than_the_original_when_scaled() -> None:
    original = photo(6000, 3000)

    assert len(normalise(original).data) < len(original)
