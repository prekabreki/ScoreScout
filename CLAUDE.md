# ScoreScout — notes for contributors

Piano-score analyzer and annotator. Parses MuseScore (`.mscz`/`.mscx`) and MusicXML
(`.musicxml`/`.mxl`/`.xml`) scores and produces analysis (key, chords, rhythm, structure, difficulty,
note annotations) plus annotated exports (MusicXML, PDF/PNG via MuseScore) and Markdown/HTML/JSON
reports.

## Build & run

```bash
pip install -r requirements.txt        # music21, anthropic, flask, fpdf2, pypdf, pytest
python app.py                          # web UI
python cli.py <score-file>             # CLI; see cli.py --help
ANTHROPIC_API_KEY= python cli.py <file> --no-llm   # fully offline
```

`config.py` reads `ANTHROPIC_API_KEY` from the environment for the optional LLM steps. PDF/PNG export
shells out to MuseScore 4 (probed on `PATH` and per-OS install locations).

`pytest -q` — no API key or MuseScore needed (the LLM is mocked; MuseScore-gated tests skip when it's
absent). CI runs on push/PR (`.github/workflows/ci.yml`).

## Gotchas

- **`--no-llm` disables all Claude calls** — both the explanation step and the Tier-4
  chord-identification call. The Tier-4 call is also skipped automatically when `ANTHROPIC_API_KEY`
  is unset.
- **Two annotation paths** — `.mscz` sources are annotated by editing raw MuseScore XML
  (`export/mscz_inject.py`) to preserve the original layout; every other format goes through music21
  (`export/annotate.py` + `export/render.py`). The two can label the same notes slightly differently.
- **MuseScore is required for PDF/PNG and `.mscz` parsing** — if it isn't found, export silently falls
  back to MusicXML.
- **Paths are configurable via env** — `PIANO_FORMATTER_LIBRARY_DIRS` and `PIANO_FORMATTER_EXPORT_DIR`
  override the per-OS home-relative defaults.
- **Flatpak MuseScore needs `/tmp` access** — see Troubleshooting in the README.

## Architecture

- `analyzer/` — pure analysis over a parsed music21 score
- `llm/` — Claude prompt + explanation generation
- `output/` — report renderers (`html.py`, `markdown.py`)
- `export/` — annotated export (`annotate.py`, `render.py`, `guide_pdf.py`, `mscz_inject.py`, `profiles.py`)
- `app.py` — Flask web UI with an in-memory + on-disk (`.cache/`) analysis cache
- `cli.py` — CLI entry point; writes a verbose `analyzer_debug.log` on every run

## Conventions

- Web-UI cache keys are normalized relative to library dirs so they're stable across machines.
- `.cache/` and `analyzer_debug.log` are runtime artifacts and gitignored.
- Tests build fixtures programmatically with music21 (synthetic scores — none bundled); the Anthropic
  client is monkeypatched; MuseScore-dependent tests use `@pytest.mark.skipif(not find_musescore())`.
