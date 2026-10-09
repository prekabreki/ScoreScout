#!/usr/bin/env python3
"""Sheet Music Analyzer — Web interface."""

import json
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_file

from analyzer.parser import parse_score, SUPPORTED_EXTENSIONS
from llm.explain import generate_explanation
from output.html import render_html
from export.render import get_available_formats
from core.pipeline import run_analysis
from core import export as core_export

log = logging.getLogger("sheet_music_analyzer")

# ── Analysis cache (in-memory + disk persistence) ──
# In-memory: keyed by resolved file path.
#   Stores score (music21, not persisted), analysis dict, explanation, cache_id.
# On disk (.cache/): one JSON per file containing analysis + explanation.
#   Score is re-parsed on demand from the original file.
_analysis_cache: dict[str, dict] = {}
_cache_id_map: dict[str, str] = {}  # cache_id (UUID) -> file_path key
# Flask's dev server is threaded by default, so "Analyze All" can interleave
# with a manual analyze/export. Guard every multi-step mutation of the two
# cache dicts so they can't be left inconsistent (audit L14).
_cache_lock = threading.RLock()


def _path_within(resolved: Path, lib: Path) -> bool:
    """True if ``resolved`` lies inside ``lib`` (containment, not str-prefix).

    Uses Path.is_relative_to so a sibling directory whose name merely starts
    with the library path (e.g. ".../Piano_backup" vs ".../Piano") is rejected.
    """
    try:
        return resolved.is_relative_to(lib.resolve())
    except ValueError:
        return False


def _cache_key_for_path(filepath: str) -> str:
    """Normalize a file path into a stable, machine-independent cache key.

    Returns a path relative to the matched LIBRARY_DIR so the key is identical
    across machines that sync the same files to different absolute locations.
    Falls back to the full resolved path for files outside any library dir.
    """
    resolved = Path(filepath).resolve()
    for lib_dir in LIBRARY_DIRS:
        try:
            return str(resolved.relative_to(lib_dir.resolve()))
        except ValueError:
            continue
    return str(resolved)


def _resolve_cache_key(key: str) -> "Path | None":
    """Resolve a cache key (relative or absolute) to an existing file on this machine."""
    p = Path(key)
    if p.is_absolute():
        return p if p.is_file() else None
    for lib_dir in LIBRARY_DIRS:
        candidate = lib_dir.resolve() / p
        if candidate.is_file():
            return candidate
    return None


def _source_signature(filepath: str | None) -> tuple[float, int] | None:
    """Return (mtime, size) of the source file, or None if unavailable.

    Stored on each cache entry so cached analysis is invalidated when the
    underlying score file changes on disk (audit M14).
    """
    if not filepath:
        return None
    try:
        st = Path(filepath).stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def _disk_cache_path(key: str) -> Path:
    """Return the JSON file path for a cache key."""
    import hashlib
    from config import CACHE_DIR
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{h}.json"


def _cache_save_disk(key: str, entry: dict):
    """Persist analysis + explanation to disk (skip score — not serializable)."""
    from config import CACHE_DIR
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        disk_data = {
            "filepath": key,
            "analysis": entry["analysis"],
            "explanation": entry.get("explanation"),
            "cache_id": entry["cache_id"],
            "timestamp": entry["timestamp"],
            "source_sig": entry.get("source_sig"),
        }
        path = _disk_cache_path(key)
        path.write_text(json.dumps(disk_data, default=str, ensure_ascii=False), encoding="utf-8")
        log.debug("Cache saved to disk: %s", path.name)
    except Exception as e:
        log.warning("Failed to save cache to disk: %s", e)


def _cache_load_disk():
    """Load all cached analyses from disk on startup."""
    from config import CACHE_DIR
    if not CACHE_DIR.is_dir():
        return
    count = 0
    for f in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            stored_path = data["filepath"]
            # Only load if the original file still exists (resolve relative keys across machines)
            if _resolve_cache_key(stored_path) is None:
                log.debug("Cache stale (file gone): %s", stored_path)
                f.unlink(missing_ok=True)
                continue
            # Normalise to a relative key so it matches _cache_key_for_path lookups,
            # even if the JSON was written with an old absolute path.
            key = _cache_key_for_path(stored_path)
            cache_id = data.get("cache_id", str(uuid.uuid4()))
            with _cache_lock:
                _analysis_cache[key] = {
                    "score": None,  # lazy — re-parsed when needed
                    "analysis": data["analysis"],
                    "explanation": data.get("explanation"),
                    "cache_id": cache_id,
                    "key": key,
                    "timestamp": data.get("timestamp", 0),
                    "source_sig": data.get("source_sig"),
                }
                _cache_id_map[cache_id] = key
            count += 1
        except Exception as e:
            log.warning("Failed to load cache file %s: %s", f.name, e)
    if count:
        log.info("Loaded %d cached analyses from disk", count)


