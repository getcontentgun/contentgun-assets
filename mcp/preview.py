import math
from io import BytesIO

from PIL import Image

PREVIEW_WIDTH = 540
SHEET_COLUMNS = 4
SHEET_THUMB_WIDTH = 270
SHEET_GAP = 8
SHEET_BACKGROUND = (128, 128, 128)
JPEG_QUALITY = 82


def png_size(png: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(png)) as image:
        return image.size


def _scaled(png: bytes, width: int) -> Image.Image:
    with Image.open(BytesIO(png)) as source:
        rgb = source.convert("RGB")
    height = max(1, round(rgb.height * width / rgb.width))
    return rgb.resize((width, height), Image.Resampling.LANCZOS)


def _jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def preview_jpeg(png: bytes, width: int = PREVIEW_WIDTH) -> bytes:
    return _jpeg(_scaled(png, width))


def contact_sheet(pngs: list[bytes]) -> bytes:
    thumbs = [_scaled(png, SHEET_THUMB_WIDTH) for png in pngs]
    columns = min(SHEET_COLUMNS, len(thumbs))
    rows = math.ceil(len(thumbs) / columns)
    cell_height = max(thumb.height for thumb in thumbs)
    sheet = Image.new(
        "RGB",
        (
            columns * SHEET_THUMB_WIDTH + (columns - 1) * SHEET_GAP,
            rows * cell_height + (rows - 1) * SHEET_GAP,
        ),
        SHEET_BACKGROUND,
    )
    for index, thumb in enumerate(thumbs):
        row, column = divmod(index, columns)
        sheet.paste(
            thumb,
            (column * (SHEET_THUMB_WIDTH + SHEET_GAP), row * (cell_height + SHEET_GAP)),
        )
    return _jpeg(sheet)
