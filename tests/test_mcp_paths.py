import os

import pytest

from errors import AssetsError
from paths import image_file, output_file, output_folder, write_file


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "Social"
    folder.mkdir()
    return folder.resolve()


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "Elsewhere"
    folder.mkdir()
    return folder.resolve()


def test_relative_output_path_resolves_against_the_root(root):
    assert output_file("post/card.png", root, overwrite=False) == root / "post" / "card.png"


def test_absolute_output_path_inside_the_root_is_accepted(root):
    target = root / "card.png"
    assert output_file(str(target), root, overwrite=False) == target


@pytest.mark.parametrize("path", ["../card.png", "post/../../card.png"])
def test_output_path_cannot_climb_out_of_the_root(root, path):
    with pytest.raises(AssetsError, match="outside the output folder"):
        output_file(path, root, overwrite=False)


def test_output_path_cannot_leave_through_a_symlink(root, outside):
    (root / "link").symlink_to(outside)
    with pytest.raises(AssetsError, match="outside the output folder"):
        output_file("link/card.png", root, overwrite=False)


@pytest.mark.parametrize("path", ["card.jpg", "card", ""])
def test_output_path_must_be_a_png(root, path):
    with pytest.raises(AssetsError, match=r"must end in \.png"):
        output_file(path, root, overwrite=False)


def test_existing_file_needs_overwrite(root):
    (root / "card.png").write_bytes(b"old")
    with pytest.raises(AssetsError, match="overwrite"):
        output_file("card.png", root, overwrite=False)
    assert output_file("card.png", root, overwrite=True) == root / "card.png"


def test_a_folder_is_not_an_output_file(root):
    (root / "card.png").mkdir()
    with pytest.raises(AssetsError, match="is a folder"):
        output_file("card.png", root, overwrite=True)


def test_output_folder_resolves_and_may_not_exist_yet(root):
    assert output_folder("carousels/ki", root) == root / "carousels" / "ki"


@pytest.mark.parametrize("path", ["", ".", "post/.."])
def test_output_folder_cannot_be_the_root_itself(root, path):
    with pytest.raises(AssetsError, match="Choose a folder below"):
        output_folder(path, root)


def test_output_folder_cannot_climb_out_of_the_root(root):
    with pytest.raises(AssetsError, match="outside the output folder"):
        output_folder("../carousel", root)


def test_output_folder_cannot_be_a_file(root):
    (root / "carousel").write_text("x")
    with pytest.raises(AssetsError, match="is a file"):
        output_folder("carousel", root)


def test_image_inside_a_root_is_accepted(root, outside):
    image = outside / "photo.jpg"
    image.write_bytes(b"jpeg")
    assert image_file(str(image), (root, outside)) == image


def test_relative_image_path_resolves_against_the_first_root(root):
    (root / "photo.jpg").write_bytes(b"jpeg")
    assert image_file("photo.jpg", (root,)) == root / "photo.jpg"


def test_image_outside_every_root_is_refused(root, outside):
    image = outside / "photo.jpg"
    image.write_bytes(b"jpeg")
    with pytest.raises(AssetsError, match="outside the folders images may come from"):
        image_file(str(image), (root,))


def test_missing_image_is_refused(root):
    with pytest.raises(AssetsError, match="does not exist"):
        image_file("missing.jpg", (root,))


def test_a_folder_is_not_an_image(root):
    (root / "photos").mkdir()
    with pytest.raises(AssetsError, match="is not a file"):
        image_file("photos", (root,))


def test_write_file_creates_parents_and_leaves_no_temporary_file(root):
    target = root / "a" / "b" / "card.png"
    write_file(target, b"png")
    assert target.read_bytes() == b"png"
    assert os.listdir(target.parent) == ["card.png"]
    assert oct(target.stat().st_mode & 0o777) == oct(0o644)


def test_write_file_replaces_an_existing_file(root):
    target = root / "card.png"
    target.write_bytes(b"old")
    write_file(target, b"new")
    assert target.read_bytes() == b"new"


def test_a_failed_write_leaves_neither_file_nor_temporary(root, monkeypatch):
    def broken_replace(source, destination):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_file(root / "card.png", b"png")
    assert os.listdir(root) == []