def _cache_ensure_score(entry: dict) -> bool:
    """Ensure the entry has a parsed score. Returns False if parsing fails."""
    if entry["score"] is not None:
        return True
    # The entry stores its own cache key, so no reverse-lookup is needed.
    filepath = entry.get("key")
    resolved_path = _resolve_cache_key(filepath) if filepath else None
    if resolved_path is None:
        return False
    try:
        log.info("Re-parsing score for export: %s", resolved_path.name)
        entry["score"] = parse_score(str(resolved_path))
        return True
    except Exception as e:
        log.error("Failed to re-parse score: %s", e)
        return False


def _cache_put(score, analysis, explanation=None, filepath: str | None = None,
               persist: bool = True) -> str:
    """Store analysis in cache (memory + always; disk only when persist).

    Set ``persist=False`` for sources whose key can never be re-resolved on this
    machine — e.g. an uploaded file whose temp copy is deleted right after the
    request (M3). Writing a disk entry for those leaves a permanent stale record
    that can never be re-parsed for export, so we keep them in-memory only.
    """
    cache_id = str(uuid.uuid4())
    key = _cache_key_for_path(filepath) if filepath else cache_id

    entry = {
        "score": score,
        "analysis": analysis,
        "explanation": explanation,
        "cache_id": cache_id,
        "key": key,
        "timestamp": time.time(),
        "source_sig": _source_signature(filepath),
    }
    with _cache_lock:
        _analysis_cache[key] = entry
        _cache_id_map[cache_id] = key
    log.debug("Cached analysis as %s (key=%s, %d entries)", cache_id, key, len(_analysis_cache))

    # Persist to disk only for re-resolvable file-path-keyed entries.
    if filepath and persist:
        _cache_save_disk(key, entry)

    return cache_id


def _cache_get(cache_id: str):
    """Retrieve (score, analysis) by cache_id. Returns None if not found."""
    key = _cache_id_map.get(cache_id, cache_id)
    entry = _analysis_cache.get(key)
    if entry is None:
        return None
    # Ensure score is loaded for export
    if not _cache_ensure_score(entry):
        return None
    return entry["score"], entry["analysis"]


def _cache_get_full(filepath: str) -> dict | None:
    """Retrieve full cache entry by file path. Returns None if not cached.

    Invalidates (and evicts) the entry if the source file's mtime/size no longer
    match what was recorded at analysis time, so editing a score re-analyzes it
    instead of returning stale results (audit M14).
    """
    key = _cache_key_for_path(filepath)
    entry = _analysis_cache.get(key)
    if entry is None:
        return None
    stored_sig = entry.get("source_sig")
    current_sig = _source_signature(filepath)
    # JSON round-trips tuples to lists, so compare element-wise.
    if stored_sig is not None and current_sig is not None and list(stored_sig) != list(current_sig):
        log.info("Cache invalidated (source changed): %s", key)
        with _cache_lock:
            _analysis_cache.pop(key, None)
            cid = entry.get("cache_id")
            if cid:
                _cache_id_map.pop(cid, None)
        _disk_cache_path(key).unlink(missing_ok=True)
        return None
    return entry

# ── Library folders ──
# Override with PIANO_FORMATTER_LIBRARY_DIRS (os.pathsep-separated); otherwise
# fall back to a sensible per-OS default. Windows keeps the OneDrive\Piano
# layout (home-relative, so it works for any user account).
def _default_library_dirs() -> list[Path]:
    if sys.platform == "win32":
        return [Path.home() / "OneDrive" / "Piano" / "mscz"]
    return [Path.home() / "Piano" / "mscz"]


_env_library_dirs = os.environ.get("PIANO_FORMATTER_LIBRARY_DIRS", "")
LIBRARY_DIRS = (
    [Path(p) for p in _env_library_dirs.split(os.pathsep) if p]
    if _env_library_dirs
    else _default_library_dirs()
)


