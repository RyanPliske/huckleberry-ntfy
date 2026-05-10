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
      Title includes ``CHILD_NAME`` (e.g. ``🟢 Nancy · Huckleberry``); body lines omit the name to avoid duplicate.

  FEED_ALERT_AFTER_MINUTES — day spacing for **feeds** in minutes (default: ``150`` = 2.5h).
  FEED_ALERT_NIGHT_AFTER_MINUTES — night spacing (default: ``180`` = 3h).
  FEED_ALERT_NIGHT_START_HOUR / FEED_ALERT_NIGHT_END_HOUR — local ``HUCKLEBERRY_TIMEZONE`` hours [0–23].
      Night is ``start`` through just before ``end``; default **22 → 7** (10pm–before-7am).
      Title ``🟢``/``🔴`` and urgent extras follow **last feed only**; diaper line is informational.
      **🔴** when there is no last bottle/nursing, **or** minutes since the newer of those is **≥** the
      active window (day ``150`` / night ``180`` by default). **🟢** when a last feed exists and age is **<** that window.
      Body feed line shows time until the next feed window (e.g. ``15m left``) or overdue (e.g. ``12m overdue``).
  FEED_ALERT_TITLE — title **suffix** after ``🔴`` when feed is overdue (default: ``Baby needs attention``).

  Voice Monkey → Alexa (optional): enable the skill and link a speaker in their app, then set:
  VOICE_MONKEY_TOKEN — API token (API Playground / API Tokens).
  VOICE_MONKEY_DEVICE — speaker id (e.g. ``echo-show-bz5bz`` from the playground URL).
  VOICE_MONKEY_GRACE_AFTER_FEED_MINUTES — Alexa only after this many minutes **past** the feed deadline
      (default: ``30``). Deadline = last feed + day/night window; ntfy can already be 🔴 before Alexa speaks.
      Requires a logged last feed; if there is none, Voice Monkey does not run (ntfy still 🔴).
      **GET** ``https://api-v3.voicemonkey.io/announce?token=…&device=…&speech=…`` — [API Playground](https://app.voicemonkey.io/playground).
      Failures log to stderr only; the process still exits 0 if ntfy succeeded.

  FEED_ALERT_WEBHOOK_URL — optional generic GET URL when feed is overdue (IFTTT, etc.); failures never fail the run.

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

# First name in ntfy title + feed line (edit if needed).
CHILD_NAME = "Nancy"


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


def _fmt_minutes_compact(total_minutes: float) -> str:
    """Human minutes/hours for countdowns (floors sub-hour)."""
    m = max(0.0, total_minutes)
    if m < 60:
        return f"{max(1, int(m))}m"
    h = int(m // 60)
    rem = int(m - h * 60)
    return f"{h}h" if rem == 0 else f"{h}h {rem}m"


def _until_next_feed_phrase(last_feed_ts: float, now_ts: float, window_minutes: float) -> str:
    """``15m left`` before the window ends, or ``12m overdue`` after."""
    elapsed_min = (now_ts - last_feed_ts) / 60.0
    remaining_min = window_minutes - elapsed_min
    if remaining_min > 0:
        if remaining_min < 1:
            secs = max(1, int(remaining_min * 60))
            return f"{secs}s left"
        return f"{_fmt_minutes_compact(remaining_min)} left"
    overdue_min = elapsed_min - window_minutes
    if overdue_min < 1:
        secs = max(1, int(overdue_min * 60))
        return f"{secs}s overdue"
    return f"{_fmt_minutes_compact(overdue_min)} overdue"


def _format_last_feed_line(
    ts: float | None, kind: str, tz_name: str, now_ts: float, window_minutes: float
) -> str:
    """Body feed line — clock of last feed + time left / overdue until next feed."""
    if ts is None:
        return "No feed · —"
    em = _feed_kind_emoji(kind)
    clock = _clock_ampm(ts, tz_name)
    tail = _until_next_feed_phrase(ts, now_ts, window_minutes)
    return f"{em} {clock} · {tail}"


def _emoji(ok: bool) -> str:
    """🟢 OK / 🔴 needs attention."""
    return "🟢" if ok else "🔴"


def _title_suffix_with_child(base: str, child_name: str) -> str:
    """Prefix child name so the ntfy Title (iOS banner) shows who the alert is for."""
    return f"{child_name} · {base}" if child_name else base


def _is_night_local(local_dt: datetime, start_hour: int, end_hour: int) -> bool:
    """True when ``local_dt`` falls in the night window (e.g. 22:00–06:59 for start=22, end=7)."""
    h = local_dt.hour
    start_hour %= 24
    end_hour %= 24
    if start_hour > end_hour:
        return h >= start_hour or h < end_hour
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return False


def _feed_alert_window_minutes(tz_name: str) -> float:
    """Day vs night target spacing between feeds (minutes), from *now* in ``tz_name``."""
    day = float(os.getenv("FEED_ALERT_AFTER_MINUTES") or "150")
    night = float(os.getenv("FEED_ALERT_NIGHT_AFTER_MINUTES") or "180")
    start_h = int(os.getenv("FEED_ALERT_NIGHT_START_HOUR") or "22")
    end_h = int(os.getenv("FEED_ALERT_NIGHT_END_HOUR") or "7")
    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
    return night if _is_night_local(local_now, start_h, end_h) else day


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
        last_feed_ts, last_feed_kind = _last_feed_info(prefs)
        now_ts = datetime.now(timezone.utc).timestamp()
        alert_after = _feed_alert_window_minutes(tz_name)

        feed_ok = False
        feed_age_min: float | None = None
        if last_feed_ts is not None:
            feed_age_min = (now_ts - last_feed_ts) / 60.0
            feed_ok = feed_age_min < alert_after

        # 🔴/urgent/warning: feed window only — diaper does not change title or priority styling.
        needs_attention = not feed_ok

        vm_grace = float(os.getenv("VOICE_MONKEY_GRACE_AFTER_FEED_MINUTES") or "30")
        voice_monkey_fire = (
            last_feed_ts is not None
            and feed_age_min is not None
            and feed_age_min >= alert_after + vm_grace
        )

        feed_text = _format_last_feed_line(last_feed_ts, last_feed_kind, tz_name, now_ts, alert_after)
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
        title_suffix = _title_suffix_with_child(title_suffix, CHILD_NAME)
        use_title = f"{_emoji(feed_ok)} {title_suffix}"
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
            if vm_token and vm_device and voice_monkey_fire:
                who = CHILD_NAME
                alexa_text = (
                    f"{who} is {int(vm_grace)} minutes past feeding time — "
                    f"please feed {who} now."
                )
                # v3: GET/POST https://api-v3.voicemonkey.io/announce — token + device in query (Playground default).
                # TTS body field is ``speech`` (v2 used ``text`` on /announcement). See https://voicemonkey.io/docs/api
                vm_url = "https://api-v3.voicemonkey.io/announce"
                vm_params = {"token": vm_token, "device": vm_device, "speech": alexa_text}
                try:
                    async with session.get(
                        vm_url,
                        params=vm_params,
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
