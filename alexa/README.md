# Huckleberry Alexa skill

Natural-language logging for Huckleberry via the same **pydantic-ai** agent as `examples/huckleberry_agent_cli.py`. Alexa invokes **AWS Lambda** directly by ARN — **API Gateway is not required** for the skill endpoint.

**Architecture, NLU vs LLM, and roadmap:** [`_docs/alexa-voice-agent-plan.md`](../_docs/alexa-voice-agent-plan.md). **Agent tools vs Alexa samples:** [`_docs/agent-tools-and-alexa-nlu.md`](../_docs/agent-tools-and-alexa-nlu.md).

## Prerequisites

- AWS account, **AWS CLI** (`aws`), **AWS SAM CLI** (`sam`), Docker.
- [Alexa developer account](https://developer.amazon.com/) and a new custom skill.
- Environment secrets: `OPENAI_API_KEY`, `HUCKLEBERRY_EMAIL`, `HUCKLEBERRY_PASSWORD`, `HUCKLEBERRY_TIMEZONE`. Optional: `OPENAI_MODEL`, `HUCKLEBERRY_CHILD_INDEX` (default `0` = first child on the account).

**Timezone vs region:** `HUCKLEBERRY_TIMEZONE` must be the **caregiver’s IANA zone** (e.g. `America/Chicago`). The agent uses it for “today”, clock times, and “ago” — not the Lambda region. Deploying in `us-east-1` does not imply Eastern time; set the same value you use locally so Lambda matches the CLI.

### Install AWS SAM CLI (if `command not found sam`)

**macOS (Homebrew):**

```bash
brew install aws-sam-cli
sam --version
```

Other platforms and installers (pip, MSI, etc.) are in the [official install guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

### Install AWS CLI (if `command not found aws`)

SAM uses the same credentials as the **AWS CLI**. Install the CLI, then configure a profile (access keys or SSO).

**macOS (Homebrew):**

```bash
brew install awscli
aws --version
aws configure
```

Use [Installing the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) for other OSes. After install, confirm which account is active:

```bash
aws sts get-caller-identity
```

**Docker** must be running for `sam build` with this project’s container image.

## Interaction model

Import or merge `interaction_model_en_US.json` in the Alexa developer console (Build → Interaction Model → JSON Editor). The sample **`invocationName`** is **`nancy margaret`** (two words). That matches how Alexa expects you to say it (“huckle” + “berry”) and avoids the **stricter one-word** invocation rules in the console. Change it only if the console rejects it or it conflicts with another skill on your account.

**Public certification:** Amazon may ask for clarity on **brand-like** names. Fine for **personal / dev** testing; for store publication, follow their invocation and trademark guidance.

The custom intent **`CaptureQueryIntent`** uses slot **`query`** (`AMAZON.SearchQuery`) so open-ended phrases route to the LLM. **Alexa still matches a carrier phrase first** (e.g. “tell me …”, “what was …”); anything that does not match any intent hits **`AMAZON.FallbackIntent`** (friendly reprompt — no raw text to send to the model). **Meta questions** like “what can you do?” are added to **`AMAZON.HelpIntent`** and answered by the **same OpenAI agent** with a fixed help prompt.

### One sentence from cold start (no “open” first)

Use Alexa’s **ask … to …** pattern so the skill and intent run in **one** turn (invocation name **`huckle berry`**):

- **Alexa, ask huckle berry to log 2 oz of formula.**
- **Alexa, ask huckle berry to log two ounces of formula.**
- **Alexa, tell huckle berry to log a pee diaper.**

Saying only *“huckle berry, log two ounces…”* without **Alexa** / **ask** / **to** often goes to **general Alexa**, not your skill.

**Critical:** the usual pattern is **ask *skill name* to *something*** — the word **“to”** after the invocation matters. If you say **“Alexa, ask huckle berry what all it can do”** (no **“to”**), Alexa often treats that as **talking to the main assistant**, not your skill — you may hear a generic line like **“I’m not sure how to help you with that”** (that string is **not** from this repo’s Lambda).

Use one of these instead:

- **Alexa, ask huckle berry to tell me what you can do.**
- **Alexa, ask huckle berry to help.** (fires Help → agent)
- **Alexa, open huckle berry** → then **what all can you do** (in-session)

### After you already said “open …”

You are **inside** the skill — do **not** repeat the invocation name. Say the carrier + detail only, e.g. **“log 2 oz of formula”** or **“please log a wet diaper.”** Repeating *“huckle berry …”* again in turn two is unnecessary and can confuse routing.

**“When was the last feeding?”** often **failed NLU** before we added **time-style carriers** (`when was {query}`, etc.) in `interaction_model_en_US.json`. **Rebuild** the model in the developer console after syncing JSON. Then in-session **“when was the last feeding”** can work (slot ≈ “the last feeding”). From cold start, use **to** plus a matching phrase, e.g. **Alexa, ask huckle berry to tell me when the last feeding was** or **Alexa, ask huckle berry to what was the last feeding**.

## Build and deploy Lambda

From the **repository root**:

```bash
sam build
sam deploy --guided
```

### `sam deploy --guided` — what to answer

Wording varies slightly by SAM version; use these as defaults.

| Question | Suggested answer |
|----------|------------------|
| **Stack Name** | `huckleberry-alexa` (or any unique name you like; must match `^[a-zA-Z][-a-zA-Z0-9]*$`). |
| **AWS Region** | `us-east-1` (N. Virginia) is a common default for US skills. Pick the region closest to you if you prefer; use the **same** region when you paste the Lambda ARN into Alexa. |
| **Confirm changes before deploy** | `y` the first time so you can review the change set. |
| **Allow SAM CLI IAM role creation** / **capabilities** | `y` — CloudFormation must create IAM resources for the Lambda execution role (and related permissions). If it lists `CAPABILITY_IAM`, accept it. |
| **Disable rollback** | `n` — keep rollback on so a failed deploy does not leave a half-broken stack. |
| **Save arguments to configuration file** | `y` — writes `samconfig.toml` so later you can run `sam deploy` without re-answering. |
| **SAM configuration file name** | Press Enter for default (`samconfig.toml`). |
| **SAM configuration environment** | Press Enter for default (`default`). |

This template has **no parameters** (no secrets in the template). After deploy, set **Lambda → Configuration → Environment variables** in the AWS console for `OPENAI_API_KEY` and `HUCKLEBERRY_*`.

**Image / ECR:** First deploy may create an ECR repository for the container image; accept the defaults so SAM can push the image.

After deploy, copy the stack output **HuckleberryAlexaFunctionArn**. In the Alexa console: **Endpoint** → **AWS Lambda ARN** → paste the ARN → grant skill trigger permission when prompted.

Set Lambda **environment variables** in the AWS console (or extend the SAM template with `AWS::SecretsManager` references). Do not commit real credentials.

## Local library / tests

```bash
uv sync --group agent --group alexa
uv run ruff check src examples alexa
uv run pytest
```

## API Gateway (optional)

This design does **not** use API Gateway for Alexa; the skill calls Lambda directly. Use API Gateway only if you also want an HTTPS API for non-Alexa clients.
