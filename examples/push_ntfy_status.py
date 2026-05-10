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
  NTFY_TITLE — optional title **suffix** after the status emoji when all OK (default: ``Huckleberry``).
      The posted title is always ``🟢`` or ``🔴`` plus a space plus this text (or the alert title when red).

  NOTIFY_CHILD_NAME — optional first name for the message, e.g. ``Nancy``. Feed line looks like
      ``Nancy · 🍼 2:07p · 2h ago`` (local clock from ``HUCKLEBERRY_TIMEZONE`` + relative; 🤱 = nursing).

  FEED_ALERT_AFTER_MINUTES — one window (default: 120) for **both** last feed and last diaper:
      title shows ``🟢`` only if **both** are within the window; otherwise ``🔴``.
      ntfy **Priority** is always ``urgent`` (max) so every run surfaces the same (last fed, etc.).
  FEED_ALERT_TITLE — title **suffix** after ``🔴`` when attention needed (default: ``Baby needs attention``).

  Voice Monkey → Alexa (optional): enable the Voice Monkey skill, create a device in their console,
      then set:
  VOICE_MONKEY_TOKEN — API token from Voice Monkey console.
  VOICE_MONKEY_DEVICE — device id for the Echo you want to speak.
      When status is 🔴 (feed or diaper outside the window), the script POSTs an announcement
      (same cadence as this script — use a less frequent external cron if you only want occasional Alexa nags).
      Failures are logged to stderr only; the process still exits 0 if ntfy succeeded.

  FEED_ALERT_WEBHOOK_URL — optional generic GET URL when status is 🔴 (IFTTT, etc.); failures never fail the run.

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
from zoneinfo import ZoneInfo

import aiohttp

from huckleberry_api import HuckleberryAPI


def _env_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _last_feed_info(feed_prefs: object) -> tuple[float | None, str]:
    """Most recent feed: timestamp (epoch s) and kind label (bottle type from Huckleberry or ``nursing``)."""
    if feed_prefs is None:
        return None, ""
    lb = getattr(feed_prefs, "lastBottle", None)
    ln = getattr(feed_prefs, "lastNursing", None)
    tb = float(lb.start) if lb is not None and getattr(lb, "start", None) is not None else None
    tn = float(ln.start) if ln is not None and getattr(ln, "start", None) is not None else None

    if tb is None and tn is None:
        return None, ""
    if tb is None:
        return tn, "nursing"
    if tn is None:
        bottle_label = getattr(lb, "bottleType", None) or "bottle"
        return tb, str(bottle_label)
    if tb >= tn:
        bottle_label = getattr(lb, "bottleType", None) or "bottle"
        return tb, str(bottle_label)
    return tn, "nursing"


