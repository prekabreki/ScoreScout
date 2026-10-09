"""Inject lyric annotations directly into .mscz files.

Preserves MuseScore's original layout by modifying the XML inside the
.mscz ZIP archive rather than round-tripping through music21.

Flow:
  1. The caller passes the score's key and chord analysis alongside the .mscz.
  2. This module unzips the .mscz and walks the XML, building the shared
     ``PartEvent`` stream: it converts MuseScore MIDI ints to key-spelled
     music21 pitches and tracks (measure, beat) through tuplets and grace notes.
  3. ``export.labeling`` selects the notes the profile wants and computes the
     label text, and this module writes them back as <Lyrics> elements.
  4. The modified .mscz is re-zipped and rendered via MuseScore CLI.

Only the XML reader/writer lives here; both label spelling and the "which
notes to label" policy are the shared core, so this path agrees with the
music21 path in ``export.annotate``.
"""

import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from export.labeling import (
    PartEvent,
    label_for,
    select_labels,
    spell_midi,
)
from export.profiles import AnnotationProfile, get_profile

log = logging.getLogger(__name__)

# MuseScore duration names → quarter-note lengths
_DUR_MAP = {
    "maxima": 32.0, "long": 16.0, "breve": 8.0,
    "whole": 4.0, "half": 2.0, "quarter": 1.0,
    "eighth": 0.5, "16th": 0.25, "32nd": 0.125,
    "64th": 0.0625, "128th": 0.03125,
}

# MuseScore marks a grace chord with one of these child tags; it sounds with
# zero metrical duration, so it must not advance the running beat counter.
_GRACE_TAGS = frozenset({
    "acciaccatura", "appoggiatura",
    "grace4", "grace16", "grace32",
    "grace8after", "grace16after", "grace32after",
})


def _is_grace_chord(chord_el: ET.Element) -> bool:
    """True if the chord carries a MuseScore grace-note marker."""
    return any(child.tag in _GRACE_TAGS for child in chord_el)


def _tuplet_ratio(tuplet_el: ET.Element) -> float:
    """Duration multiplier for notes inside a <Tuplet> element.

    MuseScore writes the tuplet's ``actualNotes`` (how many are played) and
    ``normalNotes`` (how many the written value would normally be) as child
    elements. A note inside a 3:2 triplet therefore lasts
    ``normalNotes / actualNotes`` of its written duration. Returns 1.0 when
    the ratio is absent or unusable.
    """
    actual_el = tuplet_el.find("actualNotes")
    normal_el = tuplet_el.find("normalNotes")
    if actual_el is None or normal_el is None:
        return 1.0
    try:
        actual = float(actual_el.text)
        normal = float(normal_el.text)
    except (TypeError, ValueError):
        return 1.0
    if actual <= 0 or normal <= 0:
        return 1.0
    return normal / actual


def _dur_quarters(chord_el: ET.Element, tuplet_ratio: float = 1.0) -> float:
    """Calculate duration in quarter-note units from a Chord/Rest element.

    Accounts for durationType + dots, scales by the enclosing tuplet ratio
    (``tuplet_ratio``, from ``_tuplet_ratio``), and returns 0.0 for grace
    notes, which carry no metrical duration. This keeps the running beat
    counter in ``_collect_events`` aligned with music21 so (measure, beat)-keyed
    chord-name lookups land on the right notes. The music21 export path
    (export/annotate.py) is unaffected.
    """
    if _is_grace_chord(chord_el):
        return 0.0
    dur_type = chord_el.find("durationType")
    if dur_type is None:
        return 1.0 * tuplet_ratio
    base = _DUR_MAP.get(dur_type.text, 1.0)
    dots_el = chord_el.find("dots")
    dots = int(dots_el.text) if dots_el is not None else 0
    total = base
    add = base
    for _ in range(dots):
        add /= 2
        total += add
    return total * tuplet_ratio


