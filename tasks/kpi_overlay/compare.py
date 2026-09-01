"""
tasks/kpi_overlay/compare.py — put the two KPI sources side by side.

Validating "does our KPI match Tableau's" is not a code-reading exercise: the
warehouse job is not in this repo, so the only honest answer comes from running
both over the same containers on the same day and diffing the numbers. This
module is the diff. It is pure — feed it two lists of cache entries and it
returns a report — so the same code backs both

    scripts/validate_kpi_vs_tableau.py     (a one-off report)
    kpi_overlay --source both              (a standing check on every run)

The three things it looks at, in the order they matter:

  1. ROW SET — a container in one source and not the other. A scope mismatch
     dwarfs any arithmetic difference, and it is the failure that makes pills
     silently disappear from the board.
  2. ELAPSED — the working-day count. A constant ±1 across every container is
     the "KPI method" off-by-one (start day counted or not), not noise.
  3. TARGET and COLOUR — same elapsed but a different verdict means the two
     sides hold different target tables.
"""

from __future__ import annotations

from typing import Any


def _index(entries: list[dict], key: str = "issueKey") -> dict[str, dict]:
    return {str(e[key]): e for e in entries if e.get(key)}


def _num(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def compare_containers(jira_entries: list[dict], tableau_entries: list[dict]) -> dict:
    """Diff two container-level cache-entry lists. Pure."""
    j_by_key = _index(jira_entries)
    t_by_key = _index(tableau_entries)

    only_jira = sorted(set(j_by_key) - set(t_by_key))
    only_tableau = sorted(set(t_by_key) - set(j_by_key))
    both = sorted(set(j_by_key) & set(t_by_key))

    diffs = []
    elapsed_deltas: list[int] = []
    agree_elapsed = agree_target = agree_color = 0

    for key in both:
        j, t = j_by_key[key], t_by_key[key]
        je, te = _num(j.get("elapsed")), _num(t.get("elapsed"))
        jt, tt = _num(j.get("target")), _num(t.get("target"))
        jc, tc = j.get("color"), t.get("color")

        delta = (te - je) if (je is not None and te is not None) else None
        if delta is not None:
            elapsed_deltas.append(delta)

        same_elapsed = delta == 0
        same_target = jt == tt
        same_color = jc == tc
        agree_elapsed += int(bool(same_elapsed))
        agree_target += int(bool(same_target))
        agree_color += int(bool(same_color))

        if same_elapsed and same_target and same_color:
            continue
        diffs.append({
            "issueKey": key,
            "location": j.get("location") or t.get("location"),
            "jira": {"elapsed": je, "target": jt, "color": jc,
                     "npiStart": j.get("npiStart"), "parked": j.get("parked")},
            "tableau": {"elapsed": te, "target": tt, "color": tc,
                        "npiStart": t.get("npiStart"),
                        "targetSource": t.get("targetSource"),
                        "targetHit": t.get("targetHit")},
            "elapsedDelta": delta,
            "targetDiffers": not same_target,
            "colorDiffers": not same_color,
        })

    matched = len(both)
    return {
        "matched": matched,
        "onlyInJira": only_jira,
        "onlyInTableau": only_tableau,
        "agreement": {
            "elapsed": agree_elapsed,
            "target": agree_target,
            "color": agree_color,
            "of": matched,
        },
        "elapsedDeltaHistogram": _histogram(elapsed_deltas),
        "diffs": sorted(diffs, key=lambda d: (abs(d["elapsedDelta"] or 0)), reverse=True),
    }


def compare_work_packages(jira_entries: list[dict], tableau_entries: list[dict]) -> dict:
    """Diff the flattened per-WP pill lists of the two sources."""
    j_wps = [w for c in jira_entries for w in c.get("wpKpis", [])]
    t_wps = [w for c in tableau_entries for w in c.get("wpKpis", [])]
    j_by_key = _index(j_wps)
    t_by_key = _index(t_wps)

    both = sorted(set(j_by_key) & set(t_by_key))
    diffs = []
    agree_elapsed = agree_target = agree_color = 0
    deltas: list[int] = []

    for key in both:
        j, t = j_by_key[key], t_by_key[key]
        je, te = _num(j.get("elapsed")), _num(t.get("elapsed"))
        jt, tt = _num(j.get("target")), _num(t.get("target"))
        delta = (te - je) if (je is not None and te is not None) else None
        if delta is not None:
            deltas.append(delta)
        same_elapsed = delta == 0
        same_target = jt == tt
        same_color = j.get("color") == t.get("color")
        agree_elapsed += int(bool(same_elapsed))
        agree_target += int(bool(same_target))
        agree_color += int(bool(same_color))
        if same_elapsed and same_target and same_color:
            continue
        diffs.append({
            "issueKey": key,
            "name": j.get("name") or t.get("name"),
            "containerKey": j.get("containerKey") or t.get("containerKey"),
            "jira": {"elapsed": je, "target": jt, "color": j.get("color"),
                     "state": j.get("state")},
            "tableau": {"elapsed": te, "target": tt, "color": t.get("color"),
                        "state": t.get("state"), "targetHit": t.get("targetHit")},
            "elapsedDelta": delta,
        })

    return {
        "matched": len(both),
        "onlyInJira": sorted(set(j_by_key) - set(t_by_key)),
        "onlyInTableau": sorted(set(t_by_key) - set(j_by_key)),
        "agreement": {
            "elapsed": agree_elapsed,
            "target": agree_target,
            "color": agree_color,
            "of": len(both),
        },
        "elapsedDeltaHistogram": _histogram(deltas),
        "diffs": sorted(diffs, key=lambda d: (abs(d["elapsedDelta"] or 0)), reverse=True),
    }


def _histogram(values: list[int]) -> dict[str, int]:
    """Count each delta value. A single non-zero bucket = a systematic offset."""
    hist: dict[str, int] = {}
    for v in values:
        hist[str(v)] = hist.get(str(v), 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: int(kv[0])))


