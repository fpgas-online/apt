"""Build the human-facing HTML site for fpgas-online/apt.

Consumed by `.github/workflows/pages.yml`. Invoke as:

    uv run tools/build_site.py --repo-root . --out site

The script has no third-party dependencies; everything is stdlib.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import os
import pathlib
import shutil
import sys
import tomllib
import urllib.request
from typing import Callable, Iterable


INTRO_PNG_URL = (
    "https://raw.githubusercontent.com/fpgas-online/fpgas.online-site/"
    "main/pibfpgas/src/pibfpgas/static/intro.png"
)
FPGAS_ONLINE_URL = "https://fpgas.online"
APT_REPO_URL = "https://github.com/fpgas-online/apt"
LIVE_SITE_PATH = "/apt/"
PUBLISHABLE_TOP_LEVEL = ("pool", "dists", "pubkey.gpg")


# ---------------------------------------------------------------------------
# Packages file parsing
# ---------------------------------------------------------------------------


def parse_packages_file(text: str) -> list[dict[str, str]]:
    """Parse a Debian 'Packages' file into a list of field dicts.

    Handles multi-paragraph input (blank-line separated), continuation lines
    (lines beginning with a space belong to the previous field), and empty
    input.
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: str | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip():
            if current:
                blocks.append(current)
                current = {}
                last_key = None
            continue

        if raw_line.startswith((" ", "\t")):
            if last_key is None:
                continue
            current[last_key] = current[last_key] + "\n" + raw_line.strip()
            continue

        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        current[key.strip()] = value.strip()
        last_key = key.strip()

    if current:
        blocks.append(current)
    return blocks


def suite_arch_from_path(path: pathlib.Path) -> tuple[str, str]:
    """Given a 'dists/<suite>/main/binary-<arch>/Packages' path, return (suite, arch)."""
    parts = path.parts
    try:
        dists_idx = parts.index("dists")
    except ValueError as exc:
        raise ValueError(f"not a dists/ path: {path}") from exc
    suite = parts[dists_idx + 1]
    arch_dir = parts[dists_idx + 3]
    if not arch_dir.startswith("binary-"):
        raise ValueError(f"unexpected arch dir: {arch_dir}")
    return suite, arch_dir[len("binary-"):]


# ---------------------------------------------------------------------------
# Package card grouping
# ---------------------------------------------------------------------------


