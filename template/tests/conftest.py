"""Runs the Meshloom plugin tooling (`python -m meshloom_plugin.dev`) on `plugin/`.

The tooling lives in the Meshloom repository and imports its server code, so these
tests need a Meshloom checkout with its dependencies installed:

    git clone https://github.com/TwinRocket/meshloom .meshloom
    git -C .meshloom checkout <the commit of MESHLOOM_REF in .github/workflows/release.yml>
    (cd .meshloom && uv sync --frozen --no-dev)
    MESHLOOM_DIR=.meshloom uv run pytest

Without `MESHLOOM_DIR` those tests are skipped; the release workflow sets
`REQUIRE_MESHLOOM=1` so that a missing checkout fails the release instead.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
REPLAY = ROOT / "tests" / "replay.json"


def meshloom_dir() -> Path:
    raw = os.environ.get("MESHLOOM_DIR", "")
    if raw and Path(raw).is_dir():
        return Path(raw).resolve()
    if os.environ.get("REQUIRE_MESHLOOM") == "1":
        pytest.fail("REQUIRE_MESHLOOM=1 but MESHLOOM_DIR is not a Meshloom checkout")
    pytest.skip("MESHLOOM_DIR (a Meshloom checkout with its .venv) is not set")


def dev(*args: str | Path) -> subprocess.CompletedProcess[str]:
    """`python -m meshloom_plugin.dev <args>` in the Meshloom checkout's own interpreter."""
    root = meshloom_dir()
    return subprocess.run(  # noqa: S603
        [str(root / ".venv" / "bin" / "python"), "-m", "meshloom_plugin.dev", *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def dev_json(*args: str | Path) -> dict[str, Any]:
    out = dev(*args, "--json")
    assert out.stdout, out.stderr
    return json.loads(out.stdout)
