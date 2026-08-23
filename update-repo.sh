#!/bin/bash
set -euo pipefail

# Regenerate APT metadata (Packages/Release) for every supported suite and
# sign with the fpgas-online APT key.  Invoked from .github/workflows/receive-deb.yml
# after a deb is dropped into pool/main/.

SUITES=(bookworm trixie)
ARCHES=(armhf arm64)

for suite in "${SUITES[@]}"; do
  for arch in "${ARCHES[@]}"; do
    mkdir -p "dists/${suite}/main/binary-${arch}"
    apt-ftparchive packages pool/main \
      > "dists/${suite}/main/binary-${arch}/Packages"
    gzip -k -f "dists/${suite}/main/binary-${arch}/Packages"
  done

  mkdir -p "dists/${suite}"
  apt-ftparchive release \
    -o APT::FTPArchive::Release::Origin="fpgas-online" \
    -o APT::FTPArchive::Release::Label="fpgas.online" \
    -o APT::FTPArchive::Release::Suite="${suite}" \
    -o APT::FTPArchive::Release::Codename="${suite}" \
    -o APT::FTPArchive::Release::Architectures="armhf arm64" \
    -o APT::FTPArchive::Release::Components="main" \
    "dists/${suite}" > "dists/${suite}/Release"

  gpg --batch --yes --default-key apt@fpgas.online -abs \
    -o "dists/${suite}/Release.gpg" "dists/${suite}/Release"
  gpg --batch --yes --default-key apt@fpgas.online --clearsign \
    -o "dists/${suite}/InRelease" "dists/${suite}/Release"
done