def _is_mscz_source(filepath: str) -> bool:
    """Check if the source file is a .mscz."""
    return Path(filepath).suffix.lower() == ".mscz"


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-35s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler("analyzer_debug.log", mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-8s  %(message)s"))
    root.addHandler(ch)
    logging.getLogger("music21").setLevel(logging.WARNING)


def scan_library() -> list[dict]:
    """Scan the hardcoded library dirs for supported files."""
    files = []
    for folder in LIBRARY_DIRS:
        if not folder.is_dir():
            log.warning("Library folder not found: %s", folder)
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                cached = _cache_get_full(str(f)) is not None
                files.append({
                    "name": f.stem,
                    "ext": f.suffix.lower(),
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "folder": folder.name,
                    "cached": cached,
                })
    log.info("Library scan: found %d files across %d folders", len(files), len(LIBRARY_DIRS))
    return files


app = Flask(__name__)
# Cap upload size — scores are small (MusicXML/.mxl/.mscz rarely exceed a few MB).
# Anything larger is rejected with HTTP 413 before it is read into memory.
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB

UPLOAD_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sheet Music Analyzer</title>
<style>
:root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242836;
    --border: #2e3348;
    --text: #e2e4ed;
    --text-muted: #8b8fa3;
    --accent: #7c6ef0;
    --accent-hover: #6a5bd6;
    --accent-glow: rgba(124, 110, 240, 0.2);
    --green: #4ade80;
    --red: #f87171;
    --yellow: #facc15;
    --radius: 14px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 2rem;
}
.page {
    max-width: 720px;
    margin: 0 auto;
}
h1 {
    font-size: 2.2rem;
    font-weight: 800;
    text-align: center;
    margin-bottom: 0.3rem;
    background: linear-gradient(135deg, var(--accent), #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.subtitle {
    color: var(--text-muted);
    font-size: 0.95rem;
    text-align: center;
    margin-bottom: 2rem;
}

/* ── Tabs ── */
.tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
    gap: 0;
}
.tab {
    padding: 0.6rem 1.5rem;
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text-muted);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    transition: all 0.2s;
}
.tab:hover { color: var(--text); }
.tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
}
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── Library file list ── */
.search-box {
    width: 100%;
    padding: 0.65rem 1rem;
    font-size: 0.95rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    outline: none;
    margin-bottom: 1rem;
    transition: border-color 0.2s;
}
.search-box:focus { border-color: var(--accent); }
.search-box::placeholder { color: var(--text-muted); opacity: 0.5; }

.file-list {
    max-height: 420px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
}
.file-list::-webkit-scrollbar { width: 6px; }
.file-list::-webkit-scrollbar-track { background: transparent; }
.file-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

.file-item {
    display: flex;
    align-items: center;
    padding: 0.7rem 1rem;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
    gap: 0.75rem;
}
.file-item:last-child { border-bottom: none; }
.file-item:hover { background: var(--surface2); }
.file-item.selected {
    background: var(--accent-glow);
    border-left: 3px solid var(--accent);
}
.file-item .fi-name {
    flex: 1;
    font-size: 0.9rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.file-item .fi-ext {
    font-size: 0.7rem;
    font-weight: 600;
    color: var(--accent);
    background: var(--accent-glow);
    padding: 0.15rem 0.45rem;
    border-radius: 4px;
    text-transform: uppercase;
    flex-shrink: 0;
}
.file-item .fi-folder {
    font-size: 0.75rem;
    color: var(--text-muted);
    flex-shrink: 0;
}
.file-item .fi-cached {
    font-size: 0.65rem;
    font-weight: 600;
    color: #22c55e;
    background: rgba(34, 197, 94, 0.1);
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    flex-shrink: 0;
}
.file-item .fi-size {
    font-size: 0.75rem;
    color: var(--text-muted);
    flex-shrink: 0;
    width: 55px;
    text-align: right;
}
.empty-msg {
    padding: 2rem;
    text-align: center;
    color: var(--text-muted);
    font-size: 0.9rem;
}

/* ── Drop zone ── */
.dropzone {
    border: 2px dashed var(--border);
    border-radius: var(--radius);
    padding: 3rem 2rem;
    cursor: pointer;
    transition: all 0.25s ease;
    background: var(--surface);
    position: relative;
    text-align: center;
}
.dropzone:hover, .dropzone.dragover {
    border-color: var(--accent);
    background: rgba(124, 110, 240, 0.05);
    box-shadow: 0 0 30px var(--accent-glow);
}
.dropzone-icon { font-size: 3rem; margin-bottom: 1rem; opacity: 0.6; }
.dropzone-text { font-size: 1.05rem; color: var(--text-muted); margin-bottom: 0.3rem; }
.dropzone-hint { font-size: 0.8rem; color: var(--text-muted); opacity: 0.6; }
.dropzone input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer;
}
.file-name-display {
    margin-top: 1rem;
    font-size: 0.9rem;
    color: var(--green);
    min-height: 1.4em;
    text-align: center;
}

