"""Shared Anthropic client factory.

Every Claude call goes through the same client so a single conservative
per-request timeout applies. The SDK default is 10 minutes with 2 retries,
which lets a hung call stall an analysis (and its web request) for a very long
time.
"""

import anthropic

from config import ANTHROPIC_API_KEY

CLAUDE_TIMEOUT = 60.0


def get_client() -> anthropic.Anthropic:
    """Build an Anthropic client with the project-wide request timeout."""
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=CLAUDE_TIMEOUT)
