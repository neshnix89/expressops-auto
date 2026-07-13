"""
DUMP_SOURCE — read-only source dumper for legacy KPI scripts (runs on the
company laptop). Finds `live_kpi.py` and `kpi_core*.py` under the AI folder
(and a few known legacy roots), prints each file to stdout with any hardcoded
secret string-literals masked, so the output can be pasted back into a cloud
session for migration into this repo.

READ-ONLY: this script only reads files and prints to stdout. It never writes,
uploads, or touches any live system. Secrets (JIRA_PAT, CONFLUENCE_PAT,
CLAUDE_API_KEY, Bearer tokens, sk-ant-… keys, JWT-like values) are masked so
they are never echoed.

Usage (company laptop):
    python C:\\Users\\tmoghanan\\Documents\\AI\\expressops-auto\\scripts\\dump_source.py
    python ...\\dump_source.py "C:\\some\\other\\root"   # extra root(s) to search
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# --- what to find --------------------------------------------------------
# Filename globs to collect. kpi_core may be split across files (kpi_core.py,
# kpi_core_targets.py, ...), so glob it.
TARGET_GLOBS = ["live_kpi.py", "kpi_core*.py"]

# Candidate roots to search, in priority order. The first existing one that
# yields matches wins, but we search all of them and de-duplicate by real path.
def candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    roots: list[Path] = []

    # 1) Nearest ancestor literally named "AI" (the AI folder), if any.
    for parent in here.parents:
        if parent.name.lower() == "ai":
            roots.append(parent)
            break

    # 2) The repo's parent (…/AI/expressops-auto -> …/AI).
    roots.append(here.parent.parent.parent)

    # 3) Known absolute roots from CLAUDE.md / LEGACY_REFERENCE.md.
    roots.extend([
        Path(r"C:\Users\tmoghanan\Documents\AI"),
        Path(r"C:\Users\Administrator\Documents\AI"),
        Path(r"C:\Users\Administrator\Documents\PY dump"),
        Path.home() / "Documents" / "AI",
        Path.home() / "Documents" / "PY dump",
    ])

    # 4) Any extra roots passed on the command line.
    for arg in sys.argv[1:]:
        roots.append(Path(arg))

    return roots


# --- secret masking ------------------------------------------------------
# Name *tokens* (after splitting on separators / camelCase) that mark a value
# as secret. Token-based, not substring — so `path` (contains "pat") and
# `pattern` are NOT treated as secrets, but JIRA_PAT / jiraPat / API_KEY are.
SECRET_TOKENS = {
    "pat", "token", "tokens", "secret", "secrets",
    "password", "passwd", "pwd", "bearer", "apikey",
    "credential", "credentials", "privatekey",
}

# Split an identifier into lowercased word tokens (on non-alphanumerics and
# camelCase boundaries). `api[_-]?key` is normalised to a single `apikey` token
# first so CLAUDE_API_KEY -> {claude, apikey}.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _name_tokens(name: str) -> set[str]:
    name = re.sub(r"api[_-]?key", "apikey", name, flags=re.IGNORECASE)
    parts = re.split(r"[^A-Za-z0-9]+", name)
    tokens: set[str] = set()
    for part in parts:
        for tok in _CAMEL_RE.split(part):
            if tok:
                tokens.add(tok.lower())
    return tokens


def _looks_like_token(val: str) -> bool:
    """Value-shape safety net: a long opaque credential-looking literal.

    Deliberately conservative so it does NOT mask model ids (claude-sonnet-…),
    URLs, file paths, UUIDs, or dotted module names — only high-entropy blobs
    with mixed case + digits and no hyphens/spaces (typical of base64 PATs).
    """
    if len(val) < 32 or "-" in val or " " in val:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=_.]+", val):
        return False
    return (any(c.isupper() for c in val)
            and any(c.islower() for c in val)
            and any(c.isdigit() for c in val))

# Assignment / dict-entry where the LHS name is secret-like:
#   JIRA_PAT = "xxxx"     JIRA_PAT: str = "xxxx"     "pat": "xxxx"
ASSIGN_RE = re.compile(
    r"""(?P<prefix>
            (?:^|[\{\(,;])\s*
            (?P<q>['"]?)                # optional quote around a dict key
            (?P<name>[A-Za-z_][A-Za-z0-9_]*)
            (?P=q)
            \s*(?::\s*[A-Za-z_][\w\[\], ]*)?   # optional type annotation
            \s*[:=]\s*
        )
        (?P<sq>['"])(?P<val>.*?)(?P=sq)
    """,
    re.VERBOSE,
)

# Explicit high-signal secret patterns to mask anywhere they appear.
INLINE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),            # Anthropic keys
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-.]{10,}"),  # JWT-like
    re.compile(r"(?i)(Bearer)\s+[A-Za-z0-9+/=_.\-]{16,}"),        # Bearer <tok>
]

MASK = "***MASKED***"


def _mask_value(name: str, val: str) -> bool:
    """Decide whether a string literal `val` assigned to `name` should be masked."""
    if _name_tokens(name) & SECRET_TOKENS:
        return True
    if _looks_like_token(val):
        # Long opaque token — mask even if the var name is uninformative, but
        # skip obvious non-secrets (URLs, file paths).
        if val.startswith(("http://", "https://", "C:\\", "/", "\\")):
            return False
        return True
    return False


def mask_line(line: str) -> str:
    """Return `line` with any secret string literals replaced by MASK."""
    def repl(m: re.Match) -> str:
        name = m.group("name")
        val = m.group("val")
        if _mask_value(name, val):
            return f"{m.group('prefix')}{m.group('sq')}{MASK}{m.group('sq')}"
        return m.group(0)

    line = ASSIGN_RE.sub(repl, line)

    for pat in INLINE_PATTERNS:
        line = pat.sub(
            lambda m: (m.group(1) + " " + MASK) if m.lastindex else MASK,
            line,
        )
    return line


def mask_text(text: str) -> str:
    return "".join(mask_line(ln) for ln in text.splitlines(keepends=True))


# --- main ----------------------------------------------------------------
def find_targets(roots: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        for glob in TARGET_GLOBS:
            for path in root.rglob(glob):
                try:
                    real = path.resolve()
                except OSError:
                    real = path
                if real in seen or not path.is_file():
                    continue
                seen.add(real)
                found.append(path)
    return found


def main() -> int:
    roots = candidate_roots()
    print("=" * 72)
    print("DUMP_SOURCE — read-only legacy KPI source dumper")
    print("Secrets are masked as ***MASKED*** before printing.")
    print("=" * 72)
    print("Roots searched:")
    for r in roots:
        try:
            exists = r.exists()
        except OSError:
            exists = False
        print(f"  [{'x' if exists else ' '}] {r}")
    print()

    targets = find_targets(roots)
    if not targets:
        print("!! No live_kpi.py or kpi_core*.py found under any root above.")
        print("!! Re-run with the correct folder, e.g.:")
        print('     python dump_source.py "C:\\path\\to\\legacy\\scripts"')
        # Discovery aid: list any *.py whose name mentions kpi.
        print("\n-- Any *.py mentioning 'kpi' under the searched roots --")
        seen: set[Path] = set()
        for r in roots:
            try:
                if not (r.exists() and r.is_dir()):
                    continue
            except OSError:
                continue
            for p in r.rglob("*kpi*.py"):
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    print(f"  {p}")
        return 1

    print(f"Found {len(targets)} file(s):")
    for p in targets:
        print(f"  {p}")
    print()

    for p in targets:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"!! Could not read {p}: {e}")
            continue
        masked = mask_text(raw)
        print()
        print("#" * 72)
        print(f"# FILE: {p}")
        print(f"# ({len(raw.splitlines())} lines)")
        print("#" * 72)
        print(masked)
        print(f"# END FILE: {p}")
        print("#" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