/* ── Shared bottom section ── */
.bottom {
    text-align: center;
    margin-top: 1.5rem;
}
.options {
    display: flex;
    gap: 1.5rem;
    justify-content: center;
    margin-bottom: 1.2rem;
    flex-wrap: wrap;
}
.option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    color: var(--text-muted);
    cursor: pointer;
}
.option input[type="checkbox"] {
    accent-color: var(--accent);
    width: 16px; height: 16px;
}
.analyze-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.85rem 2.5rem;
    font-size: 1.05rem;
    font-weight: 600;
    color: white;
    background: var(--accent);
    border: none;
    border-radius: 10px;
    cursor: pointer;
    transition: all 0.2s;
}
.analyze-btn:hover:not(:disabled) {
    background: var(--accent-hover);
    box-shadow: 0 4px 20px var(--accent-glow);
    transform: translateY(-1px);
}
.analyze-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.progress {
    margin-top: 1.5rem;
    min-height: 2.5rem;
    text-align: center;
}
.spinner {
    display: inline-block;
    width: 20px; height: 20px;
    border: 2.5px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 0.5rem;
}
@keyframes spin { to { transform: rotate(360deg); } }
.status-text { color: var(--text-muted); font-size: 0.9rem; }
.error-text {
    color: var(--red); font-size: 0.9rem;
    max-width: 580px; margin: 0 auto; text-align: left;
    background: rgba(248, 113, 113, 0.08);
    border: 1px solid rgba(248, 113, 113, 0.2);
    border-radius: 8px; padding: 0.8rem 1rem;
    white-space: pre-wrap; word-break: break-word;
}
</style>
</head>
<body>

<div class="page">
    <h1>Sheet Music Analyzer</h1>
    <p class="subtitle">Pick a .mscz from your library or drop a file in</p>

    <!-- Tabs -->
    <div class="tabs">
        <button class="tab active" data-tab="library">My Library</button>
        <button class="tab" data-tab="upload">Upload File</button>
    </div>

    <!-- Library tab -->
    <div class="tab-panel active" id="panel-library">
        <div style="display:flex;gap:0.5rem;align-items:center;">
            <input type="text" class="search-box" id="searchBox"
                   placeholder="Search your files..." autocomplete="off" style="flex:1;">
            <button id="refreshBtn" title="Rescan library folders"
                    style="padding:0.5rem 0.75rem;background:var(--surface2);color:var(--text-muted);border:1px solid var(--border);border-radius:8px;cursor:pointer;font-size:0.85rem;flex-shrink:0;transition:all 0.15s;"
                    onmouseover="this.style.background='var(--accent)';this.style.color='white';this.style.borderColor='var(--accent)'"
                    onmouseout="this.style.background='var(--surface2)';this.style.color='var(--text-muted)';this.style.borderColor='var(--border)'">&#x21bb; Refresh</button>
        </div>
        <div class="file-list" id="fileList"></div>
    </div>

    <!-- Upload tab -->
    <div class="tab-panel" id="panel-upload">
        <div class="dropzone" id="dropzone">
            <div class="dropzone-icon">&#119070;</div>
            <div class="dropzone-text">Drop your sheet music here, or click to browse</div>
            <div class="dropzone-hint">.mscz &middot; .musicxml &middot; .mxl &middot; .mscx</div>
            <input type="file" id="fileInput" name="file"
                   accept=".musicxml,.mxl,.xml,.mscz,.mscx">
        </div>
        <div class="file-name-display" id="uploadFileName"></div>
    </div>

    <!-- Shared bottom -->
    <div class="bottom">
        <div class="options">
            <label class="option">
                <input type="checkbox" id="skipLlm">
                Skip AI explanation
            </label>
        </div>
        <button class="analyze-btn" id="analyzeBtn" disabled>Analyze</button>
        <button class="analyze-btn" id="reanalyzeBtn" disabled style="background:var(--surface2);color:var(--text);border:1px solid var(--border);">Re-analyze</button>
        <button class="analyze-btn" id="analyzeAllBtn" style="background:var(--surface2);color:var(--text);border:1px solid var(--border);">Analyze All</button>
        <div class="progress" id="progress"></div>
    </div>
</div>

<script>
// ── Library data injected by server ──
const LIBRARY = {{ library_json }};

