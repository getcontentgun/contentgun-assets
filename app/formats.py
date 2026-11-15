from enum import StrEnum


class Format(StrEnum):
    PORTRAIT_4_5 = "4:5"
    SQUARE = "1:1"
    STORY = "9:16"
    LANDSCAPE = "16:9"


FORMAT_DIMENSIONS: dict[Format, tuple[int, int]] = {
    Format.PORTRAIT_4_5: (1080, 1350),
    Format.SQUARE: (1080, 1080),
    Format.STORY: (1080, 1920),
    Format.LANDSCAPE: (1920, 1080),
}


def dimensions(fmt: Format) -> tuple[int, int]:
    return FORMAT_DIMENSIONS[fmt]


def filename_token(fmt: Format) -> str:
    return fmt.value.replace(":", "x")
