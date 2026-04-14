"""Tests for tools/build_site.py.

Run with:  uv run python -m unittest tools.test_build_site
or:        cd tools && uv run python -m unittest test_build_site
"""

import io
import os
import pathlib
import sys
import tempfile
import unittest

# Allow running from repo root or from tools/.
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build_site  # noqa: E402


class ParsePackagesFileTests(unittest.TestCase):
    def test_parses_single_block_into_dict(self):
        text = (
            "Package: fpgas-online-cam\n"
            "Version: 0.1.0\n"
            "Architecture: arm64\n"
            "Filename: pool/main/fpgas-online-cam_0.1.0_arm64.deb\n"
            "Size: 24832\n"
            "Description: Camera capture daemon\n"
        )
        blocks = build_site.parse_packages_file(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["Package"], "fpgas-online-cam")
        self.assertEqual(blocks[0]["Version"], "0.1.0")
        self.assertEqual(blocks[0]["Architecture"], "arm64")
        self.assertEqual(blocks[0]["Size"], "24832")
        self.assertEqual(blocks[0]["Description"], "Camera capture daemon")

    def test_parses_multiple_blocks_separated_by_blank_lines(self):
        text = (
            "Package: foo\n"
            "Version: 1.0\n"
            "Architecture: all\n"
            "\n"
            "Package: bar\n"
            "Version: 2.0\n"
            "Architecture: arm64\n"
        )
        blocks = build_site.parse_packages_file(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["Package"], "foo")
        self.assertEqual(blocks[1]["Package"], "bar")

    def test_handles_multiline_description_continuation(self):
        text = (
            "Package: foo\n"
            "Version: 1.0\n"
            "Description: short summary\n"
            " longer description line 1\n"
            " longer description line 2\n"
        )
        blocks = build_site.parse_packages_file(text)
        self.assertEqual(len(blocks), 1)
        self.assertIn("short summary", blocks[0]["Description"])

    def test_ignores_empty_input(self):
        self.assertEqual(build_site.parse_packages_file(""), [])
        self.assertEqual(build_site.parse_packages_file("\n\n"), [])


class SuiteArchFromPathTests(unittest.TestCase):
    def test_extracts_from_standard_packages_path(self):
        p = pathlib.Path("site/dists/bookworm/main/binary-arm64/Packages")
        suite, arch = build_site.suite_arch_from_path(p)
        self.assertEqual(suite, "bookworm")
        self.assertEqual(arch, "arm64")

    def test_extracts_trixie_armhf(self):
        p = pathlib.Path("/abs/path/dists/trixie/main/binary-armhf/Packages")
        suite, arch = build_site.suite_arch_from_path(p)
        self.assertEqual(suite, "trixie")
        self.assertEqual(arch, "armhf")


class GroupPackageCardsTests(unittest.TestCase):
    def test_collapses_same_package_version_across_suites(self):
        records = [
            {"Package": "foo", "Version": "1.0", "Architecture": "arm64",
             "Description": "Foo daemon", "_suite": "bookworm"},
            {"Package": "foo", "Version": "1.0", "Architecture": "arm64",
             "Description": "Foo daemon", "_suite": "trixie"},
        ]
        cards = build_site.group_package_cards(records)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["package"], "foo")
        self.assertEqual(card["version"], "1.0")
        self.assertEqual(sorted(card["suites"]), ["bookworm", "trixie"])
        self.assertEqual(sorted(card["arches"]), ["arm64"])

    def test_splits_cards_by_version(self):
        records = [
            {"Package": "foo", "Version": "1.0", "Architecture": "arm64",
             "Description": "Foo", "_suite": "bookworm"},
            {"Package": "foo", "Version": "1.1", "Architecture": "arm64",
             "Description": "Foo", "_suite": "trixie"},
        ]
        cards = build_site.group_package_cards(records)
        self.assertEqual(len(cards), 2)
        versions = sorted(c["version"] for c in cards)
        self.assertEqual(versions, ["1.0", "1.1"])

    def test_collects_multiple_arches(self):
        records = [
            {"Package": "foo", "Version": "1.0", "Architecture": "arm64",
             "Description": "Foo", "_suite": "bookworm"},
            {"Package": "foo", "Version": "1.0", "Architecture": "armhf",
             "Description": "Foo", "_suite": "bookworm"},
        ]
        cards = build_site.group_package_cards(records)
        self.assertEqual(len(cards), 1)
        self.assertEqual(sorted(cards[0]["arches"]), ["arm64", "armhf"])


