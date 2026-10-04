"""Tests for tools/suite_debs.py.

Run with:  uv run --python 3.12 python -m unittest tools.test_suite_debs
"""

import contextlib
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import suite_debs  # noqa: E402


class ReleaseOfTests(unittest.TestCase):
    def test_a_suite_suffix_names_its_release(self):
        self.assertEqual(suite_debs.release_of("0.0.post776~deb12"), 12)
        self.assertEqual(suite_debs.release_of("0.0.post776~deb13"), 13)
        self.assertEqual(suite_debs.release_of("0.0.post776~deb13~pr7"), 13)

    def test_a_version_without_one_names_no_release(self):
        for version in ("0.0.post795", "20261001+ge568a408e7bd", "1:6.12.47-1+rpt1"):
            self.assertIsNone(suite_debs.release_of(version), version)

    def test_debians_own_stable_update_mark_is_not_a_suite_suffix(self):
        self.assertIsNone(suite_debs.release_of("43.0.0-3+deb13u1"))

    def test_a_suffix_must_end_there(self):
        self.assertIsNone(suite_debs.release_of("1.0~deb12x"))


class SelectTests(unittest.TestCase):
    VERSIONS = {
        "verify_0.0.post795_all.deb": "0.0.post795",
        "utils_0.0.post776.deb12_armhf.deb": "0.0.post776~deb12",
        "utils_0.0.post776.deb13_armhf.deb": "0.0.post776~deb13",
        "modules-6.12.1+rpt-rpi-v8_0.0.post776.deb12_arm64.deb": "0.0.post776~deb12",
        "future_1.0.deb14_all.deb": "1.0~deb14",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pool = pathlib.Path(self._tmp.name)
        for name in self.VERSIONS:
            (self.pool / name).write_bytes(b"deb")

    def names(self, suite):
        taken, left = suite_debs.select(self.pool, suite, lambda deb: self.VERSIONS[deb.name])
        self.assertEqual(sorted(p.name for p in [*taken, *left]), sorted(self.VERSIONS))
        return {p.name for p in taken}

    def test_a_suite_publishes_its_own_builds_and_everything_built_once(self):
        self.assertEqual(
            self.names("bookworm"),
            {
                "verify_0.0.post795_all.deb",
                "utils_0.0.post776.deb12_armhf.deb",
                "modules-6.12.1+rpt-rpi-v8_0.0.post776.deb12_arm64.deb",
            },
        )
        self.assertEqual(self.names("trixie"), {"verify_0.0.post795_all.deb", "utils_0.0.post776.deb13_armhf.deb"})

    def test_the_file_name_does_not_decide(self):
        # GitHub stored the asset's ~ as a dot; only the Version field knows.
        taken, _ = suite_debs.select(self.pool, "trixie", lambda deb: "1.0~deb13")
        self.assertEqual(len(taken), len(self.VERSIONS))

    def test_an_unknown_suite_is_an_error(self):
        with self.assertRaises(suite_debs.SuiteError):
            suite_debs.select(self.pool, "sid")


@unittest.skipUnless(shutil.which("dpkg-deb"), "needs dpkg-deb")
class RealDebTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.pool = self.root / "pool"
        self.pool.mkdir()

    def build(self, package, version, filename):
        tree = self.root / f"tree-{filename}"
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN" / "control").write_text(
            f"Package: {package}\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: t <t@example.org>\nDescription: test\n"
        )
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-b", str(tree), str(self.pool / filename)],
            check=True,
            capture_output=True,
        )

    def test_main_copies_the_suites_packages(self):
        self.build("once", "0.0.post1", "once_0.0.post1_all.deb")
        self.build("per-suite", "0.0.post1~deb12", "per-suite_0.0.post1.deb12_all.deb")
        self.build("per-suite", "0.0.post1~deb13", "per-suite_0.0.post1.deb13_all.deb")
        for suite, own in (("bookworm", "deb12"), ("trixie", "deb13")):
            out = self.root / suite
            with contextlib.redirect_stdout(io.StringIO()):
                rc = suite_debs.main(["--pool", str(self.pool), "--suite", suite, "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertEqual(
                sorted(p.name for p in out.iterdir()),
                ["once_0.0.post1_all.deb", f"per-suite_0.0.post1.{own}_all.deb"],
            )

    def test_a_file_that_is_not_a_package_is_an_error_not_a_skip(self):
        (self.pool / "broken_1.0_all.deb").write_bytes(b"not a deb")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = suite_debs.main(["--pool", str(self.pool), "--suite", "bookworm", "--out", str(self.root / "o")])
        self.assertEqual(rc, 2)
        self.assertIn("broken_1.0_all.deb", err.getvalue())

    def test_a_suite_with_no_package_is_an_error(self):
        self.build("per-suite", "0.0.post1~deb12", "per-suite_0.0.post1.deb12_all.deb")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = suite_debs.main(["--pool", str(self.pool), "--suite", "trixie", "--out", str(self.root / "o")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
