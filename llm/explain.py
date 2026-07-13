"""Claude API integration for beginner-friendly explanations."""

import logging

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS
from llm.prompt import SYSTEM_PROMPT, format_analysis_for_llm

log = logging.getLogger(__name__)


def generate_explanation(analysis: dict) -> str | None:
    """Send analysis to Claude and return beginner-friendly explanation text."""
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set — skipping LLM explanation")
        return None

    compact_json = format_analysis_for_llm(analysis)
    log.info("Sending analysis to Claude API (%s)...", CLAUDE_MODEL)
    log.debug("Prompt payload size: %d chars", len(compact_json))

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        # Conservative per-request timeout (audit L12) so a hung/slow API call
        # can't stall the whole analysis indefinitely.
        message = client.with_options(timeout=60.0).messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Here is the analysis of a piano piece. "
                        "Please write a beginner-friendly report.\n\n"
                        f"```json\n{compact_json}\n```"
                    ),
                }
            ],
        )
    except Exception as e:
        # Network/rate-limit/auth/overload/timeout — degrade gracefully.
        # Callers treat None as "no explanation available".
        log.warning("Claude API call failed: %s — continuing without explanation", e)
        log.debug("Claude API failure detail", exc_info=True)
        return None

    # Don't assume content[0] exists or is a text block: a refusal yields an
    # empty content list, and some content blocks aren't text.
    text = next(
        (block.text for block in message.content if getattr(block, "type", None) == "text"),
        None,
    )
    if text is None:
        log.warning(
            "Claude response had no text content (stop_reason=%s) — continuing without explanation",
            getattr(message, "stop_reason", None),
        )
        return None

    log.info("Claude response received — %d chars, usage: %s input / %s output tokens",
             len(text), message.usage.input_tokens, message.usage.output_tokens)
    log.debug("Response preview: %s...", text[:200])

    return text
