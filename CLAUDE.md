## Background

This repo is part of the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.
The platform provides remote access to real FPGA boards (Arty A7, NeTV2, Fomu, TinyTapeout)
via PoE-powered Raspberry Pis that are network-booted. There are two deployment sites:
Welland (private test lab) and PS1 hackerspace (public service in Chicago).

This codebase was extracted from the original monorepo [`carlfk/pici`](https://github.com/CarlFK/pici)
in April 2026 using `git filter-repo` to preserve commit history. The monorepo was split into
purpose-specific repos under the `fpgas-online` GitHub organization, where each repo produces
installable artifacts (pip packages or deb packages) consumed by the infrastructure repo.

## Repository Overview

GitHub Pages-backed APT package repository for fpgas.online. Hosts `.deb` packages
that are installed on Raspberry Pi boards.

### How It Works

1. Deb-producing repos (fpgas.online-cam, fpgas.online-setup-pi, fpgas.online-tt) build `.deb` files in CI
2. On tagged releases, they trigger the `receive-deb` workflow in this repo via `repository_dispatch`
3. The workflow downloads the deb, adds it to `pool/`, regenerates repo metadata, and deploys to GitHub Pages
4. Pis install packages from `https://fpgas-online.github.io/apt`

The primary ingest path is now **secretless pull-based ingest**: source repos listed in
`tools/package_sources.toml` publish each green `main` build's `.deb` to a GitHub Release —
by convention the current series tag's release (`v0.0`, `v0.1`, ...), since source repos' tag
rulesets only allow `vX.Y`-shaped tags — in their own repo (no cross-repo token needed). The
apt repo pulls every `<package>_*.deb` asset from *all* of the repo's releases, not just the
latest, since a repo accumulates several series releases over time. The
`.github/workflows/pull-debs.yml` workflow runs on a schedule (every 15 minutes) and on
demand, downloading any new asset via `tools/pull_debs.py`, then running `update-repo.sh`
and committing if anything changed. `receive-deb.yml` (`repository_dispatch`, above) is kept
as a legacy push-based path for compatibility; both workflows share the `apt-repo-writes`
concurrency group so they never race on `pool/`/`dists/` commits.

### Hosted Packages

- `fpgas-online-cam` -- Camera capture scripts and systemd service
- `fpgas-online-setup-pi` -- Pi environment setup (shell, FPGA detection, status reporting)

### Key Files

- `update-repo.sh` -- Regenerates `Packages`, `Release`, signs with GPG
- `pubkey.gpg` -- GPG public key for verifying package signatures
- `.github/workflows/receive-deb.yml` -- CI workflow triggered by deb-producing repos

### Adding the Repo on a Pi

```
deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] https://fpgas-online.github.io/apt bookworm main
```

The infra repo's `fpgas-apt` ansible role handles this automatically.

### GPG Signing

The repo is signed with a GPG key (apt@fpgas.online). The private key is stored as
a GitHub Actions secret `APT_GPG_PRIVATE_KEY`.

## Conventions

- **Python**: Use `uv` for all Python commands (`uv run`, `uv pip`). Never use bare `python` or `pip`.
- **Dates**: Use ISO 8601 (YYYY-MM-DD) or day-first formats. Never American-style month-first dates.
- **Commits**: Make small, discrete commits. Each logical unit of work gets its own commit.
- **License**: Apache 2.0.
- **Linting**: All repos have CI lint workflows. Fix lint errors before pushing.
- **No force push**: Branch protection is enabled on main. Never force push.

## Related Repos

| Repo | Purpose |
|------|---------|
| [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) | Ansible infrastructure (playbooks, roles, inventory) |
| [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | Django web application |
| [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) | SNMP PoE switch management |
| [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) | Camera capture and streaming |
| [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) | Raspberry Pi environment setup |
| [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi) | Netboot filesystem tools |
| [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools) | Utility scripts |
| [fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) | FPGA test designs |
| [apt](https://github.com/fpgas-online/apt) | APT package repository (GitHub Pages) |

## Linting

- shellcheck: blocking (`update-repo.sh`)
