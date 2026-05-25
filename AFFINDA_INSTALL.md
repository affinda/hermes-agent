# Affinda Hermes installation and update guide

This fork is the canonical Affinda-managed Hermes distribution for internal boards and long-running agents. Use it instead of the upstream NousResearch installer when bootstrapping Affinda-managed agents such as marketing, product, or dev-agent boards.

## Fresh Linux/macOS/WSL install

```bash
curl -fsSL https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.sh | bash
```

The Affinda installer defaults to:

- repository: `https://github.com/affinda/hermes-agent.git`
- branch: `affinda/slack-context-customization`
- install directory: `~/.hermes/hermes-agent` for normal users, `/usr/local/lib/hermes-agent` for root Linux installs

If you need to pin a different branch or repository while testing a rollout:

```bash
curl -fsSL https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.sh \
  | bash -s -- --repo-url https://github.com/affinda/hermes-agent.git --branch affinda/slack-context-customization
```

Equivalent environment-variable form for automation:

```bash
export HERMES_INSTALL_REPO_URL=https://github.com/affinda/hermes-agent.git
export HERMES_INSTALL_BRANCH=affinda/slack-context-customization
curl -fsSL https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.sh | bash
```

## Fresh Windows install

Run in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.ps1)
```

Optional explicit form:

```powershell
$env:HERMES_INSTALL_REPO_URL = "https://github.com/affinda/hermes-agent.git"
$env:HERMES_INSTALL_BRANCH = "affinda/slack-context-customization"
iex (irm https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.ps1)
```

## Migrating an existing Nous install to the Affinda fork

For an existing git-based install:

```bash
cd ~/.hermes/hermes-agent
git remote set-url origin https://github.com/affinda/hermes-agent.git
git fetch origin
git checkout affinda/slack-context-customization
git pull --ff-only origin affinda/slack-context-customization
hermes update --check
hermes update --yes
```

If Hermes was installed as root on Linux, use `/usr/local/lib/hermes-agent` instead of `~/.hermes/hermes-agent`.

## Updating agents

Normal updates are deliberately boring:

```bash
hermes update --check
hermes update --yes
```

`hermes update` pulls from the install checkout's `origin`, so an Affinda install tracks the Affinda fork. Restart any long-running gateway after updating:

```bash
hermes gateway restart
```

### Marketplace plugin skills

`hermes update` also pulls skills from configured [plugin-marketplace](https://github.com/affinda/plugin-marketplace) plugins. After the code `git pull` and bundled-skill sync, it re-fetches the marketplace and copies any new or changed skill folders into `~/.hermes/skills/marketplace/<plugin>/`. This means **a skill added to a plugin upstream appears on the agent at its next `hermes update`** — no fork change required. Local edits and deletions of synced skills are preserved (hash-tracked in `~/.hermes/skills/.marketplace_manifest`).

By default agents track the `affinda` plugin. Configure via `marketplace_plugins` in `~/.hermes/config.yaml` (each item is a plugin name or a `{repo, plugin, ref}` mapping); set it to `[]` to disable. Marketplace sync only copies SKILL.md folders — MCP servers a plugin declares are still registered separately under `mcp_servers`. Run it on demand with `python -m tools.marketplace_sync`.

## Verification checklist

Run these on each board after install or migration:

```bash
hermes --version
hermes doctor
cd ~/.hermes/hermes-agent && git remote -v && git branch --show-current
hermes update --check
```

Expected remote/branch for the current Affinda rollout:

```text
origin  https://github.com/affinda/hermes-agent.git (fetch)
affinda/slack-context-customization
```

## Current rollout note

The durable target is a simple `affinda/main` distribution. Until the Slack context customizations are promoted to the fork's `main`, the canonical install path intentionally tracks `affinda/slack-context-customization` so new agents receive the same behavior as the managed Slack gateway.
