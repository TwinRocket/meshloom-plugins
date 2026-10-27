"""The acceptance criteria of MSHL-200, end to end against a loopback release server."""

from __future__ import annotations

import io
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest
import validate_catalogue as vc
from conftest import (
    Catalogue,
    ReleaseServer,
    entry_yaml,
    make_zip,
    sample_manifest,
    sha256,
)

MIB = 1024 * 1024


def submit(
    catalogue: Catalogue,
    server: ReleaseServer,
    body: bytes,
    *,
    entry_id: str = "acme.echo",
    repo: str = "acme/echo",
    version: str = "1.0.0",
    digest: str | None = None,
    **asset: object,
) -> tuple[str, str]:
    """A pull request adding one entry with one release. Returns (base, head)."""
    base = catalogue.git("rev-parse", "HEAD")
    tag = f"v{version}"
    server.publish(repo, tag, body, **asset)
    release = {"version": version, "tag": tag, "sha256": digest or sha256(body)}
    catalogue.write(f"plugins/{entry_id}.yaml", entry_yaml([release], entry_id, repo))
    return base, catalogue.commit("add entry")


def one_release(report: vc.RunReport) -> vc.ReleaseReport:
    assert len(report.entries) == 1, report
    assert len(report.entries[0].releases) == 1, report.entries[0]
    return report.entries[0].releases[0]


def all_errors(report: vc.RunReport) -> str:
    out = list(report.errors)
    for entry in report.entries:
        out += entry.errors
        for rel in entry.releases:
            out += rel.errors
    return "\n".join(out)


# --- the sample entry passes ----------------------------------------------------


def test_sample_entry_passes(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip())
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert report.ok, all_errors(report)
    rel = one_release(report)
    assert rel.permissions is not None and "messages.read" in rel.permissions["added"]
    assert rel.french == {"name": "yes", "summary": "yes", "README": "no", "i18n strings": "n/a"}
    assert rel.diff_lines and rel.diff_lines > 0
    summary = vc.render_summary(report)
    assert "Result: passed" in summary and "Permission diff" in summary
    assert "French translation" in summary and "changed line(s)" in summary


def test_sample_entry_passes_the_sandboxed_dev_check(catalogue, server, make_ctx, sandbox):
    base, head = submit(catalogue, server, make_zip())
    report = vc.run(catalogue.root, base, head, make_ctx(sandbox))
    assert report.ok, all_errors(report)
    rel = one_release(report)
    assert rel.dry_run_calls == 1  # the echo plugin replies once to the test message


def test_pull_request_without_entry_passes(catalogue, make_ctx, capsys, tmp_path):
    base = catalogue.git("rev-parse", "HEAD")
    catalogue.write("docs/x.md", "doc\n")
    head = catalogue.commit("docs only")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert report.ok and not report.entries
    assert "No catalogue entry changed" in vc.render_summary(report)
    summary = tmp_path / "summary.md"
    code = vc.main(
        ["--repo", str(catalogue.root), "--base", base, "--head", head, "--summary", str(summary)]
    )
    assert code == 0
    assert "No catalogue entry changed" in capsys.readouterr().out
    assert "No catalogue entry changed" in summary.read_text()


# --- each refusal is readable ---------------------------------------------------


def test_wrong_sha256_fails(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip(), digest="0" * 64)
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert not report.ok
    assert "sha256 mismatch for v1.0.0: the entry says 000" in all_errors(report)


def test_oversized_archive_is_refused_on_content_length_before_download(
    catalogue, server, make_ctx
):
    body = make_zip()
    base, head = submit(catalogue, server, body, head_length=6 * MIB)
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert not report.ok
    assert "announces 6291456 bytes, over the 5242880 byte limit" in all_errors(report)
    assert "refused before download" in all_errors(report)
    assert server.gets() == []  # nothing was downloaded


def test_missing_content_length_is_refused_before_download(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip(), head_length=-1)
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "no Content-Length announced" in all_errors(report)
    assert server.gets() == []


def test_a_server_lying_about_the_size_is_cut_off(catalogue, server, make_ctx):
    big = b"\0" * (6 * MIB)
    base, head = submit(catalogue, server, big, head_length=1000, get_length=False)
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "larger than 5242880 bytes (more than announced); download stopped" in all_errors(report)


