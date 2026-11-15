import base64
import re
from dataclasses import dataclass
from pathlib import Path

LATIN = (
    "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, "
    "U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, "
    "U+FEFF, U+FFFD"
)
LATIN_EXT = (
    "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, "
    "U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, "
    "U+2113, U+2C60-2C7F, U+A720-A7FF"
)
SUBSETS = {"latin": LATIN, "latin-ext": LATIN_EXT}

WOFF2_SIGNATURE = b"wOF2"
MIN_FONT_BYTES = 1024

FONTS_KEYS = frozenset({"faces", "checks"})
FACE_KEYS = frozenset({"family", "file", "weight", "subset", "unicode_range"})
FAMILY = re.compile(r'[^"\\\x00-\x1f\x7f]+')
WEIGHT = re.compile(r"(\d{1,4})(?: (\d{1,4}))?")
CHECK = re.compile(r'(\d{1,4}) \d+(?:\.\d+)?px "([^"]+)"')
CODE_POINTS = r"U\+[0-9A-Fa-f?]{1,6}(?:-[0-9A-Fa-f]{1,6})?"
UNICODE_RANGE = re.compile(rf"{CODE_POINTS}(?:, *{CODE_POINTS})*")


@dataclass(frozen=True)
class FontFace:
    family: str
    file: str
    weight: str
    # None applies the file to every character.
    unicode_range: str | None = None

    def covers(self, family: str, weight: int) -> bool:
        bounds = [int(part) for part in self.weight.split()]
        return self.family == family and bounds[0] <= weight <= bounds[-1]


@dataclass(frozen=True)
class FontSet:
    faces: tuple[FontFace, ...]
    # Probed in the page after rendering. Every entry must be a weight some rule of the
    # bundle's templates actually draws, otherwise the check passes trivially.
    checks: tuple[str, ...]
    css: str


def _face(raw: object, source: Path, bundle: str) -> FontFace:
    def invalid(problem: str) -> RuntimeError:
        return RuntimeError(f"{source}: Bundle {bundle!r} {problem}.")

    if not isinstance(raw, dict):
        raise invalid("declares a font face that is not a table")
    unknown = sorted(set(raw) - FACE_KEYS)
    if unknown:
        raise invalid(f"has a font face with the unknown key {unknown[0]!r}")

    family = raw.get("family")
    if not isinstance(family, str) or not FAMILY.fullmatch(family) or family != family.strip():
        raise invalid(
            f"has a font face with the family {family!r}, which must be a name without quotes "
            "or surrounding spaces"
        )
    file = raw.get("file")
    if not isinstance(file, str) or file in ("", ".", "..") or Path(file).name != file:
        raise invalid(f"names the font file {file!r}, which must be a plain file name in fonts/")

    weight = raw.get("weight")
    if isinstance(weight, int) and not isinstance(weight, bool):
        weight = str(weight)
    match = WEIGHT.fullmatch(weight) if isinstance(weight, str) else None
    bounds = [int(part) for part in match.groups() if part] if match else []
    if not bounds or not 1 <= bounds[0] <= bounds[-1] <= 1000:
        raise invalid(
            f"gives {file!r} the weight {weight!r}, which must be a number from 1 to 1000 such "
            "as '400' or a range such as '200 900'"
        )

    subset = raw.get("subset")
    unicode_range = raw.get("unicode_range")
    if subset is not None and unicode_range is not None:
        raise invalid(f"gives {file!r} both a subset and a unicode_range, which is one too many")
    if subset is not None:
        if not isinstance(subset, str) or subset not in SUBSETS:
            raise invalid(
                f"gives {file!r} the subset {subset!r}. Known subsets: {', '.join(SUBSETS)}"
            )
        unicode_range = SUBSETS[subset]
    elif unicode_range is not None and (
        not isinstance(unicode_range, str) or not UNICODE_RANGE.fullmatch(unicode_range)
    ):
        raise invalid(
            f"gives {file!r} the unicode_range {unicode_range!r}, which is not a list such as "
            "'U+0000-00FF, U+0131'"
        )
    return FontFace(family, file, weight, unicode_range)


def _checks(raw: object, faces: list[FontFace], source: Path, bundle: str) -> list[str]:
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise RuntimeError(
            f"{source}: Bundle {bundle!r} declares no font checks. List them as strings in the "
            "checks of its [fonts] table."
        )
    for check in raw:
        match = CHECK.fullmatch(check)
        if match is None:
            raise RuntimeError(
                f"{source}: Bundle {bundle!r} has the font check {check!r}, which is not of the "
                "form '700 40px \"Family\"'."
            )
        weight, family = int(match.group(1)), match.group(2)
        if not any(face.covers(family, weight) for face in faces):
            raise RuntimeError(
                f"{source}: Bundle {bundle!r} checks {check!r}, but no font face of {family!r} "
                f"covers the weight {weight}."
            )
    return raw


def _face_css(face: FontFace, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    lines = [
        "@font-face {",
        f'  font-family: "{face.family}";',
        f"  font-weight: {face.weight};",
        "  font-style: normal;",
        "  font-display: block;",
        f'  src: url("data:font/woff2;base64,{encoded}") format("woff2");',
    ]
    if face.unicode_range is not None:
        lines.append(f"  unicode-range: {face.unicode_range};")
    lines.append("}")
    return "\n".join(lines)


def _read_files(faces: list[FontFace], fonts_dir: Path, bundle: str) -> list[bytes]:
    root = fonts_dir.resolve()
    contents = []
    problems = []
    for face in faces:
        path = fonts_dir / face.file
        if not path.is_file():
            problems.append(f"{face.file} is missing")
            continue
        if not path.resolve().is_relative_to(root):
            problems.append(f"{face.file} lies outside fonts/")
            continue
        data = path.read_bytes()
        if len(data) < MIN_FONT_BYTES:
            problems.append(f"{face.file} is only {len(data)} bytes")
        elif not data.startswith(WOFF2_SIGNATURE):
            problems.append(f"{face.file} is not a woff2 file")
        contents.append(data)
    if problems:
        raise RuntimeError(
            f"{fonts_dir}: Font files of bundle {bundle!r} are unusable: {'; '.join(problems)}."
        )
    return contents


def load_font_set(table: object, fonts_dir: Path, source: Path, bundle: str) -> FontSet:
    """The fonts a bundle declares in the [fonts] table of its bundle.toml, validated and
    embedded as data URIs once, at startup."""
    if table is None:
        table = {}
    if not isinstance(table, dict):
        raise RuntimeError(f"{source}: Bundle {bundle!r} must declare [fonts] as a table.")
    unknown = sorted(set(table) - FONTS_KEYS)
    if unknown:
        raise RuntimeError(
            f"{source}: Bundle {bundle!r} has the unknown key {unknown[0]!r} in [fonts]."
        )

    raw_faces = table.get("faces")
    if not isinstance(raw_faces, list) or not raw_faces:
        raise RuntimeError(
            f"{source}: Bundle {bundle!r} declares no font faces. A bundle without them would "
            "render in a fallback font; declare them as [[fonts.faces]]."
        )
    faces = [_face(raw, source, bundle) for raw in raw_faces]
    checks = _checks(table.get("checks"), faces, source, bundle)
    contents = _read_files(faces, fonts_dir, bundle)
    css = "\n".join(_face_css(face, data) for face, data in zip(faces, contents, strict=True))
    return FontSet(faces=tuple(faces), checks=tuple(checks), css=css)
