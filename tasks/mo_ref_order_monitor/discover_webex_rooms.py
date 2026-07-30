#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discovery (READ-ONLY) — list every Webex space the bot is a member of, so we
can build the Ref-order-no -> roomId routing table for mo_ref_order_monitor.

Webex is Cisco's public cloud (webexapis.com), independent of the corporate
network — this can run from the company laptop or anywhere with the token.
Pure GET, no messages sent.

Token (in priority order):
    1. --token=<BOT_TOKEN> argument
    2. WEBEX_BOT_TOKEN environment variable

Setup first:
    1. developer.webex.com -> My Apps -> Create Bot -> copy the bot access token
    2. Add the bot to each Webex space that should receive notifications
    3. Run this script:
         set WEBEX_BOT_TOKEN=<token>        (Windows)
         python discover_webex_rooms.py
       or:
         python discover_webex_rooms.py --token=<token>

Do NOT commit the token or the printed roomIds' surrounding secrets.
"""

import json
import os
import sys
import urllib.error
import urllib.request

WEBEX_API = "https://webexapis.com/v1"


def get_token() -> str:
    for arg in sys.argv[1:]:
        if arg.startswith("--token="):
            return arg.split("=", 1)[1].strip()
    tok = os.environ.get("WEBEX_BOT_TOKEN", "").strip()
    if not tok:
        print("[FATAL] No token. Pass --token=<BOT_TOKEN> or set WEBEX_BOT_TOKEN.")
        sys.exit(1)
    return tok


def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{WEBEX_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    token = get_token()

    # Who is this bot? (sanity check the token)
    try:
        me = api_get("/people/me", token)
        print(f"[OK] Authenticated as: {me.get('displayName')} ({me.get('emails')})")
        print(f"     Bot personId: {me.get('id')}")
    except urllib.error.HTTPError as exc:
        print(f"[FATAL] /people/me failed: HTTP {exc.code} {exc.reason}")
        print("        Token invalid or expired.")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] Could not reach Webex: {exc}")
        sys.exit(1)

    # List spaces the bot belongs to (group spaces first)
    print("\n[ROOMS] Spaces this bot is a member of:")
    try:
        data = api_get("/rooms?type=group&max=200&sortBy=lastactivity", token)
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] /rooms failed: {exc}")
        sys.exit(1)

    rooms = data.get("items", [])
    if not rooms:
        print("  (none) — add the bot to the target Webex spaces, then re-run.")
        return

    print(f"  Found {len(rooms)} group space(s):\n")
    for r in rooms:
        print(f"  title   : {r.get('title')}")
        print(f"  roomId  : {r.get('id')}")
        print(f"  type    : {r.get('type')}")
        print("  " + "-" * 60)

    print("\nPaste the (title, roomId) pairs back so we can build the "
          "Ref-order-no -> roomId routing table in config.yaml.")


if __name__ == "__main__":
    main()
