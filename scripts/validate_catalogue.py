"""Validate the catalogue entries a pull request changes (MSHL-200, `validate.yml`).

    python scripts/validate_catalogue.py --base <sha> [--head HEAD] --meshloom <checkout>
        [--summary FILE] [--work DIR]

For every `plugins/<id>.yaml` the pull request adds or changes since the merge base of
`--base` and `--head` (a branch behind main is judged on its own changes):

1. the entry: schema (`schema/entry.schema.json`), file name = id, id publisher = the
   GitHub owner of `repo` (lowercased; `meshloom.*` only from TwinRocket, `local.*`
   never), versions strictly increasing, existing id, repo, releases and revocations
   unchanged;
2. each new release: `Content-Length` of `plugin.zip` at the tag checked with a HEAD
   request before anything is downloaded, then a bounded download into its own fresh
   directory under the work root (never inside the repository), the sha256, the archive
   limits needed to extract it safely, extraction into another fresh directory, the
   manifest against the contract copy (`schema/manifest.schema.json`), id and version
   against the entry, the licences of `vendor/`, the permission diff with the previous
   release, the French translation status and the rendered source diff;
3. `python -m meshloom_plugin.dev check` from the pinned meshloom checkout, run inside
   bubblewrap (no network, read-only views, fresh /tmp, hard rlimits). The plugin's code is never
   executed any other way: without a working `bwrap` the release fails.

The server's own rules (licence list, reserved ids, misleading names, `https:` links,
native code, nested archives...) are applied by `dev check`, never re-implemented here;
the archive pre-check only covers what safe extraction needs and what re-zipping by
`dev check` would hide (links, executable bits, compression, hidden entries).

Untrusted strings never reach stdout or the job summary as workflow commands, Markdown
or HTML (see `plain` and `md`).

Exit status: 0 passed (including a pull request that changes no entry), 1 failed,
2 usage error.
"""

from __future__ import annotations

import argparse
import email.message
import email.parser
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
import yaml
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SCHEMA = REPO_ROOT / "schema" / "entry.schema.json"
CONTRACT_SCHEMA = REPO_ROOT / "schema" / "manifest.schema.json"

# Archive limits, as in meshloom's app/plugins/package.py at the pinned commit; the
# sandboxed `dev check` applies the server's full rules again on the extracted tree.
MAX_COMPRESSED_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_NAME_LENGTH = 300
MANIFEST_MAX_BYTES = 64 * 1024
MAX_ENTRY_FILE_BYTES = 64 * 1024
MAX_ENTRIES_PER_PR = 10
MAX_NEW_RELEASES_PER_ENTRY = 5
NATIVE_SUFFIX_RE = re.compile(r"\.(so|pyd|dll|dylib)(\.\d+)*$", re.IGNORECASE)
# What `dev check` leaves out when it zips a directory: it would never be checked.
LEFT_OUT_DIRS = {"__pycache__"}
LEFT_OUT_SUFFIXES = (".pyc", ".pyo")

DEFAULT_URL_TEMPLATE = "https://github.com/{repo}/releases/download/{tag}/plugin.zip"
GITHUB_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "meshloom-plugins-ci"
SANDBOX_TIMEOUT_SECONDS = 240
SUMMARY_DIFF_MAX_CHARS = 200_000
TWINROCKET = "twinrocket"

# PyPI classifiers that name exactly one accepted SPDX id. Others (BSD License, GPLv3
# without "only"...) are ambiguous: the package must carry License-Expression.
CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


class Failure(Exception):
    """A readable reason why a release or an entry is refused."""


# --- reports ------------------------------------------------------------------


