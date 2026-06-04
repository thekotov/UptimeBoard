from app.aggregation import aggregate, worst
from app.models.monitoring import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_RECOVERED,
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


def test_worst_recovered_beats_up_but_loses_to_trouble():
    # A recently-recovered probe makes its server read "recovered"...
    assert worst([STATUS_UP, STATUS_RECOVERED]) == STATUS_RECOVERED
    # ...but any real trouble (or even unknown) outranks it.
    assert worst([STATUS_RECOVERED, STATUS_DOWN]) == STATUS_DOWN
    assert worst([STATUS_RECOVERED, STATUS_DEGRADED]) == STATUS_DEGRADED
    assert worst([STATUS_RECOVERED, STATUS_UNKNOWN]) == STATUS_UNKNOWN


def test_aggregate_recovered_when_all_healthy_and_some_recovered():
    assert aggregate([STATUS_UP, STATUS_RECOVERED]) == STATUS_RECOVERED
    assert aggregate([STATUS_RECOVERED, STATUS_RECOVERED]) == STATUS_RECOVERED
    # recovered is ignored alongside real problems (mix -> degraded).
    assert aggregate([STATUS_RECOVERED, STATUS_DOWN]) == STATUS_DEGRADED
    # unknown is still ignored: healthy + recovered -> recovered.
    assert aggregate([STATUS_RECOVERED, STATUS_UNKNOWN]) == STATUS_RECOVERED
