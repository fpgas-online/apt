# apt

APT package repository for the fpgas.online FPGA-as-a-Service platform, served via GitHub Pages at <https://fpgas-online.github.io/apt>.

## Overview

This repository hosts Debian packages used by fpgas.online Raspberry Pi nodes. Packages are added automatically: this repo periodically pulls new `.deb` builds from each source repository's GitHub Releases and adds them to the pool, regenerating repository metadata. The landing page at <https://fpgas-online.github.io/apt> lists the hosted packages and links to their source repositories, and every subdirectory has a browsable index.

## Hosted Packages

- **fpgas-online-cam** -- Camera streaming tools for FPGA board video feeds.
- **fpgas-online-setup-pi** -- Pi node configuration, services, and environment setup.
- **fpgas-online-tt** -- Tiny Tapeout demo-board serial bridge daemon for tinytapeout.fpgas.online.

## Adding the Repository on a Pi

Import the signing key and add the repository. The snippet works on both Debian bookworm (12) and trixie (13) — the `$(lsb_release -cs)` call picks up the current codename:

```bash
curl -fsSL https://fpgas-online.github.io/apt/pubkey.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/fpgas-online.gpg

echo "deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] https://fpgas-online.github.io/apt $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/fpgas-online.list

sudo apt update
```

Then install packages:

```bash
apt install fpgas-online-setup-pi
```

## How It Works

### Pull-based ingest (primary path, secretless)

1. Each deb-producing source repository (listed in `tools/package_sources.toml`) publishes every green `main` build's `.deb` as an asset on a GitHub Release in its own repo — by convention the current series tag's release (`v0.0`, `v0.1`, ...), since source repos' tag rulesets only allow `vX.Y`-shaped tags. A repo may accumulate several such releases over time as its series advances. Each release is created with the source repo's own `GITHUB_TOKEN` — no cross-repo token is ever needed.
2. The `pull-debs` workflow in this repository runs on a schedule (every 15 minutes) and on demand (`workflow_dispatch`). For each source repo it enumerates *all* of that repo's GitHub Releases (paginated) and pulls every `<package>_*.deb` asset not already present in `pool/main/`, downloading them anonymously (`tools/pull_debs.py`). A repo with no releases yet is simply skipped.
3. If any new `.deb`s were pulled, the workflow installs `apt-ftparchive`, imports the signing key, runs `update-repo.sh` to regenerate the `dists/` metadata (Packages, Release files) for both the `bookworm` and `trixie` suites, and commits/pushes the result. This signed metadata under `dists/` is committed to `main` (it is not `.gitignore`d) so it's available to mirror in the next step. The workflow can also be run manually with `workflow_dispatch` input `force_update: true` to regenerate and commit metadata even when no new debs were pulled.
4. Because the commit above is pushed with the workflow's own `GITHUB_TOKEN`, it does **not** trigger other workflows via GitHub's `push` event (GitHub suppresses that to prevent recursive runs). So after a successful push, `pull-debs.yml` explicitly runs `gh workflow run pages.yml --ref main` to dispatch the deploy. `pages.yml` then rebuilds the human-facing index (`tools/build_site.py`) and deploys the site to GitHub Pages via `actions/deploy-pages`, copying `pool/` and the already-committed `dists/` as-is. The deploy pipeline never has access to the signing key; it only mirrors what was already signed and committed.

### Push-based ingest (legacy path)

A deb-producing repository can still trigger the `receive-deb` workflow directly via `repository_dispatch`, which downloads the `.deb` artifact into `pool/main/` and runs the same `update-repo.sh` / commit steps, then likewise dispatches `pages.yml` explicitly. This path is kept for compatibility but new source repos should prefer the pull-based model above.

## GPG Signing

`pubkey.gpg` contains the public key used to sign repository metadata. Pis import this key to verify package authenticity.

## Directory Structure

```
pool/                        Binary .deb packages (shared across suites)
pubkey.gpg                   Repository signing public key
update-repo.sh               Regenerates dists/<suite>/ metadata from pool/
dists/                       Per-suite signed repository metadata (generated and committed)
tools/
  build_site.py              Builds the human-facing index under site/
  package_sources.toml       Package name -> source GitHub repo map
  pull_debs.py               Pulls new debs from source repos' GitHub Releases
  test_build_site.py         Unit and integration tests for build_site.py
  test_pull_debs.py          Unit tests for pull_debs.py
.github/workflows/
  pull-debs.yml              Pulls new debs on a schedule, runs update-repo.sh, commits, dispatches pages.yml
  receive-deb.yml            Legacy push-based deb ingest (repository_dispatch), dispatches pages.yml
  pages.yml                  Builds and deploys the GitHub Pages site
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
