# Agent tools vs Alexa NLU

Two different layers improve “how smart” the system feels:

1. **Alexa NLU** (`alexa/interaction_model_en_US.json`) — decides **which intent** fired and what lands in **slots** before Lambda runs. More **sample utterances** and intents ⇒ more ways speech becomes a **string** passed to the agent. This does **not** add Huckleberry capabilities by itself.

2. **pydantic-ai tools** (`src/huckleberry_api/agent_runner.py`) — what the **OpenAI model** can **call** once your prompt is in Lambda. New tools ⇒ new **data or actions** the model can use. Documented below.

Voice stack overview and roadmap: [`alexa-voice-agent-plan.md`](alexa-voice-agent-plan.md).

---

## Tools available to the agent (Huckleberry + voice)

| Tool | Purpose |
|------|--------|
| **`get_feed_timing_hint`** | Read-only: last feed (bottle vs nursing, newer wins), local-ish clock, **how long ago**, **due time** and **overdue / minutes until due** using the same **spacing heuristic** as `examples/push_ntfy_status.py` (`FEED_ALERT_*` env vars). Best for “when was the last feeding?” and “when does she need fed again?” style questions. |
| **`get_last_feeding_summary`** | Read-only: last **bottle** and last **nursing** each with **local speakable time** and “ago” (prefs `start` normalized from seconds or milliseconds). |
| **`get_last_diaper_summary`** | Read-only: last diaper mode + start epoch from prefs. |
| **`log_bottle_feeding`** | Write: log a bottle. |
| **`log_diaper_change`** | Write: log a diaper. |
| **`log_breastfeeding_session`** | Write: log nursing / breast feeding (`log_nursing`). |

Adding a **new tool** here (and redeploying Lambda) gives the LLM new **facts or actions**. Teach it when to use the tool via tool **docstrings** and **`_AGENT_INSTRUCTIONS`**.

---

## Making Alexa “get it right” more often

That is **only** the interaction model (plus user phrasing):

- Add **carrier + `{query}`** patterns for `AMAZON.SearchQuery` (`CaptureQueryIntent`).
- Add phrases to **`AMAZON.HelpIntent`** for meta questions (still a fixed help prompt unless the text is in `query`).
- Tune **`AMAZON.FallbackIntent`** copy when nothing matches.

Each **session turn** still goes through NLU; opening the skill does **not** bypass it. See [`alexa-voice-agent-plan.md`](alexa-voice-agent-plan.md) § *Session open ≠ bypassing NLU*.

---

## Optional env vars (feed timing tool)

Mirrors `examples/push_ntfy_status.py` (defaults in parentheses):

| Variable | Default | Meaning |
|----------|---------|--------|
| `FEED_ALERT_AFTER_MINUTES` | `150` | Day spacing between feeds (minutes). |
| `FEED_ALERT_NIGHT_AFTER_MINUTES` | `180` | Night spacing (minutes). |
| `FEED_ALERT_NIGHT_START_HOUR` | `22` | Night window start (local `HUCKLEBERRY_TIMEZONE`). |
| `FEED_ALERT_NIGHT_END_HOUR` | `7` | Night window end (local). |

Set these on **Lambda** (and locally when testing) if you want Alexa’s “due in X” line to match your ntfy cron.

---

## Related files

- `src/huckleberry_api/agent_runner.py` — tools + `run_agent_prompt`
- `alexa/interaction_model_en_US.json` — NLU
- `alexa/app.py` — Lambda → agent
- `examples/push_ntfy_status.py` — reference heuristic for feed windows
