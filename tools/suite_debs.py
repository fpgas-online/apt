"""Pick the packages of pool/main/ that one suite publishes.

Most packages here are built once and install on every suite (scripts,
configuration, FPGA bitstreams): their version names no suite, and every
suite publishes them. A package built separately for each suite carries the
suite in its version, the way mithro/apt-repo-action's deb-version does it:
`0.0.post776~deb12` is bookworm's build, `0.0.post776~deb13` trixie's. Such a
package is published by its own suite only. Otherwise every suite would
offer both builds, and apt would install the `~deb13` one everywhere: it is
the higher version.

The suite is read from the package's own `Version` field, not from its file
name: GitHub stores a release asset's `~` as `.`, so the pool's file is
`..._0.0.post776.deb12_arm64.deb`.

Consumed by `.github/workflows/publish.yml`. Invoke as:

    uv run --python 3.12 python tools/suite_debs.py --suite bookworm --out dist/bookworm

The script has no third-party dependencies; it runs `dpkg-deb`.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Callable

# The Debian release number deb-version puts in a suite's versions (sid's
# carry none). mithro/apt-repo-action scripts/deb-version.py DEBIAN_RELEASE.
DEBIAN_RELEASE = {"bookworm": 12, "trixie": 13, "forky": 14}

_SUITE_RE = re.compile(r"~deb(\d+)(?=~|$)")


class SuiteError(Exception):
    pass


def release_of(version: str) -> int | None:
    """The Debian release a version was built for (`~deb12` -> 12), or None
    for a version that names none. `+deb13u1`, Debian's own stable-update
    mark, is not ours and names nothing here."""
    match = _SUITE_RE.search(version)
    return int(match.group(1)) if match else None


def deb_version(deb: pathlib.Path) -> str:
    run = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Version"], capture_output=True, text=True
    )
    version = run.stdout.strip()
    if run.returncode or not version:
        raise SuiteError(f"{deb.name}: dpkg-deb could not read its Version: {run.stderr.strip()}")
    return version


def select(
    pool: pathlib.Path,
    suite: str,
    version_fn: Callable[[pathlib.Path], str] = deb_version,
) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """(the debs `suite` publishes, the debs it leaves to another suite)."""
    if suite not in DEBIAN_RELEASE:
        raise SuiteError(f"{suite}: not one of {', '.join(DEBIAN_RELEASE)}")
    taken, left = [], []
    for deb in sorted(pool.glob("*.deb")):
        release = release_of(version_fn(deb))
        (taken if release in (None, DEBIAN_RELEASE[suite]) else left).append(deb)
    return taken, left


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pool", default="pool/main", type=pathlib.Path)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        taken, left = select(args.pool, args.suite)
    except SuiteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not taken:
        print(f"error: no package in {args.pool} is for {args.suite}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    for deb in taken:
        shutil.copyfile(deb, args.out / deb.name)
    print(f"{args.suite}: {len(taken)} packages; {len(left)} left to the suite they were built for")
    for deb in left:
        print(f"  not {args.suite}: {deb.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
