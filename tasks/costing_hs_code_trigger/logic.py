"""
Pure business logic for costing_hs_code_trigger.

No I/O here — functions take plain dicts/strings and return results, so the
whole decision engine is unit-testable without JIRA access (see
``test_logic.py``).

Covers:
  * Trigger gate (DMR → immediate; others → 5 prerequisite WPs Done/Won't Do)
  * Per-person "done" detection from comments authored after the trigger
  * Reminder-due calculation in Singapore working days
  * Trigger / reminder message assembly with ``{token}`` substitution
  * Marker + timestamp parsing over a container's comments

Reuses the shared helpers already proven in ``mo_trigger_comment`` rather than
duplicating them: WP lookup, order-type extraction/labelling, `[~username]`
mentions, and the working-day calendar (SG public holidays).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

# Reuse the battle-tested pure helpers from the sibling task.
from tasks.mo_trigger_comment.logic import (
    extract_order_type,
    find_wp_by_name,
    is_working_day,
    jira_mention,
    order_type_label,
)

# ── Constants ────────────────────────────────────────────────────────

# Prerequisite Work Packages for NON-DMR containers. All must be resolved
# (see ``ready_resolutions``) before the initial trigger fires.
PREREQUISITE_WPS: tuple[str, ...] = (
    "material",
    "pcb",
    "routing - technprep",
    "pe - technprep",
    "te - technprep",
)

# Prerequisite WPs count as ready when resolved. Mirrors mo_trigger_comment's
# _DONE_RESOLUTIONS: "Won't Do" and "Acknowledged" both count as resolved
# (planners use them when a step is legitimately skipped or signed off).
# Overridable via config `ready_resolutions`.
DEFAULT_READY_RESOLUTIONS: frozenset[str] = frozenset(
    {"Done", "Acknowledged", "Won't Do"}
)

# Order Type (customfield_13905) label that marks a Direct Manufacturing
# Release container. order_type_label() maps the raw dash-variant value to
# this short label for us.
DMR_LABEL = "DMR"

DEFAULT_DONE_KEYWORDS: tuple[str, ...] = ("done", "updated", "completed")
DEFAULT_NEGATION_GUARDS: tuple[str, ...] = ("not done", "no update", "pending")
DEFAULT_REMINDER_INTERVAL_WD = 2

ROLE_COSTING = "costing"
ROLE_HS_CODE = "hs_code"

_ROLE_LABELS = {ROLE_COSTING: "Costing", ROLE_HS_CODE: "HS Code"}


# ── People model ─────────────────────────────────────────────────────


@dataclass
class Person:
    """A tagged person plus which track (costing / hs_code) they belong to."""

    username: str
    display: str
    role: str

    @property
    def label(self) -> str:
        return self.display or self.username or "(unnamed)"

    @property
    def mention(self) -> str:
        """`[~username]` when we have a login, else the plain display name."""
        if self.username:
            return jira_mention(self.username)
        return self.display or "[UNASSIGNED]"


def build_people(task_config: dict[str, Any]) -> list[Person]:
    """
    Flatten the config into an ordered ``[costing, costing, hs_code]`` list.

    ``costing_people`` is a list of ``{username, display}`` dicts;
    ``hs_code_person`` is a single such dict. Missing/blank entries are
    tolerated (the person just won't be a clickable mention or auto-detectable
    as done) — main.py warns about blank usernames.
    """
    people: list[Person] = []
    for entry in task_config.get("costing_people") or []:
        if isinstance(entry, dict):
            people.append(
                Person(
                    username=str(entry.get("username") or "").strip(),
                    display=str(entry.get("display") or "").strip(),
                    role=ROLE_COSTING,
                )
            )
    hs = task_config.get("hs_code_person") or {}
    if isinstance(hs, dict):
        people.append(
            Person(
                username=str(hs.get("username") or "").strip(),
                display=str(hs.get("display") or "").strip(),
                role=ROLE_HS_CODE,
            )
        )
    return people


# ── WP helpers ───────────────────────────────────────────────────────


def _wp_resolution_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("resolution") or {}).get("name") or "").strip()


def is_dmr(container: dict[str, Any]) -> bool:
    """True when Order Type (customfield_13905) resolves to the DMR label."""
    return order_type_label(extract_order_type(container)) == DMR_LABEL


def check_trigger_ready(
    container: dict[str, Any],
    wps: list[dict[str, Any]],
    ready_resolutions: frozenset[str] | set[str] = DEFAULT_READY_RESOLUTIONS,
) -> tuple[bool, list[str]]:
    """
    Return ``(ready, reasons)``.

    * DMR container → always ready (no prerequisite WPs to wait for).
    * Otherwise → ready only when every prerequisite WP exists and its
      resolution is in ``ready_resolutions``. ``reasons`` names what blocked
      it; an empty list means fully ready.
    """
    if is_dmr(container):
        return True, []

    reasons: list[str] = []
    for canonical in PREREQUISITE_WPS:
        wp = find_wp_by_name(wps, canonical)
        if wp is None:
            reasons.append(f"{canonical}: missing")
            continue
        resolution = _wp_resolution_name(wp)
        if resolution not in ready_resolutions:
            reasons.append(
                f"{canonical}: resolution={resolution or '(none)'} "
                f"(need one of {sorted(ready_resolutions)})"
            )
    return (not reasons, reasons)


# ── Comment / timestamp helpers ──────────────────────────────────────

_TS_MS_RE = re.compile(r"\.\d+")
_TS_TZ_RE = re.compile(r"[+-]\d{2}:?\d{2}$")


def parse_jira_ts(ts: str | None) -> datetime | None:
    """
    Parse a JIRA comment timestamp, stripping milliseconds and the timezone
    offset first (same on-prem quirks JiraClient.parse_timestamp handles).
    """
    if not ts:
        return None
    cleaned = _TS_TZ_RE.sub("", _TS_MS_RE.sub("", ts))
    try:
        return datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _comment_author_name(comment: dict[str, Any]) -> str:
    return ((comment.get("author") or {}).get("name") or "").strip()


def has_marker(comments: list[dict[str, Any]], marker: str) -> bool:
    """True if any comment body contains the marker (the duplicate guard)."""
    if not marker:
        return False
    return any(marker in (c.get("body") or "") for c in comments or [])


def marker_timestamps(
    comments: list[dict[str, Any]], markers: list[str],
) -> list[datetime]:
    """Created timestamps of every comment whose body contains any marker."""
    out: list[datetime] = []
    for c in comments or []:
        body = c.get("body") or ""
        if any(m and m in body for m in markers):
            ts = parse_jira_ts(c.get("created"))
            if ts is not None:
                out.append(ts)
    return out


def first_trigger_time(
    comments: list[dict[str, Any]], trigger_marker: str,
) -> datetime | None:
    """Earliest created timestamp of a trigger-marker comment (the baseline)."""
    stamps = marker_timestamps(comments, [trigger_marker])
    return min(stamps) if stamps else None


def last_nudge_time(
    comments: list[dict[str, Any]], trigger_marker: str, reminder_marker: str,
) -> datetime | None:
    """Latest created timestamp across trigger AND reminder comments."""
    stamps = marker_timestamps(comments, [trigger_marker, reminder_marker])
    return max(stamps) if stamps else None


def person_is_done(
    comments: list[dict[str, Any]],
    username: str,
    since: datetime | None,
    keywords: list[str],
    negations: list[str],
) -> bool:
    """
    True when ``username`` authored a comment (strictly after ``since``, when
    provided) whose lower-cased body contains a done-keyword and none of the
    negation guards. A blank username can never match → never done.
    """
    if not username:
        return False
    uname = username.lower()
    kws = [k.lower() for k in keywords if k]
    negs = [n.lower() for n in negations if n]
    for c in comments or []:
        if _comment_author_name(c).lower() != uname:
            continue
        created = parse_jira_ts(c.get("created"))
        if since is not None and (created is None or created <= since):
            continue
        body = (c.get("body") or "").lower()
        if any(neg in body for neg in negs):
            continue
        if any(kw in body for kw in kws):
            return True
    return False


# ── Working-day arithmetic ───────────────────────────────────────────


def working_days_elapsed(
    since: date, today: date, holidays: set[date] | None = None,
) -> int:
    """
    Count working days strictly after ``since`` up to and including ``today``.
    Returns 0 when ``today`` is not after ``since``.
    """
    if today <= since:
        return 0
    count = 0
    d = since
    while d < today:
        d += timedelta(days=1)
        if is_working_day(d, holidays):
            count += 1
    return count


# ── Message assembly ─────────────────────────────────────────────────


def _render(template: str, tokens: dict[str, str]) -> str:
    """Replace only the known ``{token}`` markers; leave other braces intact."""
    out = template
    for key, value in tokens.items():
        out = out.replace("{" + key + "}", value)
    return out.strip()


def _mentions(people: list[Person]) -> str:
    return ", ".join(p.mention for p in people)


def assemble_trigger_comment(people: list[Person], template: str) -> str:
    """Initial comment: tags all costing people and the HS Code person."""
    costing = [p for p in people if p.role == ROLE_COSTING]
    hs = [p for p in people if p.role == ROLE_HS_CODE]
    tokens = {
        "costing_mentions": _mentions(costing),
        "hs_mention": _mentions(hs),
        "outstanding_lines": _outstanding_lines(people),
    }
    return _render(template, tokens)


def _outstanding_lines(outstanding: list[Person]) -> str:
    """One line per track that still has outstanding people, in fixed order."""
    lines: list[str] = []
    for role in (ROLE_COSTING, ROLE_HS_CODE):
        subset = [p for p in outstanding if p.role == role]
        if subset:
            lines.append(f"{_ROLE_LABELS[role]}: {_mentions(subset)}")
    return "\n".join(lines)


def assemble_reminder_comment(outstanding: list[Person], template: str) -> str:
    """Reminder comment: tags ONLY the people still outstanding, by track."""
    costing = [p for p in outstanding if p.role == ROLE_COSTING]
    hs = [p for p in outstanding if p.role == ROLE_HS_CODE]
    tokens = {
        "costing_mentions": _mentions(costing),
        "hs_mention": _mentions(hs),
        "outstanding_lines": _outstanding_lines(outstanding),
    }
    return _render(template, tokens)


# ── Decision engine ──────────────────────────────────────────────────

# Action values a Decision can carry.
ACTION_TRIGGER = "trigger"
ACTION_REMIND = "remind"
ACTION_NOOP = "noop"

# State label for a container skipped because it was part of the go-live
# baseline (already-ready backlog we deliberately never trigger).
STATE_BASELINE_SKIP = "baseline_skip"


@dataclass
class Decision:
    """The outcome of evaluating one container on one run."""

    key: str
    action: str                       # ACTION_TRIGGER / ACTION_REMIND / ACTION_NOOP
    state: str                        # human-readable state label
    body: str | None = None           # comment body to post (trigger/remind)
    reason: str = ""                  # why (esp. for noop)
    is_dmr: bool = False
    outstanding: list[str] = field(default_factory=list)


def decide(
    *,
    issue: dict[str, Any],
    wps: list[dict[str, Any]],
    people: list[Person],
    task_config: dict[str, Any],
    today: date,
) -> Decision:
    """
    Evaluate a single container and return what to do this run.

    Flow:
      1. Not yet triggered + ready  → ACTION_TRIGGER (post initial comment).
      2. Not yet triggered + not ready → ACTION_NOOP (waiting on gate).
      3. Triggered + everyone done  → ACTION_NOOP (complete).
      4. Triggered + someone outstanding + reminder due → ACTION_REMIND.
      5. Triggered + someone outstanding + not due yet → ACTION_NOOP (waiting).
    """
    key = issue.get("key", "?")
    fields = issue.get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []
    dmr = is_dmr(issue)

    trigger_marker = str(task_config.get("trigger_marker") or "").strip()
    reminder_marker = str(task_config.get("reminder_marker") or "").strip()
    ready_resolutions = frozenset(
        task_config.get("ready_resolutions") or DEFAULT_READY_RESOLUTIONS
    )
    keywords = list(task_config.get("done_keywords") or DEFAULT_DONE_KEYWORDS)
    negations = list(task_config.get("negation_guards") or DEFAULT_NEGATION_GUARDS)
    interval = int(
        task_config.get("reminder_interval_working_days")
        or DEFAULT_REMINDER_INTERVAL_WD
    )
    templates = task_config.get("messages") or {}

    triggered = has_marker(comments, trigger_marker)

    # ── Case 1/2: not yet triggered ──────────────────────────────────
    if not triggered:
        ready, reasons = check_trigger_ready(issue, wps, ready_resolutions)
        if not ready:
            return Decision(
                key=key, action=ACTION_NOOP, state="not_ready",
                reason="; ".join(reasons), is_dmr=dmr,
            )
        body = assemble_trigger_comment(people, templates.get("trigger", ""))
        return Decision(
            key=key, action=ACTION_TRIGGER, state="trigger",
            body=body, is_dmr=dmr,
            outstanding=[p.label for p in people],
            reason="DMR — immediate" if dmr else "prerequisite WPs complete",
        )

    # ── Already triggered: measure completion ────────────────────────
    baseline = first_trigger_time(comments, trigger_marker)
    outstanding = [
        p for p in people
        if not person_is_done(comments, p.username, baseline, keywords, negations)
    ]

    if not outstanding:
        return Decision(
            key=key, action=ACTION_NOOP, state="complete",
            reason="all tracks done", is_dmr=dmr,
        )

    last = last_nudge_time(comments, trigger_marker, reminder_marker)
    elapsed = working_days_elapsed(last.date(), today) if last else interval
    outstanding_labels = [p.label for p in outstanding]

    if elapsed < interval:
        return Decision(
            key=key, action=ACTION_NOOP, state="waiting",
            reason=f"{elapsed} working day(s) since last nudge (< {interval})",
            is_dmr=dmr, outstanding=outstanding_labels,
        )

    body = assemble_reminder_comment(outstanding, templates.get("reminder", ""))
    return Decision(
        key=key, action=ACTION_REMIND, state="remind", body=body,
        reason=f"{elapsed} working day(s) since last nudge (>= {interval})",
        is_dmr=dmr, outstanding=outstanding_labels,
    )


def apply_baseline(decision: Decision, baseline_keys: set[str]) -> Decision:
    """
    Suppress the INITIAL trigger for containers in the go-live baseline — the
    pre-existing backlog that was already ready when the automation was first
    switched on and that we deliberately never nag.

    Only an ``ACTION_TRIGGER`` decision is converted to a no-op; every other
    state passes through unchanged. Baseline containers are never triggered, so
    they never reach the reminder path — this keeps the safeguard to exactly
    "don't send the first comment on old backlog", nothing more.
    """
    if decision.action == ACTION_TRIGGER and decision.key in baseline_keys:
        return Decision(
            key=decision.key, action=ACTION_NOOP, state=STATE_BASELINE_SKIP,
            reason="in go-live baseline (pre-existing backlog — skipped)",
            is_dmr=decision.is_dmr,
        )
    return decision
