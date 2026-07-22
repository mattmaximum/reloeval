import os

import pytest

from atomic_write import atomic_write


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "sub" / "city.json"
    atomic_write(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'


def test_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "city.json"
    target.write_text("old content")
    atomic_write(target, "new content")
    assert target.read_text() == "new content"


def test_atomic_write_no_leftover_temp_files_on_success(tmp_path):
    target = tmp_path / "city.json"
    atomic_write(target, "content")
    leftover = [p for p in tmp_path.iterdir() if p.name != "city.json"]
    assert leftover == []


def test_crash_mid_write_leaves_original_untouched(tmp_path, monkeypatch):
    """Simulates a crash between opening the temp file and the atomic
    rename — the target file must be left exactly as it was before."""
    target = tmp_path / "city.json"
    target.write_text("original content")

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr("os.replace", boom)

    with pytest.raises(OSError):
        atomic_write(target, "new content that should never land")

    assert target.read_text() == "original content"
    leftover = [p for p in tmp_path.iterdir() if p.name != "city.json"]
    assert leftover == [], "temp file must be cleaned up after a failed write"


def test_crash_mid_write_new_file_leaves_no_file(tmp_path, monkeypatch):
    """Same crash, but for a brand-new city — no file should exist after."""
    target = tmp_path / "brand-new-city.json"

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr("os.replace", boom)

    with pytest.raises(OSError):
        atomic_write(target, "content")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
