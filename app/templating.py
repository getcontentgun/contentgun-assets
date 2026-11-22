import math
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from markupsafe import Markup, escape

from app.code import Code, CodeError, CodeSample
from app.fonts import FontSet, load_font_set
from app.formats import Format, dimensions, filename_token
from app.hyphenation import hyphenate
from app.images import ACCEPTED, file_data_uri
from app.schemas import MAX_FIELD_LENGTH

BUNDLE_FILE = "bundle.toml"
TEMPLATE_FILE = "template.html.j2"
CSS_FILE = "template.css"
META_FILE = "template.toml"
FONTS_DIR = "fonts"
ASSETS_DIR = "assets"

BUNDLE_KEYS = frozenset({"name", "description", "fonts"})
CODE_SAMPLE_KEYS = frozenset({"code", "language", "line_numbers", "highlight_lines"})
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
NAME_RULE = "lowercase letters, digits and hyphens, starting with a letter or a digit"


class UnknownTemplateError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    bundle: str
    name: str
    description: str
    formats: tuple[Format, ...]
    fields: tuple[str, ...]
    required: tuple[str, ...]
    defaults: dict[str, str]
    samples: dict[str, str]
    accepts_image: bool
    accepts_icon: bool
    fallback_image: Path | None
    limits: dict[str, int]
    directory: Path
    accepts_code: bool = False
    code_sample: CodeSample | None = None

    def supports(self, fmt: Format) -> bool:
        return fmt in self.formats


@dataclass(eq=False)
class Bundle:
    name: str
    description: str
    directory: Path
    fonts: FontSet
    specs: dict[str, TemplateSpec]
    environment: Environment


def skipped(directory: Path) -> bool:
    return directory.name.startswith(("_", "."))


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _code_sample(table: object, invalid: Callable[[str], RuntimeError]) -> CodeSample | None:
    if table is None:
        return None
    if not isinstance(table, dict):
        raise invalid("has a code_sample that is not a table")
    unknown = sorted(set(table) - CODE_SAMPLE_KEYS)
    if unknown:
        raise invalid(f"has the unknown key {unknown[0]!r} in [code_sample]")
    code = table.get("code")
    language = table.get("language")
    line_numbers = table.get("line_numbers", False)
    highlight_lines = table.get("highlight_lines", [])
    if not isinstance(code, str) or not code.strip():
        raise invalid("has a [code_sample] without code")
    if language is not None and not isinstance(language, str):
        raise invalid("has a [code_sample] whose language is not a string")
    if not isinstance(line_numbers, bool):
        raise invalid("has a [code_sample] whose line_numbers is neither true nor false")
    if not isinstance(highlight_lines, list) or any(
        type(number) is not int for number in highlight_lines
    ):
        raise invalid("has a [code_sample] whose highlight_lines is not a list of line numbers")
    sample = CodeSample(code, language, line_numbers, tuple(highlight_lines))
    try:
        sample.check()
    except CodeError as exc:
        raise invalid(f"has a [code_sample] that cannot be shown: {str(exc).rstrip('.')}") from None
    return sample