def test_licence_outside_the_accepted_list_fails(catalogue, server, make_ctx):
    body = make_zip(sample_manifest(license="GPL-2.0-only"))
    base, head = submit(catalogue, server, body)
    report = vc.run(catalogue.root, base, head, make_ctx())
    errors = all_errors(report)
    assert "manifest.json: license: 'GPL-2.0-only' is not an accepted licence" in errors
    assert "accepted: Apache-2.0, BSD-2-Clause" in errors


def test_licence_outside_the_list_fails_in_dev_check(catalogue, server, make_ctx, sandbox):
    body = make_zip(sample_manifest(license="GPL-2.0-only"))
    base, head = submit(catalogue, server, body)
    report = vc.run(catalogue.root, base, head, make_ctx(sandbox))
    assert "license_not_accepted" in all_errors(report)


def test_reserved_id_fails(catalogue, server, make_ctx):
    body = make_zip(sample_manifest(id="meshloom.echo"))
    base, head = submit(catalogue, server, body, entry_id="meshloom.echo")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "id meshloom.echo: meshloom.* is reserved for TwinRocket" in all_errors(report)


def test_local_namespace_is_refused(catalogue, server, make_ctx):
    body = make_zip(sample_manifest(id="local.echo"))
    base, head = submit(catalogue, server, body, entry_id="local.echo", repo="local/echo")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "local.* is reserved for local plugins" in all_errors(report)


def test_reserved_id_fails_in_dev_check(tmp_path, sandbox):
    tree = tmp_path / "tree"
    tree.mkdir()
    with zipfile.ZipFile(io.BytesIO(make_zip(sample_manifest(id="meshloom.echo")))) as zf:
        zf.extractall(tree)
    result = sandbox.check(tree, "third_party")
    assert not result["ok"]
    assert "id_namespace_reserved" in {e["code"] for e in result["errors"]}


def test_misleading_name_fails_in_dev_check(catalogue, server, make_ctx, sandbox):
    body = make_zip(sample_manifest(name={"en": "Meshloom Echo"}))
    base, head = submit(catalogue, server, body)
    report = vc.run(catalogue.root, base, head, make_ctx(sandbox))
    errors = all_errors(report)
    assert "dev check [package]" in errors and "name_misleading" in errors


def test_javascript_link_fails_in_dev_check(catalogue, server, make_ctx, sandbox):
    body = make_zip(sample_manifest(homepage="javascript:alert(1)"))
    base, head = submit(catalogue, server, body)
    report = vc.run(catalogue.root, base, head, make_ctx(sandbox))
    assert "url_not_https" in all_errors(report)


def test_publisher_must_be_the_repository_owner(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip(), repo="someone/echo")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "the publisher part 'acme' must be the GitHub owner of repo" in all_errors(report)


def test_manifest_id_and_version_must_match_the_entry(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip(), version="1.0.1")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "manifest version '1.0.0' is not the release version 1.0.1" in all_errors(report)


# --- permission diff ------------------------------------------------------------


def _two_releases(catalogue, server, *, flag: bool | None) -> tuple[str, str]:
    old = make_zip()
    new_manifest = sample_manifest(version="1.1.0")
    new_manifest["permissions"] = {
        **new_manifest["permissions"],
        "http": {"hosts": ["api.open-meteo.com"]},
    }
    new = make_zip(new_manifest)
    server.publish("acme/echo", "v1.0.0", old)
    server.publish("acme/echo", "v1.1.0", new)
    first = {"version": "1.0.0", "tag": "v1.0.0", "sha256": sha256(old)}
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([first]))
    base = catalogue.commit("1.0.0")
    second = {"version": "1.1.0", "tag": "v1.1.0", "sha256": sha256(new)}
    if flag is not None:
        second["permissions_changed"] = flag
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([first, second]))
    return base, catalogue.commit("1.1.0")


def test_permission_diff_without_the_flag_fails(catalogue, server, make_ctx):
    base, head = _two_releases(catalogue, server, flag=None)
    report = vc.run(catalogue.root, base, head, make_ctx())
    rel = one_release(report)
    assert rel.previous == "1.0.0"
    assert "1.1.0 adds or changes permissions (http) compared with 1.0.0" in all_errors(report)
    assert "permissions_changed: true" in all_errors(report)


