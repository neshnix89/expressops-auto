"""
MMS130 (Balance Identity. Reclassify) driver for M3 H5 via Playwright.

Reuses the SSO'd page from clients.m3_h5_client.M3H5Client and the same
"Ctrl+R search dialog → fill #cmdText → OK" launch sequence proven on
XECX450.

MMS130 is a standard M3 program, so unlike XDRX800/XECX450 it may render
in the main H5 page rather than an iframe. Every lookup therefore scans
page.frames (the main frame included) for the frame holding the MMS130
panel, and locates fields by their on-screen LABEL (the text you see in
M3) instead of guessing H5 element ids. The matched element is tagged
with a data-xops attribute so Playwright can fill it normally.

Flow per row:
  Panel A: Item number, Lot number, Warehouse, Location, (Container),
           Calc method = 1-Change status location level → NEXT
  Panel B: New status = 1 → NEXT   (dry-run: snapshot B, then go back)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger("mms130_status_change")

PROGRAM = "mms130"

# Panel A labels exactly as shown in M3 (case/whitespace-insensitive).
LABEL_ITEM = "Item number"
LABEL_LOT = "Lot number"
LABEL_WHS = "Warehouse"
LABEL_LOC = "Location"
LABEL_CONTAINER = "Container"
LABEL_CALC = "Calc method"
CALC_METHOD_CODE = "1"   # 1-Change status location level

# Panel B: first EDITABLE field whose label matches one of these is the
# target status. Override with --status-label if M3 shows something else.
STATUS_LABEL_CANDIDATES = (
    "New status", "New balance ID status", "To status", "Status new",
    "Balance ID status", "Status",
)

# ── In-page JavaScript helpers ─────────────────────────────────────────

_JS_COMMON = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
const vis = el => {
  if (!el || !el.getBoundingClientRect) return false;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const st = getComputedStyle(el);
  return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
};
const FIELDS = 'input:not([type=hidden]):not([type=button]):not([type=submit]), select, textarea';
const fieldInfo = el => ({
  id: el.id || '', name: el.getAttribute('name') || '', tag: el.tagName.toLowerCase(),
  value: el.tagName === 'SELECT'
    ? ((el.options[el.selectedIndex] || {}).text || el.value)
    : (el.value || ''),
  editable: !(el.readOnly || el.disabled || el.getAttribute('aria-readonly') === 'true'),
});
// Label candidates: leaf-ish elements whose own text is the label.
const labelEls = () => [...document.querySelectorAll('label, span, div, td, th')]
  .filter(el => vis(el) && el.children.length === 0 && norm(el.textContent));
// The field belonging to a label: label[for] first, else the nearest
// visible field on the same row to the right of the label.
const fieldFor = lab => {
  const f = lab.getAttribute('for');
  if (f) { const t = document.getElementById(f); if (t && vis(t) && t.matches(FIELDS)) return t; }
  const lr = lab.getBoundingClientRect(); const cy = (lr.top + lr.bottom) / 2;
  let best = null, bestDx = 1e9;
  for (const el of document.querySelectorAll(FIELDS)) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (Math.abs((r.top + r.bottom) / 2 - cy) > 10) continue;
    const dx = r.left - lr.right;
    if (dx < -2 || dx > 400) continue;
    if (dx < bestDx) { bestDx = dx; best = el; }
  }
  return best;
};
"""

JS_MARK_FIELD = "(args) => {" + _JS_COMMON + r"""
  const wanted = args.labels.map(norm);
  document.querySelectorAll(`[data-xops="${args.mark}"]`).forEach(e => e.removeAttribute('data-xops'));
  const labs = labelEls();
  for (const w of wanted) {
    for (const lab of labs) {
      if (norm(lab.textContent) !== w) continue;
      const el = fieldFor(lab);
      if (!el) continue;
      const info = fieldInfo(el);
      if (args.editableOnly && !info.editable) continue;
      el.setAttribute('data-xops', args.mark);
      return Object.assign({label: lab.textContent.trim()}, info);
    }
  }
  return null;
}"""

JS_DUMP_FIELDS = "() => {" + _JS_COMMON + r"""
  const out = []; const seen = new Set();
  for (const lab of labelEls()) {
    const el = fieldFor(lab);
    if (!el || seen.has(el)) continue;
    seen.add(el);
    out.push(Object.assign({label: lab.textContent.trim()}, fieldInfo(el)));
  }
  return out;
}"""

JS_PANEL_ID = "() => {" + _JS_COMMON + r"""
  for (const el of document.querySelectorAll('span, div, td, label, a')) {
    if (el.children.length) continue;
    const m = (el.textContent || '').trim().match(/^MMS130\/([A-Z])$/i);
    if (m && vis(el)) return m[1].toUpperCase();
  }
  return null;
}"""

JS_MESSAGES = "() => {" + _JS_COMMON + r"""
  const sel = '[role=alert], [class*="essage"], [class*="rror"], [class*="alert"]';
  const txt = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').trim();
    if (t && t.length < 400) txt.add(t.replace(/\s+/g, ' '));
  }
  return [...txt];
}"""

