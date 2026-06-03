from app.aggregation import aggregate, worst
from app.models.monitoring import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_UNKNOWN,
    STATUS_UP,
)


def test_worst_picks_most_severe():
    assert worst([STATUS_UP, STATUS_DOWN]) == STATUS_DOWN
    assert worst([STATUS_UP, STATUS_DEGRADED]) == STATUS_DEGRADED
    assert worst([STATUS_UP, STATUS_UP]) == STATUS_UP
    assert worst([]) == STATUS_UNKNOWN


def test_aggregate_all_up():
    assert aggregate([STATUS_UP, STATUS_UP]) == STATUS_UP


def test_aggregate_all_down():
    assert aggregate([STATUS_DOWN, STATUS_DOWN]) == STATUS_DOWN


def test_aggregate_mixed_is_degraded():
    assert aggregate([STATUS_UP, STATUS_DOWN]) == STATUS_DEGRADED
    assert aggregate([STATUS_UP, STATUS_DEGRADED]) == STATUS_DEGRADED


def test_aggregate_ignores_unknown_unless_all_unknown():
    assert aggregate([STATUS_UP, STATUS_UNKNOWN]) == STATUS_UP
    assert aggregate([STATUS_UNKNOWN, STATUS_UNKNOWN]) == STATUS_UNKNOWN
    assert aggregate([]) == STATUS_UNKNOWN
