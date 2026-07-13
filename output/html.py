"""HTML report renderer — styled, collapsible, color-coded."""

import html as _html
import json
import re


def _esc(text) -> str:
    """Escape HTML special characters, including quotes (attribute-safe)."""
    return _html.escape(str(text), quote=True)


def _js_string(value) -> str:
    """Serialize a Python value to a JSON literal safe to embed inside a <script> block.

    json.dumps already escapes quotes/backslashes for the JS string context; we additionally
    neutralize ``</`` and ``<!--`` so the value cannot break out of the surrounding
    <script> element or open an HTML comment.
    """
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_html(
    analysis: dict,
    explanation: str | None = None,
    cache_id: str | None = None,
    export_formats: list[str] | None = None,
) -> str:
    """Render a full HTML report from analysis data and optional LLM explanation."""
    meta = analysis.get("metadata", {})
    title = meta.get("title", "Unknown")
    composer = meta.get("composer", "Unknown")
    diff = analysis.get("difficulty", {})
    overall = diff.get("overall_difficulty", 0)
    stats = analysis.get("stats", {})
    rhythm = analysis.get("rhythm", {})
    key = analysis.get("key", {})
    chords = analysis.get("chords", {})
    struct = analysis.get("structure", {})
    annotations = analysis.get("annotations", {})

    # Difficulty color
    diff_color = _difficulty_color(overall)

    # Time sig string
    ts_list = rhythm.get("time_signatures", [])
    ts_str = ", ".join(t.get("signature", "?") for t in ts_list) if ts_list else "4/4"

    # Tempo
    tempos = rhythm.get("tempos", [])
    first_tempo = tempos[0] if tempos else {}
    if first_tempo.get("bpm"):
        bpm_str = f'{first_tempo.get("bpm")} BPM'
    elif first_tempo.get("from_bpm"):
        bpm_str = f'{first_tempo.get("from_bpm")} BPM'
    else:
        bpm_str = "N/A"

    # Duration
    dur = rhythm.get("estimated_duration_seconds")
    dur_str = "N/A"
    if dur:
        mins = int(dur // 60)
        secs = int(dur % 60)
        dur_str = f"{mins}:{secs:02d}"

    # Component scores
    component = diff.get("component_scores", {})
    labels = {
        "rhythm_complexity": "Rhythm Complexity",
        "hand_span": "Hand Span",
        "tempo_density": "Tempo &times; Density",
        "accidentals": "Accidentals",
        "jump_distance": "Jump Distance",
        "chord_density": "Chord Density",
    }

    bars_html = ""
    for k, label in labels.items():
        val = component.get(k, 0)
        pct = val / 10 * 100
        color = _difficulty_color(val)
        bars_html += f"""
        <div class="bar-row">
            <span class="bar-label">{label}</span>
            <div class="bar-track">
                <div class="bar-fill" style="width:{pct}%;background:{color}"></div>
            </div>
            <span class="bar-value">{val}/10</span>
        </div>"""

    # Skills
    skills = diff.get("skills_required", [])
    skills_html = "".join(f"<li>{_esc(s)}</li>" for s in skills) if skills else "<li>None detected</li>"

    # Key disagreement note
    key_note_html = ""
    if key.get("key_disagreement"):
        key_note_html = f"""
        <div class="card warning">
            <h3>Key Ambiguity</h3>
            <p>The written key signature suggests <strong>{_esc(key.get('explicit_key_signature'))}</strong>,
            but the music sounds more like <strong>{_esc(key.get('detected_key'))}</strong>.
            This is common in anime/game music with modal or borrowed-chord writing.</p>
        </div>"""

    # Chords table
    common_chords = chords.get("most_common_chords", [])[:12]
    chords_rows = ""
    for ch in common_chords:
        chords_rows += f"<tr><td>{_esc(ch.get('name', '?'))}</td><td>{_esc(ch.get('count', 0))}</td></tr>"

    # Key changes
    kc = struct.get("key_changes", [])
    kc_html = ""
    if kc:
        kc_rows = ""
        for change in kc:
            kc_rows += f"<tr><td>m.{_esc(change.get('at_measure', '?'))}</td><td>{_esc(change.get('from_key', '?'))}</td><td>{_esc(change.get('to_key', '?'))}</td></tr>"
        kc_html = f"""
        <div class="card">
            <h3>Key Changes</h3>
            <table>
                <tr><th>Measure</th><th>From</th><th>To</th></tr>
                {kc_rows}
            </table>
        </div>"""

    # Dynamics
    dynamics = struct.get("dynamics", [])
    dynamics_html = ""
    if dynamics:
        dyn_items = "".join(
            f"<span class='tag'>m.{_esc(d.get('measure', '?'))}: {_esc(str(d.get('marking', '?')))}</span>"
            for d in dynamics[:20]
        )
        dynamics_html = f"""
        <div class="card">
            <h3>Dynamic Markings</h3>
            <div class="tag-list">{dyn_items}</div>
        </div>"""

    # Note annotations
    simplified = annotations.get("simplified", [])
    annot_lines = "\n".join(_esc(line) for line in simplified[:60])
    more_note = ""
    if len(simplified) > 60:
        more_note = f"<p class='muted'>... and {len(simplified) - 60} more measures</p>"

    # Per-hand stats
    per_hand = stats.get("per_hand", [])
    hand_rows = ""
    for h in per_hand:
        hand_rows += (
            f"<tr><td>{_esc(h.get('part_name', '?'))}</td>"
            f"<td>{_esc(h.get('note_count', 0))}</td>"
            f"<td>{_esc(h.get('lowest_note', '?'))}</td>"
            f"<td>{_esc(h.get('highest_note', '?'))}</td></tr>"
        )

    # LLM section
    llm_html = ""
    if explanation:
        # Minimal markdown -> HTML for the LLM explanation.
        #
        # M9 (documented coupling, not a full markdown parser): this deliberately
        # handles only the narrow subset the explanation prompt emits — `## `
        # headers, `**bold**`, and `- ` bullet lists. It is NOT general markdown
        # (no nested lists, code fences, links, etc.). The input is HTML-escaped
        # first, so this is XSS-safe regardless of LLM output; if the prompt's
        # output format changes, update these regexes in lockstep. Kept hand-
        # rolled to avoid adding a markdown dependency.
        expl = _esc(explanation)
        # Anchor the header match to line start so a literal "## " mid-paragraph
        # isn't promoted to a heading.
        expl = re.sub(r"^## (.+)$", r"<h3>\1</h3>", expl, flags=re.MULTILINE)
        expl = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", expl)
        expl = re.sub(r"^- (.+)$", r"<li>\1</li>", expl, flags=re.MULTILINE)
        expl = re.sub(r"(<li>.*?</li>(\s*<li>.*?</li>)*)", r"<ul>\1</ul>", expl, flags=re.DOTALL)
        expl = re.sub(r"\n{2,}", "</p><p>", expl)
        expl = f"<p>{expl}</p>"
        llm_html = f"""
        <section class="llm-explanation">
            <h2>Beginner-Friendly Guide</h2>
            {expl}
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} — Sheet Music Analysis</title>
<style>
:root {{
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242836;
    --border: #2e3348;
    --text: #e2e4ed;
    --text-muted: #8b8fa3;
    --accent: #7c6ef0;
    --accent-glow: rgba(124, 110, 240, 0.15);
    --green: #4ade80;
    --yellow: #facc15;
    --orange: #fb923c;
    --red: #f87171;
    --radius: 12px;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 2rem;
    max-width: 960px;
    margin: 0 auto;
}}
h1 {{
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 0.25rem;
}}
h2 {{
    font-size: 1.3rem;
    font-weight: 600;
    margin: 2rem 0 1rem;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.4rem;
}}
h3 {{
    font-size: 1.05rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}}
.subtitle {{ color: var(--text-muted); font-size: 1rem; margin-bottom: 1.5rem; }}

/* Stat pills */
.stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}}
.stat-pill {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 1rem;
    text-align: center;
}}
.stat-pill .value {{
    font-size: 1.4rem;
    font-weight: 700;
    display: block;
}}
.stat-pill .label {{
    font-size: 0.75rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}

/* Difficulty badge */
.diff-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 1.6rem;
    font-weight: 800;
    padding: 0.3rem 0.8rem;
    border-radius: 8px;
    background: var(--accent-glow);
}}

/* Bar charts */
.bar-row {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
}}
.bar-label {{
    width: 160px;
    font-size: 0.85rem;
    color: var(--text-muted);
    text-align: right;
    flex-shrink: 0;
}}
.bar-track {{
    flex: 1;
    height: 10px;
    background: var(--surface2);
    border-radius: 5px;
    overflow: hidden;
}}
.bar-fill {{
    height: 100%;
    border-radius: 5px;
    transition: width 0.6s ease;
}}
.bar-value {{
    width: 45px;
    font-size: 0.8rem;
    color: var(--text-muted);
    flex-shrink: 0;
}}

/* Cards */
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.25rem;
    margin-bottom: 1rem;
}}
.export-spinner {{
    display: inline-block;
    width: 10px; height: 10px;
    border: 2px solid var(--accent);
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 4px;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.card.warning {{
    border-color: var(--yellow);
    background: rgba(250, 204, 21, 0.05);
}}

/* Tables */
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}}
th, td {{
    text-align: left;
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--border);
}}
th {{
    color: var(--text-muted);
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}

/* Tags */
.tag-list {{ display: flex; flex-wrap: wrap; gap: 0.4rem; }}
.tag {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.2rem 0.6rem;
    font-size: 0.8rem;
    color: var(--text-muted);
}}

/* Skills list */
.skills-list {{ list-style: none; }}
.skills-list li {{
    padding: 0.35rem 0;
    padding-left: 1.2rem;
    position: relative;
    font-size: 0.9rem;
}}
.skills-list li::before {{
    content: "\\25B8";
    position: absolute;
    left: 0;
    color: var(--accent);
}}

/* Note map */
.note-map {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem 1.25rem;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 0.8rem;
    line-height: 1.7;
    overflow-x: auto;
    white-space: pre;
    max-height: 500px;
    overflow-y: auto;
}}
.muted {{ color: var(--text-muted); font-size: 0.85rem; margin-top: 0.5rem; }}

/* Collapsible */
details {{
    margin-bottom: 1rem;
}}
details summary {{
    cursor: pointer;
    font-weight: 600;
    font-size: 1.05rem;
    padding: 0.5rem 0;
    color: var(--text);
    list-style: none;
}}
details summary::before {{
    content: "\\25B6";
    display: inline-block;
    margin-right: 0.5rem;
    font-size: 0.7rem;
    transition: transform 0.2s;
    color: var(--accent);
}}
details[open] summary::before {{
    transform: rotate(90deg);
}}

/* LLM explanation */
.llm-explanation {{
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    padding: 1.5rem;
    margin-top: 2rem;
    box-shadow: 0 0 20px var(--accent-glow);
}}
.llm-explanation h2 {{
    color: var(--accent);
    border: none;
    margin-top: 0;
}}
.llm-explanation h3 {{
    margin-top: 1.2rem;
    color: var(--accent);
}}
.llm-explanation p {{ margin-bottom: 0.8rem; }}
.llm-explanation ul {{
    margin: 0.5rem 0 0.8rem 1.5rem;
    list-style: disc;
}}
.llm-explanation li {{ margin-bottom: 0.25rem; }}
</style>
</head>
<body>

<header>
    <div style="display:flex;align-items:center;gap:1rem;margin-bottom:0.5rem;">
        <a href="/" style="display:inline-flex;align-items:center;gap:0.3rem;color:var(--accent);text-decoration:none;font-size:0.85rem;font-weight:600;padding:0.35rem 0.75rem;border:1px solid var(--accent);border-radius:8px;transition:all 0.2s;"
           onmouseover="this.style.background='var(--accent)';this.style.color='white'"
           onmouseout="this.style.background='transparent';this.style.color='var(--accent)'">
            &larr; Library
        </a>
    </div>
    <h1>{_esc(title)}</h1>
    <div class="subtitle">{_esc(composer) if composer != 'Unknown' else ''}</div>
</header>

<div class="stats-grid">
    <div class="stat-pill">
        <span class="value" style="color:{diff_color}">{_esc(overall)}</span>
        <span class="label">Difficulty /10</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(key.get('detected_key', '?'))}</span>
        <span class="label">Key</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(ts_str)}</span>
        <span class="label">Time Sig</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(bpm_str)}</span>
        <span class="label">Tempo</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(stats.get('num_measures', '?'))}</span>
        <span class="label">Measures</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(stats.get('total_notes', '?'))}</span>
        <span class="label">Total Notes</span>
    </div>
    <div class="stat-pill">
        <span class="value">{_esc(dur_str)}</span>
        <span class="label">Duration</span>
    </div>
</div>

{key_note_html}

<h2>Difficulty Breakdown</h2>
<div class="card">
    {bars_html}
</div>

<h2>Skills Required</h2>
<ul class="skills-list">
    {skills_html}
</ul>

<h2>Hands</h2>
<div class="card">
    <table>
        <tr><th>Part</th><th>Notes</th><th>Lowest</th><th>Highest</th></tr>
        {hand_rows}
    </table>
</div>

<h2>Chords</h2>
<details open>
    <summary>Most Common Chords</summary>
    <div class="card">
        <table>
            <tr><th>Chord</th><th>Count</th></tr>
            {chords_rows}
        </table>
    </div>
</details>

{kc_html}
{dynamics_html}

<h2>Note Map</h2>
<details>
    <summary>Simplified note letters by measure</summary>
    <div class="note-map">{annot_lines}</div>
    {more_note}
</details>

{llm_html}

{_render_export_controls(cache_id, export_formats)}

<footer style="margin-top:3rem;padding-top:1rem;border-top:1px solid var(--border);color:var(--text-muted);font-size:0.75rem;">
    Generated by Sheet Music Analyzer
</footer>

</body>
</html>"""


def _render_export_controls(cache_id: str | None, export_formats: list[str] | None) -> str:
    """Render the export annotated sheet music section."""
    if not cache_id:
        return ""

    formats = export_formats or ["musicxml"]
    # Put pdf first so it's the default
    if "pdf" in formats:
        formats = ["pdf"] + [f for f in formats if f != "pdf"]
    format_options = ""
    for fmt in formats:
        label = {"musicxml": "MusicXML", "pdf": "PDF", "png": "PNG"}.get(fmt, fmt)
        format_options += f'<option value="{_esc(fmt)}">{_esc(label)}</option>'

    return f"""
<div class="card" style="margin-top:2rem;">
    <h3 style="margin-bottom:0.75rem;">Export Annotated Sheet Music</h3>
    <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:1rem;">
        Download your sheet music with annotations baked in.
    </p>
    <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;">
        <select id="exportProfile" style="padding:0.5rem 0.75rem;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:8px;font-size:0.85rem;">
            <option value="full">Full (every note labeled)</option>
            <option value="guided">Guided (smart sparse labels)</option>
            <option value="clean">Clean (no annotations)</option>
        </select>
        <select id="exportFormat" style="padding:0.5rem 0.75rem;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:8px;font-size:0.85rem;">
            {format_options}
        </select>
        <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem;color:var(--text-muted);cursor:pointer;">
            <input type="checkbox" id="exportOctaves" style="accent-color:var(--accent);width:14px;height:14px;">
            Octave numbers
        </label>
        <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem;color:var(--text-muted);cursor:pointer;">
            <input type="checkbox" id="exportChordGuide" style="accent-color:var(--accent);width:14px;height:14px;">
            Chord reference page
        </label>
        <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem;color:var(--text-muted);cursor:pointer;">
            <input type="checkbox" id="exportTextGuide" style="accent-color:var(--accent);width:14px;height:14px;">
            Beginner guide (PDF)
        </label>
        <button id="exportBtn" onclick="doExport()" style="padding:0.5rem 1.5rem;background:var(--accent);color:white;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:0.85rem;transition:all 0.2s;">
            Export
        </button>
    </div>
    <div id="exportStatus" style="margin-top:0.75rem;font-size:0.85rem;color:var(--text-muted);"></div>
</div>
<script>
async function doExport() {{
    const btn = document.getElementById('exportBtn');
    const status = document.getElementById('exportStatus');
    const chordGuide = document.getElementById('exportChordGuide').checked;
    const textGuide = document.getElementById('exportTextGuide').checked;
    const fmt = document.getElementById('exportFormat').value;

    btn.disabled = true;
    btn.style.opacity = '0.6';

    // Build step descriptions
    const steps = ['Rendering score via MuseScore...'];
    if (chordGuide && fmt === 'pdf') steps.push('Generating chord reference...');
    if (textGuide && fmt === 'pdf') steps.push('Generating beginner guide (Claude API)...');
    if ((chordGuide || textGuide) && fmt === 'pdf') steps.push('Merging PDFs...');
    steps.push('Finalizing...');

    // Animate through steps
    let stepIdx = 0;
    const stepTimer = setInterval(() => {{
        if (stepIdx < steps.length) {{
            status.innerHTML = '<span style="color:var(--accent);font-weight:600;">' +
                '<span class="export-spinner"></span> ' +
                steps[stepIdx] + ' (' + (stepIdx + 1) + '/' + steps.length + ')</span>';
            stepIdx++;
        }}
    }}, fmt === 'pdf' ? 3000 : 1500);

    status.innerHTML = '<span style="color:var(--accent);font-weight:600;">' +
        '<span class="export-spinner"></span> ' + steps[0] + ' (1/' + steps.length + ')</span>';

    try {{
        const resp = await fetch('/export', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                cache_id: {_js_string(cache_id)},
                profile: document.getElementById('exportProfile').value,
                format: fmt,
                octaves: document.getElementById('exportOctaves').checked,
                chord_guide: chordGuide,
                text_guide: textGuide
            }})
        }});
        clearInterval(stepTimer);
        if (!resp.ok) {{
            const err = await resp.json();
            status.innerHTML = '<span style="color:var(--red);font-weight:600;">Export failed: ' + (err.error || 'Unknown error') + '</span>';
            btn.disabled = false;
            btn.style.opacity = '1';
            return;
        }}
        const blob = await resp.blob();
        const cd = resp.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename="?([^"]+)"?/);
        const filename = match ? match[1] : 'annotated.' + fmt;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
        status.innerHTML = '<span style="color:#22c55e;font-weight:600;">Done - saved to Piano/annotated/' + filename + '</span>';
        btn.disabled = false;
        btn.style.opacity = '1';
    }} catch (e) {{
        clearInterval(stepTimer);
        status.innerHTML = '<span style="color:var(--red);font-weight:600;">Export failed: ' + e.message + '</span>';
        btn.disabled = false;
        btn.style.opacity = '1';
    }}
}}
</script>"""


def _difficulty_color(val) -> str:
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "#8b8fa3"
    if val <= 3:
        return "#4ade80"
    if val <= 5:
        return "#facc15"
    if val <= 7:
        return "#fb923c"
    return "#f87171"
