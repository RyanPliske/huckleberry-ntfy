"""AWS Lambda entry for the Huckleberry Alexa skill (ASK SDK + ``agent_runner``).

Handler: ``lambda_handler``. Configure the skill endpoint to this function ARN (no API Gateway required).

Environment variables:

- ``OPENAI_API_KEY`` (required)
- ``HUCKLEBERRY_EMAIL``, ``HUCKLEBERRY_PASSWORD``, ``HUCKLEBERRY_TIMEZONE`` (required)
- ``HUCKLEBERRY_CHILD_INDEX`` — optional, default ``0`` (``user.childList`` index)
- ``OPENAI_MODEL`` — optional pydantic-ai model string
"""

from __future__ import annotations

import asyncio
import logging
import os

from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import is_intent_name, is_request_type
from huckleberry_api.agent_runner import run_agent_prompt

logger = logging.getLogger(__name__)
if not logging.root.handlers:
    logging.basicConfig(level=logging.INFO)


def _child_index() -> int:
    raw = os.getenv("HUCKLEBERRY_CHILD_INDEX", "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):  # type: ignore[no-untyped-def]
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):  # type: ignore[no-untyped-def]
        return (
            handler_input.response_builder.speak(
                "You can say things like log two ounces of formula, or log a pee diaper."
            )
            .ask("What would you like to log?")
            .response
        )


class CaptureQueryIntentHandler(AbstractRequestHandler):
    """Handles ``CaptureQueryIntent`` with ``AMAZON.SearchQuery`` slot ``query``."""

    def can_handle(self, handler_input):  # type: ignore[no-untyped-def]
        return is_intent_name("CaptureQueryIntent")(handler_input)

    def handle(self, handler_input):  # type: ignore[no-untyped-def]
        intent = handler_input.request_envelope.request.intent  # type: ignore[union-attr]
        slots = intent.slots or {}
        slot = slots.get("query")
        text = (slot.value if slot else None) or ""
        text = str(text).strip()
        if not text:
            return handler_input.response_builder.speak("I did not catch what to log. Try again.").response

        try:
            reply = asyncio.run(run_agent_prompt(text, child_index=_child_index()))
        except ValueError as e:
            logger.warning("Agent config or input error: %s", e)
            return handler_input.response_builder.speak("I could not run that request. Check the skill setup.").response
        except Exception:
            logger.exception("Agent run failed")
            return handler_input.response_builder.speak("Something went wrong. Try again in a moment.").response

        # Alexa SSML length limits — keep spoken reply short
        if len(reply) > 8000:
            reply = reply[:7990] + "…"
        return handler_input.response_builder.speak(reply).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):  # type: ignore[no-untyped-def]
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):  # type: ignore[no-untyped-def]
        return (
            handler_input.response_builder.speak(
                "Say natural phrases like log three ounces of formula now, or log a wet diaper."
            )
            .ask("What would you like to log?")
            .response
        )


class CancelStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):  # type: ignore[no-untyped-def]
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name("AMAZON.StopIntent")(
            handler_input
        )

    def handle(self, handler_input):  # type: ignore[no-untyped-def]
        return handler_input.response_builder.speak("Goodbye.").response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):  # type: ignore[no-untyped-def]
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):  # type: ignore[no-untyped-def]
        return handler_input.response_builder.response


sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(CaptureQueryIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

# Use the SDK wrapper: it deserializes the Lambda ``event`` dict into
# ``RequestEnvelope`` before ``invoke``. Passing a raw dict to ``skill.invoke``
# causes ``'dict' object has no attribute 'session'``.
lambda_handler = sb.lambda_handler()
