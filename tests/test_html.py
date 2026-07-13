"""XSS-escaping regression test for output/html.render_html.

Adversarial user-controlled strings (title, skills) must be HTML-escaped so a
raw <script> tag never survives into the output, and '&' is escaped to '&amp;'.
"""

from output.html import render_html

PAYLOAD = "<script>alert(1)</script>"


def _minimal_analysis(title, skill):
    return {
        "metadata": {"title": title, "composer": "Tester & Co"},
        "difficulty": {
            "overall_difficulty": 5,
            "component_scores": {
                "rhythm_complexity": 3, "hand_span": 2, "tempo_density": 4,
                "accidentals": 1, "jump_distance": 0, "chord_density": 2,
            },
            "skills_required": [skill],
        },
        "stats": {"num_measures": 8, "total_notes": 32, "per_hand": []},
        "rhythm": {"time_signatures": [{"signature": "4/4"}], "tempos": [{"bpm": 120}]},
        "key": {"detected_key": "C major"},
        "chords": {"most_common_chords": []},
        "structure": {},
        "annotations": {"simplified": []},
    }


def test_render_html_escapes_script_payload():
    analysis = _minimal_analysis(PAYLOAD, PAYLOAD)
    out = render_html(analysis)

    # The raw, unescaped script tag must not appear anywhere.
    assert PAYLOAD not in out
    # The escaped form should be present (proving the data was rendered, escaped).
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_render_html_escapes_ampersand():
    analysis = _minimal_analysis("Fast & Furious", "left & right")
    out = render_html(analysis)
    assert "&amp;" in out
    # A bare ' & ' surrounded by spaces from our data must not survive unescaped.
    assert "Fast & Furious" not in out
    assert "Fast &amp; Furious" in out
