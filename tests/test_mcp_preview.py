from io import BytesIO

from PIL import Image

from preview import (
    PREVIEW_WIDTH,
    SHEET_GAP,
    SHEET_THUMB_WIDTH,
    contact_sheet,
    png_size,
    preview_jpeg,
)


def png(size=(1080, 1350), colour=(0, 0, 0)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


def test_png_size_reads_the_dimensions():
    assert png_size(png((1080, 1080))) == (1080, 1080)


def test_preview_is_a_jpeg_scaled_to_its_width():
    preview = opened(preview_jpeg(png()))
    assert preview.format == "JPEG"
    assert preview.size == (PREVIEW_WIDTH, 675)


def test_contact_sheet_puts_four_slides_in_a_row():
    sheet = opened(contact_sheet([png() for _ in range(7)]))
    assert sheet.format == "JPEG"
    assert sheet.width == 4 * SHEET_THUMB_WIDTH + 3 * SHEET_GAP
    assert sheet.height == 2 * 338 + SHEET_GAP


def test_a_short_carousel_is_a_single_row():
    sheet = opened(contact_sheet([png((1080, 1080)) for _ in range(3)]))
    assert sheet.size == (3 * SHEET_THUMB_WIDTH + 2 * SHEET_GAP, SHEET_THUMB_WIDTH)
