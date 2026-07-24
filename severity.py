"""Shared free-text severity classifier.

Used by both the deploy-time risk badges (build_pages_site.py) and the
scoring math (scoring.py) -- both need the same "does this text say Low,
Moderate, or High/Strict" read, and duplicating the regex in two places
would drift out of sync the first time either one gets tuned.

These fields lead with a severity phrase but aren't a controlled
vocabulary (e.g. "Moderate to High - Flooding..." or "Moderate-to-strict:
Boise requires..."), so this is a heuristic read, not a parse. Picks the
HIGHEST severity keyword present -- ranges like "Low-to-moderate" or
"Moderate to High" are common, and erring toward the more severe reading
is the safer default for a hazard/strictness field. No match -> None,
rather than guessing.

"strict"/"lenient"/"permissive" are included alongside the natural-hazard
vocabulary (high/moderate/low) since this classifier also covers
regulatory-strictness fields (homeschool regulation, permitting) that use
the same "how burdensome is this" framing.
"""
from __future__ import annotations

import re
from typing import Optional

_SEVERITY_PATTERNS = [
    ("high", re.compile(r"\b(very high|high|strict)\b", re.IGNORECASE)),
    ("moderate", re.compile(r"\b(moderate|medium)\b", re.IGNORECASE)),
    ("low", re.compile(r"\b(very low|low|minimal|none|lenient|permissive)\b", re.IGNORECASE)),
]

SEVERITY_COLORS = {"high": "#b3452b", "moderate": "#b08628", "low": "#4a7a5a"}


def classify_severity(text: str) -> Optional[dict]:
    found = {level for level, pattern in _SEVERITY_PATTERNS if pattern.search(text)}
    for level in ("high", "moderate", "low"):
        if level in found:
            return {"level": level, "color": SEVERITY_COLORS[level]}
    return None
