"""Pull new .deb packages from source repos' GitHub Releases.

Secretless pull-based ingest: each source repo listed in
tools/package_sources.toml publishes every green `main` build's `.deb` as an
asset on a GitHub Release in its own repo -- by convention, the release for
the current series tag (`v0.0`, `v0.1`, ...), since source repos' tag
rulesets only allow `vX.Y`-shaped tags. A repo may accumulate several such
releases over time as its series advances. This script pulls every
`<package>_*.deb` asset across *all* of a source repo's releases -- it never
needs write access to, or a token for, the source repos.

Public release assets download anonymously; GITHUB_TOKEN (if set) is only
used to raise the GitHub API rate limit for the release-listing calls.

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

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class SizeMismatchError(Exception):
    """Raised when a downloaded asset's byte count doesn't match the size
    reported by the GitHub API."""


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


def _parse_link_header(header: str | None) -> str | None:
    """Extract the rel="next" URL from a GitHub `Link` response header, or
    None if there isn't one."""
    if not header:
        return None
    match = _LINK_NEXT_RE.search(header)
    return match.group(1) if match else None


def fetch_page(url: str, token: str | None) -> tuple[list | dict | None, str | None]:
    """Default GitHub API page fetcher. Returns (parsed_json_body,
    next_page_url). Returns (None, None) if the API returned 404 (e.g. the
    repo has no releases at all -- some GitHub API versions 404 rather than
    returning an empty list)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            next_url = _parse_link_header(resp.headers.get("Link"))
            return body, next_url
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def list_releases(
    repo: str,
    token: str | None,
    fetch_page_fn: Callable[[str, str | None], tuple[list | dict | None, str | None]] = fetch_page,
) -> list[dict]:
    """Enumerate every release of `repo`, following `Link: rel="next"`
    pagination. A repo with no releases at all (empty list, or a 404 from
    some API versions) returns []. Drafts and pre-releases are both
    returned here; callers filter drafts (pre-releases are wanted -- the
    rolling per-series release is itself a pre-release)."""
    url = f"{API_BASE}/repos/{repo}/releases?per_page=100"
    releases: list[dict] = []
    while url:
        body, next_url = fetch_page_fn(url, token)
        if body is None:
            return []  # 404: no releases
        releases.extend(body)
        url = next_url
    return releases


def download(url: str, dest: pathlib.Path, expected_size: int | None = None) -> int:
    """Download `url` (a public release asset URL) to `dest`, streaming to a
    temp file in the same directory. If `expected_size` is given, the byte
    count is verified BEFORE the temp file is renamed into place; on
    mismatch the temp file is deleted and SizeMismatchError is raised, so a
    partial/corrupt file is never left at `dest`. Returns the number of
    bytes written. No auth is sent -- release assets on public repos
    download anonymously."""
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
        if expected_size is not None and size != expected_size:
            raise SizeMismatchError(
                f"size mismatch downloading {dest.name}: "
                f"expected {expected_size} bytes, got {size}"
            )
        tmp_path.rename(dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return size


def pull_all(
    repo_root: pathlib.Path,
    dry_run: bool = False,
    list_releases_fn: Callable[[str, str | None], list[dict]] = list_releases,
    download_fn: Callable[[str, pathlib.Path, int | None], int] = download,
    token: str | None = None,
) -> int:
    """Pull every new .deb asset -- across every release -- for every
    package in tools/package_sources.toml into pool/main/. Always prints a
    final 'NEW: n' line, counting the packages that succeeded even if
    others failed. Returns 0 on success (including the "no releases yet"
    case), 2 if any package's release-listing or asset download failed."""
    sources_path = repo_root / "tools" / "package_sources.toml"
    sources = load_package_sources(sources_path)

    pool_dir = repo_root / "pool" / "main"
    pool_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in pool_dir.glob("*.deb")}

    new_files: list[str] = []
    had_error = False

    for package, repo in sources.items():
        try:
            releases = list_releases_fn(repo, token)
        except Exception:
            LOG.exception("failed to list releases for %s (%s)", package, repo)
            had_error = True
            continue

        if not releases:
            LOG.info("no releases found for %s (%s); skipping", package, repo)
            continue

        for release in releases:
            if release.get("draft"):
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
                    existing.add(name)
                    new_files.append(name)
                    continue

                download_url = asset.get("browser_download_url")
                expected_size = asset.get("size")
                LOG.info("downloading %s from %s", name, repo)
                try:
                    download_fn(download_url, dest, expected_size)
                except Exception:
                    LOG.exception("failed to download %s", name)
                    had_error = True
                    continue

                existing.add(name)
                new_files.append(name)

    print(f"NEW: {len(new_files)}")
    return 2 if had_error else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull new .deb packages from source repos' GitHub Releases."
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
