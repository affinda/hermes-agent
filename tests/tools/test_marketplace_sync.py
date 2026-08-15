"""Tests for tools/marketplace_sync.py — config resolution and skill sync."""

from pathlib import Path
from unittest.mock import patch

import tools.marketplace_sync as ms
from tools.marketplace_sync import (
    _configured_plugins,
    _resolve_git_url,
    _cache_dir_for,
    _read_manifest,
    _write_manifest,
    _sync_tree,
    sync_marketplace,
    DEFAULT_MARKETPLACE_REPO,
    DEFAULT_REF,
)


def _make_skill(parent: Path, name: str, body: str = "do a thing") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {body}\n---\n\n# {name}\n{body}\n",
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# _configured_plugins
# ---------------------------------------------------------------------------

class TestConfiguredPlugins:
    def _with_config(self, cfg):
        return patch("hermes_cli.config.load_config_readonly", return_value=cfg)

    def test_absent_key_uses_default(self):
        with self._with_config({}):
            assert _configured_plugins() == [
                {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": DEFAULT_REF},
                {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "hermesbots", "ref": DEFAULT_REF},
            ]

    def test_string_shorthand(self):
        with self._with_config({"marketplace_plugins": ["pathfindr"]}):
            assert _configured_plugins() == [
                {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "pathfindr", "ref": DEFAULT_REF}
            ]

    def test_dict_form_with_overrides(self):
        cfg = {"marketplace_plugins": [{"repo": "acme/mkt", "plugin": "foo", "ref": "dev"}]}
        with self._with_config(cfg):
            assert _configured_plugins() == [
                {"repo": "acme/mkt", "plugin": "foo", "ref": "dev"}
            ]

    def test_dict_defaults_repo_and_ref(self):
        with self._with_config({"marketplace_plugins": [{"plugin": "foo"}]}):
            assert _configured_plugins() == [
                {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "foo", "ref": DEFAULT_REF}
            ]

    def test_empty_list_disables(self):
        with self._with_config({"marketplace_plugins": []}):
            assert _configured_plugins() == []

    def test_non_list_ignored(self):
        with self._with_config({"marketplace_plugins": "affinda"}):
            assert _configured_plugins() == []

    def test_dict_without_plugin_skipped(self):
        with self._with_config({"marketplace_plugins": [{"repo": "acme/mkt"}, "ok"]}):
            assert _configured_plugins() == [
                {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "ok", "ref": DEFAULT_REF}
            ]


# ---------------------------------------------------------------------------
# git url / cache helpers
# ---------------------------------------------------------------------------

class TestGitHelpers:
    def test_shorthand_to_https(self):
        assert _resolve_git_url("affinda/plugin-marketplace") == (
            "https://github.com/affinda/plugin-marketplace.git"
        )

    def test_full_urls_pass_through(self):
        for url in (
            "https://github.com/a/b.git",
            "git@github.com:a/b.git",
            "ssh://git@github.com/a/b.git",
            "file:///tmp/b",
        ):
            assert _resolve_git_url(url) == url

    def test_cache_dir_sanitised(self, tmp_path):
        with patch.object(ms, "CACHE_ROOT", tmp_path):
            assert _cache_dir_for("affinda/plugin-marketplace").name == (
                "affinda_plugin-marketplace"
            )


# ---------------------------------------------------------------------------
# manifest roundtrip
# ---------------------------------------------------------------------------

class TestManifest:
    def test_roundtrip_and_sorted(self, tmp_path):
        mf = tmp_path / ".marketplace_manifest"
        entries = {"marketplace/affinda/z": "h1", "marketplace/affinda/a": "h2"}
        with patch.object(ms, "MANIFEST_FILE", mf):
            _write_manifest(entries)
            assert _read_manifest() == entries
        names = [ln.split(":")[0] for ln in mf.read_text().strip().splitlines()]
        assert names == sorted(names)

    def test_missing_manifest(self, tmp_path):
        with patch.object(ms, "MANIFEST_FILE", tmp_path / "nope"):
            assert _read_manifest() == {}


# ---------------------------------------------------------------------------
# _sync_tree per-skill semantics
# ---------------------------------------------------------------------------

class TestSyncTree:
    def test_new_skill_copied(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            res = _sync_tree(src, dest_root, manifest, quiet=True)
        assert res["copied"] == ["alpha"]
        assert (dest_root / "alpha" / "SKILL.md").exists()
        assert "marketplace/affinda/alpha" in manifest

    def test_unchanged_is_skipped(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            res2 = _sync_tree(src, dest_root, manifest, quiet=True)
        assert res2["copied"] == []
        assert res2["updated"] == []
        assert res2["skipped"] == 1

    def test_upstream_change_updates(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha", body="v1")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            _make_skill(src, "alpha", body="v2")  # upstream edit
            res2 = _sync_tree(src, dest_root, manifest, quiet=True)
        assert res2["updated"] == ["alpha"]
        assert "v2" in (dest_root / "alpha" / "SKILL.md").read_text()

    def test_user_modified_is_preserved(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha", body="v1")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            # user edits their local copy
            (dest_root / "alpha" / "SKILL.md").write_text("local hand-edit\n")
            # upstream also changes
            _make_skill(src, "alpha", body="v2")
            res2 = _sync_tree(src, dest_root, manifest, quiet=True)
        assert res2["user_modified"] == ["alpha"]
        assert (dest_root / "alpha" / "SKILL.md").read_text() == "local hand-edit\n"

    def test_user_deleted_is_respected(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            # user deletes the synced copy
            import shutil
            shutil.rmtree(dest_root / "alpha")
            res2 = _sync_tree(src, dest_root, manifest, quiet=True)
        assert res2["copied"] == []
        assert not (dest_root / "alpha").exists()


# ---------------------------------------------------------------------------
# sync_marketplace end-to-end (clone mocked with a local dir)
# ---------------------------------------------------------------------------

class TestSyncMarketplace:
    def _fake_clone(self, root: Path, plugin: str, skills: list[str]) -> Path:
        skills_root = root / "plugins" / plugin / "skills"
        for s in skills:
            _make_skill(skills_root, s)
        return root

    def test_installs_and_picks_up_new_skill(self, tmp_path):
        skills_dir = tmp_path / "home" / "skills"
        manifest_file = skills_dir / ".marketplace_manifest"
        clone = self._fake_clone(tmp_path / "clone", "affinda", ["suno-music"])

        plugins = [{"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": "main"}]
        with patch.object(ms, "SKILLS_DIR", skills_dir), \
             patch.object(ms, "MANIFEST_FILE", manifest_file), \
             patch.object(ms, "_configured_plugins", return_value=plugins), \
             patch.object(ms, "_ensure_clone", return_value=clone):
            r1 = sync_marketplace(quiet=True)
            assert r1["copied"] == ["suno-music"]
            assert r1["errors"] == []
            assert (skills_dir / "marketplace" / "affinda" / "suno-music" / "SKILL.md").exists()

            # A new skill is added to the plugin upstream...
            _make_skill(clone / "plugins" / "affinda" / "skills", "generated-static-pages")
            r2 = sync_marketplace(quiet=True)
            assert r2["copied"] == ["generated-static-pages"]
            assert (skills_dir / "marketplace" / "affinda" / "generated-static-pages").exists()

    def test_removed_upstream_skill_pruned_from_manifest(self, tmp_path):
        skills_dir = tmp_path / "home" / "skills"
        manifest_file = skills_dir / ".marketplace_manifest"
        clone = self._fake_clone(tmp_path / "clone", "affinda", ["a", "b"])
        plugins = [{"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": "main"}]
        with patch.object(ms, "SKILLS_DIR", skills_dir), \
             patch.object(ms, "MANIFEST_FILE", manifest_file), \
             patch.object(ms, "_configured_plugins", return_value=plugins), \
             patch.object(ms, "_ensure_clone", return_value=clone):
            sync_marketplace(quiet=True)
            import shutil
            shutil.rmtree(clone / "plugins" / "affinda" / "skills" / "b")
            r2 = sync_marketplace(quiet=True)
        assert "marketplace/affinda/b" in r2["cleaned"]

    def test_clone_failure_is_non_fatal(self, tmp_path):
        skills_dir = tmp_path / "home" / "skills"
        manifest_file = skills_dir / ".marketplace_manifest"
        plugins = [{"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": "main"}]
        with patch.object(ms, "SKILLS_DIR", skills_dir), \
             patch.object(ms, "MANIFEST_FILE", manifest_file), \
             patch.object(ms, "_configured_plugins", return_value=plugins), \
             patch.object(ms, "_ensure_clone", return_value=None):
            r = sync_marketplace(quiet=True)
        assert r["copied"] == []
        assert r["errors"] and "clone/fetch failed" in r["errors"][0]

    def test_disabled_when_no_plugins(self, tmp_path):
        with patch.object(ms, "_configured_plugins", return_value=[]):
            r = sync_marketplace(quiet=True)
        assert r == {
            "copied": [], "updated": [], "user_modified": [],
            "skipped": 0, "cleaned": [], "synced": [], "errors": [],
        }
