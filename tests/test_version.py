"""Tests that every place that names the version of the integration agrees."""

import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "custom_components" / "sbm_fizikus" / "manifest.json"


def manifest_version() -> str:
    """Return the version of the manifest, which the release tag must equal."""
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    return version


def test_pyproject_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == manifest_version()


def test_lock_file_version() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = next(
        package
        for package in lock["package"]
        if package["name"] == "homeassistant-sbm-fizikus"
    )

    assert project["version"] == manifest_version()


def test_changelog_version() -> None:
    """The newest release of the changelog, below an optional Unreleased section.

    The release workflow takes the notes of a release from its section.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", changelog, flags=re.MULTILINE)
    releases = [heading for heading in headings if heading != "Unreleased"]

    assert "Unreleased" not in headings[1:]
    assert re.fullmatch(
        rf"{re.escape(manifest_version())} \(\d{{4}}-\d{{2}}-\d{{2}}\)", releases[0]
    )


def test_design_document_version() -> None:
    design = (ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert re.findall(r"It describes version (\d+\.\d+\.\d+)\.", design) == [
        manifest_version()
    ]
