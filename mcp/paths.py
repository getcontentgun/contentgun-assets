import os
import tempfile
from pathlib import Path

from errors import AssetsError


def _resolve(path: str, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def output_file(path: str, root: Path, overwrite: bool) -> Path:
    target = _resolve(path, root)
    if not target.is_relative_to(root):
        raise AssetsError(f"{path} is outside the output folder {root}.")
    if target.suffix.lower() != ".png":
        raise AssetsError(f"The output path must end in .png, and {path} does not.")
    if target.is_dir():
        raise AssetsError(f"{target} is a folder, not a file name.")
    if target.exists() and not overwrite:
        raise AssetsError(f"{target} already exists. Set overwrite to true to replace it.")
    return target


def output_folder(path: str, root: Path) -> Path:
    target = _resolve(path, root)
    if not target.is_relative_to(root):
        raise AssetsError(f"{path} is outside the output folder {root}.")
    if target == root:
        raise AssetsError(f"Choose a folder below {root} for the carousel, not {root} itself.")
    if target.exists() and not target.is_dir():
        raise AssetsError(f"{target} is a file, not a folder.")
    return target


def image_file(path: str, roots: tuple[Path, ...], kind: str = "image") -> Path:
    source = _resolve(path, roots[0])
    if not any(source.is_relative_to(root) for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise AssetsError(f"{path} is outside the folders images may come from: {allowed}.")
    if not source.exists():
        raise AssetsError(f"The {kind} {source} does not exist.")
    if not source.is_file():
        raise AssetsError(f"The {kind} {source} is not a file.")
    return source


def write_file(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # A temporary file next to the target and os.replace, so a failure never leaves half a PNG.
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".part"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
