"""
Claude Opus 4.6 integration for container_summary.

Three layers of cost optimisation are applied in combination:
  1. Layer 1 (skip unchanged) — handled in main.py via cache.py.
  2. Layer 2 (incremental payload) — `build_incremental_payload` sends
     only new comments plus the cached narrative.
  3. Layer 3 (prompt caching) — `call_opus` marks the system prompt
     with ``cache_control: ephemeral`` so repeat calls in the same batch
     are billed at 10% of the input rate.
"""

from __future__ import annotations

import logging
import re
from typing import Any

try:  # pragma: no cover — optional import; only needed in live mode
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

from core.config_loader import Config
from core.errors import missing_dependency


MODEL = "claude-opus-4-6"
MAX_TOKENS = 1000

# Pricing ($/M tokens) — Opus 4.6, used only for the rough batch estimate.
_INPUT_PRICE_PER_M = 15.0
_OUTPUT_PRICE_PER_M = 75.0
_CACHE_WRITE_PRICE_PER_M = 18.75
_CACHE_READ_PRICE_PER_M = 1.50


SYSTEM_PROMPT = """You are analysing JIRA Work Container comment threads for an NPI operations coordinator at Pepperl+Fuchs Singapore. Write in English regardless of comment language. Translate any non-English content naturally.

Output exactly four sections in this format:

**Purpose:** [1-2 sentences: what is being built, order type, key part numbers, MO number if mentioned, delivery destination if mentioned]

**Actions:**
- → [Person Name] — [specific task they need to complete, with part numbers/document refs where relevant]
- → [Person Name] — [task]
(List every pending action with the responsible person. If no actions are pending, write "No open actions.")

**Risks:**
- [Concrete risk with specific details — part numbers, technical issues, dependencies]
(Only include genuine risks to timeline or quality. If none, write "No risks identified.")

**History:**
- [Key milestone or decision, with dates and names where available]
(3-5 bullets covering: major milestones, technical issues encountered, key decisions made. Chronological order.)

Rules:
- Reference people by display name as they appear in comments.
- Include specific part numbers, TO numbers, MO numbers, document refs when mentioned.
- Actions must name WHO needs to do WHAT. Generic actions like "team to follow up" are useless.
- Do not invent information. If unclear, say so.
- Do not use markdown formatting (no ** or * or #). Output plain text only — the caller handles HTML formatting.
- Keep total output under 300 words."""


# ── Client construction ──────────────────────────────────────────────


def create_client(config: Config):
    """Construct an anthropic client using the configured API key."""
    if anthropic is None:
        raise missing_dependency("anthropic")
    return anthropic.Anthropic(api_key=config.anthropic_api_key)


# ── Wiki-markup scrubbing ────────────────────────────────────────────

# Strip {color:...}{color}, {panel}{panel}, {quote}{quote}, image refs.
_WIKI_NOISE_RE = re.compile(
    r"\{[a-z]+(?::[^}]*)?\}",  # tags like {color:red}, {panel:title=..}
    re.IGNORECASE,
)
_IMAGE_REF_RE = re.compile(r"!([\w.\-]+\.(?:png|jpg|jpeg|gif|bmp))(?:\|[^!]*)?!")


