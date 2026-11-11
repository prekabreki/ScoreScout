"""Tests for the shared export orchestrator (core/export.py).

These pin the decision tree both front ends now share: format dispatch, the
MuseScore-unavailable MusicXML fallback, unknown-format coercion, and the web
export route wiring end to end. MuseScore is never required (the fallback test
fakes its absence).
"""

from pathlib import Path

import pytest
from music21 import stream, note, meter

from core.pipeline import run_analysis
from core.export import export_annotated


def _analysis(tmp_path):
    s = stream.Score()
    p = stream.Part()
    p.append(meter.TimeSignature("4/4"))
    for n in ["C4", "D4", "E4", "F4"]:
        p.append(note.Note(n, quarterLength=1.0))
    s.insert(0, p)
    path = tmp_path / "tune.musicxml"
    s.write("musicxml", fp=str(path))
    return path, run_analysis(str(path), use_llm=False)


def test_export_musicxml_writes_file(tmp_path):
    path, (analysis, score) = _analysis(tmp_path)
    out = tmp_path / "out.musicxml"

    result = export_annotated(
        score, analysis, "musicxml",
        out_path=str(out), source_path=str(path),
    )

    assert result.fmt == "musicxml"
    assert result.fallback is False
    assert Path(result.path).is_file()


def test_pdf_export_falls_back_to_musicxml_without_musescore(tmp_path, monkeypatch):
    import export.render as render
    monkeypatch.setattr(render, "find_musescore", lambda: None)

    path, (analysis, score) = _analysis(tmp_path)
    out = tmp_path / "out.pdf"

    result = export_annotated(
        score, analysis, "pdf",
        out_path=str(out), source_path=str(path),
    )

    assert result.fallback is True
    assert result.fmt == "musicxml"
    assert Path(result.path).suffix == ".musicxml"
    assert Path(result.path).is_file()


def test_unknown_format_is_coerced_to_musicxml(tmp_path):
    path, (analysis, score) = _analysis(tmp_path)
    out = tmp_path / "out.svg"

    result = export_annotated(
        score, analysis, "svg",
        out_path=str(out), source_path=str(path),
    )

    assert result.fmt == "musicxml"
    assert result.path.endswith(".musicxml")
    assert Path(result.path).is_file()


def test_unknown_profile_raises_value_error(tmp_path):
    path, (analysis, score) = _analysis(tmp_path)

    with pytest.raises(ValueError):
        export_annotated(
            score, analysis, "musicxml",
            out_path=str(tmp_path / "out.musicxml"),
            profile_name="bogus",
            source_path=str(path),
        )


def test_front_ends_share_one_export_orchestrator():
    import cli
    import app
    from core.export import export_annotated as shared

    assert cli.export_annotated is shared
    assert app.core_export.export_annotated is shared


def test_web_export_route_uses_orchestrator(tmp_path, monkeypatch):
    import app as appmod

    path, (analysis, score) = _analysis(tmp_path)
    monkeypatch.setattr(appmod, "LIBRARY_DIRS", [tmp_path])
    monkeypatch.setattr(appmod, "_analysis_cache", {})
    monkeypatch.setattr(appmod, "_cache_id_map", {})
    # config.EXPORT_DIR is imported inside the route, so patch the config module.
    monkeypatch.setattr("config.EXPORT_DIR", tmp_path)

    cache_id = appmod._cache_put(score, analysis, filepath=str(path), persist=False)

    client = appmod.app.test_client()
    resp = client.post("/export", json={
        "cache_id": cache_id,
        "format": "musicxml",
        "profile": "full",
    })

    assert resp.status_code == 200
    assert resp.data


def test_mscz_clean_pdf_uses_mscz_render_path(tmp_path, monkeypatch):
    import core.export as core_export

    path, (analysis, score) = _analysis(tmp_path)
    mscz_path = tmp_path / "source.mscz"
    out = tmp_path / "out.pdf"

    calls = {}

    def fake_from_mscz(mscz_path_arg, output_path, **kwargs):
        calls["mscz"] = (mscz_path_arg, output_path)
        Path(output_path).write_text("pdf")
        return output_path

    def fail_annotate(*args, **kwargs):
        raise AssertionError("clean .mscz PDF must not annotate through music21")

    monkeypatch.setattr(core_export, "export_pdf_from_mscz", fake_from_mscz)
    monkeypatch.setattr(core_export, "annotate_score", fail_annotate)

    result = export_annotated(
        score, analysis, "pdf",
        out_path=str(out), profile_name="clean", source_path=str(mscz_path),
    )

    assert result.fmt == "pdf"
    assert result.fallback is False
    assert calls["mscz"][0] == str(mscz_path)
    assert Path(result.path) == out


def test_musicxml_clean_pdf_keeps_music21_path(tmp_path, monkeypatch):
    import core.export as core_export

    path, (analysis, score) = _analysis(tmp_path)
    out = tmp_path / "out.pdf"

    called = {}

    def fake_annotate(score_arg, *args, **kwargs):
        called["annotate"] = True
        return score_arg

    def fake_export_pdf(annotated, output_path):
        called["pdf"] = output_path
        Path(output_path).write_text("pdf")

    def fail_from_mscz(*args, **kwargs):
        raise AssertionError("MusicXML source must not use the .mscz render path")

    monkeypatch.setattr(core_export, "annotate_score", fake_annotate)
    monkeypatch.setattr(core_export, "export_pdf", fake_export_pdf)
    monkeypatch.setattr(core_export, "export_pdf_from_mscz", fail_from_mscz)

    result = export_annotated(
        score, analysis, "pdf",
        out_path=str(out), profile_name="clean", source_path=str(path),
    )

    assert called.get("annotate") is True
    assert called.get("pdf") == str(out)
    assert result.fmt == "pdf"