@dataclass
class ReleaseReport:
    entry_id: str
    version: str
    previous: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    permissions: dict[str, Any] | None = None
    sensitive: bool = False
    french: dict[str, str] = field(default_factory=dict)
    diff_text: str = ""
    diff_lines: int | None = None
    dry_run_calls: int | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class EntryReport:
    path: str
    entry_id: str | None = None
    errors: list[str] = field(default_factory=list)
    releases: list[ReleaseReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(r.ok for r in self.releases)


@dataclass
class RunReport:
    errors: list[str] = field(default_factory=list)
    entries: list[EntryReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(e.ok for e in self.entries)


# --- downloads ----------------------------------------------------------------


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class UrlPolicy:
    """Which URLs (and redirects) a download may touch: HTTPS on GitHub's release hosts,
    or plain HTTP on a loopback host for the repository's tests only."""

    def __init__(self, template: str) -> None:
        sample = template.replace("{repo}", "o/r").replace("{tag}", "t")
        host = urllib.parse.urlsplit(sample).hostname or ""
        self.loopback = _is_loopback(host)
        self.hosts = {host} if self.loopback else set(GITHUB_HOSTS)

    def check(self, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        scheme_ok = parts.scheme == "https" or (self.loopback and parts.scheme == "http")
        if not scheme_ok or parts.hostname not in self.hosts or parts.username:
            raise Failure(
                f"refusing to fetch {parts.scheme}://{parts.hostname}: not a release host"
            )


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: UrlPolicy) -> None:
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.policy.check(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.method = req.get_method()  # a HEAD stays a HEAD
        return new


def _open(url: str, method: str, policy: UrlPolicy):
    policy.check(url)
    opener = urllib.request.build_opener(_Redirects(policy))
    # S310: the URL (and every redirect) passed UrlPolicy: https on release hosts only.
    headers = {"User-Agent": USER_AGENT}
    request = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310
    try:
        return opener.open(request, timeout=HTTP_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        raise Failure(f"{method} {url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise Failure(f"{method} {url} failed: {reason}") from None


def _content_length(headers: Any) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None or not raw.strip().isdigit():
        return None
    return int(raw)


def announced_size(url: str, policy: UrlPolicy) -> int:
    """HEAD the asset and return its Content-Length; refuse before any download."""
    with _open(url, "HEAD", policy) as response:
        size = _content_length(response.headers)
    if size is None:
        raise Failure(f"{url}: no Content-Length announced; nothing was downloaded")
    if size > MAX_COMPRESSED_BYTES:
        raise Failure(
            f"plugin.zip announces {size} bytes, over the {MAX_COMPRESSED_BYTES} byte limit "
            "(5 MiB); refused before download"
        )
    return size


def download(url: str, dest: Path, policy: UrlPolicy) -> str:
    """Bounded download to `dest` (a new file). Returns the sha256 hex digest."""
    digest = hashlib.sha256()
    received = 0
    with _open(url, "GET", policy) as response:
        size = _content_length(response.headers)
        if size is not None and size > MAX_COMPRESSED_BYTES:
            raise Failure(f"plugin.zip announces {size} bytes on GET; refused before download")
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            while chunk := response.read(65536):
                received += len(chunk)
                if received > MAX_COMPRESSED_BYTES:
                    raise Failure(
                        f"plugin.zip is larger than {MAX_COMPRESSED_BYTES} bytes "
                        "(more than announced); download stopped"
                    )
                digest.update(chunk)
                out.write(chunk)
    return digest.hexdigest()


# --- archive ------------------------------------------------------------------


def _unsafe_name(name: str) -> str | None:
    if not name or len(name) > MAX_NAME_LENGTH:
        return "entry name is empty or too long"
    if "\\" in name or any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
        return "entry name has a backslash, control or invisible characters"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return "absolute paths are refused"
    segments = name.rstrip("/").split("/")
    if any(s in ("", ".", "..") for s in segments):
        return "entry name escapes the package or is malformed"
    return None


def _check_info(info: zipfile.ZipInfo) -> None:
    name = info.filename
    shown = name[:80]
    if (reason := _unsafe_name(name)) is not None:
        raise Failure(f"plugin.zip: {shown!r}: {reason}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise Failure(f"plugin.zip: {shown}: symbolic links are refused")
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise Failure(f"plugin.zip: {shown}: only regular files and directories are allowed")
    if not info.is_dir() and mode & 0o111:
        raise Failure(f"plugin.zip: {shown}: executable files are refused")
    if info.flag_bits & 0x1:
        raise Failure(f"plugin.zip: {shown}: encrypted entries are refused")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise Failure(f"plugin.zip: {shown}: only stored and deflated entries are allowed")
    segments = name.rstrip("/").split("/")
    if NATIVE_SUFFIX_RE.search(segments[-1]):
        raise Failure(f"plugin.zip: {shown}: native code is refused")
    hidden = [s for s in segments if s.startswith(".") or s in LEFT_OUT_DIRS]
    if hidden or (not info.is_dir() and segments[-1].endswith(LEFT_OUT_SUFFIXES)):
        raise Failure(
            f"plugin.zip: {shown}: hidden entries, __pycache__ and bytecode are refused "
            "(dev check leaves them out, so they would be published unchecked)"
        )


def inspect_archive(path: Path) -> list[zipfile.ZipInfo]:
    """What safe extraction needs, from the central directory only. Raises Failure."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise Failure(f"plugin.zip is not a readable zip archive: {exc}") from None
    if len(infos) > MAX_ENTRIES:
        raise Failure(f"plugin.zip has {len(infos)} entries, over the {MAX_ENTRIES} limit")
    total = 0
    files: set[str] = set()
    dirs: set[str] = set()
    for info in infos:
        _check_info(info)
        shown = info.filename[:80]
        folded = unicodedata.normalize("NFC", info.filename.rstrip("/")).casefold()
        parts = folded.split("/")
        parents = {"/".join(parts[:i]) for i in range(1, len(parts))}
        if folded in files or parents & files:
            raise Failure(f"plugin.zip: {shown}: duplicate entry or a file used as a directory")
        dirs |= parents
        if info.is_dir():
            dirs.add(folded)
            continue
        if folded in dirs:
            raise Failure(f"plugin.zip: {shown}: duplicate entry or a file used as a directory")
        files.add(folded)
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise Failure(
                f"plugin.zip unpacks to more than {MAX_UNCOMPRESSED_BYTES} bytes (20 MiB)"
            )
    return infos


def extract(path: Path, dest: Path) -> None:
    """Extract an inspected archive into the empty directory `dest`, bounded by the
    declared sizes (a lying header stops the extraction). Files are 0644, dirs 0755."""
    infos = inspect_archive(path)
    root = dest.resolve()
    written = 0
    with zipfile.ZipFile(path) as zf:
        for info in infos:
            shown = info.filename[:80]
            rel = PurePosixPath(info.filename.rstrip("/"))
            target = root.joinpath(*rel.parts)
            if not target.resolve().is_relative_to(root):
                raise Failure(f"plugin.zip: {shown}: escapes the extraction directory")
            if info.is_dir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags, 0o644)
            size = 0
            try:
                with os.fdopen(fd, "wb") as out, zf.open(info) as src:
                    while chunk := src.read(65536):
                        size += len(chunk)
                        written += len(chunk)
                        if size > info.file_size or written > MAX_UNCOMPRESSED_BYTES:
                            raise Failure(f"plugin.zip: {shown}: larger than declared")
                        out.write(chunk)
            except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
                raise Failure(f"plugin.zip: {shown}: corrupt entry ({exc})") from None


# --- manifest -----------------------------------------------------------------


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))


def accepted_licenses(contract: dict[str, Any]) -> frozenset[str]:
    """The accepted SPDX list, read from the contract copy (one source of truth)."""
    return frozenset(contract["properties"]["license"]["enum"])


def _json_path(error: jsonschema.ValidationError) -> str:
    return ".".join(str(p) for p in error.absolute_path) or "(root)"


def _short(text: str, limit: int = 240) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def read_manifest(tree: Path) -> dict[str, Any]:
    path = tree / "manifest.json"
    if not path.is_file():
        raise Failure("plugin.zip has no manifest.json at its root")
    if path.stat().st_size > MANIFEST_MAX_BYTES:
        raise Failure(f"manifest.json is over {MANIFEST_MAX_BYTES} bytes")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Failure(f"manifest.json is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Failure("manifest.json is not a JSON object")
    return data


def contract_errors(manifest: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(contract)
    errors = []
    for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        path = _json_path(error)
        if path == "license" and error.validator == "enum":
            accepted = ", ".join(sorted(accepted_licenses(contract)))
            message = f"{error.instance!r} is not an accepted licence (accepted: {accepted})"
        else:
            message = _short(error.message)
        errors.append(f"manifest.json: {path}: {message}")
    return errors


def french_status(manifest: dict[str, Any]) -> dict[str, str]:
    def has_fr(key: str) -> str:
        value = manifest.get(key)
        if not isinstance(value, dict):
            return "n/a"
        return "yes" if value.get("fr") else "no"

    return {
        "name": has_fr("name"),
        "summary": has_fr("summary"),
        "README": has_fr("readme"),
        "i18n strings": has_fr("i18n"),
    }


def permission_diff(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    old = old or {}
    return {
        "added": {k: new[k] for k in sorted(new) if k not in old},
        "removed": sorted(k for k in old if k not in new),
        "changed": {
            k: {"from": old[k], "to": new[k]} for k in sorted(new) if k in old and old[k] != new[k]
        },
    }


def is_sensitive(permissions: dict[str, Any]) -> bool:
    """`messages.read` with direct messages or every channel, combined with `http`."""
    read = permissions.get("messages.read")
    if not isinstance(read, dict) or "http" not in permissions:
        return False
    return bool(read.get("direct")) or read.get("channels") == "all"


# --- vendor licences ----------------------------------------------------------


def _spdx_accepted(expression: str, accepted: frozenset[str]) -> bool:
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+-]+", expression)
    ids = [t for t in tokens if t not in ("(", ")", "AND", "OR", "WITH")]
    if not ids or "WITH" in tokens:
        return False
    if "AND" not in tokens and "OR" in tokens:
        return any(i in accepted for i in ids)
    return all(i in accepted for i in ids)


def _dist_licence(meta: email.message.Message, accepted: frozenset[str]) -> str | None:
    """The accepted licence a METADATA file declares, or None."""
    expression = meta.get("License-Expression")
    if expression:
        return expression if _spdx_accepted(expression.strip(), accepted) else None
    plain = (meta.get("License") or "").strip()
    if plain in accepted:
        return plain
    for classifier in meta.get_all("Classifier") or []:
        spdx = CLASSIFIER_LICENSES.get(classifier.strip())
        if spdx in accepted:
            return spdx
    return None


def vendor_errors(tree: Path, accepted: frozenset[str]) -> list[str]:
    """Every top-level entry of vendor/ belongs to a .dist-info whose METADATA declares
    an accepted licence (License-Expression, License, or an unambiguous classifier)."""
    vendor = tree / "vendor"
    if not vendor.is_dir():
        return []
    errors: list[str] = []
    owned: set[str] = set()
    for dist in sorted(p for p in vendor.iterdir() if p.name.endswith(".dist-info")):
        meta_path = dist / "METADATA"
        if not meta_path.is_file():
            errors.append(f"vendor/{dist.name}: no METADATA, so no declared licence")
            continue
        meta = email.parser.HeaderParser().parsestr(
            meta_path.read_text(encoding="utf-8", errors="replace")
        )
        if _dist_licence(meta, accepted) is None:
            declared = meta.get("License-Expression") or meta.get("License") or "none"
            errors.append(
                f"vendor/{dist.name}: licence {_short(declared.strip(), 80)!r} is not in the "
                f"accepted list ({', '.join(sorted(accepted))})"
            )
        top_level = dist / "top_level.txt"
        if top_level.is_file():
            owned.update(
                line.strip()
                for line in top_level.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            )
        record = dist / "RECORD"
        if record.is_file():
            for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
                first = line.split(",", 1)[0].split("/", 1)[0]
                if first and not first.endswith(".dist-info"):
                    owned.add(first.removesuffix(".py"))
    for child in sorted(vendor.iterdir()):
        if child.name.endswith(".dist-info"):
            continue
        if child.name.removesuffix(".py") not in owned:
            errors.append(
                f"vendor/{child.name}: not listed by any vendor/*.dist-info (top_level.txt or "
                "RECORD), so its licence is unknown"
            )
    return errors


# --- source diff --------------------------------------------------------------


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
    }


def source_diff(old_tree: Path, new_tree: Path) -> tuple[str, int]:
    """`git diff --no-index` of the two extracted trees (run from their common parent,
    so paths read `a/old/...` and `b/new/...`) and the number of changed lines."""
    parent = new_tree.parent
    if old_tree.parent != parent:
        raise ValueError("both trees must share a parent")
    base = ["git", "diff", "--no-index", "--no-color", "--no-ext-diff", "--no-textconv"]
    rel = ["--", old_tree.name, new_tree.name]
    text = subprocess.run(
        [*base, *rel], cwd=parent, env=_git_env(), capture_output=True, check=False
    ).stdout.decode("utf-8", "replace")
    numstat = subprocess.run(
        [*base, "--numstat", *rel], cwd=parent, env=_git_env(), capture_output=True, check=False
    ).stdout.decode("utf-8", "replace")
    lines = 0
    for row in numstat.splitlines():
        added, deleted = ([*row.split("\t"), "", ""])[:2]
        lines += int(added) if added.isdigit() else 0
        lines += int(deleted) if deleted.isdigit() else 0
    return text, lines


# --- sandbox ------------------------------------------------------------------

SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
SYSTEM_FILES = ("/etc/ld.so.cache", "/etc/localtime")
SANDBOX_PLUGIN_DIR = "/plugin"
MAX_SYMLINK_HOPS = 40
# Hard limits for bwrap and everything under it (meshloom's runner lowers them further
# for the plugin code itself). RLIMIT_NPROC counts every task of the runner's uid.
SANDBOX_RLIMITS = {
    "RLIMIT_AS": 2 * 1024 * 1024 * 1024,
    "RLIMIT_NPROC": 512,
    "RLIMIT_FSIZE": 64 * 1024 * 1024,
    "RLIMIT_CPU": 180,
    "RLIMIT_CORE": 0,
}
SANDBOX_TMPFS_BYTES = 64 * 1024 * 1024


def limit_resources() -> None:
    """`preexec_fn` of the sandbox: hard resource limits, inherited by bwrap."""
    import resource

    for name, value in SANDBOX_RLIMITS.items():
        which = getattr(resource, name)
        _soft, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(which, (value, value))


def symlink_hops(path: str) -> list[tuple[str, str]]:
    """Every (link, target) met while resolving `path`, its parent directories included,
    so the sandbox can recreate them (a venv interpreter often goes through a symlinked
    directory, such as uv's `cpython-3.13-...` alias of the exact version)."""
    hops: list[tuple[str, str]] = []
    current = os.path.abspath(path)
    while len(hops) < MAX_SYMLINK_HOPS:
        prefix = "/"
        for part in PurePosixPath(current).parts[1:]:
            prefix = os.path.join(prefix, part)
            if os.path.islink(prefix):
                target = os.readlink(prefix)
                hops.append((prefix, target))
                rest = os.path.relpath(current, prefix)
                current = os.path.normpath(os.path.join(os.path.dirname(prefix), target, rest))
                break
        else:
            return hops
    raise Failure(f"too many symbolic links while resolving {path}")


@dataclass
class Sandbox:
    """`python -m meshloom_plugin.dev check` from the pinned meshloom checkout, inside
    bubblewrap: every namespace new (no network), read-only system, interpreter,
    checkout and plugin tree, a fresh tmpfs /tmp (size-capped when bwrap can), an empty
    environment, hard resource limits (`SANDBOX_RLIMITS`) and a wall-clock timeout."""

    meshloom_dir: Path
    python: Path
    bwrap: str | None = None
    tmpfs_size: bool | None = None  # bwrap >= 0.10 has `--size`; probed when None

    def __post_init__(self) -> None:
        self.meshloom_dir = self.meshloom_dir.resolve()
        self.python = self.python.absolute()
        if self.bwrap is None:
            self.bwrap = shutil.which("bwrap")
        if self.tmpfs_size is None:
            self.tmpfs_size = self._probe_size_option()

    def _probe_size_option(self) -> bool:
        if not self.bwrap:
            return False
        try:
            out = subprocess.run(
                [self.bwrap, "--help"], capture_output=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return b"--size" in out.stdout + out.stderr

    def base_prefix(self) -> str:
        out = subprocess.run(
            [str(self.python), "-I", "-c", "import sys; print(sys.base_prefix)"],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return os.path.realpath(out.stdout.decode().strip())

    def command(self, plugin_dir: Path, origin: str, base_prefix: str) -> list[str]:
        if not self.bwrap:
            raise Failure("bwrap is not available: the plugin is never run outside the sandbox")
        args = [
            self.bwrap,
            "--unshare-all",
            "--unshare-net",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        bound: list[str] = []
        for path in SYSTEM_DIRS:
            if os.path.islink(path):
                args += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path):
                args += ["--ro-bind", path, path]
                bound.append(path)
        for path in SYSTEM_FILES:
            args += ["--ro-bind-try", path, path]
        if not _under(base_prefix, bound):
            args += ["--ro-bind", base_prefix, base_prefix]
            bound.append(base_prefix)
        checkout = str(self.meshloom_dir)
        for link, target in symlink_hops(str(self.python)):
            if not _under(link, [*bound, checkout]):
                args += ["--symlink", target, link]
        args += [
            "--ro-bind", checkout, checkout,
            "--ro-bind", str(plugin_dir.resolve()), SANDBOX_PLUGIN_DIR,
            "--proc", "/proc",
            "--dev", "/dev",
            *(["--size", str(SANDBOX_TMPFS_BYTES)] if self.tmpfs_size else []),
            "--tmpfs", "/tmp",
            "--remount-ro", "/",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp",
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "LANG", "C.UTF-8",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--chdir", checkout,
            "--",
            str(self.python),
            "-m", "meshloom_plugin.dev", "check", SANDBOX_PLUGIN_DIR,
            "--json", "--origin", origin,
        ]  # fmt: skip
        return args

    def check(self, plugin_dir: Path, origin: str) -> dict[str, Any]:
        command = self.command(plugin_dir, origin, self.base_prefix())
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=SANDBOX_TIMEOUT_SECONDS,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
                preexec_fn=limit_resources,  # single-threaded here
            )
        except subprocess.TimeoutExpired:
            raise Failure(f"dev check did not finish in {SANDBOX_TIMEOUT_SECONDS} s") from None
        try:
            report = json.loads(result.stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
            raise Failure(
                f"dev check in the sandbox gave no report (exit {result.returncode}): "
                + (" | ".join(tail) or "no output")
            ) from None
        if not isinstance(report, dict) or "ok" not in report:
            raise Failure("dev check in the sandbox gave an unexpected report")
        return report


def _under(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def _finding(f: dict[str, Any]) -> str:
    where = f.get("path") or ""
    if where and f.get("line"):
        where += f":{f['line']}"
    prefix = f"{where}: " if where else ""
    message = _short(str(f.get("message")))
    return f"dev check [{f.get('stage')}] {prefix}{f.get('code')}: {message}"


# --- entries ------------------------------------------------------------------


@dataclass
class Context:
    work_root: Path
    policy: UrlPolicy
    url_template: str
    contract: dict[str, Any]
    entry_schema: dict[str, Any]
    sandbox: Sandbox | None  # None only in this repository's tests (no dry run)


def release_url(template: str, repo: str, tag: str) -> str:
    return template.format(repo=repo, tag=urllib.parse.quote(tag, safe=""))


def parse_entry(text: str, path: str, schema: dict[str, Any]) -> dict[str, Any]:
    if len(text.encode()) > MAX_ENTRY_FILE_BYTES:
        raise Failure(f"{path} is over {MAX_ENTRY_FILE_BYTES} bytes")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Failure(f"{path} is not valid YAML: {_short(str(exc))}") from None
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        raise Failure(
            "; ".join(f"{path}: {_json_path(e)}: {_short(e.message)}" for e in errors[:10])
        )
    return data


def entry_errors(entry: dict[str, Any], path: str, base: dict[str, Any] | None) -> list[str]:
    errors = []
    entry_id: str = entry["id"]
    if PurePosixPath(path).name != f"{entry_id}.yaml":
        errors.append(f"{path}: the file must be named plugins/{entry_id}.yaml")
    publisher = entry_id.split(".", 1)[0]
    owner = entry["repo"].split("/", 1)[0].lower()
    if publisher == "local":
        errors.append(f"{path}: id {entry_id}: local.* is reserved for local plugins")
    elif publisher == "meshloom":
        if owner != TWINROCKET:
            errors.append(f"{path}: id {entry_id}: meshloom.* is reserved for TwinRocket")
    elif publisher != owner:
        errors.append(
            f"{path}: id {entry_id}: the publisher part {publisher!r} must be the GitHub owner "
            f"of repo, lowercased ({owner!r})"
        )
    versions = [r["version"] for r in entry["releases"]]
    parsed = [Version(v) for v in versions]
    if any(b <= a for a, b in zip(parsed, parsed[1:], strict=False)):
        errors.append(f"{path}: releases must be listed oldest first with increasing versions")
    for version in entry.get("revoked", []):
        if version not in versions:
            errors.append(f"{path}: revoked version {version} is not a listed release")
    if base is not None:
        if base["id"] != entry_id:
            errors.append(f"{path}: the id never changes")
        if base["repo"] != entry["repo"]:
            errors.append(f"{path}: repo never changes (ask the catalogue operator to move it)")
        old = base["releases"]
        if entry["releases"][: len(old)] != old:
            errors.append(
                f"{path}: existing releases are never changed or removed; append a new one "
                "(withdrawals go through the catalogue operator)"
            )
        if not set(base.get("revoked", [])) <= set(entry.get("revoked", [])):
            errors.append(f"{path}: a revocation is never lifted by a pull request")
    return errors


def _fresh_dir(parent: Path, prefix: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))


def fetch_tree(entry: dict[str, Any], release: dict[str, Any], ctx: Context, into: Path) -> Path:
    """Download and extract one release under `into` (fresh dirs). Raises Failure."""
    url = release_url(ctx.url_template, entry["repo"], release["tag"])
    archive = _fresh_dir(into, "download-") / "plugin.zip"
    announced_size(url, ctx.policy)
    digest = download(url, archive, ctx.policy)
    if digest != release["sha256"]:
        raise Failure(
            f"sha256 mismatch for {release['tag']}: the entry says {release['sha256']}, "
            f"plugin.zip is {digest}"
        )
    tree = _fresh_dir(into, "tree-")
    extract(archive, tree)
    return tree


def _check_permissions(
    release: dict[str, Any],
    previous: dict[str, Any] | None,
    old: dict[str, Any] | None,
    new: Any,
    report: ReleaseReport,
) -> None:
    if not isinstance(new, dict):
        return
    diff = permission_diff(old, new)
    report.permissions = diff
    report.sensitive = is_sensitive(new)
    widened = sorted([*diff["added"], *diff["changed"]])
    if previous is not None and widened and not release.get("permissions_changed"):
        report.errors.append(
            f"{release['version']} adds or changes permissions ({', '.join(widened)}) "
            f"compared with {previous['version']}: set `permissions_changed: true` on the "
            "release so the review and the users' consent cover it"
        )


def _dry_run(entry: dict[str, Any], tree: Path, ctx: Context, report: ReleaseReport) -> None:
    if ctx.sandbox is None:
        return
    origin = "bundled" if entry["id"].startswith("meshloom.") else "third_party"
    try:
        result = ctx.sandbox.check(tree, origin)
    except Failure as exc:
        report.errors.append(str(exc))
        return
    report.errors += [_finding(f) for f in result.get("errors", [])]
    report.warnings += [_finding(f) for f in result.get("warnings", [])]
    if result.get("skipped"):
        report.errors.append(
            "dev check left files out of the check: " + ", ".join(map(str, result["skipped"]))
        )
    dry = result.get("dry_run") or {}
    report.dry_run_calls = len(dry.get("calls") or []) if dry.get("ran") else None
    if not result.get("ok") and not report.errors:
        report.errors.append("dev check failed without a finding")


def validate_release(
    entry: dict[str, Any], index: int, ctx: Context, report: ReleaseReport
) -> None:
    release = entry["releases"][index]
    previous = entry["releases"][index - 1] if index > 0 else None
    work = _fresh_dir(ctx.work_root, f"{entry['id']}-{release['version']}-")
    compare = _fresh_dir(work, "compare-")
    new_tree = compare / "new"
    old_tree = compare / "old"
    try:
        fetch_tree(entry, release, ctx, work).rename(new_tree)
        manifest = read_manifest(new_tree)
    except Failure as exc:
        report.errors.append(str(exc))
        return
    report.errors += contract_errors(manifest, ctx.contract)
    if manifest.get("id") != entry["id"]:
        report.errors.append(
            f"manifest id {manifest.get('id')!r} is not the entry id {entry['id']}"
        )
    if manifest.get("version") != release["version"]:
        report.errors.append(
            f"manifest version {manifest.get('version')!r} is not the release version "
            f"{release['version']}"
        )
    report.errors += vendor_errors(new_tree, accepted_licenses(ctx.contract))
    report.french = french_status(manifest)

    old_permissions: dict[str, Any] | None = None
    if previous is not None:
        report.previous = previous["version"]
        try:
            fetch_tree(entry, previous, ctx, work).rename(old_tree)
            old_permissions = read_manifest(old_tree).get("permissions") or {}
        except Failure as exc:
            report.warnings.append(f"previous release {previous['version']}: {exc}")
    if not old_tree.exists():
        old_tree.mkdir()
    _check_permissions(release, previous, old_permissions, manifest.get("permissions"), report)
    report.diff_text, report.diff_lines = source_diff(old_tree, new_tree)
    _dry_run(entry, new_tree, ctx, report)


# --- git ----------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, env=_git_env(), capture_output=True, check=False
    )
    if result.returncode != 0:
        raise Failure(f"git {' '.join(args)}: {result.stderr.decode().strip()}")
    return result.stdout.decode("utf-8", "replace")


def changed_paths(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    out = _git(repo, "diff", "--name-status", "--no-renames", "-z", base, head)
    parts = [p for p in out.split("\0") if p]
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def _is_entry_path(path: str) -> bool:
    p = PurePosixPath(path)
    return len(p.parts) == 2 and p.parts[0] == "plugins" and p.suffix == ".yaml"


def _validate_entry(
    repo: Path, base: str, head: str, status: str, path: str, ctx: Context, report: EntryReport
) -> None:
    if status == "D":
        report.errors.append(
            f"{path}: removing an entry is a withdrawal handled by the catalogue operator"
        )
        return
    try:
        entry = parse_entry(_git(repo, "show", f"{head}:{path}"), path, ctx.entry_schema)
        base_entry = None
        if status != "A":
            base_entry = parse_entry(_git(repo, "show", f"{base}:{path}"), path, ctx.entry_schema)
    except Failure as exc:
        report.errors.append(str(exc))
        return
    report.entry_id = entry["id"]
    report.errors += entry_errors(entry, path, base_entry)
    if report.errors:
        return
    first_new = len(base_entry["releases"]) if base_entry else 0
    new_indexes = range(first_new, len(entry["releases"]))
    if len(new_indexes) > MAX_NEW_RELEASES_PER_ENTRY:
        report.errors.append(
            f"{path}: at most {MAX_NEW_RELEASES_PER_ENTRY} new releases per pull request"
        )
        return
    for index in new_indexes:
        release_report = ReleaseReport(entry["id"], entry["releases"][index]["version"])
        report.releases.append(release_report)
        validate_release(entry, index, ctx, release_report)


def run(repo: Path, base: str, head: str, ctx: Context) -> RunReport:
    """Validate what `head` changes since it left `base`: the merge base, not the base
    tip, so entries merged on main after the branch started are not read as removals."""
    report = RunReport()
    try:
        base = _git(repo, "merge-base", base, head).strip()
        changes = changed_paths(repo, base, head)
    except Failure as exc:
        report.errors.append(str(exc))
        return report
    entries = [(s, p) for s, p in changes if _is_entry_path(p)]
    if not entries:
        return report
    stray = [p for _, p in changes if not _is_entry_path(p)]
    if stray:
        report.errors.append(
            "a catalogue pull request changes only plugins/<id>.yaml files; this one also "
            "changes: " + ", ".join(sorted(stray)[:20])
        )
    if len(entries) > MAX_ENTRIES_PER_PR:
        report.errors.append(f"at most {MAX_ENTRIES_PER_PR} entries per pull request")
        return report
    for status, path in entries:
        entry_report = EntryReport(path)
        report.entries.append(entry_report)
        _validate_entry(repo, base, head, status, path, ctx, entry_report)
    return report


# --- output -------------------------------------------------------------------
#
# Every string that may come from a plugin, a release server or a pull request is
# untrusted. On stdout it must never start a workflow command (`::error ...`), and in the
# job summary it must never become Markdown or HTML. `plain` keeps it on one line with
# control characters shown escaped and `::` broken; `md` and `_code` build on it.

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_MD_SPECIAL_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>&])")


def plain(text: object) -> str:
    """One line that cannot be read as a workflow command."""
    text = _CONTROL_RE.sub(lambda m: m.group().encode("unicode_escape").decode("ascii"), str(text))
    return text.replace("::", ": :")


def md(text: object) -> str:
    """Untrusted text as literal Markdown (no markup, no HTML, no table break)."""
    return _MD_SPECIAL_RE.sub(r"\\\1", plain(text))


def _code(text: object) -> str:
    """Untrusted text as one inline code span."""
    text = plain(text)
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _render_permissions(rel: ReleaseReport) -> list[str]:
    if rel.permissions is None:
        return []
    out = ["", "**Permission diff**", ""]
    diff = rel.permissions
    if not (diff["added"] or diff["removed"] or diff["changed"]):
        out.append("- unchanged")
    for name, params in diff["added"].items():
        out.append(f"- added {_code(name)}: {_code(json.dumps(params, ensure_ascii=False))}")
    for name, change in diff["changed"].items():
        before = json.dumps(change["from"], ensure_ascii=False)
        after = json.dumps(change["to"], ensure_ascii=False)
        out.append(f"- changed {_code(name)}: {_code(before)} -> {_code(after)}")
    for name in diff["removed"]:
        out.append(f"- removed {_code(name)}")
    if rel.sensitive:
        out.append(
            "- **sensitive**: reads direct messages or every channel and has `http` "
            "(never auto-merged)"
        )
    return out


def _render_diff(rel: ReleaseReport) -> list[str]:
    if rel.diff_lines is None:
        return []
    text = rel.diff_text
    cut = len(text) > SUMMARY_DIFF_MAX_CHARS
    text = text[:SUMMARY_DIFF_MAX_CHARS]
    fence = _fence(text)
    return [
        "",
        f"**Source diff: {rel.diff_lines} changed line(s)**"
        + (" (shown truncated)" if cut else ""),
        "",
        "<details><summary>Rendered diff</summary>",
        "",
        f"{fence}diff",
        text.rstrip("\n"),
        fence,
        "",
        "</details>",
    ]


def render_summary(report: RunReport) -> str:
    out = ["## Catalogue validation", ""]
    if not report.entries and not report.errors:
        out.append("No catalogue entry changed: nothing to validate.")
        return "\n".join(out) + "\n"
    out += [f"**Result: {'passed' if report.ok else 'failed'}**", ""]
    out += [f"- error: {md(e)}" for e in report.errors]
    for entry in report.entries:
        out += ["", f"### {_code(entry.path)}", ""]
        out += [f"- error: {md(e)}" for e in entry.errors]
        for rel in entry.releases:
            against = f" (against {md(rel.previous)})" if rel.previous else " (new plugin)"
            verdict = "passed" if rel.ok else "failed"
            title = f"{md(rel.entry_id)} {md(rel.version)}"
            out += ["", f"#### {title}{against}: {verdict}", ""]
            out += [f"- error: {md(e)}" for e in rel.errors]
            out += [f"- warning: {md(w)}" for w in rel.warnings]
            if rel.dry_run_calls is not None:
                out.append(f"- dry run in the sandbox: {rel.dry_run_calls} call(s) to the host")
            out += _render_permissions(rel)
            if rel.french:
                out += ["", "**French translation**", "", "| item | fr |", "|---|---|"]
                out += [f"| {md(k)} | {md(v)} |" for k, v in rel.french.items()]
            out += _render_diff(rel)
    return "\n".join(out) + "\n"


def render_text(report: RunReport) -> str:
    if not report.entries and not report.errors:
        return "No catalogue entry changed: nothing to validate.\n"
    lines = [f"error: {plain(e)}" for e in report.errors]
    for entry in report.entries:
        lines.append(f"{plain(entry.path)}: {'ok' if entry.ok else 'FAILED'}")
        lines += [f"  error: {plain(e)}" for e in entry.errors]
        for rel in entry.releases:
            verdict = "ok" if rel.ok else "FAILED"
            lines.append(f"  {plain(rel.entry_id)} {plain(rel.version)}: {verdict}")
            lines += [f"    error: {plain(e)}" for e in rel.errors]
            lines += [f"    warning: {plain(w)}" for w in rel.warnings]
            if rel.diff_lines is not None:
                lines.append(f"    source diff: {rel.diff_lines} changed line(s)")
    lines.append("OK" if report.ok else "FAILED")
    return "\n".join(lines) + "\n"


def write_stdout(text: str) -> None:
    """In GitHub Actions, also stop workflow-command processing around the report, with a
    token nobody can guess, so even a missed escape cannot issue a command."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        sys.stdout.write(text)
        return
    token = os.urandom(16).hex()
    sys.stdout.write(f"::stop-commands::{token}\n{text}::{token}::\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--base", required=True, help="base commit of the pull request")
    parser.add_argument("--head", default="HEAD", help="head commit (default HEAD)")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="catalogue checkout")
    parser.add_argument("--meshloom", type=Path, help="meshloom checkout at the pinned commit")
    parser.add_argument("--python", type=Path, help="its interpreter (default <meshloom>/.venv)")
    parser.add_argument("--summary", type=Path, help="append the Markdown report to this file")
    parser.add_argument("--work", type=Path, help="work root for downloads (default: temp dir)")
    parser.add_argument("--url-template", default=DEFAULT_URL_TEMPLATE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    work_root = Path(args.work or tempfile.mkdtemp(prefix="catalogue-")).resolve()
    if work_root.is_relative_to(args.repo.resolve()):
        parser.error("the work root must be outside the repository checkout")
    sandbox = None
    if args.meshloom is not None:
        python = args.python or args.meshloom / ".venv" / "bin" / "python"
        sandbox = Sandbox(args.meshloom, python)
    ctx = Context(
        work_root=work_root,
        policy=UrlPolicy(args.url_template),
        url_template=args.url_template,
        contract=load_contract(),
        entry_schema=json.loads(ENTRY_SCHEMA.read_text(encoding="utf-8")),
        sandbox=sandbox,
    )
    report = run(args.repo, args.base, args.head, ctx)
    if report.entries and sandbox is None:
        # Fail closed: an entry is never accepted without the sandboxed dev check.
        report.errors.append("--meshloom is required to validate entries (dev check)")
    write_stdout(render_text(report))
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(render_summary(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
