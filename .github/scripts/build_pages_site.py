"""Deploy-only site builder for GitHub Pages.

GitHub Pages via actions/deploy-pages serves the uploaded artifact byte for
byte -- no Jekyll, no markdown rendering. reports/*.md would show up as raw
markdown text instead of a formatted page. This script converts each report
to HTML into a separate _site/ directory built only for the Pages deploy;
reports/*.md in the repo itself stay markdown (unchanged, git-friendly,
still rendered nicely by GitHub's own file viewer when browsing the repo).

Not part of the tested pipeline (fetch/render/lint/build_index) -- purely
an artifact-shaping step for deployment, run only by the CI workflow.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import markdown

from build_index import gather_cities

ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = ROOT / "reports"
SITE_DIR = ROOT / "_site"

_PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; line-height: 1.5; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
  a.back {{ display: inline-block; margin-bottom: 20px; }}
</style>
</head>
<body>
<a class="back" href="../index.html">&larr; All cities</a>
{body}
</body>
</html>
"""

_INDEX_TEMPLATE = """<!doctype html>
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


def build_site() -> Path:
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "reports").mkdir(exist_ok=True)

    records = gather_cities()
    if not records:
        index_body = '<p class="empty">No cities evaluated yet.</p>'
    else:
        items = []
        for r in records:
            report_path = REPORTS_DIR / f"{r.slug}.md"
            city = html.escape(r.normalized.city)
            state = html.escape(r.normalized.state)
            county = html.escape(r.normalized.county)
            if report_path.exists():
                html_body = markdown.markdown(
                    report_path.read_text(), extensions=["tables"]
                )
                (SITE_DIR / "reports" / f"{r.slug}.html").write_text(
                    _PAGE_TEMPLATE.format(title=f"{r.normalized.city}, {r.normalized.state}", body=html_body)
                )
                href = f"reports/{r.slug}.html"
            else:
                href = "#"
            items.append(
                f'<li><a href="{href}">{city}, {state}</a><br>'
                f'<span class="county">{county}</span></li>'
            )
        index_body = "<ul>\n" + "\n".join(items) + "\n</ul>"

    (SITE_DIR / "index.html").write_text(_INDEX_TEMPLATE.format(body=index_body))
    return SITE_DIR


if __name__ == "__main__":
    path = build_site()
    print(f"Wrote {path}")
