"""Shared helpers: a loopback release server, a throwaway catalogue git repository,
plugin archives built from `tests/fixtures/sample_plugin`, and the sandbox.

`MESHLOOM_DIR` points at a meshloom checkout at the pinned commit (with its `.venv`).
Tests that need it, or `bwrap`, skip locally; with `CATALOGUE_STRICT=1` (the CI) they
fail instead, so nothing is silently skipped where it matters.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import validate_catalogue as vc

FIXTURE = Path(__file__).parent / "fixtures" / "sample_plugin"
STRICT = os.environ.get("CATALOGUE_STRICT") == "1"


def need(condition: bool, reason: str) -> None:
    """Skip locally, fail in the CI (`CATALOGUE_STRICT=1`)."""
    if condition:
        return
    if STRICT:
        pytest.fail(f"required in the CI: {reason}")
    pytest.skip(reason)


def meshloom_dir() -> Path:
    raw = os.environ.get("MESHLOOM_DIR")
    need(bool(raw) and Path(raw or "").is_dir(), "MESHLOOM_DIR (pinned meshloom checkout)")
    return Path(raw or "").resolve()


@pytest.fixture
def sandbox() -> vc.Sandbox:
    root = meshloom_dir()
    need(shutil.which("bwrap") is not None, "bwrap (bubblewrap)")
    return vc.Sandbox(root, root / ".venv" / "bin" / "python")


# --- archives -----------------------------------------------------------------


def sample_manifest(**changes: Any) -> dict[str, Any]:
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(changes)
    return manifest


def make_zip(
    manifest: dict[str, Any] | None = None,
    extra: dict[str, bytes] | None = None,
    mode: int = 0o644,
) -> bytes:
    """The fixture plugin as a release archive, with an optional manifest and files."""
    files = {p.name: p.read_bytes() for p in sorted(FIXTURE.iterdir()) if p.is_file()}
    if manifest is not None:
        files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    files.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- release server -----------------------------------------------------------


@dataclass
class Asset:
    body: bytes
    head_length: int | None = None  # Content-Length announced on HEAD (default: real)
    get_length: bool = True  # send Content-Length on GET


@dataclass
class ReleaseServer:
    port: int
    assets: dict[str, Asset] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)

    @property
    def template(self) -> str:
        return f"http://127.0.0.1:{self.port}/{{repo}}/{{tag}}/plugin.zip"

    def publish(self, repo: str, tag: str, body: bytes, **kw: Any) -> None:
        self.assets[f"/{repo}/{tag}/plugin.zip"] = Asset(body, **kw)

    def gets(self) -> list[str]:
        return [path for method, path in self.requests if method == "GET"]


@pytest.fixture
def server() -> Iterator[ReleaseServer]:
    state = ReleaseServer(port=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _asset(self) -> Asset | None:
            state.requests.append((self.command, self.path))
            asset = state.assets.get(self.path)
            if asset is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return asset

        def do_HEAD(self) -> None:  # noqa: N802
            if (asset := self._asset()) is None:
                return
            self.send_response(200)
            length = asset.head_length if asset.head_length is not None else len(asset.body)
            self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if (asset := self._asset()) is None:
                return
            self.send_response(200)
            if asset.get_length:
                self.send_header("Content-Length", str(len(asset.body)))
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(asset.body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- catalogue repository -----------------------------------------------------


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


@dataclass
class Catalogue:
    root: Path

    def git(self, *args: str) -> str:
        env = {**os.environ, **GIT_ENV}
        out = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            check=True,
        )
        return out.stdout.decode().strip()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def remove(self, rel: str) -> None:
        (self.root / rel).unlink()

    def commit(self, message: str = "change") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def catalogue(tmp_path: Path) -> Catalogue:
    repo = Catalogue(tmp_path / "catalogue")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.write("README.md", "catalogue\n")
    repo.commit("base")
    return repo


def entry_yaml(
    releases: list[dict[str, Any]],
    entry_id: str = "acme.echo",
    repo: str = "acme/echo",
    revoked: list[str] | None = None,
) -> str:
    lines = [f"id: {entry_id}", f"repo: {repo}", "tier: reviewed", "releases:"]
    for release in releases:
        lines.append(f'  - version: "{release["version"]}"')
        lines.append(f"    tag: {release['tag']}")
        lines.append(f'    sha256: "{release["sha256"]}"')
        if "permissions_changed" in release:
            lines.append(f"    permissions_changed: {str(release['permissions_changed']).lower()}")
    if revoked:
        lines.append("revoked: [" + ", ".join(revoked) + "]")
    return "\n".join(lines) + "\n"


@pytest.fixture
def make_ctx(tmp_path: Path, server: ReleaseServer) -> Callable[..., vc.Context]:
    def build(sandbox: vc.Sandbox | None = None) -> vc.Context:
        return vc.Context(
            work_root=tmp_path / "work",
            policy=vc.UrlPolicy(server.template),
            url_template=server.template,
            contract=vc.load_contract(),
            entry_schema=json.loads(vc.ENTRY_SCHEMA.read_text(encoding="utf-8")),
            sandbox=sandbox,
        )

    return build