def _load_spec(directory: Path, bundle: str, bundle_dir: Path) -> TemplateSpec:
    name = directory.name
    template_id = f"{bundle}/{name}"
    meta_path = directory / META_FILE

    def invalid(problem: str) -> RuntimeError:
        return RuntimeError(f"{meta_path}: Template {template_id!r} {problem}.")

    if not NAME.fullmatch(name):
        raise RuntimeError(f"{directory}: The template name {name!r} must use {NAME_RULE}.")
    if not _inside(directory, bundle_dir):
        raise RuntimeError(f"{directory}: Template {template_id!r} lies outside its bundle.")
    for required_file in (META_FILE, TEMPLATE_FILE):
        if not (directory / required_file).is_file():
            raise RuntimeError(f"{directory}: Template {template_id!r} has no {required_file}.")
    for path in (meta_path, directory / TEMPLATE_FILE, directory / CSS_FILE):
        if path.exists() and not _inside(path, bundle_dir):
            raise RuntimeError(f"{path}: Template {template_id!r} reaches outside its bundle.")

    try:
        with meta_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise invalid(f"is not valid TOML: {exc}") from None

    declared_name = raw.get("name", name)
    if declared_name != name:
        raise invalid(f"names itself {declared_name!r}, which does not match its directory name")

    try:
        formats = tuple(Format(value) for value in raw.get("formats", []))
    except ValueError as exc:
        raise invalid(f"declares an unknown format: {exc}") from None
    if not formats:
        raise invalid("declares no formats")

    fields = tuple(raw.get("fields", ()))
    required = tuple(raw.get("required", ()))
    unknown_required = set(required) - set(fields)
    if unknown_required:
        raise invalid(f"requires fields it does not declare: {sorted(unknown_required)}")

    defaults = {key: str(value) for key, value in raw.get("defaults", {}).items()}
    unknown_defaults = set(defaults) - set(fields)
    if unknown_defaults:
        raise invalid(f"defaults undeclared fields: {sorted(unknown_defaults)}")

    samples = {key: str(value) for key, value in raw.get("samples", {}).items()}
    unknown_samples = set(samples) - set(fields)
    if unknown_samples:
        raise invalid(f"has samples for undeclared fields: {sorted(unknown_samples)}")
    unsampled = [field for field in required if not samples.get(field, "").strip()]
    if unsampled:
        raise invalid(f"has no sample for the required field {unsampled[0]!r}")

    limits = dict(raw.get("limits", {}))
    unknown_limits = set(limits) - set(fields)
    if unknown_limits:
        raise invalid(f"limits undeclared fields: {sorted(unknown_limits)}")
    for field, limit in limits.items():
        if type(limit) is not int or not 0 < limit <= MAX_FIELD_LENGTH:
            raise invalid(
                f"limits {field!r} to {limit!r}, which is not a whole number from 1 to "
                f"{MAX_FIELD_LENGTH}"
            )
    too_long = [
        field
        for field, sample in samples.items()
        if len(sample) > limits.get(field, MAX_FIELD_LENGTH)
    ]
    if too_long:
        raise invalid(f"has a sample for {too_long[0]!r} that exceeds its limit")

    accepts_image = bool(raw.get("image", False))
    accepts_icon = bool(raw.get("icon", False))
    fallback_image = None
    declared_fallback = raw.get("fallback_image")
    if declared_fallback:
        if not accepts_image:
            raise invalid("declares a fallback image without setting image = true")
        if accepts_icon:
            raise invalid(
                "declares a fallback image and icon = true, so a card with an icon would carry "
                "the fallback image as well"
            )
        assets = bundle_dir / ASSETS_DIR
        fallback_image = assets / str(declared_fallback)
        if not fallback_image.is_file() or not _inside(fallback_image, assets):
            raise invalid(
                f"declares the fallback image {declared_fallback!r}, which is not in {assets}"
            )
        # Once here, so a file in the wrong format stops the start instead of every render.
        try:
            file_data_uri(fallback_image)
        except OSError as exc:
            raise invalid(
                f"declares the fallback image {declared_fallback!r}, which cannot be read: "
                f"{exc.strerror or exc}"
            ) from None
        except RuntimeError:
            raise invalid(
                f"declares the fallback image {declared_fallback!r}, which is not a {ACCEPTED} file"
            ) from None

    accepts_code = bool(raw.get("code", False))
    code_sample = _code_sample(raw.get("code_sample"), invalid)
    if accepts_code and code_sample is None:
        raise invalid("sets code = true without a [code_sample] table")
    if code_sample is not None and not accepts_code:
        raise invalid("has a [code_sample] table without setting code = true")

    return TemplateSpec(
        id=template_id,
        bundle=bundle,
        name=name,
        description=raw.get("description", ""),
        formats=formats,
        fields=fields,
        required=required,
        defaults=defaults,
        samples=samples,
        accepts_image=accepts_image,
        accepts_icon=accepts_icon,
        fallback_image=fallback_image,
        limits=limits,
        directory=directory,
        accepts_code=accepts_code,
        code_sample=code_sample,
    )


def highlight(text: str, phrase: str) -> Markup:
    """Hyphenate the text and wrap the first whole-word, case-insensitive occurrence of the
    phrase in <em>. Text without that phrase is returned hyphenated and unmarked."""
    wanted = phrase.strip()
    match = None
    if wanted:
        match = re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", text, re.IGNORECASE)
    if match is None:
        return escape(hyphenate(text))
    return Markup("{}<em>{}</em>{}").format(
        hyphenate(text[: match.start()]),
        hyphenate(match.group()),
        hyphenate(text[match.end() :]),
    )


