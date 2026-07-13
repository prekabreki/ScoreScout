"""Audit M14: cached analysis must invalidate when the source file changes."""

from music21 import converter


def test_cache_invalidates_on_file_change(tmp_path, monkeypatch):
    import app

    score_file = tmp_path / "tune.musicxml"
    converter.parse("tinyNotation: 4/4 C4 D4 E4 F4").write("musicxml", fp=str(score_file))

    # Point the library at the temp dir so the cache key resolves there.
    monkeypatch.setattr(app, "LIBRARY_DIRS", [tmp_path])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    score, analysis = app._run_analysis(str(score_file), "tune.musicxml", use_llm=False)
    app._cache_put(score, analysis, filepath=str(score_file), persist=False)

    # Fresh cache: hit.
    assert app._cache_get_full(str(score_file)) is not None

    # Edit the file (size changes) -> entry must be invalidated and evicted.
    score_file.write_text(score_file.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")
    assert app._cache_get_full(str(score_file)) is None