def _scrub(body: str) -> str:
    body = _WIKI_NOISE_RE.sub("", body)
    body = _IMAGE_REF_RE.sub(r"[image: \1]", body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _format_comment(idx: int, comment: dict[str, Any]) -> str:
    author = ((comment.get("author") or {}).get("displayName") or "").strip()
    created = (comment.get("created") or "").split("T")[0]
    body = _scrub(comment.get("body") or "")
    return f"[{idx}] {author} @ {created}: {body}"


# ── Payload assembly ─────────────────────────────────────────────────


def build_full_payload(
    issue: dict[str, Any],
    identity: dict[str, Any],
    wp_rollup: dict[str, Any],
) -> str:
    """Full-history user message for a first-seen or forced-refresh container."""
    fields = issue.get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []

    header = (
        f"Container: {identity.get('key', '')} — {identity.get('summary', '')}\n"
        f"Order Type: {identity.get('order_type', '') or '(unknown)'} | "
        f"Status: {identity.get('status', '') or '(unknown)'}\n"
        f"WP Roll-up: {wp_rollup.get('summary_line', '')}\n"
    )

    if not comments:
        return header + "\nComments: (none)\n"

    lines = [_format_comment(i + 1, c) for i, c in enumerate(comments)]
    return header + "\nComments (oldest first):\n" + "\n".join(lines)


def build_incremental_payload(
    issue: dict[str, Any],
    identity: dict[str, Any],
    cached_narrative: str,
    new_comments: list[dict[str, Any]],
    starting_index: int,
    last_run_date: str = "",
) -> str:
    """Shorter user message sent when only a few new comments need folding in."""
    lines = [_format_comment(starting_index + i, c) for i, c in enumerate(new_comments)]
    activity_label = f"since {last_run_date}" if last_run_date else "since last run"
    return (
        f"Container: {identity.get('key', '')} — {identity.get('summary', '')}\n\n"
        "Previous summary:\n"
        f"{cached_narrative}\n\n"
        f"New activity {activity_label}:\n"
        + ("\n".join(lines) if lines else "(no new comments)") +
        "\n\nUpdate the summary to incorporate the new activity above."
    )


def get_new_comments(
    comments: list[dict[str, Any]], cached_comment_count: int,
) -> list[dict[str, Any]]:
    """Return comments beyond the cached count."""
    if cached_comment_count < 0:
        cached_comment_count = 0
    return comments[cached_comment_count:]


# ── API call ─────────────────────────────────────────────────────────


def call_opus(
    client: Any, user_message: str, logger: logging.Logger,
) -> tuple[str, dict[str, int]]:
    """
    Call Opus 4.6 with the cached system prompt.

    Returns ``(narrative, usage)`` where ``usage`` has
    ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``,
    ``cache_read_input_tokens``. On failure, returns ``('', {})``.
    """
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:  # noqa: BLE001 — log and fall back to Phase 1
        logger.error("Opus call failed: %s: %s", type(exc).__name__, exc)
        return "", {}

    try:
        narrative = response.content[0].text.strip()
    except (AttributeError, IndexError) as exc:
        logger.error("Opus response had unexpected shape: %s", exc)
        return "", {}

    # Strip any leftover markdown bold markers — the section renderer in
    # logic.py emits its own HTML, and a stray ** in the body would show
    # up literally in the Confluence expand macro.
    narrative = narrative.replace("**", "")

    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
        "cache_creation_input_tokens":
            getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
    }
    logger.debug("Opus usage: %s", usage)
    return narrative, usage


# ── Cost estimate ────────────────────────────────────────────────────


def estimate_batch_cost(containers_to_process: int, is_full_refresh: bool) -> float:
    """Rough pre-run cost estimate (USD). Logged before making calls."""
    if containers_to_process <= 0:
        return 0.0
    if is_full_refresh:
        per_container_input = 10_000
        per_container_output = 500
    else:
        per_container_input = 2_000
        per_container_output = 500

    input_cost = (
        containers_to_process * per_container_input / 1_000_000 * _INPUT_PRICE_PER_M
    )
    output_cost = (
        containers_to_process * per_container_output / 1_000_000 * _OUTPUT_PRICE_PER_M
    )
    return round(input_cost + output_cost, 4)


def usage_cost(usage: dict[str, int]) -> float:
    """Actual $ cost for a single Opus response's usage counters."""
    if not usage:
        return 0.0
    return round(
        usage.get("input_tokens", 0) / 1_000_000 * _INPUT_PRICE_PER_M
        + usage.get("output_tokens", 0) / 1_000_000 * _OUTPUT_PRICE_PER_M
        + usage.get("cache_creation_input_tokens", 0) / 1_000_000 * _CACHE_WRITE_PRICE_PER_M
        + usage.get("cache_read_input_tokens", 0) / 1_000_000 * _CACHE_READ_PRICE_PER_M,
        4,
    )
