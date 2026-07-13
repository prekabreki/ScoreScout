"""MuseScore-gated tests.

These skip cleanly when MuseScore isn't installed (e.g. on CI), demonstrating
the skipif pattern required for any test that shells out to MuseScore.
"""

import pytest

import export.render as render

needs_musescore = pytest.mark.skipif(
    not render.find_musescore(), reason="MuseScore not installed"
)


@needs_musescore
def test_find_musescore_returns_executable_path():
    path = render.find_musescore()
    assert path
    import os
    assert os.path.exists(path) or os.path.basename(path)
