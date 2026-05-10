# Reliable triggers (skip flaky GitHub `schedule`)

Native [`schedule`](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) is best-effort (delay/drops). For predictable runs, **trigger this workflow from outside GitHub** on your own clock.

This repo’s workflow listens for:

- **`workflow_dispatch`** — manual button, or API below  
- **`repository_dispatch`** with event type **`ntfy`** — one HTTPS POST (good for [cron-job.org](https://cron-job.org), Uptime Kuma, VPS `cron`, etc.)

## 1. Create a token

- **Classic PAT:** [Developer settings → Tokens](https://github.com/settings/tokens) → Generate → scope **`repo`** (repo is private) or minimal access if you tighten later.  
- **Fine-grained PAT:** Repository access to `huckleberry-ntfy` → **Contents: Read**, **Metadata: Read**, and permissions to **dispatch** (under “Actions” / “Contents” as required by GitHub for your account type—if dispatch fails, use classic `repo` for simplicity).

Store the token only in your scheduler’s secret store, not in the repo.

## 2. Fire the workflow (pick one)

### A. `repository_dispatch` (recommended)

```bash
export GITHUB_TOKEN="ghp_xxxxxxxx"   # your PAT

curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -d '{"event_type":"ntfy"}' \
  https://api.github.com/repos/RyanPliske/huckleberry-ntfy/dispatches
```

`event_type` must be **`ntfy`** (matches the workflow).

### B. `workflow_dispatch`

```bash
curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -d '{"ref":"main"}' \
  "https://api.github.com/repos/RyanPliske/huckleberry-ntfy/actions/workflows/ntfy-status.yml/dispatches"
```

## 3. Point an external cron at A or B

Example: **cron-job.org** → new job → **URL** = `https://api.github.com/repos/RyanPliske/huckleberry-ntfy/dispatches` → method **POST** → body `{"event_type":"ntfy"}` → add header `Authorization: Bearer <PAT>` and `Accept: application/vnd.github+json` → schedule every 5–15 minutes.

## 4. Verify

**Actions** tab should show a run with event **`repository_dispatch`** (or **`workflow_dispatch`** if you used B).

Then rotate the PAT if it ever leaks.

## 5. Feed urgency + Alexa (optional env / GitHub Secrets)

`examples/push_ntfy_status.py` compares **last bottle** vs **last nursing** and uses the **newer** timestamp as “last feed.” **`FEED_ALERT_AFTER_MINUTES`** (default `150` = 2.5h day) and **`FEED_ALERT_NIGHT_AFTER_MINUTES`** (default `180` = 3h) set how long until the next feed is “due.” Night is **22–07** local (`HUCKLEBERRY_TIMEZONE`) by default (override with **`FEED_ALERT_NIGHT_START_HOUR`** / **`FEED_ALERT_NIGHT_END_HOUR`**). 🟢/🔴 in the title follows **feed timing only** (diaper is shown in the body but does not change red/green). The feed line shows **time until that window** (e.g. `15m left`) or **overdue** after it passes. Title when overdue: **`🔴 Baby needs attention`** (override with **`FEED_ALERT_TITLE`**); when OK: **`🟢`** + **`NTFY_TITLE`**. **Priority: urgent** on every run; **`warning`** tag only when feed is overdue.

For **Echo** announcements when feed is overdue, enable [Voice Monkey](https://voicemonkey.io/docs), then add repository secrets **`VOICE_MONKEY_TOKEN`** and **`VOICE_MONKEY_DEVICE`**. The script **GET**s `https://api-v3.voicemonkey.io/announce` (token + device + `speech`, same as the [API Playground](https://app.voicemonkey.io/playground)) on each overdue run — match your external cron frequency to how often you want Alexa to speak.

Optional **`FEED_ALERT_WEBHOOK_URL`**: simple **GET** when feed is overdue (e.g. IFTTT).
