"""
Webex notifier for mo_ref_order_monitor.

Three transports, because P+F blocks both bots and App-Hub integrations:

  1. "desktop"  — drives the Webex DESKTOP APP on the company laptop (no API,
     no admin approval). Opens the space via the `webexteams://` deep link,
     then types the message with PowerShell SendKeys. Viable here only because
     notifications are IS-gated and therefore rare.
  2. "webhook"  — Incoming Webhooks integration (needs admin approval).
  3. "bot"      — bot token + roomId (blocked by policy today).

All transports are QUEUE-BASED: notify() enqueues, flush() delivers. A send
that fails (laptop locked, Webex closed, API down) stays queued and is retried
on the next poller run, so an issue alert is never silently lost.

Config (config.yaml):
    mo_ref_order_monitor:
      webex:
        enabled: true
        transport: "desktop"
        # From Webex: space menu -> "Copy space link".
        # Either a webexteams://im?space=... URI or an https://eurl.io/... link.
        space_link: "webexteams://im?space=XXXXXXXX"
        open_delay_seconds: 6      # wait for the app to focus the space
        type_delay_seconds: 1      # settle before typing
        queue_file: "outputs/mo_ref_order_monitor_webex_queue.json"
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import requests

WEBEX_MESSAGES = "https://webexapis.com/v1/messages"

# SendKeys treats these as control characters — each must be brace-wrapped.
_SENDKEYS_SPECIAL = set("+^%~(){}[]")


def sendkeys_escape(text: str) -> str:
    """Escape a string for [System.Windows.Forms.SendKeys]::SendWait."""
    out = []
    for ch in text:
        out.append("{" + ch + "}" if ch in _SENDKEYS_SPECIAL else ch)
    return "".join(out)


def powershell_quote(text: str) -> str:
    """Quote for a PowerShell double-quoted string literal."""
    return text.replace("`", "``").replace('"', '`"').replace("$", "`$")


def flatten(text: str) -> str:
    """
    Collapse to ONE line. In the desktop app Enter sends the message, so a
    multi-line body would post as several fragments.
    """
    parts = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    return "  |  ".join(parts)


class WebexNotifier:
    def __init__(self, enabled: bool, logger: logging.Logger,
                 transport: str = "desktop", queue_file: Path | None = None,
                 space_link: str = "", open_delay: float = 6.0,
                 type_delay: float = 1.0, webhook_url: str = "",
                 token: str = "", default_room: str = "",
                 routing: dict[str, str] | None = None, dry_run: bool = False):
        self.enabled = enabled
        self.log = logger
        self.transport = (transport or "desktop").strip().lower()
        self.queue_file = Path(queue_file) if queue_file else None
        self.space_link = (space_link or "").strip()
        self.open_delay = float(open_delay)
        self.type_delay = float(type_delay)
        self.webhook_url = (webhook_url or "").strip()
        self.token = (token or "").strip()
        self.default_room = (default_room or "").strip()
        self.routing = {str(k).strip().upper(): v for k, v in (routing or {}).items()}
        self.dry_run = dry_run

    # ── queue ────────────────────────────────────────────────────────
    def _load_queue(self) -> list[dict]:
        if not self.queue_file or not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_queue(self, items: list[dict]) -> None:
        if not self.queue_file:
            return
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        tmp.replace(self.queue_file)

    def notify(self, marker: str, text: str) -> bool:
        """Queue a notification. Returns True if it was queued."""
        if not self.enabled:
            self.log.info("[webex] (disabled) marker=%s -> %s", marker, flatten(text))
            return False
        q = self._load_queue()
        q.append({"marker": marker, "text": text})
        self._save_queue(q)
        self.log.info("[webex] queued marker=%s (%d pending)", marker, len(q))
        return True

    def flush(self) -> tuple[int, int]:
        """Attempt delivery of everything queued. Returns (sent, still_pending)."""
        if not self.enabled:
            return (0, 0)
        q = self._load_queue()
        if not q:
            return (0, 0)
        if self.dry_run:
            self.log.info("[webex] (dry-run) %d message(s) would be sent", len(q))
            return (0, len(q))

        remaining = []
        sent = 0
        for item in q:
            if self.send_one(item.get("marker", ""), item.get("text", "")):
                sent += 1
            else:
                remaining.append(item)
        self._save_queue(remaining)
        if remaining:
            self.log.warning("[webex] %d message(s) still pending — will retry next run",
                             len(remaining))
        return (sent, len(remaining))

    # ── transports ───────────────────────────────────────────────────
    def send_one(self, marker: str, text: str) -> bool:
        try:
            if self.transport == "desktop":
                return self._send_desktop(text)
            if self.transport == "webhook":
                return self._send_webhook(text)
            return self._send_bot(marker, text)
        except Exception as exc:  # noqa: BLE001 — never let Webex break the poller
            self.log.error("[webex] send error (%s): %s", self.transport, exc)
            return False

    def _send_desktop(self, text: str) -> bool:
        """Open the space in the Webex desktop app and type the message."""
        if not self.space_link:
            self.log.error("[webex] desktop transport needs space_link (Copy space link)")
            return False
        body = flatten(text)

        # 1) Bring the space to the foreground.
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             f'Start-Process "{powershell_quote(self.space_link)}"'],
            capture_output=True, timeout=60, check=False,
        )
        time.sleep(self.open_delay)

        # 2) Type into the compose box and send. SendKeys goes to whatever has
        #    focus, so a locked screen / stolen focus makes this fail — hence
        #    the queue: an undelivered message is retried next run.
        keys = sendkeys_escape(body) + "{ENTER}"
        ps = ("Add-Type -AssemblyName System.Windows.Forms; "
              f'[System.Windows.Forms.SendKeys]::SendWait("{powershell_quote(keys)}")')
        time.sleep(self.type_delay)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            self.log.error("[webex] SendKeys failed: %s",
                           (proc.stderr or b"").decode("utf-8", "replace")[:300])
            return False
        self.log.info("[webex] typed into desktop app: %s", body[:120])
        return True

    def _send_webhook(self, text: str) -> bool:
        if not self.webhook_url:
            self.log.error("[webex] webhook transport needs webhook_url")
            return False
        resp = requests.post(self.webhook_url, json={"markdown": text}, timeout=20)
        if resp.status_code // 100 == 2:
            return True
        self.log.error("[webex] webhook failed %s: %s", resp.status_code, resp.text[:300])
        return False

    def _send_bot(self, marker: str, text: str) -> bool:
        room = self.routing.get((marker or "").strip().upper(), self.default_room)
        if not (self.token and room):
            self.log.error("[webex] bot transport needs bot_token + roomId")
            return False
        resp = requests.post(
            WEBEX_MESSAGES,
            headers={"Authorization": f"Bearer {self.token}"},
            json={"roomId": room, "markdown": text}, timeout=20,
        )
        if resp.status_code // 100 == 2:
            return True
        self.log.error("[webex] bot send failed %s: %s", resp.status_code, resp.text[:300])
        return False
