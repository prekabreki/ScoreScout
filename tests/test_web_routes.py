"""Route-level tests for the Flask web UI (issue #38).

Every test drives ``app.app.test_client()`` against a temporary library. The
autouse fixture redirects ``config.CACHE_DIR`` / ``config.EXPORT_DIR`` under
``tmp_path``, clears the module-level cache dicts, and asserts afterwards that
the real ``.cache/`` and ``~/Piano/annotated/`` were left untouched.
"""

import io

import pytest


def _snapshot(directory):
    """Files (as relative paths) currently under ``directory``, or None."""
    if not directory.is_dir():
        return None
    return {path.relative_to(directory) for path in directory.rglob("*")}


@pytest.fixture(autouse=True)
def isolate_app(tmp_path, monkeypatch):
    import app
    import config
    from llm import chord_names, explain

    real_cache = config.CACHE_DIR
    real_export = config.EXPORT_DIR
    cache_before = _snapshot(real_cache)
    export_before = _snapshot(real_export)

    monkeypatch.setattr(app, "LIBRARY_DIRS", [tmp_path])
    monkeypatch.setattr(app, "_analysis_cache", {})
    monkeypatch.setattr(app, "_cache_id_map", {})
    # ``app`` imports these from ``config`` inside functions, so patch config.
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "export")
    # No network: keep both LLM entry points keyless.
    monkeypatch.setattr(explain, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(chord_names, "ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    yield

    assert _snapshot(real_cache) == cache_before, "test wrote into the real .cache/"
    assert _snapshot(real_export) == export_before, (
        "test wrote into the real annotated export dir"
    )


@pytest.fixture
def client():
    import app

    return app.app.test_client()


def test_index_lists_library_file(client, musicxml_score_file):
    resp = client.get("/")

    assert resp.status_code == 200
    assert musicxml_score_file.stem in resp.get_data(as_text=True)


def test_library_json_lists_library_file(client, musicxml_score_file):
    resp = client.get("/library")

    assert resp.status_code == 200
    files = resp.get_json()
    assert any(f["name"] == musicxml_score_file.stem for f in files)


def test_analyze_library_file_returns_html(client, musicxml_score_file):
    resp = client.post(
        "/analyze",
        json={"path": str(musicxml_score_file), "skip_llm": "1"},
    )

    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "<html" in text.lower()
    assert musicxml_score_file.stem in text


def test_analyze_rejects_path_outside_library(client, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside" / "piece.musicxml"

    resp = client.post("/analyze", json={"path": str(outside), "skip_llm": "1"})

    assert resp.status_code == 403


def test_analyze_missing_file_inside_library_returns_404(client, tmp_path):
    missing = tmp_path / "missing.musicxml"

    resp = client.post("/analyze", json={"path": str(missing), "skip_llm": "1"})

    assert resp.status_code == 404


def test_upload_musicxml_returns_html(client, musicxml_score_file):
    data = {
        "file": (io.BytesIO(musicxml_score_file.read_bytes()), musicxml_score_file.name),
        "skip_llm": "1",
    }

    resp = client.post("/analyze/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert musicxml_score_file.stem in resp.get_data(as_text=True)


def test_upload_rejects_unsupported_extension(client):
    data = {
        "file": (io.BytesIO(b"not a score"), "song.txt"),
        "skip_llm": "1",
    }

    resp = client.post("/analyze/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code == 400


def test_batch_one_reports_ok_then_cached(client, musicxml_score_file):
    payload = {"path": str(musicxml_score_file), "skip_llm": "1"}

    first = client.post("/analyze/batch-one", json=payload)
    assert first.status_code == 200
    assert first.get_json()["status"] == "ok"

    second = client.post("/analyze/batch-one", json=payload)
    assert second.status_code == 200
    assert second.get_json()["status"] == "cached"


def test_export_returns_musicxml_attachment(client, musicxml_score_file):
    import app

    analyzed = client.post(
        "/analyze/batch-one",
        json={"path": str(musicxml_score_file), "skip_llm": "1"},
    )
    assert analyzed.status_code == 200
    cache_id = next(iter(app._analysis_cache.values()))["cache_id"]

    resp = client.post(
        "/export",
        json={"cache_id": cache_id, "format": "musicxml", "profile": "full"},
    )

    assert resp.status_code == 200
    disposition = resp.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert ".musicxml" in disposition
    assert resp.data


def test_export_unknown_cache_id_returns_404(client):
    resp = client.post(
        "/export",
        json={"cache_id": "does-not-exist", "format": "musicxml"},
    )

    assert resp.status_code == 404
