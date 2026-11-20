import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def job(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", WORKFLOW, re.M | re.S)
    assert match is not None, name
    return match.group(1)


def test_the_tests_run_in_the_playwright_image_of_the_pin():
    pin = re.search(r"^playwright==(\S+)$", (ROOT / "requirements.txt").read_text(), re.M)
    image = re.search(
        r"^\s+image: mcr\.microsoft\.com/playwright/python:v(\S+)-noble$", job("test"), re.M
    )
    assert pin is not None
    assert image is not None
    assert image.group(1) == pin.group(1)


def test_the_render_tests_must_not_be_skipped_in_ci():
    assert re.search(r"pytest .*--require-chromium", job("test"))


def test_every_action_is_pinned_by_commit():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action


def test_no_image_is_tagged_latest():
    assert "latest" not in WORKFLOW


def test_only_the_release_writes_packages():
    assert WORKFLOW.count("packages: write") == 1
    assert "packages: write" in job("release")
    assert re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.M)


def test_the_release_runs_on_tags_after_the_tests():
    release = job("release")
    assert "if: github.ref_type == 'tag'" in release
    assert re.search(r"^    needs: test$", release, re.M)
    assert 'GITHUB_REF_NAME}" != "v${version}"' in release


def test_both_images_are_smoke_tested_before_they_are_pushed():
    release = job("release")
    for name in ("assets", "mcp"):
        smoke = release.index(f"bin/smoke {name} ")
        push = release.index(f'docker push "${{REGISTRY}}/{name}:')
        assert smoke < push, name
