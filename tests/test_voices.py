"""Per-voice analysis: a voice change must not look like a melodic leap (#39)."""

from music21 import meter, note, pitch, stream

from analyzer.difficulty import analyze_difficulty
from analyzer.key_analysis import analyze_key
from analyzer.rhythm_analysis import analyze_rhythm
from analyzer.stats import analyze_stats
from analyzer.voices import voice_sequences
from export import labeling

_MELODY = ["C5", "D5", "E5", "D5"]
_BASS = ["C3", "D3", "E3", "D3"]


def _two_voice_stepwise_score(measures: int = 8) -> stream.Score:
    """One staff, two voices, every voice moving only by step."""
    score = stream.Score()
    part = stream.Part()
    part.append(meter.TimeSignature("4/4"))

    for number in range(1, measures + 1):
        measure = stream.Measure(number=number)
        upper = stream.Voice(id="1")
        lower = stream.Voice(id="2")
        for name in _MELODY:
            upper.append(note.Note(name, quarterLength=1.0))
        for name in _BASS:
            lower.append(note.Note(name, quarterLength=1.0))
        measure.insert(0, upper)
        measure.insert(0, lower)
        part.append(measure)

    score.insert(0, part)
    return score


def _analyze(score):
    rhythm = analyze_rhythm(score)
    stats = analyze_stats(score)
    key = analyze_key(score)
    diff = analyze_difficulty(score, rhythm, stats, key)
    return stats, diff


def test_voice_sequences_finds_nested_voices():
    """Voices nested inside measures (Measure > Voice) are found and kept whole."""
    part = _two_voice_stepwise_score(measures=2).parts[0]

    sequences = voice_sequences(part)

    assert len(sequences) == 2
    # Each voice stays in time order across the barline.
    assert [n.nameWithOctave for n in sequences[0]] == _MELODY + _MELODY
    assert [n.nameWithOctave for n in sequences[1]] == _BASS + _BASS


def test_two_voice_steps_have_no_jumps_or_invented_intervals():
    score = _two_voice_stepwise_score()
    stats, diff = _analyze(score)

    assert diff["component_scores"]["jump_distance"] == 0
    assert "Large position jumps (octave+)" not in diff["skills_required"]
    interval_names = [entry["interval"] for entry in stats["most_common_intervals"]]
    assert "m7" not in interval_names


def test_single_voice_two_octave_leap_still_counts():
    score = stream.Score()
    part = stream.Part()
    part.append(meter.TimeSignature("4/4"))
    for name in ["C3", "D3", "E3", "C5"]:
        part.append(note.Note(name, quarterLength=1.0))
    score.insert(0, part)

    _, diff = _analyze(score)

    assert diff["component_scores"]["jump_distance"] > 0
    assert "Large position jumps (octave+)" in diff["skills_required"]


def test_guided_leap_rule_stays_within_one_voice():
    """The voice boundary is not a large leap, so it gets no guided label."""
    events = [
        labeling.PartEvent([pitch.Pitch(name)], measure=1, beat=float(i + 1),
                           target=i, voice="1")
        for i, name in enumerate(["C4", "D4", "E4", "F4", "G4"])
    ]
    events += [
        labeling.PartEvent([pitch.Pitch(name)], measure=1, beat=float(i + 1),
                           target=i + 5, voice="2")
        for i, name in enumerate(["C2", "D2", "E2", "F2", "G2"])
    ]

    selected = labeling.select_labels([events], "guided", key_sharps=0)

    assert [ev.target for ev in selected] == [0]
