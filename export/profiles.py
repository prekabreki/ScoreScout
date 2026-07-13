"""Annotation profile definitions."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AnnotationProfile:
    name: str
    note_letters: str  # "all", "guided", "none"
    chord_symbols: bool
    difficulty_markers: bool
    include_octaves: bool = False


PROFILES: dict[str, AnnotationProfile] = {
    "full": AnnotationProfile(
        name="full",
        note_letters="all",
        chord_symbols=False,
        difficulty_markers=False,
    ),
    "guided": AnnotationProfile(
        name="guided",
        note_letters="guided",
        chord_symbols=True,
        difficulty_markers=True,
    ),
    "clean": AnnotationProfile(
        name="clean",
        note_letters="none",
        chord_symbols=False,
        difficulty_markers=False,
    ),
}

DEFAULT_PROFILE = "full"


def get_profile(name: str, include_octaves: bool = False) -> AnnotationProfile:
    """Get a profile by name, optionally overriding the octave setting."""
    base = PROFILES.get(name)
    if base is None:
        raise ValueError(f"Unknown profile: {name}. Options: {list(PROFILES.keys())}")
    if include_octaves != base.include_octaves:
        return replace(base, include_octaves=include_octaves)
    return base
