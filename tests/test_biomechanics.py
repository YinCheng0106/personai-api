from collections.abc import Iterator

import pytest

import personai_api.services.biomechanics as biomechanics
from personai_api.services.biomechanics import (
    OneEuroFilter,
    Point,
    PushUpFSM,
    SquatFSM,
    calculate_angle,
)


def _landmarks() -> list[Point]:
    points = [Point(0.5, 0.5, visibility=1.0) for _ in range(33)]
    points[23] = Point(0.4, 0.4, visibility=1.0)
    points[24] = Point(0.6, 0.4, visibility=1.0)
    points[25] = Point(0.35, 0.6, visibility=1.0)
    points[26] = Point(0.65, 0.6, visibility=1.0)
    return points


def test_angle_and_one_euro_filter() -> None:
    assert calculate_angle((1, 0), (0, 0), (0, 1)) == pytest.approx(90)
    filter_ = OneEuroFilter(freq=12)
    first = filter_(0, 0)
    second = filter_(1, 1 / 12)
    assert first == 0
    assert 0 < second < 1
    filter_.reset()
    assert filter_(1, 0) == 1


def test_squat_fsm_counts_a_complete_synthetic_rep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values: Iterator[float] = iter(
        value for knee in (170, 150, 90, 110, 165) for value in (knee, knee, 0)
    )
    monkeypatch.setattr(biomechanics, "calculate_angle", lambda *_: next(values))
    fsm = SquatFSM()
    for _ in range(5):
        fsm.update(_landmarks())
    assert fsm.rep_count == 1


def test_pushup_fsm_counts_a_complete_synthetic_rep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values: Iterator[float] = iter(
        value
        for elbow in (170, 150, 85, 100, 165)
        for value in (elbow, elbow, 180, 180)
    )
    monkeypatch.setattr(biomechanics, "calculate_angle", lambda *_: next(values))
    fsm = PushUpFSM()
    for _ in range(5):
        fsm.update(_landmarks())
    assert fsm.rep_count == 1
