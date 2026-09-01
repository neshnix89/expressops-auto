"""
Webex notifier for mo_ref_order_monitor.

Three transports, because P+F blocks both bots and App-Hub integrations:

  1. "desktop"  — drives the Webex DESKTOP APP on the company laptop (no API,
     no admin approval). Opens the space via the `webexteams://` deep link,
     PASTES the message from the clipboard, READS THE COMPOSE BOX BACK to
     confirm the text is really there, and only then presses Enter. Viable
     here only because notifications are IS-gated and therefore rare.
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
import os
import re
import tempfile
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

WEBEX_MESSAGES = "https://webexapis.com/v1/messages"

def flatten(text: str) -> str:
    """
    Collapse to ONE line. In the desktop app Enter sends the message, so a
    multi-line body would post as several fragments.
    """
    parts = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    return "  |  ".join(parts)


def age_prefix(queued_at: str | None, now: datetime | None = None,
               threshold_hours: float = 2.0) -> str:
    """
    "(delayed 62h) " for an alert that waited, "" for a prompt one.

    A late alert is still useful — an issue raised on Friday evening matters on
    Monday — but it must not read as breaking news. The reader needs to know
    they are seeing history.
    """
    if not queued_at:
        return ""
    try:
        waited = ((now or datetime.now()) - datetime.fromisoformat(queued_at))
    except ValueError:
        return ""
    hours = waited.total_seconds() / 3600
    if hours < threshold_hours:
        return ""
    if hours < 24:
        return f"(delayed {hours:.0f}h) "
    return f"(delayed {hours / 24:.0f}d) "


def combine_alerts(texts: list[str], title: str = "ExpressOPS MO Monitor") -> str:
    """
    Fold several queued alerts into ONE multi-line message.

    One post per flush beats N posts: fewer Enter presses (each is a chance for
    the desktop transport to misfire) and the group reads a single grouped
    notification instead of a burst.
    """
    lines = [f"{title} — {len(texts)} alerts"]
    for i, t in enumerate(texts, start=1):
        lines.append(f"{i}) {flatten(t)}")
    return "\n".join(lines)


class WebexNotifier:
    def __init__(self, enabled: bool, logger: logging.Logger,
                 transport: str = "desktop", queue_file: Path | None = None,
                 space_link: str = "", open_delay: float = 6.0,
                 type_delay: float = 2.0, webhook_url: str = "",
                 token: str = "", default_room: str = "",
                 routing: dict[str, str] | None = None, dry_run: bool = False,
                 max_age_hours: float = 12.0, combine_alerts: bool = True):
        self.enabled = enabled
        self.max_age_hours = float(max_age_hours or 0)
        self.combine_alerts = bool(combine_alerts)
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
        if self.dry_run:
            # A dry-run must not write the shared queue file, or the messages
            # sit there and get sent by the next real run (observed: duplicates).
            self.log.info("[webex] (dry-run) would send marker=%s -> %s",
                          marker, flatten(text))
            return False
        q = self._load_queue()
        q.append({"marker": marker, "text": text,
                  "queued_at": datetime.now().isoformat(timespec="seconds")})
        self._save_queue(q)
        self.log.info("[webex] queued marker=%s (%d pending)", marker, len(q))
        return True

    def flush(self) -> tuple[int, int]:
        """Attempt delivery of everything queued. Returns (sent, still_pending)."""
        if not self.enabled or self.dry_run:
            # Dry-run never reads/mutates the real queue file.
            return (0, 0)
        q = self._load_queue()
        if not q:
            return (0, 0)

        # Drop only ANCIENT alerts. The desktop transport cannot type into a
        # locked screen, so anything raised after hours waits for the next
        # unlock — over a weekend that is 60+ hours. A 12h cutoff destroyed two
        # real alerts (28-Aug 16:56 and 31-Aug 18:31) before anyone could have
        # seen them. Late is worse than prompt, but far better than never: a
        # delayed alert is delivered with its age attached (see _age_prefix).
        if self.max_age_hours:
            cutoff = datetime.now() - timedelta(hours=self.max_age_hours)
            fresh = []
            for item in q:
                ts = item.get("queued_at")
                try:
                    stale = bool(ts) and datetime.fromisoformat(ts) < cutoff
                except ValueError:
                    stale = False
                if stale:
                    self.log.warning("[webex] dropping stale alert queued %s: %s",
                                     ts, flatten(item.get("text", ""))[:100])
                else:
                    fresh.append(item)
            q = fresh

        if self.transport == "desktop":
            # One visit to the space for the whole batch: re-opening the deep
            # link per message re-renders the compose box, and text typed into
            # a mid-render box is silently lost (observed: 3 "sent", 1 arrived).
            sent = self._send_desktop_many(
                [age_prefix(i.get("queued_at")) + i.get("text", "") for i in q])
            remaining = q[sent:]
        else:
            remaining = []
            sent = 0
            for item in q:
                body = age_prefix(item.get("queued_at")) + item.get("text", "")
                if self.send_one(item.get("marker", ""), body):
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

    # send_webex_desktop.ps1 exit codes
    _PS_ERRORS = {
        2: "Webex did not reach the foreground — nothing sent (will retry)",
        3: "no Webex window found — is the app running and signed in?",
        4: "bad arguments to the PowerShell helper",
        6: ("compose box did not contain the message — nothing sent (will retry). "
            "Usually the space was still switching; raise open_delay_seconds."),
        7: ("the clipboard is not usable from this session — nothing sent. "
            "Another app may be holding it, or the task is running without a "
            "desktop (uncheck 'Run whether user is logged on or not')."),
        8: ("the workstation is LOCKED — nothing sent, alert stays queued and "
            "goes out at the first run after unlock. Expected out of hours; "
            "only the webhook/bot transport can post to a locked machine."),
    }

    def _send_desktop(self, text: str) -> bool:
        """
        Open the space in the Webex desktop app and post the message.

        Delegated to send_webex_desktop.ps1, which forces the chat window to
        the foreground, pastes the text, and reads the compose box back before
        pressing Enter. Both checks are load-bearing: without the focus check
        the keystrokes land in whatever else has focus (observed: the console
        that launched us); without the read-back a still-switching space eats
        the text and a blind Enter posts nothing. A non-zero exit means nothing
        was sent, so the caller keeps the message queued.
        """
        if not self.space_link:
            self.log.error("[webex] desktop transport needs space_link (Copy space link)")
            return False
        if self.space_link.lower().startswith("http"):
            self.log.warning("[webex] space_link is an https link — it may open a browser. "
                             "Prefer the webexteams://im?space=... form.")

        script = Path(__file__).resolve().parent / "send_webex_desktop.ps1"
        if not script.exists():
            self.log.error("[webex] helper script missing: %s", script)
            return False

        body = flatten(text)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(script),
               "-SpaceLink", self.space_link,
               "-Message", body,
               "-OpenDelay", str(self.open_delay),
               "-TypeDelay", str(self.type_delay)]

        proc = subprocess.run(cmd, capture_output=True, timeout=180, check=False)

        # Exit 1 is PowerShell itself failing (e.g. a flaky Add-Type compile),
        # not a decision by our script — those are transient, so retry once
        # immediately rather than waiting for the next run. Codes 2/3/4/6 are
        # deliberate refusals and must NOT be retried here.
        if proc.returncode == 1:
            self.log.warning("[webex] transient PowerShell failure — retrying once")
            time.sleep(3)
            proc = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
        stdout = (proc.stdout or b"").decode("utf-8", "replace").strip()
        if proc.returncode == 0:
            self.log.info("[webex] sent via desktop app: %s", body[:120])
            for line in stdout.splitlines():          # window-selection trace
                self.log.debug("[webex] %s", line)
            return True

        reason = self._PS_ERRORS.get(proc.returncode, "PowerShell helper failed")
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        self.log.error("[webex] %s (exit %d) %s", reason, proc.returncode, detail[:300])
        # The candidate-window list is the fastest way to diagnose a bad target.
        for line in stdout.splitlines():
            self.log.error("[webex] %s", line)
        return False

    def _send_desktop_many(self, texts: list[str]) -> int:
        """
        Post several queued alerts during ONE visit to the space.

        Returns how many were actually sent, so the caller dequeues exactly
        those and retries the rest next run. The helper prints "SENT n" once
        per post and only after it has read the compose box back, so a success
        here means the text really was in the box when Enter was pressed —
        short of reading the space itself, that is as honest as this transport
        gets.
        """
        if not texts:
            return 0
        if not self.space_link:
            self.log.error("[webex] desktop transport needs space_link (Copy space link)")
            return 0
        script = Path(__file__).resolve().parent / "send_webex_desktop.ps1"
        if not script.exists():
            self.log.error("[webex] helper script missing: %s", script)
            return 0

        # Several alerts -> one grouped post. The payload is RAW text now: the
        # helper pastes it from the clipboard rather than typing, so there is no
        # SendKeys escaping to get wrong and emoji survive intact.
        combined = self.combine_alerts and len(texts) > 1
        payload = combine_alerts(texts) if combined else flatten(texts[0])

        tmp = Path(tempfile.gettempdir()) / f"eo_webex_{os.getpid()}.txt"
        tmp.write_text(payload, encoding="utf-8")
        try:
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", str(script),
                   "-SpaceLink", self.space_link,
                   "-MessageFile", str(tmp),
                   "-OpenDelay", str(self.open_delay),
                   "-TypeDelay", str(self.type_delay)]
            proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
            if proc.returncode == 1:
                self.log.warning("[webex] transient PowerShell failure — retrying once")
                time.sleep(3)
                proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)

            # Record what was delivered. Without this the log cannot be compared
            # against the space, which is exactly what was needed to spot the
            # two alerts lost on 13-Aug.
            stdout = (proc.stdout or b"").decode("utf-8", "replace")
            if proc.returncode == 0:
                for t in texts:
                    self.log.info("[webex] delivered: %s", flatten(t)[:200])
                # Keep the per-attempt verdict on a SUCCESS too. "attempt 1:
                # MATCH" vs "attempt 3: MATCH" is the difference between a
                # healthy machine and one that is one slow space-switch away
                # from failing, and only the log will remember.
                for line in stdout.splitlines():
                    if "attempt" in line or "round-trip" in line:
                        self.log.info("[webex] %s", line.strip())
            posts = len(re.findall(r"^SENT \d+", stdout, re.M))
            # One combined post carries all queued alerts, so it clears them all.
            sent = len(texts) if (combined and posts >= 1) else posts
            if sent:
                self.log.info("[webex] sent %d alert(s) as %d post(s) via desktop app",
                              sent, posts)
            if sent < len(texts):
                reason = self._PS_ERRORS.get(proc.returncode, "PowerShell helper failed")
                detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
                self.log.error("[webex] %s (exit %d) %s", reason, proc.returncode,
                               detail[:300])
                for line in stdout.splitlines():
                    if not line.startswith("SENT"):
                        self.log.error("[webex] %s", line)
            return sent
        except Exception as exc:  # noqa: BLE001
            self.log.error("[webex] batch send error: %s", exc)
            return 0
        finally:
            tmp.unlink(missing_ok=True)

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