// ── State ──
let selectedPath = null;   // library file path
let uploadedFile = null;   // File object from drag/drop
let mode = 'library';      // 'library' | 'upload'

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        const panel = document.getElementById('panel-' + tab.dataset.tab);
        panel.classList.add('active');
        mode = tab.dataset.tab;
        updateButton();
    });
});

// ── Library list ──
const fileListEl = document.getElementById('fileList');
const searchBox  = document.getElementById('searchBox');

function renderList(filter) {
    const q = (filter || '').toLowerCase();
    const matches = LIBRARY.filter(f =>
        f.name.toLowerCase().includes(q) || f.folder.toLowerCase().includes(q)
    );
    if (!matches.length) {
        fileListEl.innerHTML = '<div class="empty-msg">' +
            (LIBRARY.length ? 'No matches' : 'No supported files found in library folders') +
            '</div>';
        return;
    }
    fileListEl.innerHTML = matches.map(f =>
        `<div class="file-item${f.path === selectedPath ? ' selected' : ''}" data-path="${escAttr(f.path)}">
            <span class="fi-ext">${esc(f.ext.replace('.',''))}</span>
            <span class="fi-name">${esc(f.name)}</span>
            ${f.cached ? '<span class="fi-cached">Analyzed</span>' : ''}
            <span class="fi-folder">${esc(f.folder)}</span>
            <span class="fi-size">${f.size_kb} KB</span>
        </div>`
    ).join('');

    fileListEl.querySelectorAll('.file-item').forEach(el => {
        el.addEventListener('click', async () => {
            selectedPath = el.dataset.path;
            uploadedFile = null;
            renderList(searchBox.value);
            updateButton();

            // If already analyzed, open results immediately on click
            const f = LIBRARY.find(f => f.path === el.dataset.path);
            if (f && f.cached) {
                analyzeBtn.disabled = true;
                progress.innerHTML = '<span class="spinner"></span><span class="status-text">Loading cached results...</span>';
                const skipLlm = document.getElementById('skipLlm').checked ? '1' : '';
                try {
                    const resp = await fetch('/analyze', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ path: selectedPath, skip_llm: skipLlm })
                    });
                    if (resp.ok) {
                        const html = await resp.text();
                        document.open();
                        document.write(html);
                        document.close();
                        return;
                    }
                } catch (e) {}
                progress.textContent = '';
                analyzeBtn.disabled = false;
            }
        });
    });
}

searchBox.addEventListener('input', () => renderList(searchBox.value));
renderList('');

// ── Refresh library ──
document.getElementById('refreshBtn').addEventListener('click', async () => {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = '...';
    try {
        const resp = await fetch('/library');
        if (resp.ok) {
            const fresh = await resp.json();
            LIBRARY.length = 0;
            fresh.forEach(f => LIBRARY.push(f));
            selectedPath = null;
            renderList(searchBox.value);
            updateButton();
        }
    } catch (e) {}
    btn.innerHTML = '&#x21bb; Refresh';
    btn.disabled = false;
});

// ── Upload / drag-drop ──
const dropzone  = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const uploadFN  = document.getElementById('uploadFileName');

['dragenter','dragover'].forEach(e =>
    dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.add('dragover'); }));
['dragleave','drop'].forEach(e =>
    dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.remove('dragover'); }));

dropzone.addEventListener('drop', ev => {
    if (ev.dataTransfer.files.length) {
        fileInput.files = ev.dataTransfer.files;
        onUploadFile();
    }
});
fileInput.addEventListener('change', onUploadFile);

function onUploadFile() {
    const f = fileInput.files[0];
    if (f) {
        uploadedFile = f;
        selectedPath = null;
        uploadFN.textContent = f.name + ' (' + (f.size / 1024).toFixed(1) + ' KB)';
        updateButton();
    }
}

// ── Button state ──
const analyzeBtn = document.getElementById('analyzeBtn');
const reanalyzeBtn = document.getElementById('reanalyzeBtn');
function updateButton() {
    if (mode === 'library') {
        analyzeBtn.disabled = !selectedPath;
        // Re-analyze enabled only for already-cached library files
        const sel = LIBRARY.find(f => f.path === selectedPath);
        reanalyzeBtn.disabled = !(selectedPath && sel && sel.cached);
        reanalyzeBtn.style.display = 'inline-block';
    } else {
        analyzeBtn.disabled = !uploadedFile;
        reanalyzeBtn.disabled = true;
        reanalyzeBtn.style.display = 'none';
    }
}

// ── Submit ──
const progress = document.getElementById('progress');