def disagreement_rate(container_report: dict) -> float:
    """Share of matched containers whose colour differs. 0.0 when nothing matched."""
    matched = container_report.get("matched", 0)
    if not matched:
        return 0.0
    agree = container_report.get("agreement", {}).get("color", 0)
    return (matched - agree) / matched


def format_report(container_report: dict, wp_report: dict | None = None,
                  max_rows: int = 25) -> str:
    """Render the diff as plain text for a log or a console run."""
    lines: list[str] = []
    cr = container_report
    a = cr["agreement"]
    lines.append("CONTAINERS")
    lines.append(f"  matched            : {cr['matched']}")
    lines.append(f"  only in JIRA       : {len(cr['onlyInJira'])}"
                 + (f"  {', '.join(cr['onlyInJira'][:10])}" if cr["onlyInJira"] else ""))
    lines.append(f"  only in Tableau    : {len(cr['onlyInTableau'])}"
                 + (f"  {', '.join(cr['onlyInTableau'][:10])}" if cr["onlyInTableau"] else ""))
    lines.append(f"  same elapsed       : {a['elapsed']}/{a['of']}")
    lines.append(f"  same target        : {a['target']}/{a['of']}")
    lines.append(f"  same colour        : {a['color']}/{a['of']}")
    lines.append(f"  elapsed delta hist : {cr['elapsedDeltaHistogram'] or '{}'}"
                 "   (tableau minus jira)")
    if cr["diffs"]:
        lines.append("")
        lines.append(f"  {'CONTAINER':<16} {'JIRA e/t':>10} {'TABLEAU e/t':>12}  "
                     f"{'JIRA':<7} {'TABLEAU':<7} DELTA")
        for d in cr["diffs"][:max_rows]:
            j, t = d["jira"], d["tableau"]
            lines.append(
                f"  {d['issueKey']:<16} {str(j['elapsed']) + '/' + str(j['target']):>10} "
                f"{str(t['elapsed']) + '/' + str(t['target']):>12}  "
                f"{str(j['color']):<7} {str(t['color']):<7} "
                f"{d['elapsedDelta'] if d['elapsedDelta'] is not None else '-'}"
            )
        if len(cr["diffs"]) > max_rows:
            lines.append(f"  ... and {len(cr['diffs']) - max_rows} more")

    if wp_report:
        wa = wp_report["agreement"]
        lines.append("")
        lines.append("WORK PACKAGES")
        lines.append(f"  matched            : {wp_report['matched']}")
        lines.append(f"  only in JIRA       : {len(wp_report['onlyInJira'])}")
        lines.append(f"  only in Tableau    : {len(wp_report['onlyInTableau'])}")
        lines.append(f"  same elapsed       : {wa['elapsed']}/{wa['of']}")
        lines.append(f"  same target        : {wa['target']}/{wa['of']}")
        lines.append(f"  same colour        : {wa['color']}/{wa['of']}")
        lines.append(f"  elapsed delta hist : {wp_report['elapsedDeltaHistogram'] or '{}'}")
        for d in wp_report["diffs"][:max_rows]:
            j, t = d["jira"], d["tableau"]
            lines.append(
                f"  {d['issueKey']:<16} {str(d['name'])[:22]:<22} "
                f"jira={j['elapsed']}/{j['target']} {j['color']}  "
                f"tableau={t['elapsed']}/{t['target']} {t['color']}"
            )
        if len(wp_report["diffs"]) > max_rows:
            lines.append(f"  ... and {len(wp_report['diffs']) - max_rows} more")

    return "\n".join(lines)