def _clock_ampm(ts: float, tz_name: str) -> str:
    """Local time like ``2:07p`` (12h, no space before am/pm)."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    h12 = dt.hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"


def _feed_kind_emoji(kind: str) -> str:
    return "🤱" if kind == "nursing" else "🍼"


def _diaper_mode_emoji(mode: str | None) -> str:
    if mode == "pee":
        return "💧"
    if mode == "poo":
        return "💩"
    if mode == "both":
        return "💧💩"
    if mode == "dry":
        return "✅"
    return "🧷"


def _format_last_feed_line(child_name: str, ts: float | None, kind: str, tz_name: str) -> str:
    """Single summary line: clock + relative; bottle vs nursing as emoji only."""
    if ts is None:
        return f"{child_name} · no feed" if child_name else "No feed · —"
    em = _feed_kind_emoji(kind)
    clock = _clock_ampm(ts, tz_name)
    ago_s = _ago(ts)
    if child_name:
        return f"{child_name} · {em} {clock} · {ago_s}"
    return f"{em} {clock} · {ago_s}"


def _emoji(ok: bool) -> str:
    """🟢 OK / 🔴 needs attention."""
    return "🟢" if ok else "🔴"


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

        prefs = feed.prefs if feed else None
        child_name = (os.getenv("NOTIFY_CHILD_NAME") or "").strip()
        last_feed_ts, last_feed_kind = _last_feed_info(prefs)
        now_ts = datetime.now(timezone.utc).timestamp()
        alert_after = float(os.getenv("FEED_ALERT_AFTER_MINUTES") or "120")

        feed_ok = False
        if last_feed_ts is not None:
            feed_ok = (now_ts - last_feed_ts) / 60.0 < alert_after

        diaper_ok = False
        if diaper and diaper.prefs and diaper.prefs.lastDiaper and diaper.prefs.lastDiaper.start is not None:
            ld = diaper.prefs.lastDiaper
            d_ts = float(ld.start)
            diaper_ok = (now_ts - d_ts) / 60.0 < alert_after

        overall_ok = feed_ok and diaper_ok
        needs_attention = not overall_ok

        feed_text = _format_last_feed_line(child_name, last_feed_ts, last_feed_kind, tz_name)
        lines: list[str] = [feed_text]

        if feed and feed.timer and feed.timer.active:
            lines.append("Feed timer: active")

        if diaper and diaper.prefs and diaper.prefs.lastDiaper and diaper.prefs.lastDiaper.start is not None:
            ld = diaper.prefs.lastDiaper
            d_ts = float(ld.start)
            dem = _diaper_mode_emoji(ld.mode)
            lines.append(f"{dem} {_clock_ampm(d_ts, tz_name)} · {_ago(d_ts)}")
        else:
            lines.append("🧷 —")

        body = "\n".join(lines)

        ntfy_url = f"{ntfy_server}/{topic}"
        title_suffix = (
            (os.getenv("FEED_ALERT_TITLE") or "Baby needs attention")
            if needs_attention
            else title
        )
        use_title = f"{_emoji(overall_ok)} {title_suffix}"
        headers: dict[str, str] = {
            "Title": use_title,
            "Tags": "baby,bottle,warning" if needs_attention else "baby,bottle",
            # https://docs.ntfy.sh/publish/#priority — always max so routine “last fed” pings aren’t downgraded.
            "Priority": "urgent",
        }

        async with session.post(ntfy_url, data=body.encode("utf-8"), headers=headers) as resp:
            if resp.status < 200 or resp.status >= 300:
                text = await resp.text()
                print(f"ntfy failed HTTP {resp.status}: {text}", file=sys.stderr)
                sys.exit(1)

        # Optional extras: never call sys.exit — ntfy above is the only hard requirement.
        if needs_attention:
            vm_token = os.getenv("VOICE_MONKEY_TOKEN")
            vm_device = os.getenv("VOICE_MONKEY_DEVICE")
            if vm_token and vm_device:
                who = child_name or "the baby"
                alexa_text = (
                    f"Check on {who}. Feed or diaper may need attention — "
                    f"over {int(alert_after)} minutes since the last check-in window."
                )
                vm_url = "https://api-v2.voicemonkey.io/announcement"
                try:
                    async with session.post(
                        vm_url,
                        headers={
                            "Authorization": vm_token,
                            "Content-Type": "application/json",
                        },
                        json={"device": vm_device, "text": alexa_text},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as vm_resp:
                        if vm_resp.status < 200 or vm_resp.status >= 300:
                            vtxt = await vm_resp.text()
                            print(f"Voice Monkey HTTP {vm_resp.status}: {vtxt}", file=sys.stderr)
                        else:
                            print("Sent Voice Monkey announcement.")
                except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
                    print(f"Voice Monkey request failed: {err}", file=sys.stderr)
                except Exception as err:
                    print(f"Voice Monkey failed (ignored): {err}", file=sys.stderr)

            hook = os.getenv("FEED_ALERT_WEBHOOK_URL")
            if hook:
                try:
                    async with session.get(hook, timeout=aiohttp.ClientTimeout(total=30)) as h_resp:
                        if h_resp.status < 200 or h_resp.status >= 300:
                            print(f"FEED_ALERT_WEBHOOK_URL HTTP {h_resp.status}", file=sys.stderr)
                        else:
                            print("Called FEED_ALERT_WEBHOOK_URL.")
                except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
                    print(f"Webhook failed: {err}", file=sys.stderr)
                except Exception as err:
                    print(f"Webhook failed (ignored): {err}", file=sys.stderr)

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
