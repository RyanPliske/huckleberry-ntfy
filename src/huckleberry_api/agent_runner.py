"""pydantic-ai + OpenAI agent that calls ``HuckleberryAPI`` tools (bottle, diaper, nursing).

Used by ``examples/huckleberry_agent_cli.py`` and the Alexa Lambda handler. Install optional
extras ``[agent]`` (``pip install huckleberry-api[agent]``) or ``uv sync --group agent``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
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


_AGENT_INSTRUCTIONS = """\
You are a concise baby-care assistant. The caregiver has already selected one child in the app.
Use the tools to read or write Huckleberry data — do not invent events.

Rules:
- For bottles: ``bottle_type`` must be one of the allowed literals (default Formula). ``units`` is ml or oz.
- For diapers (writes): ``mode`` is exactly one of: pee, poo, both, dry.
- **Breastfeeding** = **nursing** = **breast feeding** (same Huckleberry action). Use ``log_breastfeeding_session``: either ``duration_minutes`` + optional ``end_time_iso`` (default now), OR both ``start_time_iso`` and ``end_time_iso``. ``side`` is left or right (ending side when using duration-only).
- For ``at_time_iso`` / time fields: null / omit / ``now`` means local now; otherwise ISO-8601 (naive = local zone).
- After a successful write, reply in one short sentence what was logged.
"""


agent = Agent(
    model=os.getenv("OPENAI_MODEL", "openai:gpt-4o-mini"),
    deps_type=HuckDeps,
    instructions=_AGENT_INSTRUCTIONS,
)


@agent.tool
async def get_last_feeding_summary(ctx: RunContext[HuckDeps]) -> str:
    """Return a short text summary of last bottle and last breast / nursing session for context (read-only)."""
    doc = await ctx.deps.api.get_feed_summary(ctx.deps.child_uid)
    if not doc or not doc.prefs:
        return "No feed summary on file."
    p = doc.prefs
    parts: list[str] = []
    if p.lastBottle and p.lastBottle.start is not None:
        parts.append(f"last bottle start epoch={p.lastBottle.start} type={p.lastBottle.bottleType!r}")
    if p.lastNursing and p.lastNursing.start is not None:
        parts.append(f"last nursing start epoch={p.lastNursing.start}")
    return "; ".join(parts) if parts else "No last bottle or nursing in prefs."


@agent.tool
async def get_last_diaper_summary(ctx: RunContext[HuckDeps]) -> str:
    """Return a short read-only summary from diaper prefs (e.g. last change mode and start)."""
    doc = await ctx.deps.api.get_diaper_summary(ctx.deps.child_uid)
    if not doc or not doc.prefs:
        return "No diaper summary on file."
    ld = doc.prefs.lastDiaper
    if ld is None or ld.start is None:
        return "No last diaper in prefs."
    mode = ld.mode if ld.mode is not None else "?"
    return f"last_diaper mode={mode!r} start_epoch={ld.start}"


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
