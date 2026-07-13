import os
import sys
from pathlib import Path

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096


def _default_export_dir() -> Path:
    """Where annotated exports land by default.

    Windows keeps the existing OneDrive\\Piano layout (home-relative so it
    works for any user); other platforms default under ~/Piano. Override on
    any OS with the PIANO_FORMATTER_EXPORT_DIR env var.
    """
    if sys.platform == "win32":
        return Path.home() / "OneDrive" / "Piano" / "annotated"
    return Path.home() / "Piano" / "annotated"


EXPORT_DIR = Path(os.environ.get("PIANO_FORMATTER_EXPORT_DIR") or _default_export_dir())
CACHE_DIR = Path(__file__).parent / ".cache"