def test_permission_diff_with_the_flag_passes_and_is_shown(catalogue, server, make_ctx):
    base, head = _two_releases(catalogue, server, flag=True)
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert report.ok, all_errors(report)
    rel = one_release(report)
    assert rel.sensitive  # direct messages + http
    summary = vc.render_summary(report)
    assert 'added `http`: `{"hosts": ["api.open-meteo.com"]}`' in summary
    assert "**sensitive**" in summary
    assert "a/old/manifest.json" in rel.diff_text and "b/new/manifest.json" in rel.diff_text
    assert rel.diff_lines is not None and 0 < rel.diff_lines < 20


def test_existing_releases_never_change(catalogue, server, make_ctx):
    body = make_zip()
    first = {"version": "1.0.0", "tag": "v1.0.0", "sha256": sha256(body)}
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([first]))
    base = catalogue.commit("1.0.0")
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([{**first, "sha256": "1" * 64}]))
    head = catalogue.commit("rewrite")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "existing releases are never changed or removed" in all_errors(report)


def test_removing_an_entry_is_refused(catalogue, server, make_ctx):
    body = make_zip()
    release = {"version": "1.0.0", "tag": "v1.0.0", "sha256": sha256(body)}
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([release]))
    base = catalogue.commit("1.0.0")
    catalogue.remove("plugins/acme.echo.yaml")
    head = catalogue.commit("remove")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "removing an entry is a withdrawal" in all_errors(report)


def test_a_pull_request_mixing_entries_and_other_files_fails(catalogue, server, make_ctx):
    base = catalogue.git("rev-parse", "HEAD")
    body = make_zip()
    server.publish("acme/echo", "v1.0.0", body)
    release = {"version": "1.0.0", "tag": "v1.0.0", "sha256": sha256(body)}
    catalogue.write("plugins/acme.echo.yaml", entry_yaml([release]))
    catalogue.write("scripts/validate_catalogue.py", "print('ok')\n")
    head = catalogue.commit("mixed")
    report = vc.run(catalogue.root, base, head, make_ctx())
    assert "also changes: scripts/validate_catalogue.py" in all_errors(report)


def test_entry_schema_errors_are_readable(catalogue, make_ctx):
    base = catalogue.git("rev-parse", "HEAD")
    catalogue.write("plugins/acme.echo.yaml", "id: acme.echo\nrepo: acme/echo\ntier: gold\n")
    head = catalogue.commit("bad")
    report = vc.run(catalogue.root, base, head, make_ctx())
    errors = all_errors(report)
    assert "plugins/acme.echo.yaml: (root): 'releases' is a required property" in errors
    assert "plugins/acme.echo.yaml: tier: 'reviewed' was expected" in errors


# --- untrusted archives -----------------------------------------------------------


