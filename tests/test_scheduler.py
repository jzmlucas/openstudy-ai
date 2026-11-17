from datetime import datetime, timedelta, timezone

from app.services.fsrs_scheduler import calculate_schedule, retrievability


def test_first_good_review_creates_adaptive_interval():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    result = calculate_schedule(
        stability=1.0,
        difficulty=5.0,
        reps=0,
        lapses=0,
        last_review=None,
        rating="good",
        now=now,
    )
    assert result["reps"] == 1
    assert result["stability"] > 1
    assert result["due_at"] > now
    assert result["state"] == "learning"


def test_again_rating_schedules_relearning_and_increments_lapse():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc).replace(tzinfo=None)
    result = calculate_schedule(
        stability=3.0,
        difficulty=5.0,
        reps=3,
        lapses=1,
        last_review=now - timedelta(days=3),
        rating="again",
        now=now,
    )
    assert result["state"] == "relearning"
    assert result["lapses"] == 2
    assert result["due_at"] == now + timedelta(minutes=10)


def test_retrievability_decreases_with_elapsed_time():
    assert retrievability(3.0, 1.0) > retrievability(3.0, 10.0)


def test_invalid_rating_is_rejected():
    try:
        calculate_schedule(
            stability=1, difficulty=5, reps=0, lapses=0,
            last_review=None, rating="unknown"
        )
    except ValueError:
        assert True
    else:
        assert False, "Expected ValueError for invalid rating"
