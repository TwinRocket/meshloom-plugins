"""`schema/` is a byte-for-byte copy of meshloom's plugin contract at the pinned commit
(`MESHLOOM_REF` in .github/workflows/validate.yml), and the copies are usable."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml
from conftest import meshloom_dir

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema"
COPIES = {
    "manifest.schema.json": "docs/dev/plugins/manifest.schema.json",
    "vectors/manifest.json": "docs/dev/plugins/vectors/manifest.json",
}


def pinned_ref() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/validate.yml").read_text())
    return workflow["env"]["MESHLOOM_REF"]


def test_the_meshloom_checkout_is_at_the_pin():
    root = meshloom_dir()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=True, text=True
    ).stdout.strip()
    assert head == pinned_ref()


@pytest.mark.parametrize(("copy", "source"), COPIES.items())
def test_the_schema_copies_equal_the_pin(copy, source):
    root = meshloom_dir()
    assert (SCHEMA / copy).read_bytes() == (root / source).read_bytes(), (
        f"schema/{copy} differs from meshloom {pinned_ref()}:{source}; copy it again"
    )


def test_valid_vectors_pass_the_contract_schema():
    contract = json.loads((SCHEMA / "manifest.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(contract)
    vectors = json.loads((SCHEMA / "vectors/manifest.json").read_text())["vectors"]
    valid = [v for v in vectors if v["valid"]]
    assert valid
    for vector in valid:
        assert not list(validator.iter_errors(vector["manifest"])), vector["name"]


def test_the_entry_schema_is_valid_and_accepts_an_example():
    schema = json.loads((SCHEMA / "entry.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    example = {
        "id": "acme.echo",
        "repo": "acme/meshloom-echo",
        "tier": "reviewed",
        "releases": [{"version": "1.0.0", "tag": "v1.0.0", "sha256": "a" * 64}],
    }
    assert not list(jsonschema.Draft202012Validator(schema).iter_errors(example))


def test_the_fixture_plugin_is_the_reference_plugin():
    """tests/fixtures/sample_plugin is meshloom's tests/plugin_dev/echo at the pin."""
    root = meshloom_dir()
    fixture = Path(__file__).parent / "fixtures" / "sample_plugin"
    source = root / "tests" / "plugin_dev" / "echo"
    assert sorted(p.name for p in fixture.iterdir()) == sorted(p.name for p in source.iterdir())
    for path in fixture.iterdir():
        assert path.read_bytes() == (source / path.name).read_bytes(), path.name
