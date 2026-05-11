#!/usr/bin/env python3
"""Experimental **pydantic-ai** + **OpenAI** agent that calls `HuckleberryAPI` tools (CRUD-style writes).

This is a **local CLI** proof-of-concept for natural-language logging (e.g. “2 oz formula just now”).
The same agent logic lives in ``huckleberry_api.agent_runner`` for Lambda / Alexa.

Dependencies (extra group):

  uv sync --group agent

Environment:

  HUCKLEBERRY_EMAIL, HUCKLEBERRY_PASSWORD, HUCKLEBERRY_TIMEZONE — same as other examples.
  OPENAI_API_KEY — from https://platform.openai.com/api-keys
  OPENAI_MODEL — optional; default ``openai:gpt-4o-mini`` (pydantic-ai model string).

Usage:

  uv run --group agent python examples/huckleberry_agent_cli.py "Log 2 ounces of formula now"

  uv run --group agent python examples/huckleberry_agent_cli.py --child-index 0 "Log a pee diaper"

  uv run --group agent python examples/huckleberry_agent_cli.py "Log 10 minutes of breast feeding on the left ending now"

Tools map to `api.log_bottle`, `api.log_diaper`, `api.log_nursing` (tool name ``log_breastfeeding_session``),
and read-only summaries. Enum values must match ``firebase_types.py`` (the model is instructed; tools validate).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from huckleberry_api.agent_runner import run_agent_prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Huckleberry + pydantic-ai + OpenAI (experimental).")
    parser.add_argument("prompt", help="Natural language instruction for the agent")
    parser.add_argument(
        "--child-index",
        type=int,
        default=0,
        metavar="N",
        help="Index into user.childList (default: 0)",
    )
    args = parser.parse_args()

    async def _run() -> None:
        try:
            out = await run_agent_prompt(args.prompt, child_index=args.child_index)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e
        print(out)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
