# Affinda Hermes installation and update guide

Affinda-managed Hermes installations track the Affinda distribution while retaining the current upstream install and update safety mechanisms.

## Linux, macOS, or WSL

```bash
curl -fsSL https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.sh | bash
```

Defaults:

- repository: `https://github.com/affinda/hermes-agent.git`
- branch: `affinda/slack-context-customization`
- install directory: the normal Hermes managed-install location

Override the rollout source when testing:

```bash
curl -fsSL https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.sh \
  | bash -s -- --repo-url https://github.com/example/hermes-agent.git --branch example/test
```

Automation can instead set `HERMES_INSTALL_REPO_URL`, `HERMES_INSTALL_REPO_URL_SSH`, and `HERMES_INSTALL_BRANCH`.

## Windows

```powershell
iex (irm https://raw.githubusercontent.com/affinda/hermes-agent/affinda/slack-context-customization/scripts/install.ps1)
```

PowerShell supports `-RepoUrl` and `-Branch`, or the `HERMES_INSTALL_REPO_URL`, `HERMES_INSTALL_REPO_URL_SSH`, and `HERMES_INSTALL_BRANCH` environment variables.

## Existing installations

Point an existing git checkout at the managed distribution once:

```bash
cd ~/.hermes/hermes-agent
git remote set-url origin https://github.com/affinda/hermes-agent.git
git fetch origin affinda/slack-context-customization
git checkout -B affinda/slack-context-customization origin/affinda/slack-context-customization
```

Root Linux installations may use `/usr/local/lib/hermes-agent` instead.

## Updates

```bash
hermes update --check
hermes update --yes
```

The updater defaults to `affinda/slack-context-customization`; `hermes update --branch <name>` remains available for rollout testing. It preserves upstream's local-change handling, transactional dependency refresh, backup policy, and recovery behavior. Restart a long-running gateway after an update:

```bash
hermes gateway restart
```

Verify the checkout with:

```bash
hermes --version
hermes doctor
git -C ~/.hermes/hermes-agent remote -v
git -C ~/.hermes/hermes-agent branch --show-current
```
