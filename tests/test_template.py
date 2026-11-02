"""The plugin template (`template/`, MSHL-211): it must stay a working plugin that the
catalogue accepts, with a release workflow that obeys the same pinning rules.

The template is a folder of this repository, not a package: its own tests run in a copy
(`MESHLOOM_DIR` points at the pinned meshloom checkout, as for the rest of this suite).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import validate_catalogue as vc
import yaml
from conftest import meshloom_dir
from test_validate import all_errors, one_release, submit

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
RELEASE = TEMPLATE / ".github" / "workflows" / "release.yml"
VALIDATE = ROOT / ".github" / "workflows" / "validate.yml"
# What a published template must hold (the folder is copied as is into a new repository).
FILES = [
    "LICENSE",
    "README.md",
    "README.fr.md",
    "pyproject.toml",
    ".gitignore",
    ".github/workflows/release.yml",
    "plugin/manifest.json",
    "plugin/plugin.py",
    "plugin/config.schema.json",
    "plugin/LICENSE",
    "plugin/README.md",
    "plugin/README.fr.md",
    "plugin/i18n/en.json",
    "plugin/i18n/fr.json",
    "tests/conftest.py",
    "tests/test_plugin.py",
    "tests/replay.json",
]


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def pack(tmp_path: Path) -> tuple[bytes, str]:
    """`pack` with the pinned meshloom, on a copy of the template's plugin folder."""
    root = meshloom_dir()
    plugin = tmp_path / "plugin"
    shutil.copytree(TEMPLATE / "plugin", plugin)
    out = tmp_path / "dist"
    done = subprocess.run(
        [
            str(root / ".venv" / "bin" / "python"),
            "-m",
            "meshloom_plugin.dev",
            "pack",
            str(plugin),
            "--strict",
            "--out",
            str(out),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    digest, _ = (out / "plugin.zip.sha256").read_text(encoding="utf-8").split()
    return (out / "plugin.zip").read_bytes(), digest


# --- the folder -----------------------------------------------------------------------


@pytest.mark.parametrize("name", FILES)
def test_the_template_has_its_files(name):
    assert (TEMPLATE / name).is_file(), name


def test_the_template_holds_nothing_else():
    found = {
        p.relative_to(TEMPLATE).as_posix()
        for p in TEMPLATE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert found == set(FILES)


def test_the_licence_has_a_placeholder_for_the_author():
    for name in ("LICENSE", "plugin/LICENSE"):
        text = (TEMPLATE / name).read_text(encoding="utf-8")
        assert "YOUR NAME" in text and "TwinRocket" not in text


def test_the_template_tests_pass_in_a_copy(tmp_path):
    root = meshloom_dir()
    copy = tmp_path / "copy"
    shutil.copytree(TEMPLATE, copy)
    env = {**os.environ, "MESHLOOM_DIR": str(root), "REQUIRE_MESHLOOM": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=copy,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --- the archive it produces is accepted by the catalogue ----------------------------


def test_packed_archive_passes_the_catalogue_validation(catalogue, server, make_ctx, tmp_path):
    body, digest = pack(tmp_path)
    base, head = submit(
        catalogue, server, body, entry_id="acme.hello", repo="acme/meshloom-hello", digest=digest
    )
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert report.ok, all_errors(report)
    assert one_release(report).french["name"] == "yes"


def test_packed_archive_passes_the_sandboxed_dev_check(
    catalogue, server, make_ctx, sandbox, tmp_path
):
    body, digest = pack(tmp_path)
    base, head = submit(
        catalogue, server, body, entry_id="acme.hello", repo="acme/meshloom-hello", digest=digest
    )
    report = vc.run(catalogue.root, base, head, make_ctx(sandbox))
    assert report.ok, all_errors(report)
    assert one_release(report).dry_run_calls is not None


# --- the release workflow -------------------------------------------------------------


def test_the_release_workflow_uses_the_same_meshloom_pin_as_the_catalogue():
    assert load(RELEASE)["env"]["MESHLOOM_REF"] == load(VALIDATE)["env"]["MESHLOOM_REF"]
    assert load(RELEASE)["env"]["MESHLOOM_REPO"] == load(VALIDATE)["env"]["MESHLOOM_REPO"]


def test_the_release_workflow_triggers_on_version_tags_and_by_hand_only():
    workflow = load(RELEASE)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"push": {"tags": ["v*"]}, "workflow_dispatch": None}


def test_the_release_workflow_has_least_privilege():
    workflow = load(RELEASE)
    assert workflow["permissions"] == {}
    (job,) = workflow["jobs"].values()
    assert job["permissions"] == {"contents": "write"}
    assert job["runs-on"] == "ubuntu-latest" and 0 < job["timeout-minutes"] <= 15
    text = RELEASE.read_text(encoding="utf-8")
    assert "secrets" not in text.lower() and "pull_request" not in text
    assert "persist-credentials: false" in text


def test_the_release_workflow_pins_its_actions_like_the_catalogue():
    allowed = {"actions/checkout", "astral-sh/setup-uv"}
    pinned = {
        m.group(1): m.group(2)
        for m in re.finditer(
            r"uses: ([\w./-]+)@([0-9a-f]{40})", VALIDATE.read_text(encoding="utf-8")
        )
    }
    used = re.findall(r"uses: (\S+)", RELEASE.read_text(encoding="utf-8"))
    assert used
    for action in used:
        name, _, ref = action.partition("@")
        assert name in allowed, name
        assert ref == pinned[name], f"{name} must use the SHA the catalogue pins"


def test_the_release_workflow_never_replaces_an_asset():
    text = RELEASE.read_text(encoding="utf-8")
    assert "--clobber" not in text and "gh release upload" not in text
    assert "gh release create" in text and "--verify-tag" in text
    for asset in ("dist/plugin.zip", "dist/plugin.zip.sha256", "plugin/manifest.json"):
        assert asset in text
