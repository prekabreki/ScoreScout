"""Inject lyric annotations directly into .mscz files.

Preserves MuseScore's original layout by modifying the XML inside the
.mscz ZIP archive rather than round-tripping through music21.

Flow:
  1. music21 analyzes the score and produces annotation labels.
  2. This module unzips the .mscz, walks the XML in parallel with music21,
     matches notes by (staff, measure-index, beat-offset, pitch), and injects
     <Lyrics> elements.
  3. The modified .mscz is re-zipped and rendered via MuseScore CLI.
"""

import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from music21 import note as m21note
from music21.stream import Score

from export.annotate import _fix_flats, _note_name, _lookup_chord_name_at, _is_usable_chord_name

log = logging.getLogger(__name__)

# MuseScore duration names → quarter-note lengths
_DUR_MAP = {
    "maxima": 32.0, "long": 16.0, "breve": 8.0,
    "whole": 4.0, "half": 2.0, "quarter": 1.0,
    "eighth": 0.5, "16th": 0.25, "32nd": 0.125,
    "64th": 0.0625, "128th": 0.03125,
}

NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

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
    counter in ``_inject_full`` aligned with music21 so (measure, beat)-keyed
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


def _label_for_chord_with_analysis(
    chord_el: ET.Element, octaves: bool,
    chord_info: dict | None, measure_num: int, beat: float,
) -> str:
    """Build a label, trying chord name lookup for 3+ note chords."""
    notes = chord_el.findall("Note")
    if not notes:
        return ""

    pitches = []
    for n in notes:
        p_el = n.find("pitch")
        if p_el is not None:
            pitches.append(int(p_el.text))
    pitches.sort()

    if not pitches:
        return ""

    num = len(pitches)
    names = []
    for midi in pitches:
        pc = NOTE_NAMES[midi % 12]
        if octaves:
            octave = (midi // 12) - 1
            names.append("%s%d" % (pc, octave))
        else:
            names.append(pc)

    if num == 1:
        return names[0]
    elif num == 2:
        if NOTE_NAMES[pitches[0] % 12] == NOTE_NAMES[pitches[1] % 12]:
            return names[0]
        return "/".join(names)
    else:
        # Try chord name from analysis
        chord_name = _lookup_chord_from_info(chord_info, measure_num, beat)
        if chord_name and "power" in chord_name.lower():
            return "/".join(names)
        if chord_name:
            return chord_name
        if len(names) > 3:
            return "/".join(names[:3]) + "+"
        return "/".join(names)


def _lookup_chord_from_info(chord_info: dict | None, measure: int, beat: float) -> str | None:
    """Look up chord name from analysis data by measure+beat."""
    if not chord_info:
        return None
    progression = chord_info.get("chord_progression_full", [])
    if not progression:
        return None
    for entry in progression:
        if entry.get("measure") == measure and abs(entry.get("beat", 1.0) - beat) < 0.25:
            name = entry.get("name", "")
            if name and not name.startswith("["):
                from export.annotate import _simplify_chord_name
                simplified = _simplify_chord_name(name)
                if simplified and _is_usable_chord_name(simplified):
                    return _fix_flats(simplified)
    return None


def inject_mscz(
    mscz_path: str,
    output_path: str,
    profile_name: str = "full",
    octaves: bool = False,
    chord_info: dict | None = None,
) -> str:
    """Inject lyric annotations into a .mscz file.

    Args:
        mscz_path: Path to the original .mscz file.
        output_path: Where to write the modified .mscz.
        profile_name: 'full', 'guided', or 'clean'.
        octaves: Include octave numbers in labels.
        chord_info: Chord analysis dict for chord name lookup.

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

        total_injected = 0

        if profile_name == "full":
            total_injected = _inject_full(content_staves, octaves, chord_info)
        elif profile_name == "guided":
            total_injected = _inject_full(content_staves, octaves, chord_info)
            # TODO: guided mode could be sparser, but for now use full

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


def _inject_full(
    content_staves: list[ET.Element],
    octaves: bool,
    chord_info: dict | None,
) -> int:
    """Inject labels on every note/chord across all staves."""
    count = 0

    for staff in content_staves:
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
                        label = _label_for_chord_with_analysis(
                            elem, octaves, chord_info, measure_num, beat,
                        )
                        if label:
                            lyrics = ET.Element("Lyrics")
                            text_el = ET.SubElement(lyrics, "text")
                            text_el.text = label

                            children = list(elem)
                            note_idx = next(
                                (i for i, c in enumerate(children) if c.tag == "Note"),
                                len(children),
                            )
                            elem.insert(note_idx, lyrics)
                            count += 1

                        beat += _dur_quarters(elem, ratio)

                    elif elem.tag == "Rest":
                        beat += _dur_quarters(elem, ratio)

    return count
