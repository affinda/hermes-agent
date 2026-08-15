#!/usr/bin/env python3
"""
Marketplace Sync -- Pull skills from Affinda plugin-marketplace plugins.

Sibling of ``skills_sync.py``. Where ``skills_sync`` seeds skills bundled
*inside* this repo, this module pulls skills from one or more plugins in a
remote Claude-Code-style plugin marketplace (default:
``affinda/plugin-marketplace``) and copies them into
``~/.hermes/skills/marketplace/<plugin>/``.

Why this exists
---------------
``hermes update`` does ``git pull`` + ``sync_skills()``. That auto-pulls new
*bundled* skills, but the marketplace lives in a separate repo. This module
gives the same "new skill upstream → appears on next update" behaviour for
marketplace plugins: each run re-fetches the marketplace and copies any skill
folders that are new or changed.

Configuration (``~/.hermes/config.yaml``)
------------------------------------------
``marketplace_plugins`` — list of plugins to track. Each item is either a
bare plugin name (uses the default marketplace repo) or a mapping::

    marketplace_plugins:
      - affinda                         # shorthand: default repo, ref=main
      - repo: affinda/plugin-marketplace
        plugin: pathfindr
        ref: main

When the key is absent the default is ``[affinda, hermesbots]`` (the base tool
set plus the shared Hermes-bots skills). Set it to an empty list
(``marketplace_plugins: []``) to disable marketplace sync entirely.

Manifest
--------
Tracked in ``~/.hermes/skills/.marketplace_manifest`` (same ``name:hash``
format as the bundled manifest, but keyed by the destination path relative to
the skills dir, e.g. ``marketplace/affinda/suno-music``). The new / updated /
user-modified / deleted semantics mirror ``skills_sync.sync_skills`` so local
edits and deletions are respected.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List

from hermes_constants import get_hermes_home
from agent.skill_utils import is_excluded_skill_path
from tools.skills_sync import _dir_hash, _read_skill_name
from utils import atomic_replace

logger = logging.getLogger(__name__)


HERMES_HOME = get_hermes_home()
SKILLS_DIR = HERMES_HOME / "skills"
MANIFEST_FILE = SKILLS_DIR / ".marketplace_manifest"
CACHE_ROOT = HERMES_HOME / "marketplace-cache"

# Skills land under ~/.hermes/skills/<DEST_CATEGORY>/<plugin>/ so they are
# clearly attributable and never clobber bundled skill category dirs.
DEST_CATEGORY = "marketplace"

DEFAULT_MARKETPLACE_REPO = "affinda/plugin-marketplace"
DEFAULT_REF = "main"
DEFAULT_PLUGINS: List[dict] = [
    {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "affinda", "ref": DEFAULT_REF},
    {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": "hermesbots", "ref": DEFAULT_REF},
]

# Bound git operations so a hanging network never blocks `hermes update`.
_GIT_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _configured_plugins() -> List[dict]:
    """Resolve the list of plugins to sync from ``~/.hermes/config.yaml``.

    Returns a list of ``{"repo", "plugin", "ref"}`` dicts. Absent config →
    the default (``affinda`` + ``hermesbots`` from the default marketplace).
    An explicit empty list disables sync.
    """
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly()
    except Exception:  # pragma: no cover - config is best-effort here
        cfg = {}

    raw = cfg.get("marketplace_plugins")
    if raw is None:
        raw = DEFAULT_PLUGINS
    if not isinstance(raw, list):
        logger.debug("marketplace_plugins is not a list (%r); ignoring", type(raw))
        return []

    resolved: List[dict] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                resolved.append(
                    {"repo": DEFAULT_MARKETPLACE_REPO, "plugin": name, "ref": DEFAULT_REF}
                )
        elif isinstance(item, dict) and item.get("plugin"):
            resolved.append(
                {
                    "repo": str(item.get("repo") or DEFAULT_MARKETPLACE_REPO),
                    "plugin": str(item["plugin"]),
                    "ref": str(item.get("ref") or DEFAULT_REF),
                }
            )
    return resolved


# ---------------------------------------------------------------------------
# Manifest (name:hash, keyed by skills-dir-relative dest path)
# ---------------------------------------------------------------------------

def _read_manifest() -> Dict[str, str]:
    if not MANIFEST_FILE.exists():
        return {}
    try:
        result: Dict[str, str] = {}
        for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, hash_val = line.partition(":")
            if name.strip():
                result[name.strip()] = hash_val.strip()
        return result
    except (OSError, IOError):
        return {}


def _write_manifest(entries: Dict[str, str]) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(f"{name}:{h}" for name, h in sorted(entries.items())) + "\n"
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(MANIFEST_FILE.parent),
            prefix=".marketplace_manifest_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            atomic_replace(tmp_path, MANIFEST_FILE)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:  # pragma: no cover - disk-failure path
        logger.debug("Failed to write marketplace manifest %s: %s", MANIFEST_FILE, e)


# ---------------------------------------------------------------------------
# Git fetch of the marketplace
# ---------------------------------------------------------------------------

def _resolve_git_url(repo: str) -> str:
    """Turn ``owner/repo`` into a public HTTPS clone URL; pass URLs through.

    The default marketplace is public, so HTTPS works in installs without a
    configured GitHub SSH key and avoids an interactive SSH authentication
    prompt during ``hermes update``.
    """
    if repo.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return repo
    return f"https://github.com/{repo}.git"


def _cache_dir_for(repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", repo.strip())
    return CACHE_ROOT / slug


def _run_git(args: List[str], cwd: Path | None = None) -> bool:
    git = shutil.which("git")
    if not git:
        logger.debug("git not found on PATH; cannot sync marketplace")
        return False
    try:
        result = subprocess.run(
            [git, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("git %s failed: %s", " ".join(args), e)
        return False
    if result.returncode != 0:
        logger.debug("git %s exited %d: %s", " ".join(args), result.returncode, result.stderr.strip())
        return False
    return True


def _ensure_clone(repo: str, ref: str) -> Path | None:
    """Clone (shallow) or refresh the marketplace cache for *repo* at *ref*.

    Returns the checkout path, or ``None`` if the fetch failed (sync then
    becomes a no-op for this plugin — never fatal).
    """
    url = _resolve_git_url(repo)
    dest = _cache_dir_for(repo)

    if (dest / ".git").is_dir():
        ok = (
            _run_git(["fetch", "--depth", "1", "origin", ref], cwd=dest)
            and _run_git(["checkout", "-B", ref, f"origin/{ref}"], cwd=dest)
        )
        return dest if ok else None

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    # Clear any partial/broken dir before a fresh clone.
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    ok = _run_git(["clone", "--depth", "1", "--branch", ref, url, str(dest)])
    return dest if ok else None


# ---------------------------------------------------------------------------
# Copy / manifest sync
# ---------------------------------------------------------------------------

def _iter_skill_dirs(src: Path):
    for skill_md in sorted(src.rglob("SKILL.md")):
        if is_excluded_skill_path(skill_md):
            continue
        yield skill_md.parent


def _sync_tree(src: Path, dest_root: Path, manifest: Dict[str, str], quiet: bool) -> dict:
    """Copy skill folders from *src* into *dest_root*, updating *manifest*.

    Mirrors ``skills_sync.sync_skills`` per-skill semantics. Manifest keys are
    the destination path relative to ``SKILLS_DIR`` (e.g.
    ``marketplace/affinda/suno-music``). Returns counters plus ``seen_keys``
    so the caller can prune entries for skills removed upstream.
    """
    copied: List[str] = []
    updated: List[str] = []
    user_modified: List[str] = []
    skipped = 0
    seen_keys: set[str] = set()

    for skill_dir in _iter_skill_dirs(src):
        rel = skill_dir.relative_to(src)
        dest = dest_root / rel
        key = str(dest.relative_to(SKILLS_DIR))
        seen_keys.add(key)
        src_hash = _dir_hash(skill_dir)
        name = _read_skill_name(skill_dir / "SKILL.md", skill_dir.name)

        if key not in manifest:
            # New skill — never synced before.
            if dest.exists():
                skipped += 1
                if _dir_hash(dest) == src_hash:
                    manifest[key] = src_hash
                elif not quiet:
                    print(
                        f"  ⚠ {name}: a local skill already exists at {key} and "
                        f"differs from the marketplace — yours was kept."
                    )
            else:
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, dest)
                    copied.append(name)
                    manifest[key] = src_hash
                    if not quiet:
                        print(f"  + {name}")
                except (OSError, IOError) as e:
                    if not quiet:
                        print(f"  ! Failed to copy {name}: {e}")
            continue

        if not dest.exists():
            # In manifest but user deleted it — respect that.
            skipped += 1
            continue

        origin_hash = manifest.get(key, "")
        user_hash = _dir_hash(dest)

        if not origin_hash:
            manifest[key] = user_hash
            skipped += 1
            continue

        if user_hash != origin_hash:
            user_modified.append(name)
            if not quiet:
                print(f"  ~ {name} (user-modified, skipping)")
            continue

        if src_hash != origin_hash:
            backup = dest.with_suffix(".bak")
            try:
                shutil.move(str(dest), str(backup))
                try:
                    shutil.copytree(skill_dir, dest)
                    manifest[key] = src_hash
                    updated.append(name)
                    if not quiet:
                        print(f"  ↑ {name} (updated)")
                    shutil.rmtree(backup, ignore_errors=True)
                except (OSError, IOError):
                    if backup.exists() and not dest.exists():
                        shutil.move(str(backup), str(dest))
                    raise
            except (OSError, IOError) as e:
                if not quiet:
                    print(f"  ! Failed to update {name}: {e}")
        else:
            skipped += 1

    return {
        "copied": copied,
        "updated": updated,
        "user_modified": user_modified,
        "skipped": skipped,
        "seen_keys": seen_keys,
    }


def _write_plugin_description(dest_root: Path, repo: str, plugin: str) -> None:
    desc = dest_root / "DESCRIPTION.md"
    if desc.exists():
        return
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        desc.write_text(
            f"Skills synced from the '{plugin}' plugin of {repo} "
            f"(managed by hermes update; see tools/marketplace_sync.py).\n",
            encoding="utf-8",
        )
    except (OSError, IOError) as e:  # pragma: no cover
        logger.debug("Could not write %s: %s", desc, e)


def sync_marketplace(quiet: bool = False) -> dict:
    """Sync configured marketplace plugin skills into ``~/.hermes/skills/``.

    Returns a summary dict: ``copied``, ``updated``, ``user_modified``,
    ``skipped``, ``cleaned``, ``synced`` (list of ``repo:plugin``), ``errors``.
    Network/clone failures are captured in ``errors`` and never raise.
    """
    plugins = _configured_plugins()
    summary = {
        "copied": [],
        "updated": [],
        "user_modified": [],
        "skipped": 0,
        "cleaned": [],
        "synced": [],
        "errors": [],
    }
    if not plugins:
        return summary

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    all_seen: set[str] = set()
    synced_prefixes: List[str] = []

    for entry in plugins:
        repo, plugin, ref = entry["repo"], entry["plugin"], entry["ref"]
        clone = _ensure_clone(repo, ref)
        if clone is None:
            summary["errors"].append(f"{repo}#{ref}: clone/fetch failed (skipped)")
            continue

        src = clone / "plugins" / plugin / "skills"
        if not src.is_dir():
            summary["errors"].append(f"{repo}: plugin '{plugin}' has no skills/ directory")
            continue

        dest_root = SKILLS_DIR / DEST_CATEGORY / plugin
        res = _sync_tree(src, dest_root, manifest, quiet)
        _write_plugin_description(dest_root, repo, plugin)

        summary["copied"] += res["copied"]
        summary["updated"] += res["updated"]
        summary["user_modified"] += res["user_modified"]
        summary["skipped"] += res["skipped"]
        all_seen |= res["seen_keys"]
        summary["synced"].append(f"{repo}:{plugin}")
        synced_prefixes.append(f"{DEST_CATEGORY}/{plugin}/")

    # Prune manifest entries for skills removed upstream — but only within the
    # prefixes of plugins we synced successfully this run, so a failed fetch
    # never wipes tracking. The on-disk copy is left in place (conservative,
    # matching skills_sync); only the manifest entry is dropped.
    cleaned: List[str] = []
    for key in list(manifest.keys()):
        if key in all_seen:
            continue
        if any(key.startswith(p) for p in synced_prefixes):
            del manifest[key]
            cleaned.append(key)
    summary["cleaned"] = cleaned

    _write_manifest(manifest)
    return summary


if __name__ == "__main__":
    print("Syncing marketplace plugin skills into ~/.hermes/skills/ ...")
    result = sync_marketplace(quiet=False)
    parts = [
        f"{len(result['copied'])} new",
        f"{len(result['updated'])} updated",
        f"{result['skipped']} unchanged",
    ]
    if result["user_modified"]:
        parts.append(f"{len(result['user_modified'])} user-modified (kept)")
    if result["cleaned"]:
        parts.append(f"{len(result['cleaned'])} cleaned from manifest")
    print(f"\nDone: {', '.join(parts)}.")
    if result["synced"]:
        print(f"Plugins: {', '.join(result['synced'])}")
    for err in result["errors"]:
        print(f"  ! {err}")
