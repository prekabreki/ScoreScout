# ScoreScout

> Point it at a MuseScore file; it works out how hard the piece is and writes the note names and
> chords onto the score for you.

You've downloaded sheet music for a piece you love, and you can't yet tell how hard it is or where to
start. Reading it fluently is the skill you're still building. ScoreScout reads the score and works
that out for you: the key, the chords, the rhythm and structure, and an honest 1–10 difficulty. Then
it hands the score back marked up, with note letters, octave numbers, and chord shapes written onto
the staff, plus a chord-reference page and an optional plain-language walkthrough. MuseScore itself
does the rendering, so the annotated copy still looks like real sheet music. That leaves you with a
practice plan instead of a wall of notes you can't read yet — no teacher needed, and fully offline
unless you turn on the AI guide.

## Features

- **Analysis** — key detection, chord progression (with an optional LLM chord-identification pass),
  rhythm patterns, structural sections, a 1–10 difficulty estimate, and per-note annotations.
- **Annotated exports** — overlay note letters, octave numbers, and chord shapes onto the original
  score, plus an appended chord-reference page. Exports as MusicXML, PDF, or PNG. For `.mscz` sources
  the annotations are injected into the raw MuseScore XML, so the original engraving/layout is
  preserved.
- **Reports** — Markdown, HTML, or JSON.
- **Optional beginner's guide** — plain-language explanation via Claude (`ANTHROPIC_API_KEY`); skip
  entirely with `--no-llm`.
- **Two interfaces** — a Flask web UI (`app.py`) and a CLI (`cli.py`).

## Requirements

- **Python 3.10+**
- **[MuseScore 4](https://musescore.org/)** — required for `.mscz` parsing and PDF/PNG export. Put it
  on your `PATH` (the app also probes the standard per-OS install locations). MusicXML analysis works
  without it; export then degrades to MusicXML.
- **An Anthropic API key** — only for the optional LLM guide/chord pass (`export ANTHROPIC_API_KEY=...`);
  everything else runs without one.

## Install

```bash
pip install -r requirements.txt
```

## Usage

**Web UI:**

```bash
python app.py
```

Then open the URL it prints.

**CLI:**

```bash
# Analysis report to stdout (Markdown)
python cli.py path/to/score.mscz

# HTML report to a file
python cli.py score.mscz --format html -o report.html

# Fully offline — no Claude calls at all
python cli.py score.mscz --no-llm

# Annotated PDF with note letters + an appended chord-reference page
python cli.py score.mscz --export annotated.pdf --annotations full --chord-guide

# Annotated MusicXML with octave numbers
python cli.py score.mscz --export annotated.musicxml --annotations guided --octaves
```

Accepts `.mscz`, `.mscx`, `.musicxml`, `.mxl`, and `.xml`.

`--annotations` profiles: `full` (every note), `guided` (selective), `clean` (chord labels only).
Add `--octaves` for octave numbers (C4 vs C), `--chord-guide` for the reference page, and
`--text-guide` to append Claude's written guide to a PDF export.

## How it works

```
analyzer/   pure analysis over a parsed music21 score (key, chords, rhythm, structure, difficulty, annotations)
export/     annotated-score export — MusicXML, PDF/PNG via MuseScore, and raw-XML .mscz injection
output/     report renderers (Markdown, HTML)
llm/        optional Claude prompt + explanation
app.py      Flask web UI
cli.py      CLI entry point
```

`.mscz` sources are annotated by editing the raw MuseScore XML so the original engraving is preserved;
every other format is routed through music21.

## Troubleshooting

**Flatpak MuseScore produces empty output (Linux).** With MuseScore installed as a Flatpak, `.mscz`
parsing and PDF/PNG export can silently write empty files (parsing then fails with
`no element found: line 1, column 0`). The sandbox grants only `filesystems=home` by default, but the
tool writes intermediates to `/tmp`. Grant it once:

```bash
flatpak override --user --filesystem=/tmp org.musescore.MuseScore
```

**No MuseScore found.** PDF/PNG export and `.mscz` parsing fall back to MusicXML output rather than
failing — install MuseScore 4 if you need rendered PDFs.

## Configuration

- `PIANO_FORMATTER_LIBRARY_DIRS` — `os.pathsep`-separated score-library directories.
- `PIANO_FORMATTER_EXPORT_DIR` — where annotated exports are written.
- `ANTHROPIC_API_KEY` — enables the optional LLM guide and chord-identification pass.

## Development

```bash
pip install -r requirements.txt
pytest -q      # no API key or MuseScore needed — the LLM is mocked and MuseScore-gated tests skip
```

CI (`.github/workflows/ci.yml`) runs the suite on push/PR.

## License

MIT — see [LICENSE](LICENSE).
