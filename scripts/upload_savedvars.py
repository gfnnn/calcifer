"""Companion uploader: push WoW SavedVariables (.lua) to the bot's data spine.

Runs on the GM's PC, where the addons save their data, separate from the bot.
It is **stdlib-only** (``urllib``) so it needs no install: copy it next to any
Python 3 and run it against the bot's upload endpoint::

    python upload_savedvars.py \
        --url https://your-app/api/upload/savedvars \
        --token "$SAVEDVARS_UPLOAD_SECRET" \
        "C:/Program Files/World of Warcraft/_retail_/WTF/Account/<ACCT>/SavedVariables/RCLootCouncil.lua"

The URL and token may instead come from the ``SAVEDVARS_UPLOAD_URL`` /
``SAVEDVARS_UPLOAD_SECRET`` environment variables. Each file is POSTed in turn
and the server's ingestion summary (what it detected, rows imported) is printed.
A non-zero exit code means at least one upload failed, so it is safe to wire
into a scheduled task.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 30


def _build_request(url: str, token: str, data: bytes) -> urllib.request.Request:
    """Construct the authenticated POST carrying the raw ``.lua`` bytes."""
    return urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "X-Upload-Token": token,
            "Content-Type": "text/plain; charset=utf-8",
        },
    )


def post(url: str, token: str, text: str, *, opener=None, timeout=DEFAULT_TIMEOUT):
    """POST one file's text; return ``(status_code, parsed_or_raw_body)``."""
    open_fn = opener or urllib.request.urlopen
    req = _build_request(url, token, text.encode("utf-8"))
    try:
        with open_fn(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload WoW SavedVariables to the bot's data spine.")
    parser.add_argument("files", nargs="+", help="One or more SavedVariables .lua files.")
    parser.add_argument(
        "--url",
        default=os.environ.get("SAVEDVARS_UPLOAD_URL"),
        help="Upload endpoint, e.g. https://your-app/api/upload/savedvars "
        "(or set SAVEDVARS_UPLOAD_URL).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SAVEDVARS_UPLOAD_SECRET"),
        help="Shared upload secret (or set SAVEDVARS_UPLOAD_SECRET).",
    )
    args = parser.parse_args(argv)

    if not args.url or not args.token:
        print("error: --url and --token (or their env vars) are required", file=sys.stderr)
        return 2

    failures = 0
    for path in args.files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"{path}: cannot read ({exc})", file=sys.stderr)
            failures += 1
            continue
        status, body = post(args.url, args.token, text)
        if status == 200:
            print(f"{path}: ok -> {json.dumps(body) if isinstance(body, dict) else body}")
        else:
            print(f"{path}: failed (HTTP {status}) {body}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
