"""Regression tests for analyzer/chord_shapes (issue #13 / audit M15).

The key bug: an m6 / m7 *dyad* (two notes) must resolve to an interval label
``(m6)`` / ``(m7)``, NOT to a chord label ``m6`` / ``m7``. The dyad and chord
namespaces used to collide in the display table.
"""

from analyzer.chord_shapes import lookup_chord, format_chord_symbol


def _symbol_for(pitch_classes):
    result = lookup_chord(set(pitch_classes))
    assert result is not None, f"lookup_chord returned None for {pitch_classes}"
    root, quality = result
    return format_chord_symbol(root, quality), quality


def test_m6_dyad_is_interval_not_chord():
    # C + Ab = a minor sixth (8 semitones) dyad.
    symbol, quality = _symbol_for({0, 8})
    assert quality.startswith("int:"), quality
    assert symbol.endswith("(m6)"), symbol
    assert not symbol.endswith("m6") or symbol.endswith("(m6)")


def test_m7_dyad_is_interval_not_chord():
    # C + Bb = a minor seventh (10 semitones) dyad.
    symbol, quality = _symbol_for({0, 10})
    assert quality.startswith("int:"), quality
    assert symbol.endswith("(m7)"), symbol


def test_m6_chord_is_chord_label():
    # Full minor-six chord {0,3,7,9} must label as a chord 'm6', no parens.
    symbol, quality = _symbol_for({0, 3, 7, 9})
    assert quality == "m6"
    assert symbol == "Cm6"


def test_m7_chord_is_chord_label():
    symbol, quality = _symbol_for({0, 3, 7, 10})
    assert quality == "m7"
    assert symbol == "Cm7"


def test_extended_chord_pitch_class_sets():
    # Exact, fully-spelled extended voicings resolve to their qualities.
    cases = {
        "maj9": {0, 4, 7, 11, 2},
        "9":    {0, 4, 7, 10, 2},
        "m9":   {0, 3, 7, 10, 2},
        "11":   {0, 4, 7, 10, 2, 5},
        "13":   {0, 4, 7, 10, 2, 9},
    }
    for expected_quality, pcs in cases.items():
        result = lookup_chord(pcs)
        assert result is not None, f"no match for {expected_quality} {pcs}"
        root, quality = result
        assert quality == expected_quality, (pcs, quality, expected_quality)
        # And the C-rooted display symbol is "C<quality>".
        assert format_chord_symbol("C", quality) == "C" + expected_quality
