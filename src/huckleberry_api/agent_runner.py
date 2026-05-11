"""pydantic-ai + OpenAI agent that calls ``HuckleberryAPI`` tools (bottle, diaper, nursing).

Used by ``examples/huckleberry_agent_cli.py`` and the Alexa Lambda handler. Install optional
extras ``[agent]`` (``pip install huckleberry-api[agent]``) or ``uv sync --group agent``.

Optional **Lambda / CLI** env for ``get_feed_timing_hint`` (same as ``examples/push_ntfy_status.py``):
``FEED_ALERT_AFTER_MINUTES``, ``FEED_ALERT_NIGHT_AFTER_MINUTES``, ``FEED_ALERT_NIGHT_START_HOUR``,
``FEED_ALERT_NIGHT_END_HOUR``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from pydantic_ai import Agent, RunContext

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import BottleType, DiaperMode, FeedSide, VolumeUnits


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def _parse_event_time(at_time_iso: str | None, tz_name: str) -> datetime:
    """Local ``now`` or ISO8601 (naive times are interpreted in ``tz_name``)."""
    if at_time_iso is None or not str(at_time_iso).strip() or str(at_time_iso).strip().lower() == "now":
        return datetime.now(ZoneInfo(tz_name))
    raw = str(at_time_iso).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(ZoneInfo(tz_name))


@dataclass
class HuckDeps:
    """Per-run deps passed into ``RunContext`` for tools."""

    api: HuckleberryAPI
    child_uid: str
    tz_name: str


def _raw_start_to_epoch_seconds(raw: object) -> float | None:
    """Huckleberry prefs ``start`` may be Unix **seconds** or **milliseconds**; normalize to seconds."""
    if raw is None:
        return None
    x = float(raw)
    if x > 1e12:
        x /= 1000.0
    return x


def _last_feed_epoch_seconds_and_kind(prefs: object) -> tuple[float | None, str]:
    """Most recent feed: epoch seconds and kind label (same semantics as ``push_ntfy_status._last_feed_info``)."""
    if prefs is None:
        return None, ""
    lb = getattr(prefs, "lastBottle", None)
    ln = getattr(prefs, "lastNursing", None)
    tb = _raw_start_to_epoch_seconds(getattr(lb, "start", None)) if lb is not None else None
    tn = _raw_start_to_epoch_seconds(getattr(ln, "start", None)) if ln is not None else None

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


def _is_night_local(local_dt: datetime, start_hour: int, end_hour: int) -> bool:
    """True when ``local_dt`` is in the night window (e.g. 22:00–06:59 for start=22, end=7)."""
    h = local_dt.hour
    start_hour %= 24
    end_hour %= 24
    if start_hour > end_hour:
        return h >= start_hour or h < end_hour
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return False


def _feed_alert_window_minutes_from_env(tz_name: str) -> float:
    """Day vs night spacing between feeds (minutes) — env mirrors ``examples/push_ntfy_status.py``."""
    day = float(os.getenv("FEED_ALERT_AFTER_MINUTES") or "150")
    night = float(os.getenv("FEED_ALERT_NIGHT_AFTER_MINUTES") or "180")
    start_h = int(os.getenv("FEED_ALERT_NIGHT_START_HOUR") or "22")
    end_h = int(os.getenv("FEED_ALERT_NIGHT_END_HOUR") or "7")
    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
    return night if _is_night_local(local_now, start_h, end_h) else day


def _local_ampm(ts: float, tz_name: str) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    h12 = dt.hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"


def _age_phrase_seconds(ts: float) -> str:
    delta = max(0.0, datetime.now(timezone.utc).timestamp() - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h, rem = divmod(int(delta), 3600)
    m = rem // 60
    if m:
        return f"{h} hour{'s' if h != 1 else ''} and {m} minutes ago"
    return f"{h} hour{'s' if h != 1 else ''} ago"


def _format_local_feed_time(ts: float, tz_name: str) -> str:
    """Spoken-style local time; adds calendar day if not today (``HUCKLEBERRY_TIMEZONE``)."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
    clock = _local_ampm(ts, tz_name)
    if dt.date() == now_local.date():
        return f"{clock} today"
    return f"{clock} on {dt.strftime('%b')} {dt.day}"


def _diaper_mode_spoken(mode: DiaperMode | None) -> str:
    """Short parent-facing label for voice (Huckleberry ``mode`` literals)."""
    if mode is None:
        return "a diaper change"
    return {
        "pee": "wet (pee)",
        "poo": "dirty (poop)",
        "both": "wet and dirty",
        "dry": "a dry check",
    }.get(mode, repr(mode))


