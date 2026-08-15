"""Affinda-managed distribution defaults and source routing."""

from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest

from hermes_cli.config import DEFAULT_CONFIG
from hermes_cli.main import _resolve_update_branch
from hermes_cli import update_cmd


MANAGED_BRANCH = "affinda/slack-context-customization"
MANAGED_REPO = "https://github.com/affinda/hermes-agent.git"


def test_managed_defaults_are_intentionally_limited():
    slack = DEFAULT_CONFIG["slack"]

    assert DEFAULT_CONFIG["agent"]["gateway_notify_interval"] == 600
    assert DEFAULT_CONFIG["compression"]["abort_on_summary_failure"] is True
    assert slack["dm_top_level_threads_as_sessions"] is False
    assert slack["human_context_enabled"] is True
    assert slack["context_lookback_messages"] == 20
    assert slack["thread_gap_messages"] == 20
    assert slack["attention_window_minutes"] == 5
    assert slack["event_packet_enabled"] is True


def test_update_defaults_to_managed_branch_but_honors_override():
    assert _resolve_update_branch(SimpleNamespace(branch=None)) == MANAGED_BRANCH
    assert _resolve_update_branch(SimpleNamespace(branch="")) == MANAGED_BRANCH
    assert _resolve_update_branch(SimpleNamespace(branch="release/test")) == "release/test"


def test_updater_recognizes_affinda_as_distribution_upstream():
    assert update_cmd.OFFICIAL_REPO_URL == MANAGED_REPO
    assert not update_cmd._is_fork(MANAGED_REPO)
    assert not update_cmd._is_fork("git@github.com:affinda/hermes-agent.git")
    assert update_cmd._is_fork(
        "https://github.com/NousResearch/hermes-agent.git"
    )


def test_zip_fallback_sanitizes_slash_branch_in_local_filename(
    tmp_path, monkeypatch
):
    captured = {}

    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(tmp_path))

    def fail_after_capture(url, destination):
        captured["url"] = url
        captured["destination"] = destination
        raise RuntimeError("stop after destination capture")

    monkeypatch.setattr("urllib.request.urlretrieve", fail_after_capture)
    monkeypatch.setattr(update_cmd._m().sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    with pytest.raises(SystemExit):
        update_cmd._update_via_zip(SimpleNamespace(branch=None))

    assert captured["url"].endswith(f"/refs/heads/{MANAGED_BRANCH}.zip")
    destination = Path(captured["destination"])
    assert destination.parent != tmp_path / "hermes-agent-affinda"
    assert destination.name == "hermes-agent-affinda-slack-context-customization.zip"


def test_installers_default_to_same_distribution_and_sanitize_zip_ref():
    root = Path(__file__).resolve().parents[2]
    shell = (root / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "install.ps1").read_text(encoding="utf-8")

    for text in (shell, powershell):
        assert MANAGED_BRANCH in text
        assert MANAGED_REPO in text
    assert "--repo-url" in shell
    assert '${HERMES_INSTALL_REPO_URL_SSH:-${HERMES_INSTALL_REPO_URL:-' in shell
    assert "[string]$RepoUrl" in powershell
    assert "$safeZipLabel" in powershell
