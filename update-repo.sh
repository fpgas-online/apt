#!/bin/bash
set -euo pipefail

# Generate Packages files for each architecture
for arch in armhf arm64; do
  apt-ftparchive packages pool/main > dists/bookworm/main/binary-${arch}/Packages
  gzip -k -f dists/bookworm/main/binary-${arch}/Packages
done

# Generate Release file
apt-ftparchive release \
  -o APT::FTPArchive::Release::Origin="fpgas-online" \
  -o APT::FTPArchive::Release::Label="fpgas.online" \
  -o APT::FTPArchive::Release::Suite="bookworm" \
  -o APT::FTPArchive::Release::Codename="bookworm" \
  -o APT::FTPArchive::Release::Architectures="armhf arm64" \
  -o APT::FTPArchive::Release::Components="main" \
  dists/bookworm > dists/bookworm/Release

# Sign
gpg --default-key apt@fpgas.online -abs -o dists/bookworm/Release.gpg dists/bookworm/Release
gpg --default-key apt@fpgas.online --clearsign -o dists/bookworm/InRelease dists/bookworm/Release
