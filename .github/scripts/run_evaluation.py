"""CI entrypoint for the evaluate-city workflow.

Calls the same fetch_city_bulk/render_city functions evaluate.py uses, but
unlike evaluate.py (which sys.exit(1)s on any error) this distinguishes
three outcomes via exit code, so the workflow can post a different comment
and decide whether to deploy/close the issue for each:

  0 - success, gap rate acceptable
  2 - CityNotFoundError (unparseable/unresolvable city name)
  3 - excessive gap rate (>=90% unresolved/missing/flagged) -- looks like a
      misconfigured API key, not a real per-field gap
  1 - any other pipeline failure

Writes `slug`, `gap_summary`, and `error_message` to $GITHUB_OUTPUT for the
workflow's comment step to read.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openai import AsyncOpenAI

from fetch import OPENROUTER_BASE_URL, CityNotFoundError, fetch_city_bulk
from lint import find_gaps
from models import load_schema
from render import render_city

GAP_FAILURE_THRESHOLD = 0.90


def total_and_gap_field_counts(schema: dict, gaps: dict) -> tuple[int, int]:
    from models import fetchable_fields

    total = sum(len(fetchable_fields(schema, key)) for key in schema["categories"])
    gap_count = sum(len(fields) for fields in gaps.values())
    return total, gap_count


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        print(f"[output] {name}={value}")
        return
    with open(output_path, "a") as f:
        if "\n" in value:
            f.write(f"{name}<<EOF\n{value}\nEOF\n")
        else:
            f.write(f"{name}={value}\n")


async def _main(city_state_input: str) -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        write_output("error_message", "OPENROUTER_API_KEY is not set.")
        return 1

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    schema = load_schema()

    try:
        record = await fetch_city_bulk(client, schema, city_state_input)
    except CityNotFoundError as e:
        write_output("error_message", str(e))
        return 2
    except Exception as e:
        write_output("error_message", f"{type(e).__name__}: {e}")
        return 1

    write_output("slug", record.slug)

    gaps = find_gaps(schema, record)
    total, gap_count = total_and_gap_field_counts(schema, gaps)
    gap_rate = (gap_count / total) if total else 0.0

    if gap_rate >= GAP_FAILURE_THRESHOLD:
        write_output(
            "error_message",
            f"{gap_count}/{total} fields ({gap_rate:.0%}) came back "
            "unresolved/missing/flagged -- this looks like a misconfigured "
            "or invalid OPENROUTER_API_KEY, not a normal per-field gap. "
            "Check the repo secret.",
        )
        return 3

    try:
        render_city(record.slug)
    except Exception as e:
        write_output("error_message", f"Rendering failed: {type(e).__name__}: {e}")
        return 1

    if gap_count:
        lines = [f"{gap_count}/{total} fields still need a backfill:"]
        for category_key, fields in gaps.items():
            for field_key, reason in fields.items():
                lines.append(f"- [{reason}] {category_key}.{field_key}")
        write_output("gap_summary", "\n".join(lines))
    else:
        write_output("gap_summary", "All fields valid and up to date.")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('Usage: python run_evaluation.py "City, ST"', file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(_main(sys.argv[1])))