JS_CLICK_BUTTON = "(names) => {" + _JS_COMMON + r"""
  const wanted = names.map(norm);
  const btns = [...document.querySelectorAll('button, [role=button], a.btn, input[type=button]')].filter(vis);
  for (const w of wanted) {
    for (const b of btns) {
      const t = norm(b.innerText || b.value);
      const a = norm(b.getAttribute('aria-label') || b.getAttribute('title'));
      if (t === w || a === w || a.startsWith(w)) { b.click(); return true; }
    }
  }
  return false;
}"""


class MMS130Error(RuntimeError):
    """A row-level failure: M3 rejected the input or the panel did not behave."""


class MMS130Driver:
    """Drives MMS130 in an already-authenticated M3 H5 page."""

    def __init__(self, page, debug_dir: Path, status_labels: tuple[str, ...] = STATUS_LABEL_CANDIDATES):
        self.page = page
        self.debug_dir = debug_dir
        self.status_labels = status_labels
        self.frame = None
        debug_dir.mkdir(parents=True, exist_ok=True)

    # ── Program launch / frame discovery ──────────────────────────────

    def open(self) -> None:
        """Open MMS130 via the portal search dialog (same sequence as XECX450)."""
        page = self.page
        logger.info("Opening MMS130 via search dialog...")
        page.evaluate("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'r', code: 'KeyR', keyCode: 82, which: 82,
                ctrlKey: true, bubbles: true, cancelable: true
            }));
        """)
        page.wait_for_timeout(2000)
        cmd = page.locator("#cmdText")
        if not cmd.is_visible():
            page.evaluate(
                "$('#cmdText').parents().each(function(){$(this).show()}); "
                "$('#cmdText').show().focus()"
            )
            page.wait_for_timeout(1000)
        cmd.click()
        cmd.fill(PROGRAM)
        try:
            page.locator('button:has-text("OK")').first.click()
        except Exception:
            cmd.press("Enter")

        self.frame = self._wait_for_frame(timeout=40)
        if not self.frame:
            self.screenshot("open_failed")
            raise RuntimeError(
                "MMS130 panel not found after launch — see open_failed.png in the debug folder."
            )
        time.sleep(2)  # let the panel finish rendering
        logger.info("MMS130 open (frame: %s)", (self.frame.url or "main")[:80])

    def _wait_for_frame(self, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for fr in self.page.frames:
                try:
                    if fr.evaluate(JS_MARK_FIELD, {"labels": [LABEL_CALC], "mark": "probe", "editableOnly": False}):
                        return fr
                except Exception:
                    continue
            time.sleep(1)
        return None

    def _ensure_frame(self):
        try:
            if self.frame and self.panel() is not None:
                return self.frame
        except Exception:
            pass
        self.frame = self._wait_for_frame(timeout=15)
        if not self.frame:
            raise MMS130Error("MMS130 panel lost")
        return self.frame

    # ── Small primitives ──────────────────────────────────────────────

    def panel(self) -> str | None:
        """Current panel letter from the 'MMS130/X' footer, e.g. 'A' or 'B'."""
        for fr in [self.frame] + [f for f in self.page.frames if f is not self.frame]:
            try:
                p = fr.evaluate(JS_PANEL_ID)
                if p:
                    return p
            except Exception:
                continue
        return None

    def messages(self) -> list[str]:
        out: list[str] = []
        for fr in self.page.frames:
            try:
                out += fr.evaluate(JS_MESSAGES)
            except Exception:
                pass
        return list(dict.fromkeys(out))

    def dump_fields(self) -> list[dict[str, Any]]:
        return self._ensure_frame().evaluate(JS_DUMP_FIELDS)

    def screenshot(self, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
        path = self.debug_dir / f"{safe}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            logger.debug("screenshot failed: %s", exc)
        return path

    def _mark(self, labels: list[str] | tuple[str, ...], mark: str, editable_only: bool = True):
        return self._ensure_frame().evaluate(
            JS_MARK_FIELD, {"labels": list(labels), "mark": mark, "editableOnly": editable_only}
        )

    def _set(self, label: str, value: str, mark: str, required: bool = True) -> None:
        info = self._mark([label], mark)
        if not info:
            if required:
                raise MMS130Error(f"field '{label}' not found or not editable")
            return
        loc = self.frame.locator(f'[data-xops="{mark}"]')
        loc.click()
        loc.fill("")
        if value:
            loc.fill(value)
        actual = loc.input_value()
        if actual.strip().upper() != value.strip().upper():
            # Some H5 inputs ignore fill(); fall back to real keystrokes.
            loc.press("Control+a")
            loc.type(value, delay=40)
            actual = loc.input_value()
        if actual.strip().upper() != value.strip().upper():
            raise MMS130Error(f"could not set '{label}' (wanted {value!r}, field shows {actual!r})")

    def _ensure_calc_method(self) -> None:
        info = self._mark([LABEL_CALC], "calc", editable_only=False)
        if not info:
            raise MMS130Error("Calc method field not found")
        if info["value"].strip().startswith(CALC_METHOD_CODE):
            return
        loc = self.frame.locator('[data-xops="calc"]')
        if info["tag"] == "select":
            opts = loc.locator("option").all_inner_texts()
            target = next((o for o in opts if o.strip().startswith(f"{CALC_METHOD_CODE}-")), None)
            if not target:
                raise MMS130Error(f"Calc method option {CALC_METHOD_CODE} not in {opts}")
            loc.select_option(label=target)
        else:
            # Custom H5 combobox: open it and click the option text.
            loc.click()
            self.frame.get_by_text(re.compile(rf"^\s*{CALC_METHOD_CODE}\s*-\s*Change status", re.I)).first.click()
        info = self._mark([LABEL_CALC], "calc", editable_only=False)
        if not info or not info["value"].strip().startswith(CALC_METHOD_CODE):
            raise MMS130Error(f"Calc method is {info and info['value']!r}, expected {CALC_METHOD_CODE}")

    def _press_next(self) -> None:
        if not self._ensure_frame().evaluate(JS_CLICK_BUTTON, ["Next"]):
            self.frame.locator("body").press("Enter")

    def _press_previous(self) -> None:
        if not self._ensure_frame().evaluate(JS_CLICK_BUTTON, ["Previous", "Back", "<"]):
            self.frame.locator("body").press("F12")

    def new_messages(self, baseline: set[str]) -> list[str]:
        return [m for m in self.messages() if m not in baseline]

    def _wait_panel_change(self, from_panel: str, baseline: set[str], timeout: float = 20) -> str | None:
        """Wait until the footer shows a different panel, or a NEW message appears."""
        deadline = time.time() + timeout
        time.sleep(1)
        while time.time() < deadline:
            p = self.panel()
            if p and p != from_panel:
                time.sleep(1)
                return p
            if self.new_messages(baseline):
                time.sleep(0.5)
                p = self.panel()
                return p if p != from_panel else from_panel
            time.sleep(0.5)
        return self.panel()

    # ── Row workflow ──────────────────────────────────────────────────

    def back_to_panel_a(self) -> None:
        for _ in range(3):
            if self.panel() == "A":
                return
            self._press_previous()
            time.sleep(2)
        if self.panel() != "A":
            raise RuntimeError(f"cannot return to MMS130/A (on {self.panel()})")

    def fill_panel_a(self, item: str, lot: str, whs: str, loc: str, container: str = "") -> None:
        if self.panel() != "A":
            self.back_to_panel_a()
        self._set(LABEL_ITEM, item, "itno")
        self._set(LABEL_LOT, lot, "bano")
        self._set(LABEL_WHS, whs, "whlo")
        self._set(LABEL_LOC, loc, "whsl")
        self._set(LABEL_CONTAINER, container, "camu", required=False)
        self._ensure_calc_method()

    def goto_panel_b(self, tag: str) -> list[dict[str, Any]]:
        """Press NEXT on A; return panel B's fields. Raises MMS130Error with M3's message on rejection."""
        baseline = set(self.messages())
        self._press_next()
        p = self._wait_panel_change("A", baseline)
        if p != "B":
            msg = " | ".join(self.new_messages(baseline)) or f"stayed on panel {p}"
            shot = self.screenshot(f"{tag}_panelA_error")
            raise MMS130Error(f"M3 rejected panel A: {msg} (screenshot {shot.name})")
        return self.dump_fields()

    def apply_status(self, new_status: str, tag: str) -> None:
        """On panel B: set the status and confirm. Must end back on panel A."""
        info = self._mark(self.status_labels, "stas")
        if not info:
            self.screenshot(f"{tag}_no_status_field")
            raise MMS130Error(
                f"no editable status field on panel B (looked for {list(self.status_labels)}). "
                "Run with --discover and use --status-label."
            )
        loc = self.frame.locator('[data-xops="stas"]')
        loc.click()
        loc.fill(new_status)
        if loc.input_value().strip() != new_status:
            loc.press("Control+a")
            loc.type(new_status, delay=40)
        logger.info("  panel B: '%s' %s → %s", info["label"], info["value"], new_status)

        baseline = set(self.messages())
        self._press_next()
        p = self._wait_panel_change("B", baseline)
        if p == "A":
            msgs = self.new_messages(baseline)
            if any(re.search(r"error|not |invalid|does not exist|must", m, re.I) for m in msgs):
                self.screenshot(f"{tag}_after_confirm")
                raise MMS130Error("returned to panel A but M3 shows: " + " | ".join(msgs))
            return
        shot = self.screenshot(f"{tag}_panel{p}_after_confirm")
        msg = " | ".join(self.new_messages(baseline)) or f"now on panel {p}"
        if p == "B":
            raise MMS130Error(f"M3 rejected panel B: {msg} (screenshot {shot.name})")
        # Unknown extra panel/dialog — stop the whole run rather than guess.
        raise RuntimeError(f"unexpected panel {p} after confirming ({msg}); screenshot {shot.name}")
