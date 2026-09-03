"""Tests for tools/pull_debs.py.

Run with:  uv run --python 3.12 python -m unittest tools.test_pull_debs
or:        cd tools && uv run --python 3.12 python -m unittest test_pull_debs
"""

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

# Allow running from repo root or from tools/.
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pull_debs  # noqa: E402


def _write_sources_toml(repo_root: pathlib.Path, packages: dict[str, str]) -> None:
    tools_dir = repo_root / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    lines = ["[packages]"]
    for name, repo in packages.items():
        lines.append(f'"{name}" = "{repo}"')
    (tools_dir / "package_sources.toml").write_text("\n".join(lines) + "\n")


def _release(assets: list[dict], tag: str = "v0.0", draft: bool = False) -> dict:
    return {"tag_name": tag, "draft": draft, "prerelease": True, "assets": assets}


def _asset(name: str, size: int, url: str | None = None) -> dict:
    return {
        "name": name,
        "size": size,
        "browser_download_url": url or f"https://example.invalid/{name}",
    }


class IsValidAssetNameTests(unittest.TestCase):
    def test_accepts_well_formed_name(self):
        self.assertTrue(
            pull_debs.is_valid_asset_name("fpgas-online-tt_0.1.0.post3_all.deb")
        )

    def test_rejects_path_traversal(self):
        self.assertFalse(pull_debs.is_valid_asset_name("../evil_1.0_all.deb"))
        self.assertFalse(pull_debs.is_valid_asset_name("foo/bar_1.0_all.deb"))

    def test_rejects_bad_arch(self):
        self.assertFalse(pull_debs.is_valid_asset_name("foo_1.0_sparc.deb"))

    def test_rejects_missing_underscore_segments(self):
        self.assertFalse(pull_debs.is_valid_asset_name("foo_all.deb"))
        self.assertFalse(pull_debs.is_valid_asset_name("foo.deb"))


class ParseLinkHeaderTests(unittest.TestCase):
    def test_extracts_next_url(self):
        header = (
            '<https://api.github.com/repos/org/foo/releases?per_page=100&page=2>; rel="next", '
            '<https://api.github.com/repos/org/foo/releases?per_page=100&page=3>; rel="last"'
        )
        self.assertEqual(
            pull_debs._parse_link_header(header),
            "https://api.github.com/repos/org/foo/releases?per_page=100&page=2",
        )

    def test_returns_none_without_next(self):
        header = '<https://api.github.com/repos/org/foo/releases?page=3>; rel="last"'
        self.assertIsNone(pull_debs._parse_link_header(header))

    def test_returns_none_for_empty_header(self):
        self.assertIsNone(pull_debs._parse_link_header(None))
        self.assertIsNone(pull_debs._parse_link_header(""))


class ListReleasesTests(unittest.TestCase):
    def test_follows_pagination_and_concatenates(self):
        page1_url = "https://api.github.com/repos/org/foo/releases?per_page=100"
        page2_url = "https://api.github.com/repos/org/foo/releases?per_page=100&page=2"
        pages = {
            page1_url: ([_release([], tag="v0.1")], page2_url),
            page2_url: ([_release([], tag="v0.0")], None),
        }

        def fetch_page_fn(url, token):
            return pages[url]

        releases = pull_debs.list_releases("org/foo", None, fetch_page_fn=fetch_page_fn)
        self.assertEqual([r["tag_name"] for r in releases], ["v0.1", "v0.0"])

    def test_404_returns_empty_list(self):
        def fetch_page_fn(url, token):
            return None, None

        self.assertEqual(
            pull_debs.list_releases("org/foo", None, fetch_page_fn=fetch_page_fn), []
        )

    def test_empty_release_list_returns_empty(self):
        def fetch_page_fn(url, token):
            return [], None

        self.assertEqual(
            pull_debs.list_releases("org/foo", None, fetch_page_fn=fetch_page_fn), []
        )


class _FakeResponse:
    def __init__(self, data: bytes, headers: dict | None = None):
        self._data = data
        self._pos = 0
        self.headers = headers or {}

    def read(self, n=-1):
        if n is None or n < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = pathlib.Path(self._tmp.name) / "pkg_1.0_all.deb"

    def _leftover_files(self):
        return [p for p in self.dest.parent.iterdir() if p != self.dest]

    def test_success_renames_temp_file_into_place(self):
        data = b"hello world"
        with unittest.mock.patch.object(
            pull_debs.urllib.request, "urlopen", return_value=_FakeResponse(data)
        ):
            size = pull_debs.download("https://example.invalid/x", self.dest, len(data))
        self.assertEqual(size, len(data))
        self.assertEqual(self.dest.read_bytes(), data)
        self.assertEqual(self._leftover_files(), [])

    def test_size_mismatch_raises_and_leaves_no_file_at_dest(self):
        data = b"short"
        with unittest.mock.patch.object(
            pull_debs.urllib.request, "urlopen", return_value=_FakeResponse(data)
        ):
            with self.assertRaises(pull_debs.SizeMismatchError):
                pull_debs.download("https://example.invalid/x", self.dest, 99999)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self._leftover_files(), [])


class PullAllTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = pathlib.Path(self._tmp.name)
        (self.repo_root / "pool" / "main").mkdir(parents=True)

    def pool_dir(self) -> pathlib.Path:
        return self.repo_root / "pool" / "main"

    def run_pull(self, list_releases_fn, download_fn=None, dry_run=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pull_debs.pull_all(
                repo_root=self.repo_root,
                dry_run=dry_run,
                list_releases_fn=list_releases_fn,
                download_fn=download_fn or self._unexpected_download,
                token=None,
            )
        return rc, buf.getvalue()

    def _unexpected_download(self, url, dest, expected_size):
        raise AssertionError(f"download() should not have been called for {url}")

    def test_no_releases_is_an_error(self):
        # A registered repo that publishes no releases at all -- the state
        # mithro/rp1-jtag was in: it publishes only to its own Pages apt
        # archive, so nothing here would ever have been pulled.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return []  # 404 or genuinely empty

        with self.assertLogs(pull_debs.LOG, level="ERROR") as logs:
            rc, out = self.run_pull(list_releases_fn)
        self.assertEqual(rc, 2)
        self.assertIn("NEW: 0", out)
        self.assertEqual(list(self.pool_dir().glob("*.deb")), [])
        self.assertIn("provides nothing", "\n".join(logs.output))
        self.assertIn("no releases at all", "\n".join(logs.output))

    def test_releases_without_a_matching_asset_is_an_error(self):
        # Releases exist, but none carries an asset for this package name --
        # typically a typo in package_sources.toml, so the message differs.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [_release([_asset("otherpkg_1.0_all.deb", 4)])]

        with self.assertLogs(pull_debs.LOG, level="ERROR") as logs:
            rc, out = self.run_pull(list_releases_fn)
        self.assertEqual(rc, 2)
        self.assertIn("NEW: 0", out)
        self.assertIn("provides nothing", "\n".join(logs.output))
        self.assertIn("1 release(s) exist", "\n".join(logs.output))

    def test_asset_already_in_pool_still_counts_as_provided(self):
        # Steady state: nothing new to pull, but the repo does still offer the
        # package. That must stay green, or every quiet run would go red.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        (self.pool_dir() / "foo_1.0_all.deb").write_bytes(b"old")

        def list_releases_fn(repo, token):
            return [_release([_asset("foo_1.0_all.deb", 3)])]

        rc, out = self.run_pull(list_releases_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)

    def test_only_badly_named_assets_counts_as_providing_nothing(self):
        # An asset that is rejected by the name check must not be credited as
        # provision, or a repo publishing only malformed names looks healthy.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [_release([_asset("foo_1.0_sparc.deb", 4)])]

        with self.assertLogs(pull_debs.LOG, level="ERROR") as logs:
            rc, _ = self.run_pull(list_releases_fn)
        self.assertEqual(rc, 2)
        self.assertIn("provides nothing", "\n".join(logs.output))

    def test_draft_only_release_counts_as_providing_nothing(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [_release([_asset("foo_1.0_all.deb", 4)], draft=True)]

        with self.assertLogs(pull_debs.LOG, level="ERROR") as logs:
            rc, _ = self.run_pull(list_releases_fn)
        self.assertEqual(rc, 2)
        self.assertIn("provides nothing", "\n".join(logs.output))

    def test_dead_source_still_reported_after_healthy_ones_are_pulled(self):
        # The failure is deferred to the end of the run: bar's deb must still
        # land in the pool even though foo provides nothing.
        _write_sources_toml(self.repo_root, {"foo": "org/foo", "bar": "org/bar"})

        def list_releases_fn(repo, token):
            if repo == "org/foo":
                return []
            return [_release([_asset("bar_1.0_all.deb", 4)])]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        with self.assertLogs(pull_debs.LOG, level="ERROR"):
            rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 2)
        self.assertIn("NEW: 1", out)
        self.assertTrue((self.pool_dir() / "bar_1.0_all.deb").exists())

    def test_new_asset_is_downloaded(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"
        payload = b"debcontents"

        def list_releases_fn(repo, token):
            return [_release([_asset(name, len(payload))])]

        def download_fn(url, dest, expected_size):
            self.assertEqual(expected_size, len(payload))
            dest.write_bytes(payload)
            return len(payload)

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertEqual((self.pool_dir() / name).read_bytes(), payload)

    def test_assets_across_multiple_releases_are_all_considered(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [
                _release([_asset("foo_0.2_all.deb", 4)], tag="v0.2"),
                _release([_asset("foo_0.1_all.deb", 4)], tag="v0.1"),
                _release([_asset("foo_0.0_all.deb", 4)], tag="v0.0"),
            ]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 3", out)
        for name in ("foo_0.2_all.deb", "foo_0.1_all.deb", "foo_0.0_all.deb"):
            self.assertTrue((self.pool_dir() / name).exists())

    def test_draft_release_is_skipped(self):
        # The published release keeps this repo "providing"; the point under
        # test is that the draft's asset is not among what gets downloaded.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [
                _release([_asset("foo_9.9_all.deb", 4)], tag="v9.9", draft=True),
                _release([_asset("foo_1.0_all.deb", 4)], tag="v1.0"),
            ]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertFalse((self.pool_dir() / "foo_9.9_all.deb").exists())
        self.assertTrue((self.pool_dir() / "foo_1.0_all.deb").exists())

    def test_existing_asset_is_skipped(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"
        (self.pool_dir() / name).write_bytes(b"already-here")

        def list_releases_fn(repo, token):
            return [_release([_asset(name, 999)])]

        rc, out = self.run_pull(list_releases_fn)  # unexpected_download would fail
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)
        self.assertEqual((self.pool_dir() / name).read_bytes(), b"already-here")

    def test_size_mismatch_removes_file_and_fails(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"

        def list_releases_fn(repo, token):
            return [_release([_asset(name, 12345)])]

        def download_fn(url, dest, expected_size):
            raise pull_debs.SizeMismatchError("simulated mismatch")

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.pool_dir() / name).exists())

    def test_bad_asset_name_is_rejected(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [
                _release(
                    [
                        _asset("foo_../../etc/passwd_all.deb", 10),
                        _asset("foo_1.0_sparc.deb", 10),
                        # A legitimate asset, so this exercises name rejection
                        # rather than the "provides nothing" check.
                        _asset("foo_1.0_all.deb", 4),
                    ]
                )
            ]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertEqual(
            [p.name for p in self.pool_dir().glob("*.deb")], ["foo_1.0_all.deb"]
        )

    def test_dry_run_downloads_nothing(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"

        def list_releases_fn(repo, token):
            return [_release([_asset(name, 10)])]

        rc, out = self.run_pull(list_releases_fn, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertEqual(list(self.pool_dir().glob("*.deb")), [])

    def test_asset_belonging_to_other_package_is_ignored(self):
        # foo must also be provided here, otherwise this would trip the
        # "provides nothing" check and stop testing what it means to test.
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def list_releases_fn(repo, token):
            return [
                _release([_asset("bar_1.0_all.deb", 10), _asset("foo_1.0_all.deb", 4)])
            ]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertTrue((self.pool_dir() / "foo_1.0_all.deb").exists())
        self.assertFalse((self.pool_dir() / "bar_1.0_all.deb").exists())

    def test_multiple_packages_each_queried(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo", "bar": "org/bar"})
        seen_repos = []

        def list_releases_fn(repo, token):
            seen_repos.append(repo)
            if repo == "org/foo":
                return [_release([_asset("foo_1.0_all.deb", 4)])]
            return [_release([_asset("bar_1.0_all.deb", 4)])]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 2", out)
        self.assertEqual(sorted(seen_repos), ["org/bar", "org/foo"])

    def test_one_package_failing_does_not_stop_the_others(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo", "bar": "org/bar"})

        def list_releases_fn(repo, token):
            if repo == "org/foo":
                raise RuntimeError("simulated network error")
            return [_release([_asset("bar_1.0_all.deb", 4)])]

        def download_fn(url, dest, expected_size):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(list_releases_fn, download_fn)
        self.assertNotEqual(rc, 0)  # overall run is still flagged red
        self.assertIn("NEW: 1", out)  # but bar's deb still landed
        self.assertTrue((self.pool_dir() / "bar_1.0_all.deb").exists())


if __name__ == "__main__":
    unittest.main()