_AGENT_INSTRUCTIONS = """\
You are a concise baby-care assistant. The caregiver has already selected one child in the app.
Use the tools to read or write Huckleberry data — do not invent events.

Rules:
- For bottles: ``bottle_type`` must be one of the allowed literals (default Formula). ``units`` is ml or oz.
- For diapers (writes): ``mode`` is exactly one of: pee, poo, both, dry.
- **Breastfeeding** = **nursing** = **breast feeding** (same Huckleberry action). Use ``log_breastfeeding_session``: either ``duration_minutes`` + optional ``end_time_iso`` (default now), OR both ``start_time_iso`` and ``end_time_iso``. ``side`` is left or right (ending side when using duration-only).
- For ``at_time_iso`` / time fields: null / omit / ``now`` means local now; otherwise ISO-8601 (naive = local zone).
- After a successful write, reply in one short sentence what was logged.
- **Whenever** the user asks **when** the last feed was, **what time**, or **how long ago**: you **MUST** call ``get_feed_timing_hint`` or ``get_last_feeding_summary`` first — **never** answer from memory or from bottle type alone. Your reply **MUST** include the **local clock time** the tool returns (for example 2:30p today or 2:30 PM).
- **Whenever** the user asks **when** the last diaper was, **last diaper change**, **last wet diaper**, **poopy diaper**, or **how long since** a diaper: you **MUST** call ``get_last_diaper_summary`` first — **never** guess. Your reply **MUST** include the **local clock time** the tool returns.
- For **next feed due** or **overdue**: call ``get_feed_timing_hint`` first. Use ``get_last_feeding_summary`` when both last bottle and last nursing times matter separately.
"""


agent = Agent(
    model=os.getenv("OPENAI_MODEL", "openai:gpt-4o-mini"),
    deps_type=HuckDeps,
    instructions=_AGENT_INSTRUCTIONS,
)


@agent.tool
async def get_feed_timing_hint(ctx: RunContext[HuckDeps]) -> str:
    """Read-only: **local clock time** of last feed (bottle vs nursing, whichever is newer), how long ago, due / overdue.

    Always includes a speakable local time (12h + am/pm). Spacing uses the same optional env vars as
    ``examples/push_ntfy_status.py``: ``FEED_ALERT_AFTER_MINUTES`` (default 150),
    ``FEED_ALERT_NIGHT_AFTER_MINUTES`` (default 180), ``FEED_ALERT_NIGHT_START_HOUR`` / ``FEED_ALERT_NIGHT_END_HOUR`` (defaults 22 and 7, local ``HUCKLEBERRY_TIMEZONE``). Not medical advice — same heuristic as the ntfy script.
    """
    tz = ctx.deps.tz_name
    doc = await ctx.deps.api.get_feed_summary(ctx.deps.child_uid)
    if not doc or not doc.prefs:
        return "No feed summary on file."
    last_ts, kind = _last_feed_epoch_seconds_and_kind(doc.prefs)
    if last_ts is None:
        return "No last bottle or nursing found in feed prefs."

    window_min = _feed_alert_window_minutes_from_env(tz)
    ago = _age_phrase_seconds(last_ts)
    due_ts = last_ts + window_min * 60.0
    due_clock = _local_ampm(due_ts, tz)
    now_ts = datetime.now(timezone.utc).timestamp()
    overdue_min = (now_ts - due_ts) / 60.0

    when_spoken = _format_local_feed_time(last_ts, tz)
    kind_spoken = "nursing" if kind == "nursing" else f"bottle ({kind})"
    base = (
        f"Last feed was {kind_spoken} at {when_spoken} local time ({ago}). "
        f"Using a spacing window of {int(window_min)} minutes from prefs, the next feed would be around {due_clock} local."
    )
    if overdue_min > 0:
        om = int(round(overdue_min))
        base += f" That is about {om} minute{'s' if om != 1 else ''} overdue by that heuristic."
    else:
        remain_min = int(round((due_ts - now_ts) / 60.0))
        if remain_min <= 0:
            base += " That window is about up now."
        else:
            base += f" About {remain_min} minute{'s' if remain_min != 1 else ''} until that due time."
    return base


@agent.tool
async def get_last_feeding_summary(ctx: RunContext[HuckDeps]) -> str:
    """Return last bottle and last nursing with **local speakable times** (not raw epochs) for voice replies."""
    tz = ctx.deps.tz_name
    doc = await ctx.deps.api.get_feed_summary(ctx.deps.child_uid)
    if not doc or not doc.prefs:
        return "No feed summary on file."
    p = doc.prefs
    parts: list[str] = []
    if p.lastBottle and p.lastBottle.start is not None:
        ts = _raw_start_to_epoch_seconds(p.lastBottle.start)
        if ts is not None:
            bt = p.lastBottle.bottleType or "bottle"
            parts.append(
                f"Last bottle ({bt}): {_format_local_feed_time(ts, tz)} — {_age_phrase_seconds(ts)}"
            )
    if p.lastNursing and p.lastNursing.start is not None:
        ts = _raw_start_to_epoch_seconds(p.lastNursing.start)
        if ts is not None:
            parts.append(f"Last nursing: {_format_local_feed_time(ts, tz)} — {_age_phrase_seconds(ts)}")
    return "; ".join(parts) if parts else "No last bottle or nursing in prefs."


