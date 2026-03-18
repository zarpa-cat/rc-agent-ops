import pytest

from rc_agent_ops.risk import RiskTracker, SubscriberRisk


@pytest.fixture
def tracker():
    return RiskTracker(db_path=":memory:")


def test_risk_default_clean(tracker):
    assert tracker.get("unknown_user") == SubscriberRisk.CLEAN


def test_risk_mark_suspected(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    assert tracker.get("user_1") == SubscriberRisk.SUSPECTED


def test_risk_mark_blocked(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_risk_mark_clean_clears(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_risk_history(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    history = tracker.history("user_1", limit=20)
    assert len(history) == 3
    # Reverse chronological order (by insertion id)
    assert history[0].risk == SubscriberRisk.CLEAN
    assert history[1].risk == SubscriberRisk.BLOCKED
    assert history[2].risk == SubscriberRisk.SUSPECTED


def test_risk_list_at_risk(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    tracker.mark("user_2", SubscriberRisk.BLOCKED, "expired")
    at_risk = tracker.list_at_risk()
    ids = {sub_id for sub_id, _ in at_risk}
    assert ids == {"user_1", "user_2"}


def test_risk_list_at_risk_excludes_clean(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    at_risk = tracker.list_at_risk()
    ids = {sub_id for sub_id, _ in at_risk}
    assert "user_1" not in ids
