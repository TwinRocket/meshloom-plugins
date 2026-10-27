"""The workflow safety rules: pull_request only, no secrets, GitHub-hosted runners only
(fail closed), bounded jobs, pinned official actions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"
WORKFLOWS = sorted((GITHUB / "workflows").glob("*.y*ml"))
RUNS_ON_VAR = "${{ vars.RUNNER || 'ubuntu-latest' }}"
ALLOWED_ACTIONS = {"actions/checkout", "actions/setup-python", "astral-sh/setup-uv"}


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow: dict[str, Any]) -> Any:
    return workflow.get("on", workflow.get(True))  # PyYAML reads `on:` as True


def test_there_is_a_workflow():
    assert [p.name for p in WORKFLOWS] == ["validate.yml"]


@pytest.mark.parametrize("path", sorted(p for p in GITHUB.rglob("*") if p.is_file()), ids=str)
def test_no_secrets_and_no_pull_request_target_anywhere(path):
    text = path.read_text(encoding="utf-8")
    assert "secrets." not in text and "secrets[" not in text
    assert "pull_request_target" not in text
    assert "workflow_run" not in text


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_pull_request_is_the_only_trigger(path):
    assert triggers(load(path)) == {"pull_request": None}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_token_is_read_only(path):
    workflow = load(path)
    assert workflow["permissions"] == {}
    for job in workflow["jobs"].values():
        assert job["permissions"] in ({}, {"contents": "read"})


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_jobs_are_bounded_and_runs_are_per_pull_request(path):
    workflow = load(path)
    assert workflow["concurrency"] == {
        "group": "validate-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }
    for job in workflow["jobs"].values():
        assert 0 < job["timeout-minutes"] <= 15


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_self_hosted_runners_are_refused_fail_closed(path):
    jobs = load(path)["jobs"]
    preflight = jobs["preflight"]
    assert preflight["runs-on"] == "ubuntu-latest"
    step = preflight["steps"][0]
    assert step["env"]["RUNNER_LABEL"] == "${{ vars.RUNNER }}"
    assert "exit 1" in step["run"] and "grep -Eqx" in step["run"]
    for name, job in jobs.items():
        runs_on = job["runs-on"]
        if runs_on == "ubuntu-latest":
            continue
        assert runs_on == RUNS_ON_VAR, name
        needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
        assert "preflight" in needs, name
        guard = job["steps"][0]["run"]
        assert '"${RUNNER_ENVIRONMENT:-}" != "github-hosted"' in guard, name
        assert "exit 1" in guard, name


def test_the_preflight_allowlist_fails_closed():
    """The label check accepts GitHub-hosted Ubuntu labels only."""
    step = load(WORKFLOWS[0])["jobs"]["preflight"]["steps"][0]["run"]
    pattern = re.search(r"grep -Eqx '([^']+)'", step).group(1)
    for good in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-24.04-arm", "ubuntu-22.04"):
        assert re.fullmatch(pattern, good), good
    for bad in ("self-hosted", "flasher-builder", "ubuntu-latest,self-hosted", "linux", "macos-15"):
        assert not re.fullmatch(pattern, bad), bad


def test_the_result_job_requires_every_job():
    jobs = load(WORKFLOWS[0])["jobs"]
    result = jobs["result"]
    assert result["if"] == "always()"
    assert set(result["needs"]) == set(jobs) - {"result"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_official_and_pinned_by_sha(path):
    for job in load(path)["jobs"].values():
        for step in job["steps"]:
            if "uses" not in step:
                continue
            action, _, ref = step["uses"].partition("@")
            assert action in ALLOWED_ACTIONS, action
            assert re.fullmatch(r"[0-9a-f]{40}", ref), step["uses"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_keep_no_credentials(path):
    for job in load(path)["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_the_meshloom_pin_is_a_full_commit_sha():
    assert re.fullmatch(r"[0-9a-f]{40}", load(WORKFLOWS[0])["env"]["MESHLOOM_REF"])
