import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

from markupsafe import Markup, escape
from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound

# A code card neither wraps nor hyphenates, so these two caps are what keeps the code inside its
# box: a template's floor is measured against them.
MAX_CODE_LINES = 14
MAX_CODE_COLUMNS = 48
# Bounds the request before normalization, which may still strip indentation and blank lines.
MAX_CODE_LENGTH = 2000
MAX_LANGUAGE_LENGTH = 40
TAB_SIZE = 4
PLAIN = "text"

# Characters a card cannot show honestly: control characters, invisible format characters such as
# the bidi overrides, which can make code read differently from what it does, surrogates and the
# Unicode line and paragraph separators.
FORBIDDEN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

# The classes a bundle colors, as tok-<class>. First match wins, so a subtype comes before the
# type it belongs to.
TOKEN_CLASSES = (
    (Token.Comment.PreprocFile, "string"),
    (Token.Comment.Preproc, "keyword"),
    (Token.Comment, "comment"),
    (Token.Operator.Word, "keyword"),
    (Token.Keyword, "keyword"),
    (Token.Name.Builtin, "builtin"),
    (Token.Name.Function, "function"),
    (Token.Name.Class, "type"),
    (Token.Name.Exception, "type"),
    (Token.Name.Decorator, "decorator"),
    (Token.Name.Tag, "tag"),
    (Token.Name.Attribute, "attribute"),
    (Token.Literal.String, "string"),
    (Token.Literal.Number, "number"),
    (Token.Operator, "operator"),
    (Token.Punctuation, "punctuation"),
    (Token.Generic.Inserted, "inserted"),
    (Token.Generic.Deleted, "deleted"),
)


class CodeError(ValueError):
    """Code a card cannot show. The message is a full sentence for the caller."""


@dataclass(frozen=True)
class Code:
    """Checked code, highlighted as one <span class="line"> per line."""

    html: Markup
    language: str
    lines: int
    line_numbers: bool


@dataclass(frozen=True)
class CodeSample:
    """The code of a template's [code_sample] table, the preview of the debug route."""

    code: str
    language: str | None = None
    line_numbers: bool = False
    highlight_lines: tuple[int, ...] = ()

    def check(self) -> Code:
        return check_code(self.code, self.language, self.line_numbers, self.highlight_lines)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def columns(line: str) -> int:
    """The width of a line in monospace columns. A wide East Asian character takes two: its
    fallback glyph is up to 1em wide, a column of a monospace font about 0.6em."""
    return sum(
        2 if unicodedata.east_asian_width(character) in ("W", "F") else 1 for character in line
    )


def normalize(raw: str) -> list[str]:
    """The lines a card shows: unified line breaks, tabs as spaces, no trailing whitespace, no
    blank lines at either end, and no indentation that every line shares."""
    lines = [
        line.expandtabs(TAB_SIZE).rstrip()
        for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    indent = min((len(line) - len(line.lstrip(" ")) for line in lines if line), default=0)
    return [line[indent:] for line in lines]


@cache
def token_class(token_type) -> str | None:
    for parent, name in TOKEN_CLASSES:
        if token_type in parent:
            return name
    return None


def _lexer(language: str):
    try:
        return get_lexer_by_name(language, stripnl=False, stripall=False, ensurenl=False, tabsize=0)
    except ClassNotFound:
        raise CodeError(
            f"Unknown language {language!r}. Use a short name of Pygments, such as python, "
            "javascript or bash."
        ) from None


def _highlight(lines: list[str], language: str, line_numbers: bool, marked: set[int]) -> Markup:
    # Line-based lexers, such as the one for diffs, match a line only with its line break, so
    # the last line needs one too; the empty row it leaves is dropped below.
    text = "\n".join(lines) + "\n"
    tokens = list(_lexer(language).get_tokens(text))
    # Every line below must be the line that was checked, so a lexer that loses or adds a
    # character falls back to plain text.
    if "".join(value for _, value in tokens) != text:
        tokens = [(Token.Text, text)]
    rows: list[list[str]] = [[]]
    for token_type, value in tokens:
        name = token_class(token_type)
        for index, piece in enumerate(value.split("\n")):
            if index:
                rows.append([])
            if piece:
                escaped = str(escape(piece))
                rows[-1].append(f'<span class="tok-{name}">{escaped}</span>' if name else escaped)
    rows.pop()
    html = []
    for number, parts in enumerate(rows, start=1):
        classes = "line hl" if number in marked else "line"
        gutter = f'<span class="ln">{number}</span>' if line_numbers else ""
        # An empty block has no height, so an empty line keeps one space.
        html.append(f'<span class="{classes}">{gutter}{"".join(parts) or " "}</span>')
    return Markup("".join(html))


def check_code(
    raw: str,
    language: str | None = None,
    line_numbers: bool = False,
    highlight_lines: Sequence[int] = (),
) -> Code:
    """The code of a request as the markup of a card, or a CodeError that names what is wrong.
    Lines are counted after normalize(), as the card numbers them."""
    lines = normalize(raw)
    if not lines:
        raise CodeError("The code is blank.")
    if len(lines) > MAX_CODE_LINES:
        raise CodeError(f"The code has {len(lines)} lines; at most {MAX_CODE_LINES} are accepted.")
    for number, line in enumerate(lines, start=1):
        for character in line:
            if unicodedata.category(character) in FORBIDDEN:
                raise CodeError(
                    f"Line {number} of the code contains the character U+{ord(character):04X}, "
                    "which a card cannot show."
                )
        width = columns(line)
        if width > MAX_CODE_COLUMNS:
            raise CodeError(
                f"Line {number} of the code is {width} columns wide; at most "
                f"{MAX_CODE_COLUMNS} are accepted."
            )
    for number in highlight_lines:
        if not 1 <= number <= len(lines):
            raise CodeError(
                f"highlight_lines names line {number}, but the code has "
                f"{_count(len(lines), 'line')}."
            )
    alias = (language or "").strip().lower() or PLAIN
    html = _highlight(lines, alias, line_numbers, set(highlight_lines))
    return Code(html=html, language=alias, lines=len(lines), line_numbers=line_numbers)
