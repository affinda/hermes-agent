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

    def test_update_does_not_touch_preexisting_bak_sibling(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "alpha", body="v1")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            unrelated = dest_root / "alpha.bak"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("unrelated", encoding="utf-8")
            _make_skill(src, "alpha", body="v2")
            result = _sync_tree(src, dest_root, manifest, quiet=True)

        assert result["updated"] == ["alpha"]
        assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "unrelated"

    def test_backup_cleanup_failure_keeps_new_sync_consistent_and_retains_backup(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / "src"
        _make_skill(src, "alpha", body="v1")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            original_hash = manifest["marketplace/affinda/alpha"]
            _make_skill(src, "alpha", body="v2")
            real_rmtree = ms.shutil.rmtree
            failed_once = False

            def fail_first_backup_cleanup(path, *args, **kwargs):
                nonlocal failed_once
                if (
                    not failed_once
                    and ".marketplace-backup-" in Path(path).name
                ):
                    failed_once = True
                    raise OSError("simulated backup cleanup failure")
                return real_rmtree(path, *args, **kwargs)

            monkeypatch.setattr(ms.shutil, "rmtree", fail_first_backup_cleanup)
            result = _sync_tree(src, dest_root, manifest, quiet=True)

        assert result["updated"] == ["alpha"]
        assert manifest["marketplace/affinda/alpha"] != original_hash
        assert "v2" in (dest_root / "alpha" / "SKILL.md").read_text()
        retained = list(dest_root.glob(".alpha.marketplace-backup-*"))
        assert len(retained) == 1
        assert "v1" in (retained[0] / "skill" / "SKILL.md").read_text()

    def test_incomplete_rollback_retains_only_known_good_backup(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / "src"
        _make_skill(src, "alpha", body="v1")
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}
        with patch.object(ms, "SKILLS_DIR", skills_dir):
            _sync_tree(src, dest_root, manifest, quiet=True)
            original_hash = manifest["marketplace/affinda/alpha"]
            _make_skill(src, "alpha", body="v2")
            real_copytree = ms.shutil.copytree
            real_rmtree = ms.shutil.rmtree

            def fail_new_copy(source, destination, *args, **kwargs):
                if Path(destination) == dest_root / "alpha":
                    Path(destination).mkdir(parents=True)
                    (Path(destination) / "partial.txt").write_text("partial")
                    raise OSError("simulated copy failure")
                return real_copytree(source, destination, *args, **kwargs)

            def fail_partial_cleanup(path, *args, **kwargs):
                if Path(path) == dest_root / "alpha":
                    raise OSError("simulated rollback cleanup failure")
                return real_rmtree(path, *args, **kwargs)

            monkeypatch.setattr(ms.shutil, "copytree", fail_new_copy)
            monkeypatch.setattr(ms.shutil, "rmtree", fail_partial_cleanup)
            result = _sync_tree(src, dest_root, manifest, quiet=True)

        assert result["updated"] == []
        assert manifest["marketplace/affinda/alpha"] == original_hash
        retained = list(dest_root.glob(".alpha.marketplace-backup-*"))
        assert len(retained) == 1
        assert "v1" in (retained[0] / "skill" / "SKILL.md").read_text()

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

    def test_symlinked_marketplace_content_is_rejected(self, tmp_path):
        src = tmp_path / "src"
        skill = _make_skill(src, "unsafe")
        secret = tmp_path / "outside-secret.txt"
        secret.write_text("must not be copied", encoding="utf-8")
        (skill / "secret.txt").symlink_to(secret)
        skills_dir = tmp_path / "skills"
        dest_root = skills_dir / "marketplace" / "affinda"
        manifest: dict = {}

        with patch.object(ms, "SKILLS_DIR", skills_dir):
            result = _sync_tree(src, dest_root, manifest, quiet=True)

        assert result["copied"] == []
        assert result["skipped"] == 1
        assert not (dest_root / "unsafe").exists()

    def test_symlinked_destination_parent_is_rejected(self, tmp_path):
        src = tmp_path / "src"
        _make_skill(src, "safe-source")
        skills_dir = tmp_path / "skills"
        managed_parent = skills_dir / "marketplace"
        managed_parent.mkdir(parents=True)
        outside = tmp_path / "outside-destination"
        outside.mkdir()
        (managed_parent / "affinda").symlink_to(outside, target_is_directory=True)
        manifest: dict = {}

        with patch.object(ms, "SKILLS_DIR", skills_dir):
            result = _sync_tree(
                src,
                managed_parent / "affinda",
                manifest,
                quiet=True,
            )

        assert result["copied"] == []
        assert result["skipped"] == 1
        assert not (outside / "safe-source").exists()


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

    def test_symlinked_plugin_skills_root_is_rejected(self, tmp_path):
        skills_dir = tmp_path / "home" / "skills"
        manifest_file = skills_dir / ".marketplace_manifest"
        clone = tmp_path / "clone"
        plugin_dir = clone / "plugins" / "affinda"
        plugin_dir.mkdir(parents=True)
        outside = tmp_path / "outside-source"
        _make_skill(outside, "exfiltration")
        (plugin_dir / "skills").symlink_to(outside, target_is_directory=True)
        plugins = [
            {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": "main"}
        ]

        with patch.object(ms, "SKILLS_DIR", skills_dir), \
             patch.object(ms, "MANIFEST_FILE", manifest_file), \
             patch.object(ms, "_configured_plugins", return_value=plugins), \
             patch.object(ms, "_ensure_clone", return_value=clone):
            result = sync_marketplace(quiet=True)

        assert result["copied"] == []
        assert result["errors"] and "unsafe skills/ directory" in result["errors"][0]
        assert not (
            skills_dir / "marketplace" / "affinda" / "exfiltration"
        ).exists()

    def test_disabled_when_no_plugins(self, tmp_path):
        with patch.object(ms, "_configured_plugins", return_value=[]):
            r = sync_marketplace(quiet=True)
        assert r == {
            "copied": [], "updated": [], "user_modified": [],
            "skipped": 0, "cleaned": [], "synced": [], "errors": [],
        }
