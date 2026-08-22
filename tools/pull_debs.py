"""Pull new .deb packages from source repos' rolling `debs` releases.

Secretless pull-based ingest: each source repo listed in
tools/package_sources.toml publishes every green `main` build's `.deb` as an
asset on a rolling GitHub Release tagged `debs` in its own repo (created with
the repo's own GITHUB_TOKEN; pre-release; assets accumulate). This script
pulls -- it never needs write access to, or a token for, the source repos.
Public release assets download anonymously; GITHUB_TOKEN (if set) is only
used to raise the GitHub API rate limit for the release-lookup calls.

Consumed by `.github/workflows/pull-debs.yml`. Invoke as:

    uv run --python 3.12 python tools/pull_debs.py [--repo-root .] [--dry-run]

The script has no third-party dependencies; everything is stdlib.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from typing import Callable

LOG = logging.getLogger("pull_debs")

API_BASE = "https://api.github.com"
USER_AGENT = "fpgas-online-apt"

# package_arch-agnostic-version_arch.deb, e.g. fpgas-online-tt_0.1.0.post3_all.deb
ASSET_NAME_RE = re.compile(
    r"^[a-z0-9.+-]+_[A-Za-z0-9.+~:-]+_(all|amd64|arm64|armhf)\.deb$"
)


def load_package_sources(path: pathlib.Path) -> dict[str, str]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data.get("packages", {})


def is_valid_asset_name(name: str) -> bool:
    """Reject path traversal and anything not matching the expected
    `<package>_<version>_<arch>.deb` shape."""
    if "/" in name or "\\" in name:
        return False
    return bool(ASSET_NAME_RE.match(name))


def fetch_json(url: str, token: str | None) -> dict | None:
    """Default GitHub API fetcher. Returns the parsed JSON body, or None if
    the API returned 404 (e.g. no rolling 'debs' release exists yet)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download(url: str, dest: pathlib.Path) -> int:
    """Download `url` (a public release asset URL) to `dest`, streaming to a
    temp file in the same directory and renaming atomically on success.
    Returns the number of bytes written. No auth is sent -- release assets
    on public repos download anonymously."""
    headers = {"User-Agent": USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".part"
    )
    tmp_path = pathlib.Path(tmp_name)
    size = 0
    try:
        with os.fdopen(fd, "wb") as tmp_f:
            with urllib.request.urlopen(req, timeout=120) as resp:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    tmp_f.write(chunk)
                    size += len(chunk)
        tmp_path.rename(dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return size


def pull_all(
    repo_root: pathlib.Path,
    dry_run: bool = False,
    fetch_json_fn: Callable[[str, str | None], dict | None] = fetch_json,
    download_fn: Callable[[str, pathlib.Path], int] = download,
    token: str | None = None,
) -> int:
    """Pull every new .deb asset for every package in
    tools/package_sources.toml into pool/main/. Always prints a final
    'NEW: n' line. Returns 0 on success (including the "no release yet"
    case), non-zero if any HTTP/network error or size-mismatch occurred."""
    sources_path = repo_root / "tools" / "package_sources.toml"
    sources = load_package_sources(sources_path)

    pool_dir = repo_root / "pool" / "main"
    pool_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in pool_dir.glob("*.deb")}

    new_files: list[str] = []
    had_error = False

    for package, repo in sources.items():
        url = f"{API_BASE}/repos/{repo}/releases/tags/debs"
        try:
            release = fetch_json_fn(url, token)
        except Exception:
            LOG.exception("failed to fetch 'debs' release for %s (%s)", package, repo)
            had_error = True
            continue

        if release is None:
            LOG.info("no rolling 'debs' release yet for %s (%s); skipping", package, repo)
            continue

        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if not name.endswith(".deb") or not name.startswith(f"{package}_"):
                continue  # not one of ours
            if not is_valid_asset_name(name):
                LOG.warning("rejecting asset with unexpected name: %s", name)
                continue
            if name in existing:
                continue

            dest = pool_dir / name
            if dry_run:
                LOG.info("[dry-run] would download %s (from %s)", name, repo)
                new_files.append(name)
                continue

            download_url = asset.get("browser_download_url")
            expected_size = asset.get("size")
            LOG.info("downloading %s from %s", name, repo)
            try:
                actual_size = download_fn(download_url, dest)
            except Exception:
                LOG.exception("failed to download %s", name)
                had_error = True
                continue

            if expected_size is not None and actual_size != expected_size:
                LOG.error(
                    "size mismatch for %s: expected %s bytes, got %s; discarding",
                    name, expected_size, actual_size,
                )
                dest.unlink(missing_ok=True)
                had_error = True
                continue

            existing.add(name)
            new_files.append(name)

    print(f"NEW: {len(new_files)}")
    return 1 if had_error else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull new .deb packages from source repos' rolling 'debs' releases."
    )
    parser.add_argument("--repo-root", default=".", type=pathlib.Path)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be fetched; download nothing."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    token = os.environ.get("GITHUB_TOKEN")
    return pull_all(repo_root=args.repo_root.resolve(), dry_run=args.dry_run, token=token)


if __name__ == "__main__":
    sys.exit(main())
