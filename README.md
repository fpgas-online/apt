# apt

APT package repository for the fpgas.online FPGA-as-a-Service platform, served via GitHub Pages.

## Overview

This repository hosts Debian packages used by fpgas.online Raspberry Pi nodes. Packages are added automatically when deb-producing repositories (such as `fpgas.online-setup-pi`) trigger the `receive-deb` GitHub Actions workflow, which adds the `.deb` to the pool and regenerates repository metadata.

## Hosted Packages

- **fpgas-online-setup-pi** -- Pi node configuration, services, and environment setup.
- **fpgas-online-cam** -- Camera streaming tools for FPGA board video feeds.

## Adding the Repository on a Pi

Import the signing key and add the repository:

```bash
curl -fsSL https://fpgas-online.github.io/apt/pubkey.gpg \
  | gpg --dearmor -o /usr/share/keyrings/fpgas-online.gpg

echo "deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] https://fpgas-online.github.io/apt bookworm main" \
  > /etc/apt/sources.list.d/fpgas-online.list

apt update
```

Then install packages:

```bash
apt install fpgas-online-setup-pi
```

## How It Works

1. A deb-producing repository builds a `.deb` and triggers the `receive-deb` workflow in this repository via `repository_dispatch`.
2. The workflow downloads the `.deb` artifact and places it in `pool/`.
3. `update-repo.sh` regenerates the `dists/` metadata (Packages, Release files) and signs with the repository GPG key.
4. GitHub Pages serves the repository at `https://fpgas-online.github.io/apt`.

## GPG Signing

`pubkey.gpg` contains the public key used to sign repository metadata. Pis import this key to verify package authenticity.

## Directory Structure

```
pool/                        Binary .deb packages
pubkey.gpg                   Repository signing public key
update-repo.sh               Regenerates dists/ metadata from pool/
dists/                       Generated repository metadata (not committed)
.github/workflows/
  receive-deb.yml            Workflow triggered by deb-producing repos
  lint.yml                   CI linting
```

## Related Repositories

- [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) -- Source for the `fpgas-online-setup-pi` package
- [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) -- Ansible playbooks that configure Pis to use this repo

## License

See [LICENSE](LICENSE).