analyzeBtn.addEventListener('click', async () => {
    analyzeBtn.disabled = true;
    progress.innerHTML = '<span class="spinner"></span><span class="status-text">Analyzing — this may take a moment...</span>';

    const skipLlm = document.getElementById('skipLlm').checked ? '1' : '';

    try {
        let resp;
        if (mode === 'library' && selectedPath) {
            // Send path to server
            resp = await fetch('/analyze', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ path: selectedPath, skip_llm: skipLlm })
            });
        } else if (uploadedFile) {
            const data = new FormData();
            data.append('file', uploadedFile);
            data.append('skip_llm', skipLlm);
            resp = await fetch('/analyze/upload', { method: 'POST', body: data });
        } else {
            return;
        }

        if (!resp.ok) {
            const err = await resp.json();
            progress.innerHTML = '<div class="error-text">' + esc(err.error || 'Unknown error') + '</div>';
            analyzeBtn.disabled = false;
            return;
        }
        const html = await resp.text();
        document.open();
        document.write(html);
        document.close();
    } catch (e) {
        progress.innerHTML = '<div class="error-text">Request failed: ' + esc(e.message) + '</div>';
        analyzeBtn.disabled = false;
    }
});

// ── Re-analyze (force) ──
reanalyzeBtn.addEventListener('click', async () => {
    if (!selectedPath) return;
    reanalyzeBtn.disabled = true;
    analyzeBtn.disabled = true;
    progress.innerHTML = '<span class="spinner"></span><span class="status-text">Re-analyzing (cache cleared)...</span>';

    const skipLlm = document.getElementById('skipLlm').checked ? '1' : '';
    try {
        const resp = await fetch('/analyze', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path: selectedPath, skip_llm: skipLlm, force: '1' })
        });
        if (!resp.ok) {
            const err = await resp.json();
            progress.innerHTML = '<div class="error-text">' + esc(err.error || 'Unknown error') + '</div>';
            reanalyzeBtn.disabled = false;
            analyzeBtn.disabled = false;
            return;
        }
        const html = await resp.text();
        document.open();
        document.write(html);
        document.close();
    } catch (e) {
        progress.innerHTML = '<div class="error-text">Request failed: ' + esc(e.message) + '</div>';
        reanalyzeBtn.disabled = false;
        analyzeBtn.disabled = false;
    }
});

// ── Analyze All ──
const analyzeAllBtn = document.getElementById('analyzeAllBtn');
let batchRunning = false;

