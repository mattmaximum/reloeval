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
import json
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
from scoring import compute_all_scores, scoring_methodology
from severity import classify_severity

ROOT = Path(__file__).parent.parent.parent
TEMPLATES_DIR = ROOT / "templates"
SITE_DIR = ROOT / "_site"


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
                classify_severity(field["display_value"])
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


def comparison_payload(record, context: dict) -> dict:
    """Slimmed-down per-city data for the client-side comparison tool on
    the index page -- same field contexts the report page uses, minus
    chart_svg (a full inline SVG per city would bloat a JSON blob meant
    to hold every evaluated city at once) and the markdown-formatted
    citation string (HTML only needs citation_url/citation_date)."""
    return {
        "city": record.normalized.city,
        "state": record.normalized.state,
        "county": record.normalized.county,
        "overall_summary": context.get("overall_summary"),
        "lens_scores": context["lens_scores"],
        "categories": [
            {
                "key": category["key"],
                "label": category["label"],
                "summary": category.get("summary"),
                "pros_cons": category.get("pros_cons"),
                "fields": [
                    {k: v for k, v in field.items() if k not in ("chart_svg", "citation")}
                    for field in category["fields"]
                ],
            }
            for category in context["categories"]
        ],
    }


def find_field(categories: list[dict], category_key: str, field_key: str) -> Optional[dict]:
    category = next((c for c in categories if c["key"] == category_key), None)
    if category is None:
        return None
    return next((f for f in category["fields"] if f["key"] == field_key), None)


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
    compare_template = env.get_template("compare_page.html.j2")
    scoring_template = env.get_template("scoring_page.html.j2")

    records = gather_cities()
    # Relative scoring needs every city at once (min-max normalization),
    # so this has to happen here, not per-city inside the loop below.
    all_scores = compute_all_scores(schema, records)

    index_cities = []
    comparison_data = {}
    for record in records:
        context = build_render_context(schema, record)
        context["categories"] = enrich_categories(context["categories"])
        context["completion"] = completion_stats(schema, record)
        context["scorecard"] = build_scorecard(context["categories"])
        context["lens_scores"] = all_scores[record.slug]

        report_html = report_template.render(**context)
        (SITE_DIR / "reports" / f"{record.slug}.html").write_text(report_html)

        bd_score_field = find_field(context["categories"], "geographic_hazards", "bd_score")

        index_cities.append({
            "city": record.normalized.city,
            "state": record.normalized.state,
            "county": record.normalized.county,
            "href": f"reports/{record.slug}.html",
            "completion": context["completion"],
            "lens_scores": context["lens_scores"],
            "first_evaluated_date": record.first_evaluated_date,
            "bd_score_field": bd_score_field,
        })
        comparison_data[record.slug] = comparison_payload(record, context)

    (SITE_DIR / "index.html").write_text(index_template.render(cities=index_cities))
    (SITE_DIR / "compare.html").write_text(compare_template.render(city_count=len(records)))
    (SITE_DIR / "scoring.html").write_text(scoring_template.render(**scoring_methodology(schema)))
    (SITE_DIR / "cities-data.json").write_text(json.dumps(comparison_data))
    return SITE_DIR


if __name__ == "__main__":
    path = build_site()
    print(f"Wrote {path}")