@agent.tool
async def get_last_diaper_summary(ctx: RunContext[HuckDeps]) -> str:
    """Read-only: last diaper change with **local speakable time** and how long ago (prefs ``start`` normalized).

    Use for any question about **when** the last diaper was or **how long ago** it was.
    """
    tz = ctx.deps.tz_name
    doc = await ctx.deps.api.get_diaper_summary(ctx.deps.child_uid)
    if not doc or not doc.prefs:
        return "No diaper summary on file."
    ld = doc.prefs.lastDiaper
    if ld is None or ld.start is None:
        return "No last diaper in prefs."
    ts = _raw_start_to_epoch_seconds(ld.start)
    if ts is None:
        return "No last diaper timestamp in prefs."
    mode_phrase = _diaper_mode_spoken(ld.mode)
    when_spoken = _format_local_feed_time(ts, tz)
    ago = _age_phrase_seconds(ts)
    return f"Last diaper was {mode_phrase} at {when_spoken} local time ({ago})."


@agent.tool
async def log_breastfeeding_session(
    ctx: RunContext[HuckDeps],
    duration_minutes: float | None = None,
    start_time_iso: str | None = None,
    end_time_iso: str | None = None,
    side: FeedSide = "left",
) -> str:
    """Log a completed **breastfeeding** session (synonyms: **nursing**, **breast feeding**).

    Calls Huckleberry ``log_nursing``. **Either** pass ``duration_minutes`` (session length; end defaults to now
    / ``end_time_iso``), **or** pass both ``start_time_iso`` and ``end_time_iso`` for an explicit interval.
    """
    tz = ctx.deps.tz_name

    def _non_empty(s: str | None) -> bool:
        return bool(s and str(s).strip())

    if _non_empty(start_time_iso) and _non_empty(end_time_iso):
        start = _parse_event_time(start_time_iso, tz)
        end = _parse_event_time(end_time_iso, tz)
    elif duration_minutes is not None and duration_minutes > 0:
        end = _parse_event_time(end_time_iso, tz)
        start = end - timedelta(minutes=float(duration_minutes))
    else:
        return (
            "Refused: provide ``duration_minutes`` (positive) with optional ``end_time_iso``, "
            "or both ``start_time_iso`` and ``end_time_iso``."
        )

    if end <= start:
        return "Refused: end time must be after start time."
    if side == "none":
        return "Refused: use side 'left' or 'right' for breastfeeding (not 'none')."

    await ctx.deps.api.log_nursing(
        ctx.deps.child_uid,
        start_time=start,
        end_time=end,
        side=side,
    )
    return f"Logged breastfeeding: {start.isoformat()} → {end.isoformat()} side={side!r}."


@agent.tool
async def log_bottle_feeding(
    ctx: RunContext[HuckDeps],
    amount: float,
    units: VolumeUnits = "oz",
    bottle_type: BottleType = "Formula",
    at_time_iso: str | None = None,
) -> str:
    """Log a completed bottle feed at the given time (default: now, local timezone)."""
    if amount <= 0:
        return "Refused: amount must be positive."
    start = _parse_event_time(at_time_iso, ctx.deps.tz_name)
    await ctx.deps.api.log_bottle(
        ctx.deps.child_uid,
        start_time=start,
        amount=amount,
        bottle_type=bottle_type,
        units=units,
    )
    return f"Logged bottle: {amount} {units} {bottle_type!r} at {start.isoformat()}."


@agent.tool
async def log_diaper_change(
    ctx: RunContext[HuckDeps],
    mode: DiaperMode,
    at_time_iso: str | None = None,
) -> str:
    """Log a diaper change (pee / poo / both / dry) at the given time (default: now)."""
    start = _parse_event_time(at_time_iso, ctx.deps.tz_name)
    await ctx.deps.api.log_diaper(ctx.deps.child_uid, start_time=start, mode=mode)
    return f"Logged diaper mode={mode!r} at {start.isoformat()}."


async def run_agent_prompt(prompt: str, *, child_index: int = 0) -> str:
    """Run the LLM agent with ``prompt`` against Huckleberry for ``user.childList[child_index]``.

    Raises ``ValueError`` for missing configuration or invalid ``child_index``.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("Missing OPENAI_API_KEY")

    email = _require_env("HUCKLEBERRY_EMAIL")
    password = _require_env("HUCKLEBERRY_PASSWORD")
    tz_name = _require_env("HUCKLEBERRY_TIMEZONE")

    async with aiohttp.ClientSession() as session:
        api = HuckleberryAPI(email=email, password=password, timezone=tz_name, websession=session)
        await api.authenticate()
        user = await api.get_user()
        if not user or not user.childList:
            raise ValueError("No children on this account.")
        if child_index < 0 or child_index >= len(user.childList):
            raise ValueError(f"child_index {child_index} out of range (0..{len(user.childList) - 1}).")

        child_uid = user.childList[child_index].cid
        deps = HuckDeps(api=api, child_uid=child_uid, tz_name=tz_name)
        result = await agent.run(prompt, deps=deps)
        return str(result.output)