analyzeAllBtn.addEventListener('click', async () => {
    if (batchRunning) return;
    batchRunning = true;
    analyzeAllBtn.disabled = true;
    analyzeBtn.disabled = true;
    analyzeAllBtn.textContent = 'Running...';

    const skipLlm = document.getElementById('skipLlm').checked ? '1' : '';
    const pending = LIBRARY.filter(f => !f.cached);
    const total = pending.length;

    if (total === 0) {
        progress.innerHTML = '<span style="color:#22c55e;font-weight:600;">All files already analyzed!</span>';
        batchRunning = false;
        analyzeAllBtn.disabled = false;
        analyzeAllBtn.textContent = 'Analyze All';
        updateButton();
        return;
    }

    let done = 0;
    let failed = 0;

    for (const f of pending) {
        done++;
        progress.innerHTML =
            '<span class="spinner"></span>' +
            '<span class="status-text">Batch: ' + done + '/' + total + ' — ' + esc(f.name) + '</span>' +
            (failed ? '<span style="color:var(--red);margin-left:8px;">(' + failed + ' failed)</span>' : '');

        try {
            const resp = await fetch('/analyze/batch-one', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ path: f.path, skip_llm: skipLlm })
            });
            if (resp.ok) {
                f.cached = true;
            } else {
                const err = await resp.json().catch(() => ({}));
                console.warn('Failed:', f.name, err.error || resp.status);
                failed++;
            }
        } catch (e) {
            console.warn('Failed:', f.name, e.message);
            failed++;
        }
    }

    progress.innerHTML = '<span style="color:#22c55e;font-weight:600;">Batch complete: ' +
        (total - failed) + '/' + total + ' analyzed' +
        (failed ? ' (' + failed + ' failed)' : '') + '</span>';

    renderList(searchBox.value);  // refresh badges
    batchRunning = false;
    analyzeAllBtn.disabled = false;
    analyzeAllBtn.textContent = 'Analyze All';
    updateButton();
});

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escAttr(s) { return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
</script>
</body>
</html>"""


def _run_analysis_pipeline(filepath: str, original_name: str, skip_llm: bool,
                           force: bool = False, persist: bool = True):
    """Full pipeline: analyze -> cache -> optional LLM -> render HTML.

    If the file was previously analyzed, returns cached results instantly.
    Set force=True to discard cache and re-analyze from scratch.
    Set persist=False for transient sources (uploads) so no stale disk-cache
    entry is written for a path that won't exist after the request (M3).
    """
    if force:
        key = _cache_key_for_path(filepath)
        with _cache_lock:
            old = _analysis_cache.pop(key, None)
            if old:
                old_id = old.get("cache_id")
                if old_id:
                    _cache_id_map.pop(old_id, None)
        if old:
            disk = _disk_cache_path(key)
            disk.unlink(missing_ok=True)
            log.info("Cleared cache for %s (force re-analysis)", original_name)

    # Check cache first
    cached = _cache_get_full(filepath)
    if cached is not None:
        log.info("Cache hit for %s — skipping re-analysis", original_name)
        analysis = cached["analysis"]
        explanation = cached["explanation"]
        cache_id = cached["cache_id"]

        # If we have no explanation yet and LLM is requested, generate it now
        if explanation is None and not skip_llm:
            try:
                explanation = generate_explanation(analysis)
                cached["explanation"] = explanation
                # Persist updated explanation to disk
                key = _cache_key_for_path(filepath)
                _cache_save_disk(key, cached)
            except Exception as e:
                log.warning("LLM explanation failed: %s", e)
                log.debug(traceback.format_exc())

        export_formats = get_available_formats()
        return render_html(analysis, explanation, cache_id=cache_id, export_formats=export_formats)

    # Cache miss — run full analysis
    analysis, score = run_analysis(
        filepath, use_llm=not skip_llm, title_fallback=original_name,
    )

    explanation = None
    if not skip_llm:
        try:
            explanation = generate_explanation(analysis)
        except Exception as e:
            log.warning("LLM explanation failed: %s", e)
            log.debug(traceback.format_exc())

    cache_id = _cache_put(score, analysis, explanation=explanation, filepath=filepath,
                          persist=persist)

    export_formats = get_available_formats()
    return render_html(analysis, explanation, cache_id=cache_id, export_formats=export_formats)


@app.route("/library", methods=["GET"])
def library_json():
    """Return fresh library file list as JSON."""
    files = scan_library()
    return jsonify(files)


@app.route("/")
def index():
    files = scan_library()
    page = UPLOAD_PAGE.replace("{{ library_json }}", json.dumps(files))
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/analyze", methods=["POST"])
def analyze_library():
    """Analyze a file from the hardcoded library folders."""
    data = request.get_json(force=True)
    filepath = data.get("path", "")
    skip_llm = data.get("skip_llm") == "1"
    force = data.get("force") == "1"

    # Security: only allow files inside the library dirs. Use is_relative_to
    # (not str-prefix matching) so a sibling dir like ".../Piano_backup" can't
    # masquerade as an allowed ".../Piano" path.
    resolved = Path(filepath).resolve()
    allowed = any(
        _path_within(resolved, lib) for lib in LIBRARY_DIRS
    )
    if not allowed:
        log.warning("Blocked path outside library dirs: %s", filepath)
        return jsonify(error="File is not inside an allowed library folder."), 403

    if not resolved.is_file():
        return jsonify(error=f"File not found: {filepath}"), 404

    try:
        log.info("Analyzing library file: %s%s", filepath, " (force)" if force else "")
        html = _run_analysis_pipeline(str(resolved), resolved.name, skip_llm, force=force)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        log.error("Analysis failed: %s", e)
        log.debug(traceback.format_exc())
        return jsonify(error=str(e)), 500


@app.route("/analyze/batch-one", methods=["POST"])
def analyze_batch_one():
    """Lightweight analysis for batch mode — analyze + cache, return JSON (no HTML)."""
    data = request.get_json(force=True)
    filepath = data.get("path", "")
    skip_llm = data.get("skip_llm") == "1"

    resolved = Path(filepath).resolve()
    allowed = any(
        _path_within(resolved, lib) for lib in LIBRARY_DIRS
    )
    if not allowed:
        return jsonify(error="Not in library"), 403
    if not resolved.is_file():
        return jsonify(error="File not found"), 404

    # Skip if already cached
    if _cache_get_full(str(resolved)) is not None:
        return jsonify(status="cached")

    try:
        log.info("Batch analyzing: %s", resolved.name)
        analysis, score = run_analysis(
            str(resolved), use_llm=not skip_llm, title_fallback=resolved.name,
        )

        explanation = None
        if not skip_llm:
            try:
                explanation = generate_explanation(analysis)
            except Exception as e:
                log.warning("LLM explanation failed: %s", e)

        _cache_put(score, analysis, explanation=explanation, filepath=str(resolved))
        return jsonify(status="ok", title=analysis.get("metadata", {}).get("title", ""))
    except Exception as e:
        log.error("Batch analysis failed for %s: %s", resolved.name, e)
        return jsonify(error=str(e)), 500


@app.route("/analyze/upload", methods=["POST"])
def analyze_upload():
    """Analyze an uploaded file."""
    if "file" not in request.files:
        return jsonify(error="No file uploaded"), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify(error="Empty filename"), 400

    ext = Path(uploaded.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return jsonify(
            error=f"Unsupported file type '{ext}'.\nSupported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        ), 400

    skip_llm = request.form.get("skip_llm") == "1"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        uploaded.save(tmp)
        tmp.close()
        log.info("Uploaded file saved to %s (%s)", tmp.name, uploaded.filename)
        # persist=False: the temp file is unlinked in `finally`, so a disk-cache
        # entry keyed on it could never be re-resolved for export (M3).
        html = _run_analysis_pipeline(tmp.name, uploaded.filename, skip_llm, persist=False)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        log.error("Analysis failed: %s", e)
        log.debug(traceback.format_exc())
        return jsonify(error=str(e)), 500
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@app.route("/export", methods=["POST"])
def export_annotated():
    """Export annotated sheet music as MusicXML, PDF, or PNG."""
    data = request.get_json(force=True)
    cache_id = data.get("cache_id", "")
    profile_name = data.get("profile", "full")
    fmt = data.get("format", "musicxml")
    include_octaves = data.get("octaves", False)
    include_chord_guide = data.get("chord_guide", False)
    include_text_guide = data.get("text_guide", False)

    cached = _cache_get(cache_id)
    if cached is None:
        return jsonify(error="Analysis expired or not found. Please re-analyze the file."), 404

    score, analysis = cached

    # Resolve original file path for mscz lookup.
    # The cache key is a library-relative path (or absolute for files outside any
    # library dir), NOT a usable filesystem path — resolve it to an absolute,
    # existing file before using it as a source path. May be None if the source
    # no longer exists on this machine (e.g. a deleted upload temp file).
    cache_key = _cache_id_map.get(cache_id, cache_id)
    resolved_source = _resolve_cache_key(cache_key)
    source_path = str(resolved_source) if resolved_source else None

    if source_path is None and _is_mscz_source(cache_key):
        log.warning(
            "Original .mscz source for cache key %r not found on this machine - "
            "falling back to music21 export path", cache_key,
        )

    title = analysis.get("metadata", {}).get("title", "annotated")
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()

    # On-disk filenames carry a per-request token so concurrent web requests
    # for the same title can't clobber each other's intermediate/output files
    # in the shared EXPORT_DIR (M5). The user-facing download name stays clean.
    token = uuid.uuid4().hex[:8]
    disk_stem = f"{safe_title}_{profile_name}_{token}"

    from config import EXPORT_DIR
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Reuse a cached explanation for the optional text guide; persist any newly
    # generated one so we never call Claude twice for the same score.
    cache_entry = _analysis_cache.get(cache_key)
    explanation = cache_entry.get("explanation") if cache_entry else None

    def _store_explanation(text: str):
        if cache_entry is not None:
            cache_entry["explanation"] = text
            _cache_save_disk(cache_key, cache_entry)

    try:
        result = core_export.export_annotated(
            score, analysis, fmt,
            out_path=str(EXPORT_DIR / f"{disk_stem}.{fmt}"),
            profile_name=profile_name,
            include_octaves=include_octaves,
            source_path=source_path,
            include_chord_guide=include_chord_guide,
            include_text_guide=include_text_guide,
            explanation=explanation,
            on_explanation=_store_explanation,
        )
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        log.error("Export failed: %s", e)
        log.debug(traceback.format_exc())
        return jsonify(error=str(e)), 500

    filename = f"{safe_title}_{profile_name}.{result.fmt}"
    return send_file(result.path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    setup_logging()
    _cache_load_disk()
    port = int(os.environ.get("PORT", 5000))
    log.info("Starting web server on http://localhost:%d", port)
    print(f"\n  Sheet Music Analyzer running at: http://localhost:{port}\n")
    webbrowser.open(f"http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