def inject_mscz(
    mscz_path: str,
    output_path: str,
    profile_name: str = "full",
    octaves: bool = False,
    chord_info: dict | None = None,
    key_info: dict | None = None,
) -> str:
    """Inject lyric annotations into a .mscz file.

    Args:
        mscz_path: Path to the original .mscz file.
        output_path: Where to write the modified .mscz.
        profile_name: 'full', 'guided', or 'clean'.
        octaves: Include octave numbers in labels.
        chord_info: Chord analysis dict for chord name lookup.
        key_info: Key analysis dict; its signed sharps/flats count spells
            MuseScore MIDI ints the same way the music21 path spells notes.

    Returns:
        Path to the output .mscz file.
    """
    if profile_name == "clean":
        # Clean = no annotations, just copy
        shutil.copy2(mscz_path, output_path)
        return output_path

    work_dir = tempfile.mkdtemp(prefix="mscz_inject_")
    try:
        # Extract
        with zipfile.ZipFile(mscz_path, "r") as z:
            z.extractall(work_dir)

        # Find the .mscx file
        mscx_name = None
        for name in os.listdir(work_dir):
            if name.endswith(".mscx"):
                mscx_name = name
                break
        if mscx_name is None:
            # Check subdirectories
            for dirpath, _, filenames in os.walk(work_dir):
                for fname in filenames:
                    if fname.endswith(".mscx"):
                        mscx_name = os.path.relpath(os.path.join(dirpath, fname), work_dir)
                        break
                if mscx_name:
                    break

        if mscx_name is None:
            raise RuntimeError("No .mscx file found inside %s" % mscz_path)

        mscx_path = os.path.join(work_dir, mscx_name)
        tree = ET.parse(mscx_path)
        root = tree.getroot()

        # Find the Score element containing content staves
        score_el = root.find(".//Score")
        if score_el is None:
            raise RuntimeError("No <Score> element in .mscx")

        content_staves = [c for c in score_el if c.tag == "Staff"]
        if not content_staves:
            raise RuntimeError("No content <Staff> elements in Score")

        key_sharps = key_info.get("sharps_flats_count", 0) if key_info else 0
        profile = get_profile(profile_name)
        total_injected = _inject_labels(
            content_staves, profile, key_sharps, chord_info, octaves,
        )

        log.info("Injected %d lyric labels into %s", total_injected, Path(mscz_path).name)

        # Write modified XML
        tree.write(mscx_path, xml_declaration=True, encoding="UTF-8")

        # Re-zip
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _, filenames in os.walk(work_dir):
                for fname in filenames:
                    filepath = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(filepath, work_dir)
                    z.write(filepath, arcname)

        log.info("Modified .mscz written: %s (%.1f KB)",
                 output_path, Path(output_path).stat().st_size / 1024)
        return output_path

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _collect_events(
    content_staves: list[ET.Element],
    key_sharps: int,
) -> list[list[PartEvent]]:
    """Flatten each staff's XML into the shared :class:`PartEvent` stream.

    This is the only piece of the .mscz path that knows the XML shape: it walks
    measures/voices in document order, advances a beat counter through tuplets
    and grace notes, and converts each ``<pitch>`` MIDI int to a key-spelled
    music21 ``Pitch``. Selection and label text are then the shared core.
    """
    parts: list[list[PartEvent]] = []

    # One staff becomes one part. The guided policy's per-part rules (first
    # note, first accidental, rest re-entry, next-note leap) then run per staff
    # exactly as they run per music21 Part on the other path.
    for staff in content_staves:
        events: list[PartEvent] = []
        measures = [c for c in staff if c.tag == "Measure"]
        measure_num = 0

        for measure in measures:
            measure_num += 1

            # Collect voice containers. Some .mscx files wrap notes in
            # <voice> elements, others put Chord/Rest directly in <Measure>.
            voices: list[ET.Element] = []
            has_voice_tags = any(c.tag == "voice" for c in measure)
            if has_voice_tags:
                voices = [c for c in measure if c.tag == "voice"]
            else:
                # Treat the measure itself as a single voice
                voices = [measure]

            for voice in voices:
                beat = 1.0  # beat position (1-indexed like music21)
                # Nested tuplets are written as <Tuplet> ... <Tuplet> ... and
                # closed innermost-first with <endTuplet/>; the stack multiplies
                # their ratios together.
                tuplet_stack: list[float] = []

                for elem in voice:
                    if elem.tag == "Tuplet":
                        tuplet_stack.append(_tuplet_ratio(elem))
                        continue
                    if elem.tag == "endTuplet":
                        if tuplet_stack:
                            tuplet_stack.pop()
                        continue

                    ratio = 1.0
                    for tuplet in tuplet_stack:
                        ratio *= tuplet

                    if elem.tag == "Chord":
                        midis = sorted(
                            int(p.text) for p in elem.findall("Note/pitch") if p.text
                        )
                        pitches = [spell_midi(m, key_sharps) for m in midis]
                        dur = _dur_quarters(elem, ratio)
                        events.append(PartEvent(
                            pitches, measure_num, beat, dur, target=elem,
                        ))
                        beat += dur

                    elif elem.tag == "Rest":
                        dur = _dur_quarters(elem, ratio)
                        events.append(PartEvent(
                            [], measure_num, beat, dur, is_rest=True,
                        ))
                        beat += dur

        parts.append(events)

    return parts


def _inject_labels(
    content_staves: list[ET.Element],
    profile: AnnotationProfile,
    key_sharps: int,
    chord_info: dict | None,
    octaves: bool,
) -> int:
    """Label the selected notes/chords through the shared core, writing lyrics."""
    parts = _collect_events(content_staves, key_sharps)
    selected = select_labels(parts, profile, key_sharps=key_sharps)

    count = 0
    for ev in selected:
        label = label_for(ev.pitches, chord_info, ev.measure, ev.beat, octaves)
        if not label:
            continue

        lyrics = ET.Element("Lyrics")
        text_el = ET.SubElement(lyrics, "text")
        text_el.text = label

        children = list(ev.target)
        note_idx = next(
            (i for i, c in enumerate(children) if c.tag == "Note"),
            len(children),
        )
        ev.target.insert(note_idx, lyrics)
        count += 1

    return count
