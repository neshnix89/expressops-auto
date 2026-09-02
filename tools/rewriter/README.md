# Rewrite Desk

A small always-on window for rewriting messages in your own voice: refine a
draft, write from rough points, or reply to an email. It runs as a page inside
your claude.ai account, so there is no API key, no per-token bill, and nothing
to install. The page itself is a published Claude artifact; this folder only
holds the launcher that opens it as a floating window.

## Daily use

Double-click `launch_rewriter.bat` (or pin it to the taskbar). A 460 px window
opens bottom-right and stays on top. Leave it open.

1. Copy text anywhere, click into the top box, Ctrl+V.
2. Pick a mode: Refine, Draft from points, Reply to email.
3. Optional: tone chips and Format (Email, Teams message, JIRA comment).
4. Ctrl+Enter. Copy the result.

"What changed" under the result lists the edits made. Read it. The point of
the tool is to need it less over time.

## First-time setup

- Sign in to claude.ai in Edge once.
- Open "Voice & settings" in the window, paste two or three messages you wrote
  yourself and were happy with, adjust the house rules, save. This is what
  stops the output sounding like generic AI. It is kept in the browser only.
- The first Rewrite asks permission to use your Claude account. Allow it.

## Notes

- `--no-top` keeps it as a normal window.
- `REWRITER_URL` overrides the page address if it is ever republished elsewhere.
- Clipboard paste inside the page may be blocked by the browser; Ctrl+V in the
  box always works.
- Company data goes to claude.ai exactly as it does when you use Claude chat.
  Nothing is stored on any server by this tool.
