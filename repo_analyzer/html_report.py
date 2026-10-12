"""report.html: one self-contained page (styles, script and data inlined) that opens from disk in any browser."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
@@CSS@@
</style>
</head>
<body>
<main id="app" class="page"><noscript>This report needs JavaScript to display. The same results are in score.json
and report.md next to this file.</noscript></main>
<script type="application/json" id="report-data">@@DATA@@</script>
<script>
@@JS@@
</script>
</body>
</html>
"""


def _safe_json(obj: object) -> str:
    """JSON that cannot end the <script> element early."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def build_html(result: dict, final_map: dict) -> str:
    name = (result.get("run") or {}).get("name") or result.get("repo") or "Project"
    parts = {
        "@@TITLE@@": html.escape(f"{name} · component effort"),
        "@@CSS@@": (WEB / "report.css").read_text(encoding="utf-8"),
        "@@JS@@": (WEB / "report.js").read_text(encoding="utf-8").replace("</", "<\\/"),
        "@@DATA@@": _safe_json({"score": result, "map": final_map}),
    }
    page = PAGE
    for marker, value in parts.items():          # one pass per marker; values are never re-scanned for markers
        head, sep, tail = page.partition(marker)
        page = head + value + tail
    return page


def write_html_report(path: Path, result: dict, final_map: dict) -> None:
    tmp = Path(f"{path}.tmp")
    tmp.write_text(build_html(result, final_map), encoding="utf-8")
    os.replace(tmp, path)
