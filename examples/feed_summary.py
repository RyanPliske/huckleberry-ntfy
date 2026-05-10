#!/usr/bin/env python3
"""Print latest feed and diaper summaries from Huckleberry Firestore.

Requires environment variables (same as integration tests):

  HUCKLEBERRY_EMAIL      — account email
  HUCKLEBERRY_PASSWORD   — account password
  HUCKLEBERRY_TIMEZONE    — IANA zone, e.g. America/Denver

From the repo root (with dev deps / editable install so ``huckleberry_api`` resolves):

  uv run python examples/feed_summary.py

Optional:

  --child-index N   — use childList[N] (default 0)
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


def _fmt_ts(seconds: float | int | None) -> str:
    if seconds is None:
        return "(none)"
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()


async def run(child_index: int) -> None:
    email = _env_required("HUCKLEBERRY_EMAIL")
    password = _env_required("HUCKLEBERRY_PASSWORD")
    tz_name = _env_required("HUCKLEBERRY_TIMEZONE")

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
        print(f"Child UID: {child_uid}")
        print()

        feed = await api.get_feed_summary(child_uid)
        if feed is None:
            print("feed/{child_uid}: document missing or unreadable.")
        else:
            timer = feed.timer
            if timer:
                print(f"Feed timer active: {timer.active}  paused: {timer.paused}")
            prefs = feed.prefs
            if prefs and prefs.lastBottle and prefs.lastBottle.start is not None:
                lb = prefs.lastBottle
                print("Last bottle:")
                print(f"  start (UTC): {_fmt_ts(lb.start)}")
                print(f"  type:        {lb.bottleType}")
                print(f"  amount:      {lb.bottleAmount} {lb.bottleUnits or ''}".rstrip())
            else:
                print("Last bottle: (no summary in prefs)")

            if prefs and prefs.lastNursing and prefs.lastNursing.start is not None:
                ln = prefs.lastNursing
                print("Last nursing:")
                print(f"  start (UTC): {_fmt_ts(ln.start)}")
                print(f"  duration s:  {ln.duration}")
                print(f"  left / right s: {ln.leftDuration} / {ln.rightDuration}")
            else:
                print("Last nursing: (no summary in prefs)")

        print()

        diaper = await api.get_diaper_summary(child_uid)
        if diaper is None:
            print("diaper/{child_uid}: document missing or unreadable.")
        elif diaper.prefs and diaper.prefs.lastDiaper and diaper.prefs.lastDiaper.start is not None:
            ld = diaper.prefs.lastDiaper
            print("Last diaper:")
            print(f"  start (UTC): {_fmt_ts(ld.start)}")
            print(f"  mode:        {ld.mode}")
        else:
            print("Last diaper: (no summary in prefs)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Huckleberry feed/diaper summaries.")
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
