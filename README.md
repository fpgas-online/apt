# apt

APT package repository for the fpgas.online FPGA-as-a-Service platform, served via GitHub Pages at <https://apt.fpgas.online/>.

## Overview

This repository hosts Debian packages used by fpgas.online Raspberry Pi nodes. Packages are added automatically: this repo periodically pulls new `.deb` builds from each source repository's GitHub Releases and adds them to the pool, regenerating repository metadata. The landing page at <https://apt.fpgas.online/> lists the hosted packages and the setup for each suite.

## Hosted Packages

- **fpgas-online-cam** -- Camera streaming tools for FPGA board video feeds.
- **fpgas-online-setup-pi** -- Pi node configuration, services, and environment setup.
- **fpgas-online-tt** -- Tiny Tapeout demo-board serial bridge daemon for tinytapeout.fpgas.online.

## Adding the Repository on a Pi

This repository follows the same convention as every other apt repository
published from mithro/* and fpgas-online/*
([docs/conventions.md](https://github.com/mithro/apt-repo-action/blob/main/docs/conventions.md)):
one flat repository per suite, key `apt.gpg`, keyring `/etc/apt/keyrings/apt.gpg`.

```bash
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://apt.fpgas.online/apt.gpg | sudo tee /etc/apt/keyrings/apt.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/apt.gpg] https://apt.fpgas.online/$(. /etc/os-release; echo $VERSION_CODENAME)/ ./" \
  | sudo tee /etc/apt/sources.list.d/apt.list
sudo apt update
sudo apt install fpgas-online-setup-pi
```

Suites: `bookworm`, `trixie`. The infra repo's `fpgas-apt` ansible role does this.

The previous layout (`dists/<suite>/main`, `pubkey.gpg`,
`/usr/share/keyrings/fpgas-online.gpg`) is still served, frozen, while clients
move (`legacy-paths` in `.github/workflows/publish.yml`); it no longer receives
new packages.

## How It Works

### Pull-based ingest (primary path, secretless)

1. Each deb-producing source repository (listed in `tools/package_sources.toml`) publishes every green `main` build's `.deb` as an asset on a GitHub Release in its own repo — by convention the current series tag's release (`v0.0`, `v0.1`, ...), since source repos' tag rulesets only allow `vX.Y`-shaped tags. A repo may accumulate several such releases over time as its series advances. Each release is created with the source repo's own `GITHUB_TOKEN` — no cross-repo token is ever needed.
2. The `pull-debs` workflow in this repository runs on a schedule (every 15 minutes) and on demand (`workflow_dispatch`). For each source repo it enumerates *all* of that repo's GitHub Releases (paginated) and pulls every `<package>_*.deb` asset not already present in `pool/main/`, downloading them anonymously (`tools/pull_debs.py`). A repo with no releases yet is simply skipped.
3. If any new `.deb`s were pulled, the workflow commits them to `pool/main/` and dispatches `publish.yml` (a push made with the workflow's own `GITHUB_TOKEN` never triggers other workflows by itself). `workflow_dispatch` with `force_update: true` republishes without new debs.
4. `publish.yml` uploads the pool as each suite's packages and calls the shared [`mithro/apt-repo-action`](https://github.com/mithro/apt-repo-action) publish workflow, which indexes, signs (secret `APT_GPG_PRIVATE_KEY`) and deploys to GitHub Pages.

### Push-based ingest (legacy path)

A deb-producing repository can still trigger the `receive-deb` workflow directly via `repository_dispatch`, which downloads the `.deb` artifact into `pool/main/`, commits it, then likewise dispatches `publish.yml`. This path is kept for compatibility but new source repos should prefer the pull-based model above.

## GPG Signing

The private key is the `APT_GPG_PRIVATE_KEY` secret; the shared publish workflow
exports the public half on every publish as `apt.gpg` (binary) and `apt.asc`
(armoured).

## Directory Structure

```
pool/main/                   Binary .deb packages (the store; every suite publishes all of it)
packaging/apt-intro.html     Description placed on the generated index page
tools/
  package_sources.toml       Package name -> source GitHub repo map
  pull_debs.py               Pulls new debs from source repos' GitHub Releases
  test_pull_debs.py          Unit tests for pull_debs.py
.github/workflows/
  pull-debs.yml              Pulls new debs on a schedule, commits them, dispatches publish.yml
  receive-deb.yml            Legacy push-based deb ingest (repository_dispatch), dispatches publish.yml
  publish.yml                Indexes, signs and deploys via mithro/apt-repo-action
  lint.yml                   CI linting
```

## Adding a Package

1. Add an entry to `tools/package_sources.toml` mapping the package name to its source GitHub repo, e.g. `"fpgas-online-foo" = "fpgas-online/fpgas.online-foo"`.
2. Have the source repo's CI publish each green `main` build's `.deb` as an asset on a GitHub Release in its own repo — by convention the current series tag's release (`v0.0`, `v0.1`, ...) — created with that repo's own `GITHUB_TOKEN`. The apt repo pulls every `<package>_*.deb` asset from *all* of the repo's releases, so older series releases don't need to be cleaned up. The `pull-debs` workflow here will pick up new assets on its next scheduled run (or trigger it manually via `workflow_dispatch`).

## Related Repositories

- [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) -- Source for the `fpgas-online-cam` package
- [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) -- Ansible playbooks that configure Pis to use this repo
- [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) -- Source for the `fpgas-online-setup-pi` package
- [fpgas.online-tt](https://github.com/fpgas-online/fpgas.online-tt) -- Source for the `fpgas-online-tt` package

## License

See [LICENSE](LICENSE).
