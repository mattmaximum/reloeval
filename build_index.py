"""Index generator: scan reports/ (via cities/*.json for structured data)
and write index.html. Manual, on-demand step — not auto-triggered after
every evaluation, consistent with "no auto-refresh."
"""
from __future__ import annotations

import html
from pathlib import Path

from models import CityRecord

CITIES_DIR = Path(__file__).parent / "cities"
REPORTS_DIR = Path(__file__).parent / "reports"
INDEX_PATH = Path(__file__).parent / "index.html"

_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Relocation Evaluator</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; }}
  h1 {{ font-size: 1.4rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 10px 0; border-bottom: 1px solid #ddd; }}
  a {{ text-decoration: none; font-weight: 600; }}
  .county {{ color: #666; font-size: 0.9rem; }}
  .empty {{ color: #666; }}
</style>
</head>
<body>
<h1>Relocation Evaluator</h1>
{body}
</body>
</html>
"""


def gather_cities() -> list[CityRecord]:
    if not CITIES_DIR.exists():
        return []
    records = [CityRecord.model_validate_json(p.read_text()) for p in CITIES_DIR.glob("*.json")]
    return sorted(records, key=lambda r: (r.normalized.city, r.normalized.state))


def build_index() -> Path:
    records = gather_cities()
    if not records:
        body = '<p class="empty">No cities evaluated yet. Run <code>fetch.py</code> to add one.</p>'
    else:
        items = []
        for r in records:
            report_path = REPORTS_DIR / f"{r.slug}.md"
            city = html.escape(r.normalized.city)
            state = html.escape(r.normalized.state)
            county = html.escape(r.normalized.county)
            href = f"reports/{r.slug}.md" if report_path.exists() else "#"
            items.append(
                f'<li><a href="{href}">{city}, {state}</a><br>'
                f'<span class="county">{county}</span></li>'
            )
        body = "<ul>\n" + "\n".join(items) + "\n</ul>"

    INDEX_PATH.write_text(_TEMPLATE.format(body=body))
    return INDEX_PATH


if __name__ == "__main__":
    path = build_index()
    print(f"Wrote {path}")
