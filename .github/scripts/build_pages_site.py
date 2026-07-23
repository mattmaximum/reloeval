"""Deploy-only site builder for GitHub Pages.

GitHub Pages via actions/deploy-pages serves the uploaded artifact byte for
byte -- no Jekyll, no markdown rendering -- so this builds real HTML pages
directly from each city's JSON (via render.build_render_context, the same
context-building logic reports/*.md uses) rather than converting the .md
file. Both outputs read the same underlying data and can never drift on
substance, only presentation: reports/*.md stays a plain, git-diffable
document; the deployed page gets richer presentation (risk badges, a
climate chart, collapsible sections, a completion badge) that plain
markdown can't express.

Not part of the tested pipeline (fetch/render/lint/build_index) -- purely
an artifact-shaping step for deployment, run only by the CI workflow.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader, select_autoescape

from build_index import gather_cities
from lint import find_gaps
from models import fetchable_fields, load_schema
from render import build_render_context

ROOT = Path(__file__).parent.parent.parent
TEMPLATES_DIR = ROOT / "templates"
SITE_DIR = ROOT / "_site"

# These fields lead with a severity phrase but aren't a controlled
# vocabulary (e.g. "Moderate to High - Flooding..."), so this is a
# heuristic read, not a parse. Picks the HIGHEST severity keyword present
# -- ranges like "Low-to-moderate" or "Moderate to High" are common, and
# erring toward the more severe reading is the safer default for a hazard
# field. No match -> no badge, rather than guessing.
_RISK_PATTERNS = [
    ("high", re.compile(r"\b(very high|high)\b", re.IGNORECASE)),
    ("moderate", re.compile(r"\b(moderate|medium)\b", re.IGNORECASE)),
    ("low", re.compile(r"\b(very low|low|minimal|none)\b", re.IGNORECASE)),
]
_RISK_COLORS = {"high": "#b3452b", "moderate": "#b08628", "low": "#4a7a5a"}


def classify_risk(text: str) -> Optional[dict]:
    found = {level for level, pattern in _RISK_PATTERNS if pattern.search(text)}
    for level in ("high", "moderate", "low"):
        if level in found:
            return {"level": level, "color": _RISK_COLORS[level]}
    return None


def completion_stats(schema: dict, record) -> dict:
    total = sum(len(fetchable_fields(schema, key)) for key in schema["categories"])
    gaps = find_gaps(schema, record)
    gap_count = sum(len(fields) for fields in gaps.values())
    valid = total - gap_count
    percent = round(100 * valid / total) if total else 0
    return {"valid": valid, "total": total, "percent": percent}


def render_climate_chart_svg(monthly_rows: list[dict]) -> str:
    months = [row["month"][:3] for row in monthly_rows]
    highs = [row["avg_high_f"] for row in monthly_rows]
    lows = [row["avg_low_f"] for row in monthly_rows]

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(months, highs, marker="o", color="#c77b2e", label="Avg High (°F)")
    ax.plot(months, lows, marker="o", color="#5b8fa8", label="Avg Low (°F)")
    ax.set_ylabel("Temperature (°F)")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    fig.tight_layout()

    buf = io.StringIO()
    fig.savefig(buf, format="svg", transparent=True)
    plt.close(fig)
    svg = buf.getvalue()
    return svg[svg.index("<svg"):]


def enrich_categories(categories: list[dict]) -> list[dict]:
    """Add deploy-only presentation extras (risk badges, an inline climate
    chart) on top of render.build_render_context's shared field contexts.
    Kept out of render.py since these are purely HTML-page concerns --
    reports/*.md has no use for a risk color or an embedded SVG."""
    for category in categories:
        for field in category["fields"]:
            field["risk_badge"] = (
                classify_risk(field["display_value"])
                if field["risk_field"] and field["status"] == "valid"
                else None
            )
            if field["is_table"] and field["key"] == "monthly_climate_table" and field["status"] == "valid":
                try:
                    field["chart_svg"] = render_climate_chart_svg(field["display_value"])
                except Exception:
                    field["chart_svg"] = None
            else:
                field["chart_svg"] = None
    return categories


def build_scorecard(categories: list[dict]) -> list[dict]:
    return [
        field
        for category in categories
        for field in category["fields"]
        if field["highlight"] and field["status"] == "valid"
    ]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_site() -> Path:
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "reports").mkdir(exist_ok=True)

    schema = load_schema()
    env = _env()
    report_template = env.get_template("report_page.html.j2")
    index_template = env.get_template("index_page.html.j2")

    index_cities = []
    for record in gather_cities():
        context = build_render_context(schema, record)
        context["categories"] = enrich_categories(context["categories"])
        context["completion"] = completion_stats(schema, record)
        context["scorecard"] = build_scorecard(context["categories"])

        report_html = report_template.render(**context)
        (SITE_DIR / "reports" / f"{record.slug}.html").write_text(report_html)

        index_cities.append({
            "city": record.normalized.city,
            "state": record.normalized.state,
            "county": record.normalized.county,
            "href": f"reports/{record.slug}.html",
            "completion": context["completion"],
        })

    (SITE_DIR / "index.html").write_text(index_template.render(cities=index_cities))
    return SITE_DIR


if __name__ == "__main__":
    path = build_site()
    print(f"Wrote {path}")
