# apt

APT package repository for the fpgas.online FPGA-as-a-Service platform, served via GitHub Pages at <https://fpgas-online.github.io/apt>.

## Overview

This repository hosts Debian packages used by fpgas.online Raspberry Pi nodes. Packages are added automatically when deb-producing repositories (such as `fpgas.online-setup-pi`) trigger the `receive-deb` GitHub Actions workflow, which adds the `.deb` to the pool and regenerates repository metadata. The landing page at <https://fpgas-online.github.io/apt> lists the hosted packages and links to their source repositories, and every subdirectory has a browsable index.

## Hosted Packages

- **fpgas-online-setup-pi** -- Pi node configuration, services, and environment setup.
- **fpgas-online-cam** -- Camera streaming tools for FPGA board video feeds.

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

1. A deb-producing repository builds a `.deb` and triggers the `receive-deb` workflow in this repository via `repository_dispatch`.
2. The workflow downloads the `.deb` artifact and places it in `pool/main/`.
3. `update-repo.sh` regenerates the `dists/` metadata (Packages, Release files) for both the `bookworm` and `trixie` suites from the shared pool, and signs each suite with the repository GPG key.
4. The `pages.yml` workflow — triggered by the `receive-deb` commit to `main` — rebuilds the human-facing index (`tools/build_site.py`) and deploys the site to GitHub Pages via `actions/deploy-pages`. The deploy pipeline never has access to the signing key; it only mirrors what was already signed and committed.

## GPG Signing

`pubkey.gpg` contains the public key used to sign repository metadata. Pis import this key to verify package authenticity.

## Directory Structure

```
pool/                        Binary .deb packages (shared across suites)
pubkey.gpg                   Repository signing public key
update-repo.sh               Regenerates dists/<suite>/ metadata from pool/
dists/                       Per-suite signed repository metadata (generated)
tools/
  build_site.py              Builds the human-facing index under site/
  package_sources.toml       Package name -> source GitHub repo map
  test_build_site.py         Unit and integration tests for build_site.py
.github/workflows/
  receive-deb.yml            Ingests new debs, runs update-repo.sh, commits
  pages.yml                  Builds and deploys the GitHub Pages site
  lint.yml                   CI linting
```

## Related Repositories

- [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) -- Source for the `fpgas-online-cam` package
- [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) -- Source for the `fpgas-online-setup-pi` package
- [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) -- Ansible playbooks that configure Pis to use this repo

## License

See [LICENSE](LICENSE).
