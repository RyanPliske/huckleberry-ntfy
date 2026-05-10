"""Tests for one-shot feed/diaper document reads."""

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseDiaperDocumentData, FirebaseFeedDocumentData


class TestSummaries:
    """Smoke tests for get_feed_summary / get_diaper_summary."""

    async def test_get_feed_summary(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Fetched feed document validates; prefs may be absent on a fresh profile."""
        summary = await api.get_feed_summary(child_uid)
        if summary is None:
            return

        assert isinstance(summary, FirebaseFeedDocumentData)

    async def test_get_diaper_summary(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Fetched diaper document validates."""
        summary = await api.get_diaper_summary(child_uid)
        if summary is None:
            return

        assert isinstance(summary, FirebaseDiaperDocumentData)
