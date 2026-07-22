"""Shared fixtures: redirect every module's on-disk paths to a temp dir so
tests never touch the real cities/reports/ directories."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    cities_dir = tmp_path / "cities"
    reports_dir = tmp_path / "reports"
    templates_dir = Path(__file__).parent.parent / "templates"
    index_path = tmp_path / "index.html"
    cities_dir.mkdir()
    reports_dir.mkdir()

    import fetch
    import lint
    import render
    import build_index

    monkeypatch.setattr(fetch, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(lint, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(render, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(render, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(render, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(build_index, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(build_index, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(build_index, "INDEX_PATH", index_path)

    return {"cities": cities_dir, "reports": reports_dir, "index": index_path}