def group_package_cards(records: Iterable[dict]) -> list[dict]:
    """Collapse per-(suite, arch) package records into one card per (Package, Version)."""
    grouped: dict[tuple[str, str], dict] = {}
    for rec in records:
        key = (rec.get("Package", ""), rec.get("Version", ""))
        card = grouped.setdefault(
            key,
            {
                "package": key[0],
                "version": key[1],
                "suites": set(),
                "arches": set(),
                "description": rec.get("Description", ""),
            },
        )
        suite = rec.get("_suite")
        if suite:
            card["suites"].add(suite)
        arch = rec.get("Architecture")
        if arch:
            card["arches"].add(arch)

    cards = []
    for card in grouped.values():
        card["suites"] = sorted(card["suites"])
        card["arches"] = sorted(card["arches"])
        cards.append(card)
    cards.sort(key=lambda c: (c["package"], c["version"]))
    return cards


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_size(num_bytes: int) -> str:
    """Human-readable binary size (B / KiB / MiB / GiB)."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        num_bytes_f = num_bytes / 1024
        if num_bytes_f < 1024 or unit == "TiB":
            return f"{num_bytes_f:.1f} {unit}"
        num_bytes = int(num_bytes_f)
    # Unreachable.
    return f"{num_bytes} B"


def source_url_for(package: str, sources: dict[str, str]) -> str | None:
    slug = sources.get(package)
    if not slug:
        return None
    return f"https://github.com/{slug}"


def load_package_sources(toml_path: pathlib.Path) -> dict[str, str]:
    if not toml_path.exists():
        return {}
    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)
    return dict(data.get("packages", {}))


# ---------------------------------------------------------------------------
# CSS (shared across root and per-directory listings)
# ---------------------------------------------------------------------------


_CSS = """
:root {
  color-scheme: light;
  --fg: #222;
  --muted: #666;
  --bg: #fafafa;
  --card: #ffffff;
  --border: #e4e4e4;
  --accent: #1961b5;
  --code-bg: #0f1625;
  --code-fg: #e4ecff;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  color: var(--fg);
  background: var(--bg);
  line-height: 1.5;
}
main {
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
}
header.hero {
  text-align: center;
  padding: 1rem 0 0.5rem;
}
header.hero img {
  max-width: 320px;
  width: 80%;
  height: auto;
  image-rendering: pixelated;
}
header.hero h1 {
  font-size: 1.8rem;
  margin: 0.5rem 0 0.25rem;
}
header.hero p.tagline {
  margin: 0.25rem 0 1rem;
  color: var(--muted);
}
nav.links a {
  margin: 0 0.5rem;
  color: var(--accent);
  text-decoration: none;
  font-weight: 500;
}
nav.links a:hover { text-decoration: underline; }
h2 {
  font-size: 1.25rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.25rem;
  margin: 2rem 0 1rem;
}
pre.code {
  background: var(--code-bg);
  color: var(--code-fg);
  padding: 1rem 1.25rem;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 0.85rem;
  line-height: 1.4;
}
pre.code code { white-space: pre; }
ul.cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.75rem;
  list-style: none;
  padding: 0;
  margin: 0;
}
@media (min-width: 640px) {
  ul.cards { grid-template-columns: 1fr 1fr; }
}
ul.cards li.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.85rem 1rem;
}
ul.cards li.card h3 {
  margin: 0 0 0.25rem;
  font-size: 1rem;
}
ul.cards li.card h3 a {
  color: var(--accent);
  text-decoration: none;
}
ul.cards li.card p.meta {
  margin: 0 0 0.35rem;
  color: var(--muted);
  font-size: 0.85rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
ul.cards li.card p.desc {
  margin: 0;
  font-size: 0.9rem;
}
ul.tree {
  list-style: none;
  padding-left: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9rem;
}
ul.tree ul {
  list-style: none;
  padding-left: 1.25rem;
  border-left: 1px dashed var(--border);
  margin-left: 0.25rem;
}
ul.tree li { padding: 0.1rem 0; }
ul.tree a { color: var(--accent); text-decoration: none; }
ul.tree a:hover { text-decoration: underline; }
ul.tree span.size {
  color: var(--muted);
  margin-left: 0.5rem;
  font-size: 0.8rem;
}
table.dirlist {
  width: 100%;
  border-collapse: collapse;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9rem;
}
table.dirlist th, table.dirlist td {
  padding: 0.4rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
table.dirlist th { color: var(--muted); font-weight: 500; }
table.dirlist td.size, table.dirlist td.mtime { color: var(--muted); }
p.placeholder {
  background: var(--card);
  border: 1px dashed var(--border);
  padding: 1rem;
  border-radius: 8px;
  color: var(--muted);
}
footer.site-footer {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        f"<body><main>\n{body}\n</main></body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Root landing page
# ---------------------------------------------------------------------------


def _render_package_card(card: dict) -> str:
    name = html.escape(card["package"])
    version = html.escape(card["version"])
    suites = ", ".join(html.escape(s) for s in card["suites"])
    arches = ", ".join(html.escape(a) for a in card["arches"])
    description = html.escape(card.get("description", "")).split("\n", 1)[0]
    source_url = card.get("source_url")
    if source_url:
        heading = (
            f"<h3><a href=\"{html.escape(source_url)}\" rel=\"noopener\">"
            f"{name}</a></h3>"
        )
    else:
        heading = f"<h3>{name}</h3>"
    meta_bits = [f"v{version}"]
    if suites:
        meta_bits.append(suites)
    if arches:
        meta_bits.append(arches)
    meta = " &middot; ".join(meta_bits)
    return (
        "<li class=\"card\">"
        f"{heading}"
        f"<p class=\"meta\">{meta}</p>"
        f"<p class=\"desc\">{description}</p>"
        "</li>"
    )


def _render_tree_ul(nodes: list[dict]) -> str:
    if not nodes:
        return ""
    items = []
    for node in nodes:
        name = html.escape(node["name"])
        rel = html.escape(node["rel_path"])
        if node["is_dir"]:
            children_html = _render_tree_ul(node.get("children", []))
            items.append(
                f"<li><a href=\"{rel}/\">{name}/</a>{children_html}</li>"
            )
        else:
            size = (
                f" <span class=\"size\">{format_size(node['size'])}</span>"
                if node.get("size") is not None
                else ""
            )
            items.append(f"<li><a href=\"{rel}\">{name}</a>{size}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def render_root_index(cards: list[dict], tree: list[dict]) -> str:
    if cards:
        cards_html = (
            "<ul class=\"cards\">"
            + "".join(_render_package_card(c) for c in cards)
            + "</ul>"
        )
    else:
        cards_html = (
            "<p class=\"placeholder\">No packages published yet. "
            "Packages appear here automatically once the first deb is "
            "received from a source repo.</p>"
        )

    tree_html = _render_tree_ul(tree) if tree else (
        "<p class=\"placeholder\">Repository is empty.</p>"
    )

    body = f"""
<header class="hero">
  <img src="intro.png" alt="Remote FPGA access: you to the internet to a Raspberry Pi to an FPGA">
  <h1>fpgas.online APT repository</h1>
  <p class="tagline">Debian packages for the fpgas.online FPGA-as-a-Service platform.</p>
  <nav class="links">
    <a href="{FPGAS_ONLINE_URL}">fpgas.online</a>
    <a href="{APT_REPO_URL}">github.com/fpgas-online/apt</a>
  </nav>
</header>

<section id="setup">
  <h2>Add this repository on a Raspberry Pi</h2>
  <p>Works on Debian <strong>bookworm</strong> (12) and <strong>trixie</strong> (13). The snippet below auto-detects which suite you are on.</p>
  <pre class="code"><code>curl -fsSL https://fpgas-online.github.io/apt/pubkey.gpg \\
  | sudo gpg --dearmor -o /usr/share/keyrings/fpgas-online.gpg
echo "deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] \\
  https://fpgas-online.github.io/apt $(lsb_release -cs) main" \\
  | sudo tee /etc/apt/sources.list.d/fpgas-online.list
sudo apt update</code></pre>
</section>

<section id="packages">
  <h2>Packages</h2>
  {cards_html}
</section>

<section id="contents">
  <h2>Repository contents</h2>
  {tree_html}
</section>

<footer class="site-footer">
  Built from <a href="{APT_REPO_URL}">fpgas-online/apt</a>.
</footer>
"""
    return _page("fpgas.online APT repository", body)


# ---------------------------------------------------------------------------
# Per-directory listings
# ---------------------------------------------------------------------------


def render_directory_listing(
    rel_path: str,
    entries: list[dict],
    is_root: bool,
) -> str:
    rel_display = rel_path.rstrip("/")
    title = f"Index of /{rel_display}/"
    depth = len([p for p in rel_display.split("/") if p])
    root_href = "../" * depth if depth else "./"

    rows = []
    if not is_root:
        rows.append(
            "<tr>"
            "<td><a href=\"../\">../</a></td>"
            "<td class=\"size\">-</td>"
            "<td class=\"mtime\">-</td>"
            "</tr>"
        )
    dirs = [e for e in entries if e["is_dir"]]
    files = [e for e in entries if not e["is_dir"]]
    for entry in sorted(dirs, key=lambda e: e["name"]) + sorted(files, key=lambda e: e["name"]):
        name = html.escape(entry["name"])
        if entry["is_dir"]:
            link = f"<a href=\"{name}/\">{name}/</a>"
            size_txt = "-"
        else:
            link = f"<a href=\"{name}\">{name}</a>"
            size_txt = format_size(entry["size"]) if entry.get("size") is not None else "-"
        mtime = html.escape(entry.get("mtime_iso", ""))
        rows.append(
            f"<tr><td>{link}</td>"
            f"<td class=\"size\">{size_txt}</td>"
            f"<td class=\"mtime\">{mtime}</td></tr>"
        )

    back_bits = []
    if rel_display:
        back_bits.append(
            f"<p><a href=\"{root_href}\">&uarr; Repository root</a></p>"
        )

    body = f"""
<header class="hero">
  <h1>{html.escape(title)}</h1>
</header>

{"".join(back_bits)}

<table class="dirlist">
  <thead>
    <tr><th>Name</th><th>Size</th><th>Modified</th></tr>
  </thead>
  <tbody>
    {"".join(rows)}
  </tbody>
</table>

<footer class="site-footer">
  <a href="{root_href}">fpgas.online APT repository</a>
</footer>
"""
    return _page(title, body)


# ---------------------------------------------------------------------------
# File mirroring, tree walking, end-to-end build
# ---------------------------------------------------------------------------


_EXCLUDE_FILES = {".gitkeep"}


def _mirror_publishable_files(repo_root: pathlib.Path, out_dir: pathlib.Path) -> None:
    pubkey = repo_root / "pubkey.gpg"
    if pubkey.exists():
        shutil.copy2(pubkey, out_dir / "pubkey.gpg")

    for top in ("pool", "dists"):
        src = repo_root / top
        if not src.exists():
            continue
        for path in src.rglob("*"):
            rel = path.relative_to(repo_root)
            dst = out_dir / rel
            if path.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            elif path.is_file() and path.name not in _EXCLUDE_FILES:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)


def _parse_all_packages(site_dir: pathlib.Path) -> list[dict]:
    records: list[dict] = []
    for pkgfile in site_dir.glob("dists/*/main/binary-*/Packages"):
        suite, _arch = suite_arch_from_path(pkgfile)
        for rec in parse_packages_file(pkgfile.read_text()):
            rec["_suite"] = suite
            records.append(rec)
    return records


def _build_tree(out_dir: pathlib.Path, rel: pathlib.Path = pathlib.Path(".")) -> list[dict]:
    base = out_dir / rel
    children = []
    if not base.is_dir():
        return children
    for entry in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        if entry.name == "index.html":
            continue
        child_rel = (rel / entry.name) if str(rel) != "." else pathlib.Path(entry.name)
        rel_str = str(child_rel).replace(os.sep, "/")
        if entry.is_dir():
            children.append({
                "name": entry.name,
                "rel_path": rel_str,
                "is_dir": True,
                "children": _build_tree(out_dir, child_rel),
            })
        else:
            children.append({
                "name": entry.name,
                "rel_path": rel_str,
                "is_dir": False,
                "size": entry.stat().st_size,
            })
    return children


def _directory_entries(out_dir: pathlib.Path, rel: pathlib.Path) -> list[dict]:
    base = out_dir / rel if str(rel) != "." else out_dir
    entries = []
    for entry in base.iterdir():
        if entry.name == "index.html":
            continue
        stat = entry.stat()
        mtime_iso = (
            _dt.datetime.fromtimestamp(stat.st_mtime, _dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        entries.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size": stat.st_size if entry.is_file() else None,
            "mtime_iso": mtime_iso,
        })
    return entries


def _write_per_directory_indexes(out_dir: pathlib.Path) -> None:
    for current, dirnames, _filenames in os.walk(out_dir):
        current_path = pathlib.Path(current)
        rel = current_path.relative_to(out_dir)
        if str(rel) == ".":
            continue
        entries = _directory_entries(out_dir, rel)
        rel_url = str(rel).replace(os.sep, "/")
        html_out = render_directory_listing(
            rel_path=rel_url, entries=entries, is_root=False,
        )
        (current_path / "index.html").write_text(html_out)


def _fetch_intro_png_from_upstream() -> bytes:
    with urllib.request.urlopen(INTRO_PNG_URL, timeout=30) as resp:
        return resp.read()


def build_site(
    repo_root: pathlib.Path,
    out_dir: pathlib.Path,
    fetch_intro: Callable[[], bytes] = _fetch_intro_png_from_upstream,
) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    _mirror_publishable_files(repo_root, out_dir)

    intro_bytes = fetch_intro()
    (out_dir / "intro.png").write_bytes(intro_bytes)

    sources = load_package_sources(repo_root / "tools" / "package_sources.toml")
    records = _parse_all_packages(out_dir)
    cards = group_package_cards(records)
    for card in cards:
        card["source_url"] = source_url_for(card["package"], sources)

    tree = _build_tree(out_dir)
    root_html = render_root_index(cards=cards, tree=tree)
    (out_dir / "index.html").write_text(root_html)

    _write_per_directory_indexes(out_dir)

    _self_check(out_dir)


def _self_check(out_dir: pathlib.Path) -> None:
    missing = []
    if not (out_dir / "index.html").exists():
        missing.append("index.html (root)")
    for current, _dirnames, _filenames in os.walk(out_dir):
        p = pathlib.Path(current)
        if p == out_dir:
            continue
        if not (p / "index.html").exists():
            rel = p.relative_to(out_dir)
            missing.append(f"index.html in {rel}")
    if missing:
        raise SystemExit("self-check failed: missing " + ", ".join(missing))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the fpgas.online/apt site.")
    parser.add_argument("--repo-root", default=".", type=pathlib.Path)
    parser.add_argument("--out", default="site", type=pathlib.Path)
    args = parser.parse_args(argv)

    build_site(
        repo_root=args.repo_root.resolve(),
        out_dir=args.out.resolve(),
    )
    print(f"Built site at {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