def percent(value: str, default: float = 50) -> float:
    """A position from 0 to 100 for CSS. Accepts a trailing %; blank or unreadable input
    falls back to the default, so a typo moves the crop instead of breaking the render."""
    try:
        number = float(value.strip().removesuffix("%"))
    except (AttributeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return min(max(number, 0.0), 100.0)


def registered(text: str) -> Markup:
    """Escape the text unless it is markup already and wrap every ® in <sup>, so a template
    can style the mark."""
    return Markup(str(escape(text)).replace("®", '<sup class="registered">®</sup>'))


def inline_svg(assets: Path, file_name: str) -> Markup:
    path = assets / file_name
    if not path.is_file() or not _inside(path, assets):
        raise RuntimeError(f"Unknown asset {file_name!r} in {assets}.")
    return Markup(path.read_text(encoding="utf-8"))


class BundleLoader(FileSystemLoader):
    """Jinja refuses '..' in a template name on its own; this also refuses a file whose real
    path leaves the bundle, as through a symlink."""

    def __init__(self, root: Path):
        super().__init__(root)
        self._root = root

    def get_source(self, environment: Environment, template: str):
        source, filename, uptodate = super().get_source(environment, template)
        if not _inside(Path(filename), self._root):
            raise TemplateNotFound(template)
        return source, filename, uptodate


def _environment(bundle_dir: Path) -> Environment:
    environment = Environment(
        loader=BundleLoader(bundle_dir),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        auto_reload=True,
    )
    environment.globals["inline_svg"] = partial(inline_svg, bundle_dir / ASSETS_DIR)
    environment.filters["hyphenate"] = hyphenate
    environment.filters["highlight"] = highlight
    environment.filters["percent"] = percent
    environment.filters["registered"] = registered
    return environment


def _load_bundle(directory: Path) -> Bundle:
    name = directory.name
    if not NAME.fullmatch(name):
        raise RuntimeError(f"{directory}: The bundle name {name!r} must use {NAME_RULE}.")
    # Resolved once, so a bundle linked into the root works and everything inside it is
    # measured against its real location.
    bundle_dir = directory.resolve()
    source = bundle_dir / BUNDLE_FILE

    def invalid(problem: str) -> RuntimeError:
        return RuntimeError(f"{source}: Bundle {name!r} {problem}.")

    if not source.is_file():
        raise RuntimeError(f"{directory}: Bundle {name!r} has no {BUNDLE_FILE}.")
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise invalid(f"is not valid TOML: {exc}") from None
    unknown = sorted(set(raw) - BUNDLE_KEYS)
    if unknown:
        raise invalid(f"has the unknown key {unknown[0]!r}")
    declared_name = raw.get("name", name)
    if declared_name != name:
        raise invalid(f"names itself {declared_name!r}, which does not match its directory name")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise invalid("has a description that is not a string")

    fonts = load_font_set(raw.get("fonts"), bundle_dir / FONTS_DIR, source, name)

    reserved = {FONTS_DIR, ASSETS_DIR}
    directories = (
        path
        for path in bundle_dir.iterdir()
        if path.is_dir() and not skipped(path) and path.name not in reserved
    )
    specs = {}
    for template_dir in sorted(directories):
        spec = _load_spec(template_dir, name, bundle_dir)
        specs[spec.name] = spec
    if not specs:
        raise RuntimeError(f"{directory}: Bundle {name!r} has no templates.")

    return Bundle(
        name=name,
        description=description,
        directory=bundle_dir,
        fonts=fonts,
        specs=specs,
        environment=_environment(bundle_dir),
    )


def load_bundles(root: Path) -> dict[str, Bundle]:
    """Every bundle in root by name, validated. A root without bundles gives an empty dict."""
    if not root.is_dir():
        raise RuntimeError(f"{root}: The bundle directory does not exist.")
    bundles = {}
    directories = (path for path in root.iterdir() if path.is_dir() and not skipped(path))
    for directory in sorted(directories):
        bundle = _load_bundle(directory)
        bundles[bundle.name] = bundle
    return bundles


class TemplateRegistry:
    """The templates one caller can use, from bundles that other registries may share, and the
    template a request without one gets."""

    def __init__(self, bundles: dict[str, Bundle], default_template: str | None = None):
        self._bundles = bundles
        self._specs = {
            spec.id: spec for bundle in bundles.values() for spec in bundle.specs.values()
        }
        self.default_template = default_template

    @classmethod
    def load(cls, root: Path, default_template: str | None = None) -> "TemplateRegistry":
        bundles = load_bundles(root)
        if not bundles:
            raise RuntimeError(f"{root}: No bundles found.")
        return cls(bundles, default_template)

    @property
    def ids(self) -> list[str]:
        return sorted(self._specs)

    @property
    def specs(self) -> list[TemplateSpec]:
        return [self._specs[template_id] for template_id in self.ids]

    @property
    def bundles(self) -> list[Bundle]:
        return [self._bundles[name] for name in sorted(self._bundles)]

    def bundle(self, name: str) -> Bundle:
        return self._bundles[name]

    def get(self, template_id: str) -> TemplateSpec:
        try:
            return self._specs[template_id]
        except KeyError:
            raise UnknownTemplateError(template_id) from None

    def font_checks(self, spec: TemplateSpec) -> tuple[str, ...]:
        return self._bundles[spec.bundle].fonts.checks

    def _css(self, bundle: Bundle, spec: TemplateSpec) -> str:
        # Deliberately uncached: the design loop is edit template.css and reload, and a
        # few kilobytes of disk read is nothing next to a screenshot.
        path = spec.directory / CSS_FILE
        if not path.is_file():
            return ""
        if not _inside(path, bundle.directory):
            raise RuntimeError(f"{path} lies outside the bundle {bundle.name!r}.")
        return path.read_text(encoding="utf-8")

    def render_html(
        self,
        spec: TemplateSpec,
        fmt: Format,
        fields: dict[str, str],
        fit_text: bool = True,
        image: str | None = None,
        icon: str | None = None,
        code: Code | None = None,
    ) -> str:
        if not spec.supports(fmt):
            raise UnsupportedFormatError(fmt)

        bundle = self._bundles[spec.bundle]
        width, height = dimensions(fmt)
        template = bundle.environment.get_template(f"{spec.name}/{TEMPLATE_FILE}")
        return template.render(
            width=width,
            height=height,
            format=fmt.value,
            format_token=filename_token(fmt),
            fields=fields,
            image=image,
            icon=icon,
            code=code,
            css=Markup(self._css(bundle, spec)),
            font_css=Markup(bundle.fonts.css),
            font_checks=list(bundle.fonts.checks),
            fit_text=fit_text,
        )