def _raw_zip(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info, data in entries:
            zf.writestr(info, data)
    return buf


def _info(name: str, mode: int = 0o100644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    return info


@pytest.mark.parametrize(
    ("name", "mode", "message"),
    [
        ("../evil.py", 0o100644, "escapes the package"),
        ("/etc/passwd", 0o100644, "absolute paths are refused"),
        ("a\\b.py", 0o100644, "backslash"),
        ("link.py", 0o120777, "symbolic links are refused"),
        ("run.sh", 0o100755, "executable files are refused"),
        ("fifo", 0o010644, "only regular files and directories"),
        ("lib/x.so", 0o100644, "native code is refused"),
        (".env", 0o100644, "hidden entries"),
        ("__pycache__/x.pyc", 0o100644, "hidden entries"),
    ],
)
def test_unsafe_archive_entries_are_refused(tmp_path, name, mode, message):
    archive = tmp_path / "plugin.zip"
    archive.write_bytes(_raw_zip([(_info(name, mode), b"x")]).getvalue())
    with pytest.raises(vc.Failure, match=message):
        vc.inspect_archive(archive)


def test_duplicate_names_by_case_are_refused(tmp_path):
    archive = tmp_path / "plugin.zip"
    entries = [(_info("Plugin.py"), b"a"), (_info("plugin.py"), b"b")]
    archive.write_bytes(_raw_zip(entries).getvalue())
    with pytest.raises(vc.Failure, match="duplicate entry"):
        vc.inspect_archive(archive)


def test_too_many_entries_are_refused(tmp_path):
    archive = tmp_path / "plugin.zip"
    entries = [(_info(f"f{i}.txt"), b"") for i in range(vc.MAX_ENTRIES + 1)]
    archive.write_bytes(_raw_zip(entries).getvalue())
    with pytest.raises(vc.Failure, match="over the 2000 limit"):
        vc.inspect_archive(archive)


def test_declared_size_over_the_limit_is_refused(tmp_path):
    archive = tmp_path / "plugin.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_info("big.txt"), b"\0" * (21 * MIB))
    archive.write_bytes(buf.getvalue())
    with pytest.raises(vc.Failure, match="unpacks to more than"):
        vc.inspect_archive(archive)


def test_extraction_writes_plain_files_into_the_given_directory(tmp_path):
    archive = tmp_path / "plugin.zip"
    archive.write_bytes(make_zip())
    dest = tmp_path / "out"
    dest.mkdir()
    vc.extract(archive, dest)
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["LICENSE", "README.md", "manifest.json", "plugin.py"]
    for path in dest.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_downloads_happen_only_in_fresh_dirs_under_the_work_root(catalogue, server, make_ctx):
    base, head = submit(catalogue, server, make_zip())
    ctx = make_ctx()
    vc.run(catalogue.root, base, head, ctx)
    zips = list(ctx.work_root.rglob("plugin.zip"))
    assert len(zips) == 1
    assert zips[0].parent.name.startswith("download-")
    assert not list(catalogue.root.rglob("plugin.zip"))
    assert not ctx.work_root.resolve().is_relative_to(catalogue.root.resolve())


def test_the_work_root_cannot_be_inside_the_repository(catalogue):
    with pytest.raises(SystemExit):
        vc.main(["--repo", str(catalogue.root), "--base", "HEAD", "--work", str(catalogue.root)])


def test_only_github_release_hosts_are_fetched():
    policy = vc.UrlPolicy(vc.DEFAULT_URL_TEMPLATE)
    policy.check("https://github.com/acme/echo/releases/download/v1/plugin.zip")
    policy.check("https://release-assets.githubusercontent.com/x")
    for url in ("http://github.com/x", "https://evil.example/x", "https://u@github.com/x"):
        with pytest.raises(vc.Failure, match="not a release host"):
            policy.check(url)


def test_plugin_code_never_runs_without_bwrap(tmp_path):
    box = vc.Sandbox(tmp_path, Path("/usr/bin/python3"), bwrap="")
    with pytest.raises(vc.Failure, match="never run outside the sandbox"):
        box.command(tmp_path, "third_party", "/usr")


def test_the_sandbox_recreates_the_interpreter_symlinks(tmp_path):
    """A venv python reached through a symlinked directory (uv's minor-version alias)
    must resolve inside the sandbox too."""
    real = tmp_path / "python" / "cpython-3.13.9"
    (real / "bin").mkdir(parents=True)
    (real / "bin" / "python3.13").write_text("")
    alias = tmp_path / "python" / "cpython-3.13"
    alias.symlink_to("cpython-3.13.9")
    checkout = tmp_path / "checkout"
    (checkout / ".venv" / "bin").mkdir(parents=True)
    venv_python = checkout / ".venv" / "bin" / "python"
    venv_python.symlink_to(alias / "bin" / "python3.13")
    box = vc.Sandbox(checkout, venv_python, bwrap="/usr/bin/bwrap")
    args = box.command(tmp_path, "third_party", str(real.resolve()))
    links = [args[i + 2] for i, arg in enumerate(args) if arg == "--symlink"]
    assert str(alias) in links
    assert args[args.index(str(alias)) - 1] == "cpython-3.13.9"
    assert f"--ro-bind {real.resolve()} {real.resolve()}" in " ".join(args)
    assert str(venv_python) not in links  # inside the bound checkout already


def test_the_sandbox_has_no_network_and_read_only_views(tmp_path):
    box = vc.Sandbox(tmp_path, Path("/opt/py/bin/python"), bwrap="/usr/bin/bwrap")
    cmd = box.command(tmp_path, "third_party", "/opt/py")
    for flag in ("--unshare-all", "--unshare-net", "--clearenv", "--die-with-parent"):
        assert flag in cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    joined = " ".join(cmd)
    assert f"--ro-bind {tmp_path.resolve()} /plugin" in joined
    assert "--ro-bind /opt/py /opt/py" in joined
    assert "--tmpfs /tmp" in joined and "--remount-ro /" in joined
    assert "--bind " not in joined  # nothing writable from the host
    assert cmd[-7:] == [
        "-m", "meshloom_plugin.dev", "check", "/plugin", "--json", "--origin", "third_party",
    ]  # fmt: skip


def test_a_missing_bwrap_fails_the_release(catalogue, server, make_ctx, tmp_path):
    base, head = submit(catalogue, server, make_zip())
    box = vc.Sandbox(tmp_path, Path(shutil.which("python3") or "/usr/bin/python3"), bwrap="")
    report = vc.run(catalogue.root, base, head, make_ctx(box))
    assert "bwrap is not available" in all_errors(report)


def test_entries_are_never_accepted_without_the_dev_check(catalogue, server, capsys):
    base, head = submit(catalogue, server, make_zip())
    code = vc.main(
        ["--repo", str(catalogue.root), "--base", base, "--head", head,
         "--url-template", server.template]
    )  # fmt: skip
    assert code == 1
    assert "--meshloom is required" in capsys.readouterr().out


# --- vendor/ licences ---------------------------------------------------------------


def _vendored(tmp_path: Path, metadata: str, *, record: str | None = "lib/__init__.py,,\n"):
    tree = tmp_path / "tree"
    (tree / "vendor" / "lib").mkdir(parents=True)
    (tree / "vendor" / "lib" / "__init__.py").write_text("")
    dist = tree / "vendor" / "lib-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(metadata)
    if record is not None:
        (dist / "RECORD").write_text(record)
    return tree


ACCEPTED = frozenset(vc.accepted_licenses(vc.load_contract()))


@pytest.mark.parametrize(
    "metadata",
    [
        "Name: lib\nLicense-Expression: MIT\n",
        "Name: lib\nLicense-Expression: MIT OR GPL-2.0-only\n",
        "Name: lib\nLicense-Expression: Apache-2.0 AND MIT\n",
        "Name: lib\nLicense: BSD-3-Clause\n",
        "Name: lib\nClassifier: License :: OSI Approved :: MIT License\n",
    ],
)
def test_vendor_with_an_accepted_licence_passes(tmp_path, metadata):
    assert vc.vendor_errors(_vendored(tmp_path, metadata), ACCEPTED) == []


@pytest.mark.parametrize(
    "metadata",
    [
        "Name: lib\nLicense-Expression: GPL-2.0-only\n",
        "Name: lib\nLicense-Expression: MIT AND GPL-2.0-only\n",
        "Name: lib\nLicense-Expression: GPL-3.0-only WITH Classpath-exception-2.0\n",
        "Name: lib\nClassifier: License :: OSI Approved :: BSD License\n",
        "Name: lib\n",
    ],
)
def test_vendor_with_another_licence_fails(tmp_path, metadata):
    errors = vc.vendor_errors(_vendored(tmp_path, metadata), ACCEPTED)
    assert errors and "is not in the accepted list" in errors[0]


def test_vendor_module_without_metadata_fails(tmp_path):
    tree = _vendored(tmp_path, "Name: lib\nLicense-Expression: MIT\n", record=None)
    errors = vc.vendor_errors(tree, ACCEPTED)
    assert errors == [
        "vendor/lib: not listed by any vendor/*.dist-info (top_level.txt or RECORD), so its "
        "licence is unknown"
    ]


# --- summary rendering ----------------------------------------------------------------


def test_the_diff_fence_is_longer_than_any_backtick_run():
    rel = vc.ReleaseReport("acme.echo", "1.0.0", diff_text="+ ```` x\n", diff_lines=1)
    entry = vc.EntryReport("plugins/acme.echo.yaml", "acme.echo", releases=[rel])
    summary = vc.render_summary(vc.RunReport(entries=[entry]))
    assert "`````diff" in summary


def test_json_output_of_dev_check_becomes_readable_findings():
    finding = {"stage": "scan", "code": "import_forbidden", "message": "no", "path": "a.py"}
    assert vc._finding({**finding, "line": 3}) == "dev check [scan] a.py:3: import_forbidden: no"
    assert json.dumps(finding)  # the report is plain JSON
