"""The plugin against Meshloom's own tooling, and the files the tooling does not compare.

Add a case to `replay.json` and an assertion here for every behaviour you add.
"""

from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

from conftest import PLUGIN, REPLAY, dev, dev_json

MANIFEST = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))


# --- Meshloom's checks (the CI of the catalogue runs the same) -----------------------


def test_check_passes():
    report = dev_json("check", PLUGIN)
    assert report["ok"], report["errors"]
    assert report["plugin"]["id"] == MANIFEST["id"]


def test_check_strict_passes():
    # --strict turns the blocking findings of the static scan into errors.
    assert dev_json("check", PLUGIN, "--strict")["ok"]


def test_replay_answers_the_command_only():
    report = dev_json("run", PLUGIN, "--replay", REPLAY)
    assert report["ok"], report["errors"]
    assert [send["text"] for send in report["sends"]] == ["Hello Alice!", "Hello Carol!"]
    assert report["refusals"] == []


def test_pack_is_reproducible_and_holds_only_the_plugin(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    assert dev("pack", PLUGIN, "--out", first).returncode == 0
    assert dev("pack", PLUGIN, "--out", second).returncode == 0
    archive = first / "plugin.zip"
    assert archive.read_bytes() == (second / "plugin.zip").read_bytes()
    digest, name = (first / "plugin.zip.sha256").read_text(encoding="utf-8").split()
    assert name == "plugin.zip" and len(digest) == 64
    names = zipfile.ZipFile(archive).namelist()
    assert "manifest.json" in names and "plugin.py" in names
    assert not [n for n in names if n.startswith(("tests/", ".github/")) or n.endswith(".pyc")]


# --- What nothing else compares ------------------------------------------------------


def test_every_declared_file_exists():
    declared = [MANIFEST["entry"].split(":")[0], "LICENSE", MANIFEST["config_schema"]]
    declared += [*MANIFEST["readme"].values(), *MANIFEST["i18n"].values()]
    for path in declared:
        assert (PLUGIN / path).is_file(), path


def test_texts_in_plugin_py_equal_the_i18n_files():
    # The plugin sandbox allows no file access, so plugin.py keeps the texts inline; the
    # i18n files are the copy translators and reviewers read. They must not drift apart.
    tree = ast.parse((PLUGIN / "plugin.py").read_text(encoding="utf-8"))
    inline = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TEXTS"
    )
    files = {
        lang: json.loads((PLUGIN / path).read_text(encoding="utf-8"))
        for lang, path in MANIFEST["i18n"].items()
    }
    assert inline == files


def test_english_and_french_are_both_there():
    for field in ("name", "summary"):
        assert {"en", "fr"} <= set(MANIFEST[field]), field
    assert {"en", "fr"} <= set(MANIFEST["readme"]) and {"en", "fr"} <= set(MANIFEST["i18n"])
    schema = json.loads((PLUGIN / "config.schema.json").read_text(encoding="utf-8"))
    for key, prop in schema["properties"].items():
        for text in ("title", "description"):
            if text in prop:
                assert {"en", "fr"} <= set(prop[text]), f"{key}.{text}"


def test_the_secret_field_is_write_only():
    schema = json.loads((PLUGIN / "config.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["api_key"]["writeOnly"] is True
    assert "default" not in schema["properties"]["api_key"]


def test_the_licence_is_declared_and_shipped():
    assert (PLUGIN / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert MANIFEST["license"] == "MIT"


def test_no_stray_build_output_in_the_plugin_folder():
    stray = [p for p in Path(PLUGIN).rglob("*") if p.name in {"plugin.zip", "plugin.zip.sha256"}]
    assert stray == []
