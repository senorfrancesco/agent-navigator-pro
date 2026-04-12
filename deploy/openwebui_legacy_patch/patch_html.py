from __future__ import annotations

import argparse
from pathlib import Path

EXPECTED_OPENWEBUI_VERSION = "0.8.12"
PATCH_MARKER = "agent-nav-openwebui-autopoll-v2"


def inject_script(html: str, script: str, *, marker: str = PATCH_MARKER) -> tuple[str, bool]:
    if marker in html:
        return html, False

    script_block = f'<script id="{marker}">\n{script.rstrip()}\n</script>\n'
    if "</body>" in html:
        return html.replace("</body>", f"{script_block}</body>", 1), True
    if "</html>" in html:
        return html.replace("</html>", f"{script_block}</html>", 1), True
    return html + "\n" + script_block, True


def patch_html_file(html_path: Path, script_path: Path, *, marker: str = PATCH_MARKER) -> bool:
    html = html_path.read_text(encoding="utf-8")
    script = script_path.read_text(encoding="utf-8")
    patched_html, changed = inject_script(html, script, marker=marker)
    if changed:
        html_path.write_text(patched_html, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("html_path")
    parser.add_argument("script_path")
    parser.add_argument("--marker", default=PATCH_MARKER)
    args = parser.parse_args(argv)

    patch_html_file(Path(args.html_path), Path(args.script_path), marker=args.marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
