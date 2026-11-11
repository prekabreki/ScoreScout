"""Display spelling for music21 names.

Analyzers emit music21's internal flat notation (``B-``, ``E-``) and nothing
converted it before it reached a report, the LLM prompt or the chord guide.
:func:`display_name` is the one conversion point: ``B-`` -> ``Bb``,
``B--`` -> ``Bbb``, while sharps and naturals are untouched. Idempotent, so it
is also safe on text that has already been converted.
"""

from __future__ import annotations

import re

_UPPER_FLAT = re.compile(r"([A-G])(-+)(?![A-Za-z-])")
_LOWER_FLAT = re.compile(r"(?<![A-Za-z])([a-g])(-+)(?![A-Za-z-])")
# A chord symbol is one whitespace-free token, so its flat root can be followed
# by letters without forming a word: music21 writes Bbmaj7 as "B-maj7". Prose
# such as "B-major triad" (pitchedCommonName) keeps its space and is untouched.
_SYMBOL_FLAT = re.compile(r"([A-G])(-+)")


def _expand(match: re.Match) -> str:
    return match.group(1).upper() + "b" * len(match.group(2))


def display_name(text: str) -> str:
    """Convert music21's flat notation to beginner-friendly spelling.

    ``B-`` -> ``Bb`` and ``B--`` -> ``Bbb``; sharps (``F#``) and naturals
    (``C``) pass through unchanged. Already-converted text, such as ``Bb``,
    is left alone. A lowercase tonic (music21 writes minor keys as ``b- minor``)
    is uppercased so it reads as ``Bb minor``. Whitespace-free chord symbols
    such as ``B-maj7`` become ``Bbmaj7``.
    """
    text = _UPPER_FLAT.sub(_expand, text)
    text = _LOWER_FLAT.sub(_expand, text)
    if not any(ch.isspace() for ch in text):
        text = _SYMBOL_FLAT.sub(_expand, text)
    return text
