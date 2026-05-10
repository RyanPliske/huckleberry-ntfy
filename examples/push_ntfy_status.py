#!/usr/bin/env python3
"""Fetch Huckleberry feed/diaper summaries and push a summary to your phone via ntfy.

What is ntfy?
  A small service that accepts HTTP POSTs and shows them as notifications on iOS/Android.
  Public server: https://ntfy.sh — or run your own (see https://ntfy.sh/docs/).

Phone setup (once):
  1. Install the **ntfy** app from the App Store / Play Store.
  2. In the app, subscribe to a **topic** you invent — treat it like a password.
     Example topic (generate your own): ``k8mQp2_baby_status_x7``.
  3. Put that same string in ``NTFY_TOPIC`` below when you run this script.

Environment variables:
  HUCKLEBERRY_EMAIL, HUCKLEBERRY_PASSWORD, HUCKLEBERRY_TIMEZONE — same as other examples.
  NTFY_TOPIC — required; the topic you subscribed to in the app.
  NTFY_SERVER — optional; default ``https://ntfy.sh`` (no trailing slash).
  NTFY_TITLE — optional notification title (default: Huckleberry).

Usage:
  uv run python examples/push_ntfy_status.py

Cron example (every hour):
  0 * * * * cd /path/to/py-huckleberry-api && \
    export $(grep -v '^#' .env | xargs) && \
    uv run python examples/push_ntfy_status.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

import aiohttp

from huckleberry_api import HuckleberryAPI


def _env_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _ago(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    ts = float(seconds)
    delta = datetime.now(timezone.utc).timestamp() - ts
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m ago"


async def run(child_index: int) -> None:
    email = _env_required("HUCKLEBERRY_EMAIL")
    password = _env_required("HUCKLEBERRY_PASSWORD")
    tz_name = _env_required("HUCKLEBERRY_TIMEZONE")
    topic = _env_required("NTFY_TOPIC")

    # GitHub Actions sets missing secrets to ""; getenv(..., default) still returns "" if the var exists.
    ntfy_server = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    title = os.getenv("NTFY_TITLE") or "Huckleberry"

    async with aiohttp.ClientSession() as session:
        api = HuckleberryAPI(email=email, password=password, timezone=tz_name, websession=session)
        await api.authenticate()

        user = await api.get_user()
        if not user or not user.childList:
            print("No children on this account.", file=sys.stderr)
            sys.exit(1)

        if child_index < 0 or child_index >= len(user.childList):
            print(f"child-index {child_index} out of range (0..{len(user.childList) - 1}).", file=sys.stderr)
            sys.exit(1)

        child_uid = user.childList[child_index].cid

        feed = await api.get_feed_summary(child_uid)
        diaper = await api.get_diaper_summary(child_uid)

        lines: list[str] = []

        if feed and feed.prefs:
            lb = feed.prefs.lastBottle
            if lb and lb.start is not None:
                bt = lb.bottleType or "bottle"
                lines.append(f"Bottle ({bt}): {_ago(lb.start)}")
            else:
                lines.append("Bottle: —")

            ln = feed.prefs.lastNursing
            if ln and ln.start is not None:
                lines.append(f"Nursing: {_ago(ln.start)}")
            else:
                lines.append("Nursing: —")
        else:
            lines.append("Bottle: —")
            lines.append("Nursing: —")

        if feed and feed.timer and feed.timer.active:
            lines.append("Feed timer: active")

        if diaper and diaper.prefs and diaper.prefs.lastDiaper and diaper.prefs.lastDiaper.start is not None:
            ld = diaper.prefs.lastDiaper
            mode = ld.mode or "?"
            lines.append(f"Diaper ({mode}): {_ago(ld.start)}")
        else:
            lines.append("Diaper: —")

        body = "\n".join(lines)

        ntfy_url = f"{ntfy_server}/{topic}"
        headers = {
            "Title": title,
            "Tags": "baby,bottle",
        }

        async with session.post(ntfy_url, data=body.encode("utf-8"), headers=headers) as resp:
            if resp.status < 200 or resp.status >= 300:
                text = await resp.text()
                print(f"ntfy failed HTTP {resp.status}: {text}", file=sys.stderr)
                sys.exit(1)

        print("Sent notification to ntfy.")
        print(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Push Huckleberry status to ntfy.")
    parser.add_argument(
        "--child-index",
        type=int,
        default=0,
        metavar="N",
        help="Index into user.childList (default: 0)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.child_index))


if __name__ == "__main__":
    main()
