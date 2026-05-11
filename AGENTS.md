# Huckleberry API

## Source of Truth

- Firebase payload findings and schema discoveries live in:
  - `src/huckleberry_api/firebase_types.py`
- Behavior and write/read logic live in:
  - `src/huckleberry_api/api.py`

## Critical Rules

1. **Validate values before adding/changing them**
   - For enums, modes, units, state values, keys, or option lists originating from the app/Firebase schema, validate against APK/Firebase evidence first.
   - Never add guessed values. If key or value cannot ve verified with decompiled sources or live data, it must not be added.

2. **Keep types strict**
   - Prefer explicit strict models and constrained literals.
   - Avoid loosening to broad `Any`/open dicts/lists.

3. **Use `uv` for Python commands**
   - Run tests and Python commands with `uv` (for example: `uv run pytest ...`).

4. **Keep discoveries near code**
   - Add new schema findings as comments/docstrings on the relevant classes/fields in `firebase_types.py`.

## Reverse-Engineering Workflow

- Use: `.copilot/skills/huckleberry-apk-reverse/SKILL.md`
- Current decompilation context: `jadx output latest/`

## Experimental LLM agent

- `src/huckleberry_api/agent_runner.py` — shared **pydantic-ai** + OpenAI tools (`run_agent_prompt`). Tool list + NLU vs tools: `_docs/agent-tools-and-alexa-nlu.md`.
- `examples/huckleberry_agent_cli.py` — local CLI (`uv sync --group agent` or `pip install .[agent]`).
- `alexa/` + root `template.yaml` — **Alexa skill** Lambda (ASK SDK); build with SAM, endpoint = Lambda ARN (no API Gateway). Sample invocation name **`huckle berry`** (two words) in `alexa/interaction_model_en_US.json`. See `alexa/README.md` (`uv sync --group alexa` or `pip install .[alexa]`). **Voice vs chat agent, NLU limits, plan forward:** `_docs/alexa-voice-agent-plan.md`.

## Maintenance

When discovering new payload structures or semantics:
- Update `src/huckleberry_api/firebase_types.py` first.
- Add/update tests as needed.