class FormatSizeTests(unittest.TestCase):
    def test_bytes_under_one_kib(self):
        self.assertEqual(build_site.format_size(0), "0 B")
        self.assertEqual(build_site.format_size(512), "512 B")

    def test_kibibytes(self):
        self.assertEqual(build_site.format_size(1024), "1.0 KiB")
        self.assertEqual(build_site.format_size(1536), "1.5 KiB")

    def test_mebibytes(self):
        self.assertEqual(build_site.format_size(1024 * 1024), "1.0 MiB")
        self.assertEqual(build_site.format_size(int(2.5 * 1024 * 1024)), "2.5 MiB")


class SourceUrlTests(unittest.TestCase):
    def test_known_package_resolves(self):
        sources = {"fpgas-online-cam": "fpgas-online/fpgas.online-cam"}
        self.assertEqual(
            build_site.source_url_for("fpgas-online-cam", sources),
            "https://github.com/fpgas-online/fpgas.online-cam",
        )

    def test_unknown_package_returns_none(self):
        self.assertIsNone(build_site.source_url_for("mystery", {}))


class RenderDirectoryListingTests(unittest.TestCase):
    def test_lists_directories_before_files_and_includes_up_link(self):
        entries = [
            {"name": "foo.deb", "is_dir": False, "size": 2048, "mtime_iso": "2026-04-14T00:00:00Z"},
            {"name": "subdir", "is_dir": True, "size": None, "mtime_iso": "2026-04-14T00:00:00Z"},
        ]
        html = build_site.render_directory_listing(
            rel_path="pool/main", entries=entries, is_root=False,
        )
        self.assertIn("Index of /pool/main/", html)
        self.assertIn("subdir/", html)
        self.assertIn("foo.deb", html)
        self.assertIn("2.0 KiB", html)
        # Directory listed before the file in output order.
        self.assertLess(html.index("subdir/"), html.index("foo.deb"))
        # Up-one-level link present.
        self.assertIn('href="../"', html)
        # Repository-root link is relative: depth 2 -> "../../".
        self.assertIn('href="../../"', html)

    def test_deeper_directory_has_longer_relative_root_link(self):
        html = build_site.render_directory_listing(
            rel_path="dists/bookworm/main/binary-arm64",
            entries=[],
            is_root=False,
        )
        # depth 4 -> "../../../../"
        self.assertIn('href="../../../../"', html)


