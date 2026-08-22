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


def _release(assets: list[dict]) -> dict:
    return {"tag_name": "debs", "assets": assets}


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


class PullAllTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = pathlib.Path(self._tmp.name)
        (self.repo_root / "pool" / "main").mkdir(parents=True)

    def pool_dir(self) -> pathlib.Path:
        return self.repo_root / "pool" / "main"

    def run_pull(self, fetch_json_fn, download_fn=None, dry_run=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pull_debs.pull_all(
                repo_root=self.repo_root,
                dry_run=dry_run,
                fetch_json_fn=fetch_json_fn,
                download_fn=download_fn or self._unexpected_download,
                token=None,
            )
        return rc, buf.getvalue()

    def _unexpected_download(self, url, dest):
        raise AssertionError(f"download() should not have been called for {url}")

    def test_404_release_is_skipped_without_error(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def fetch_json_fn(url, token):
            return None  # 404: no rolling release yet

        rc, out = self.run_pull(fetch_json_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)
        self.assertEqual(list(self.pool_dir().glob("*.deb")), [])

    def test_new_asset_is_downloaded(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"
        payload = b"debcontents"

        def fetch_json_fn(url, token):
            return _release([_asset(name, len(payload))])

        def download_fn(url, dest):
            dest.write_bytes(payload)
            return len(payload)

        rc, out = self.run_pull(fetch_json_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertEqual((self.pool_dir() / name).read_bytes(), payload)

    def test_existing_asset_is_skipped(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"
        (self.pool_dir() / name).write_bytes(b"already-here")

        def fetch_json_fn(url, token):
            return _release([_asset(name, 999)])

        rc, out = self.run_pull(fetch_json_fn)  # unexpected_download would fail
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)
        self.assertEqual((self.pool_dir() / name).read_bytes(), b"already-here")

    def test_size_mismatch_removes_file_and_fails(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"

        def fetch_json_fn(url, token):
            return _release([_asset(name, 12345)])

        def download_fn(url, dest):
            dest.write_bytes(b"short")
            return 5  # doesn't match expected size 12345

        rc, out = self.run_pull(fetch_json_fn, download_fn)
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.pool_dir() / name).exists())

    def test_bad_asset_name_is_rejected(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def fetch_json_fn(url, token):
            return _release(
                [
                    _asset("foo_../../etc/passwd_all.deb", 10),
                    _asset("foo_1.0_sparc.deb", 10),
                ]
            )

        rc, out = self.run_pull(fetch_json_fn)  # unexpected_download would fail
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)
        self.assertEqual(list(self.pool_dir().glob("*.deb")), [])

    def test_dry_run_downloads_nothing(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})
        name = "foo_1.0_all.deb"

        def fetch_json_fn(url, token):
            return _release([_asset(name, 10)])

        rc, out = self.run_pull(fetch_json_fn, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.pool_dir().glob("*.deb")), [])

    def test_asset_belonging_to_other_package_is_ignored(self):
        _write_sources_toml(self.repo_root, {"foo": "org/foo"})

        def fetch_json_fn(url, token):
            return _release([_asset("bar_1.0_all.deb", 10)])

        rc, out = self.run_pull(fetch_json_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 0", out)

    def test_multiple_packages_each_queried(self):
        _write_sources_toml(
            self.repo_root, {"foo": "org/foo", "bar": "org/bar"}
        )
        seen_urls = []

        def fetch_json_fn(url, token):
            seen_urls.append(url)
            if "org/foo" in url:
                return _release([_asset("foo_1.0_all.deb", 4)])
            return None

        def download_fn(url, dest):
            dest.write_bytes(b"data")
            return 4

        rc, out = self.run_pull(fetch_json_fn, download_fn)
        self.assertEqual(rc, 0)
        self.assertIn("NEW: 1", out)
        self.assertEqual(len(seen_urls), 2)
        self.assertTrue(any("org/foo" in u and u.endswith("/releases/tags/debs") for u in seen_urls))


if __name__ == "__main__":
    unittest.main()
