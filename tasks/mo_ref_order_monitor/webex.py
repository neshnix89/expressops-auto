"""
Webex notifier for mo_ref_order_monitor.

Two transports, because some P+F spaces block bots ("this bot is not allowed
by one or more of the participating organizations in this space"):

  1. incoming_webhook (RECOMMENDED when bots are blocked)
     A Cisco-provided *integration*, not a bot: it posts into the one space it
     was created for. Configure `webhook_url`; no token, no roomId needed.

  2. bot token
     Needs a bot added to each space + a roomId per space. Supports routing
     different markers to different rooms.

Until one is configured (`enabled: true` + a URL or token), this is a no-op
that only logs what it *would* send, so the JIRA poller works standalone.

Config (config.yaml):
    webex:
      bot_token: "..."                 # only for the bot transport
    mo_ref_order_monitor:
      webex:
        enabled: false
        transport: "webhook"           # "webhook" | "bot"
        webhook_url: "https://webexapis.com/v1/webhooks/incoming/XXXX"
        default_room_id: ""            # bot transport only
        routing: {}                    # bot transport only: marker -> roomId
"""

from __future__ import annotations

import logging

import requests

WEBEX_MESSAGES = "https://webexapis.com/v1/messages"


class WebexNotifier:
    def __init__(self, enabled: bool, logger: logging.Logger,
                 transport: str = "webhook", webhook_url: str = "",
                 token: str = "", default_room: str = "",
                 routing: dict[str, str] | None = None, dry_run: bool = False):
        self.enabled = enabled
        self.log = logger
        self.transport = (transport or "webhook").strip().lower()
        self.webhook_url = (webhook_url or "").strip()
        self.token = (token or "").strip()
        self.default_room = (default_room or "").strip()
        self.routing = {str(k).strip().upper(): v for k, v in (routing or {}).items()}
        self.dry_run = dry_run

    def room_for(self, marker: str) -> str:
        return self.routing.get((marker or "").strip().upper(), self.default_room)

    def _configured(self, marker: str) -> tuple[bool, str]:
        """(ready, target-description) for the active transport."""
        if self.transport == "webhook":
            return bool(self.webhook_url), "incoming-webhook"
        room = self.room_for(marker)
        return bool(self.token and room), f"room={room}"

    def notify(self, marker: str, text: str) -> bool:
        """
        Send `text` for a stage marker. Returns True only if actually sent.
        Logs and returns False when disabled / unconfigured / dry-run.
        """
        ready, target = self._configured(marker)
        if not self.enabled or not ready:
            self.log.info("[webex] (skipped: disabled/unconfigured) marker=%s -> %s",
                          marker, text.replace("\n", " | "))
            return False
        if self.dry_run:
            self.log.info("[webex] (dry-run) %s marker=%s -> %s",
                          target, marker, text.replace("\n", " | "))
            return False

        try:
            if self.transport == "webhook":
                resp = requests.post(self.webhook_url,
                                     json={"markdown": text}, timeout=20)
            else:
                resp = requests.post(
                    WEBEX_MESSAGES,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"roomId": self.room_for(marker), "markdown": text},
                    timeout=20,
                )
            if resp.status_code // 100 == 2:
                self.log.info("[webex] sent (%s) marker=%s", target, marker)
                return True
            self.log.error("[webex] send failed %s: %s",
                           resp.status_code, resp.text[:300])
            return False
        except requests.RequestException as exc:
            self.log.error("[webex] send error: %s", exc)
            return False