class RenderRootIndexTests(unittest.TestCase):
    def test_contains_all_required_sections(self):
        cards = [
            {
                "package": "fpgas-online-cam",
                "version": "0.1.0",
                "suites": ["bookworm", "trixie"],
                "arches": ["arm64", "armhf"],
                "description": "Camera capture daemon",
                "source_url": "https://github.com/fpgas-online/fpgas.online-cam",
            },
        ]
        tree = [
            {"name": "pubkey.gpg", "is_dir": False, "rel_path": "pubkey.gpg",
             "size": 1656, "children": []},
        ]
        html = build_site.render_root_index(cards=cards, tree=tree)
        # Header and branding.
        self.assertIn("fpgas.online APT repository", html)
        self.assertIn('src="intro.png"', html)
        # External links.
        self.assertIn("https://fpgas.online", html)
        self.assertIn("github.com/fpgas-online/apt", html)
        # Setup block uses lsb_release.
        self.assertIn("$(lsb_release -cs)", html)
        self.assertIn("/etc/apt/sources.list.d/fpgas-online.list", html)
        # Package card.
        self.assertIn("fpgas-online-cam", html)
        self.assertIn("0.1.0", html)
        self.assertIn("bookworm", html)
        self.assertIn("trixie", html)
        self.assertIn("github.com/fpgas-online/fpgas.online-cam", html)
        # Forbidden phrases (per user note during brainstorming).
        self.assertNotIn("GitHub Pages", html)
        self.assertNotIn("apt@fpgas.online", html)

    def test_no_packages_placeholder_when_cards_empty(self):
        html = build_site.render_root_index(cards=[], tree=[])
        self.assertIn("No packages published yet", html)

    def test_escapes_package_description_html(self):
        cards = [
            {
                "package": "foo",
                "version": "1.0",
                "suites": ["bookworm"],
                "arches": ["arm64"],
                "description": "<script>alert(1)</script>",
                "source_url": None,
            },
        ]
        html = build_site.render_root_index(cards=cards, tree=[])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class BuildSiteEndToEndTests(unittest.TestCase):
    """Full build against a synthetic repo root."""

    def _make_repo(self, root: pathlib.Path) -> None:
        (root / "pool" / "main").mkdir(parents=True)
        (root / "pool" / "main" / ".gitkeep").write_text("")
        (root / "pool" / "main" / "fpgas-online-cam_0.1.0_arm64.deb").write_bytes(b"\x00" * 2048)
        (root / "pubkey.gpg").write_bytes(b"-----FAKE PUBKEY-----\n")

        for suite in ("bookworm", "trixie"):
            pkgdir = root / "dists" / suite / "main" / "binary-arm64"
            pkgdir.mkdir(parents=True)
            packages = (
                "Package: fpgas-online-cam\n"
                "Version: 0.1.0\n"
                "Architecture: arm64\n"
                "Filename: pool/main/fpgas-online-cam_0.1.0_arm64.deb\n"
                "Size: 2048\n"
                "Description: Camera capture daemon\n"
            )
            (pkgdir / "Packages").write_text(packages)
            (pkgdir / "Packages.gz").write_bytes(b"\x1f\x8b")  # fake gzip header
            (root / "dists" / suite / "Release").write_text("Codename: " + suite + "\n")

    def test_build_site_produces_expected_layout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td) / "repo"
            repo.mkdir()
            self._make_repo(repo)
            # Write a minimal package_sources.toml next to the build script's expected location.
            tools_dir = repo / "tools"
            tools_dir.mkdir()
            (tools_dir / "package_sources.toml").write_text(
                '[packages]\n'
                '"fpgas-online-cam" = "fpgas-online/fpgas.online-cam"\n'
            )

            out = pathlib.Path(td) / "site"

            # Use an injectable image fetcher so the test does not hit the network.
            fake_png = b"\x89PNG\r\n\x1a\nfake"
            build_site.build_site(
                repo_root=repo,
                out_dir=out,
                fetch_intro=lambda: fake_png,
            )

            self.assertTrue((out / "index.html").exists())
            self.assertTrue((out / "intro.png").exists())
            self.assertEqual((out / "intro.png").read_bytes(), fake_png)
            self.assertTrue((out / "pubkey.gpg").exists())
            self.assertTrue((out / "pool" / "main" / "fpgas-online-cam_0.1.0_arm64.deb").exists())
            self.assertTrue((out / "dists" / "bookworm" / "main" / "binary-arm64" / "Packages").exists())
            self.assertTrue((out / "dists" / "trixie" / "main" / "binary-arm64" / "Packages").exists())
            # .gitkeep should not be mirrored.
            self.assertFalse((out / "pool" / "main" / ".gitkeep").exists())

            # Per-directory index.html must exist for every published subdirectory.
            for d in [
                out / "pool",
                out / "pool" / "main",
                out / "dists",
                out / "dists" / "bookworm",
                out / "dists" / "bookworm" / "main",
                out / "dists" / "bookworm" / "main" / "binary-arm64",
                out / "dists" / "trixie",
                out / "dists" / "trixie" / "main",
                out / "dists" / "trixie" / "main" / "binary-arm64",
            ]:
                self.assertTrue((d / "index.html").exists(), f"missing index.html in {d}")

            # Root index.html has the package card and both suites.
            root_html = (out / "index.html").read_text()
            self.assertIn("fpgas-online-cam", root_html)
            self.assertIn("0.1.0", root_html)
            self.assertIn("bookworm", root_html)
            self.assertIn("trixie", root_html)
            self.assertIn('href="pool/main/fpgas-online-cam_0.1.0_arm64.deb"', root_html)


if __name__ == "__main__":
    unittest.main()
