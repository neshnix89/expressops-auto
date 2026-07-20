"""
Webex notifier for mo_ref_order_monitor.

Routes a stage notification to a Webex space by VHRORN marker. Until a bot
token + room routing are configured (webex.enabled: true), it is a no-op that
only logs what it *would* send — so the JIRA poller works standalone.

Routing config (config.yaml):
    webex:
      bot_token: "..."
    mo_ref_order_monitor:
      webex:
        enabled: false
        default_room_id: ""          # fallback when a marker isn't mapped
        routing:                     # VHRORN value -> Webex roomId
          QM:   "ROOMID_AAA"
          TEST: "ROOMID_BBB"
"""

from __future__ import annotations

import logging

import requests

WEBEX_MESSAGES = "https://webexapis.com/v1/messages"


class WebexNotifier:
    def __init__(self, token: str, enabled: bool, default_room: str,
                 routing: dict[str, str], logger: logging.Logger,
                 dry_run: bool = False):
        self.token = token or ""
        self.enabled = enabled
        self.default_room = default_room or ""
        self.routing = {str(k).strip().upper(): v for k, v in (routing or {}).items()}
        self.log = logger
        self.dry_run = dry_run

    def room_for(self, marker: str) -> str:
        return self.routing.get((marker or "").strip().upper(), self.default_room)

    def notify(self, marker: str, text: str) -> bool:
        """
        Send `text` to the room mapped for `marker`. Returns True if actually
        sent. Logs and returns False when disabled / unconfigured / dry-run.
        """
        room = self.room_for(marker)
        if not self.enabled or not self.token or not room:
            self.log.info("[webex] (skipped: disabled/unconfigured) marker=%s -> %s",
                          marker, text)
            return False
        if self.dry_run:
            self.log.info("[webex] (dry-run) room=%s marker=%s -> %s", room, marker, text)
            return False
        try:
            resp = requests.post(
                WEBEX_MESSAGES,
                headers={"Authorization": f"Bearer {self.token}"},
                json={"roomId": room, "markdown": text},
                timeout=20,
            )
            if resp.status_code // 100 == 2:
                self.log.info("[webex] sent to room=%s marker=%s", room, marker)
                return True
            self.log.error("[webex] send failed %s: %s", resp.status_code, resp.text[:300])
            return False
        except requests.RequestException as exc:
            self.log.error("[webex] send error: %s", exc)
            return False
