from __future__ import annotations

import math

import pytest

from personai_api.services.biomechanics import (
    BiomechanicsAnalyzer,
    Point,
    SquatConfig,
    SquatFSM,
    SquatState,
)
from personai_api.services.pose_visibility import PoseLandmark, TrackingState


def _squat_frame(
    knee_angle: float,
    *,
    hip_y: float = 0.25,
    ankle_y: float = 0.85,
) -> list[Point]:
    """Build symmetric bilateral leg geometry for a target knee angle."""

    visibility = 1.0
    points = [Point(0.5, 0.5, visibility=visibility) for _ in range(33)]
    knee_y = (hip_y + ankle_y) / 2
    half_leg_height = (ankle_y - hip_y) / 2
    knee_offset = half_leg_height / math.tan(math.radians(knee_angle / 2))

    shoulder_y = max(0.0, hip_y - 0.2)
    points[PoseLandmark.LEFT_SHOULDER] = Point(0.35, shoulder_y, visibility=visibility)
    points[PoseLandmark.RIGHT_SHOULDER] = Point(0.65, shoulder_y, visibility=visibility)
    points[PoseLandmark.LEFT_HIP] = Point(0.35, hip_y, visibility=visibility)
    points[PoseLandmark.RIGHT_HIP] = Point(0.65, hip_y, visibility=visibility)
    points[PoseLandmark.LEFT_KNEE] = Point(
        0.35 + knee_offset, knee_y, visibility=visibility
    )
    points[PoseLandmark.RIGHT_KNEE] = Point(
        0.65 - knee_offset, knee_y, visibility=visibility
    )
    points[PoseLandmark.LEFT_ANKLE] = Point(0.35, ankle_y, visibility=visibility)
    points[PoseLandmark.RIGHT_ANKLE] = Point(0.65, ankle_y, visibility=visibility)
    return points


def _feed_fsm(
    fsm: SquatFSM,
    phases: tuple[tuple[float, float, int], ...],
) -> None:
    for knee_angle, hip_y, frames in phases:
        landmarks = _squat_frame(knee_angle, hip_y=hip_y)
        for _ in range(frames):
            fsm.update(landmarks)


def _analyze_repeated(
    analyzer: BiomechanicsAnalyzer,
    landmarks: list[Point],
    timestamp: float,
    frames: int,
) -> float:
    for _ in range(frames):
        analyzer.analyze(landmarks, timestamp)
        timestamp += 1 / 30
    return timestamp


def _acquire_and_arm(analyzer: BiomechanicsAnalyzer, timestamp: float = 0.0) -> float:
    standing = _squat_frame(170.0)
    timestamp = _analyze_repeated(analyzer, standing, timestamp, 5)
    assert analyzer.tracking_state == TrackingState.ACTIVE
    assert isinstance(analyzer._fsm, SquatFSM)
    assert analyzer._fsm._armed is True
    return timestamp


def _analyze_valid_squat(
    analyzer: BiomechanicsAnalyzer,
    timestamp: float,
) -> float:
    for knee_angle, hip_y, frames in (
        (145.0, 0.32, 24),
        (85.0, 0.43, 32),
        (120.0, 0.35, 24),
        (170.0, 0.25, 36),
    ):
        timestamp = _analyze_repeated(
            analyzer,
            _squat_frame(knee_angle, hip_y=hip_y),
            timestamp,
            frames,
        )
    return timestamp


def test_seated_knee_swing_does_not_count_as_squat() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    timestamp = 0.0
    standing = _squat_frame(170.0, hip_y=0.45)

    for _ in range(3):
        result = analyzer.analyze(standing, timestamp)
        timestamp += 1 / 30
    assert result.tracking_state == TrackingState.ACTIVE

    # Repeat two complete legacy knee-angle cycles. Only the knees move;
    # bilateral hips and ankles remain fixed for every frame.
    for _ in range(2):
        for knee_angle, frames in (
            (170.0, 12),
            (145.0, 16),
            (85.0, 24),
            (120.0, 16),
            (170.0, 30),
        ):
            landmarks = _squat_frame(knee_angle, hip_y=0.45)
            for _ in range(frames):
                result = analyzer.analyze(landmarks, timestamp)
                timestamp += 1 / 30
                assert result.tracking_state == TrackingState.ACTIVE

    assert result.rep_count == 0


def test_complete_knee_angle_cycle_without_meaningful_hip_descent_does_not_count() -> (
    None
):
    fsm = SquatFSM()
    _feed_fsm(
        fsm,
        (
            (170.0, 0.25, 3),
            (145.0, 0.26, 1),
            (85.0, 0.28, 1),
            (120.0, 0.27, 1),
            (170.0, 0.25, 1),
        ),
    )

    assert fsm._armed is True
    assert fsm.state == SquatState.IDLE
    assert fsm.rep_count == 0


def test_static_seated_pose_does_not_count() -> None:
    fsm = SquatFSM()
    seated = _squat_frame(90.0, hip_y=0.45)

    for _ in range(60):
        fsm.update(seated)

    assert fsm._armed is False
    assert fsm.state == SquatState.IDLE
    assert fsm.rep_count == 0


def test_squat_guard_uses_provisional_config_defaults() -> None:
    config = SquatConfig()

    assert config.min_hip_descent_ratio == 0.18
    assert config.standing_return_tolerance == 0.06
    assert config.standing_confirmation_frames == 3


def test_normal_squat_landmark_sequence_counts_exactly_one() -> None:
    fsm = SquatFSM()
    _feed_fsm(
        fsm,
        (
            (170.0, 0.25, 3),
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.35, 1),
            (170.0, 0.25, 1),
        ),
    )

    assert fsm.state == SquatState.IDLE
    assert fsm.rep_count == 1


def test_multiple_valid_squats_count_exactly() -> None:
    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 3),))

    for _ in range(3):
        _feed_fsm(
            fsm,
            (
                (145.0, 0.32, 1),
                (85.0, 0.43, 1),
                (120.0, 0.35, 1),
                (170.0, 0.25, 3),
            ),
        )

    assert fsm.rep_count == 3


def test_squat_must_return_hip_near_standing_baseline_to_count() -> None:
    fsm = SquatFSM()
    _feed_fsm(
        fsm,
        (
            (170.0, 0.25, 3),
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.38, 1),
            (170.0, 0.35, 1),
        ),
    )

    assert fsm.state == SquatState.IDLE
    assert fsm.rep_count == 0


def test_standing_baseline_freezes_during_cycle_then_refreshes_in_idle() -> None:
    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 3),))

    assert fsm._standing_hip_y == pytest.approx(0.25)
    assert fsm._standing_leg_scale == pytest.approx(0.60)

    fsm.update(_squat_frame(145.0, hip_y=0.32, ankle_y=0.75))
    fsm.update(_squat_frame(85.0, hip_y=0.43, ankle_y=0.75))

    assert fsm._standing_hip_y == pytest.approx(0.25)
    assert fsm._standing_leg_scale == pytest.approx(0.60)
    assert fsm._max_hip_descent_ratio == pytest.approx(0.30)

    fsm.update(_squat_frame(120.0, hip_y=0.35, ankle_y=0.75))
    fsm.update(_squat_frame(170.0, hip_y=0.25))
    assert fsm.rep_count == 1
    assert fsm._armed is True

    for _ in range(3):
        fsm.update(_squat_frame(170.0, hip_y=0.27, ankle_y=0.87))

    assert fsm._standing_hip_y == pytest.approx(0.27)
    assert fsm._standing_leg_scale == pytest.approx(0.60)


def test_manual_squat_reset_clears_guard_state_and_reps() -> None:
    fsm = SquatFSM()
    _feed_fsm(
        fsm,
        (
            (170.0, 0.25, 3),
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.35, 1),
            (170.0, 0.25, 1),
            (145.0, 0.32, 1),
        ),
    )
    assert fsm.rep_count == 1
    assert fsm.state == SquatState.DESCENDING

    fsm.reset()

    assert fsm.rep_count == 0
    assert fsm.state == SquatState.IDLE
    assert fsm._armed is False
    assert fsm._standing_candidate_frames == 0
    assert fsm._standing_hip_y is None
    assert fsm._standing_leg_scale is None
    assert fsm._max_hip_descent_ratio == 0.0


def test_tracking_interruption_discards_incomplete_squat_cycle() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    timestamp = _acquire_and_arm(analyzer)

    timestamp = _analyze_repeated(
        analyzer, _squat_frame(145.0, hip_y=0.32), timestamp, 24
    )
    timestamp = _analyze_repeated(
        analyzer, _squat_frame(85.0, hip_y=0.43), timestamp, 32
    )
    assert analyzer._fsm.state == SquatState.BOTTOM

    paused = analyzer.pose_missing(timestamp)
    assert paused.tracking_state == TrackingState.PAUSED
    timestamp += 1 / 30

    standing = _squat_frame(170.0)
    timestamp = _analyze_repeated(analyzer, standing, timestamp, 3)

    assert analyzer.tracking_state == TrackingState.ACTIVE
    assert analyzer._fsm.state == SquatState.IDLE
    assert analyzer._fsm.rep_count == 0
    assert analyzer._fsm._armed is False
    assert analyzer._fsm._standing_hip_y is None


def test_lost_requires_new_standing_baseline_before_next_valid_squat() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    timestamp = _acquire_and_arm(analyzer)
    timestamp = _analyze_repeated(
        analyzer, _squat_frame(145.0, hip_y=0.32), timestamp, 24
    )
    assert analyzer._fsm.state == SquatState.DESCENDING

    analyzer.pose_missing(timestamp)
    lost = analyzer.pose_missing(timestamp + 1.1)
    timestamp += 1.1 + 1 / 30

    assert lost.tracking_state == TrackingState.LOST
    assert analyzer._fsm._armed is False
    assert analyzer._fsm._standing_hip_y is None
    assert analyzer._fsm._standing_leg_scale is None

    half_squat = _squat_frame(120.0, hip_y=0.42)
    timestamp = _analyze_repeated(analyzer, half_squat, timestamp, 8)

    assert analyzer.tracking_state == TrackingState.ACTIVE
    assert analyzer._fsm._armed is False
    assert analyzer._fsm.state == SquatState.IDLE
    assert analyzer._fsm.rep_count == 0

    timestamp = _analyze_repeated(analyzer, _squat_frame(170.0), timestamp, 40)
    assert analyzer._fsm._armed is True

    _analyze_valid_squat(analyzer, timestamp)

    assert analyzer._fsm.state == SquatState.IDLE
    assert analyzer._fsm.rep_count == 1


def test_shallow_squat_reversal_reports_insufficient_depth() -> None:
    """Returning upright without reaching BOTTOM must abandon the cycle."""
    from personai_api.services.pose_visibility import FormErrorCode

    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 3),))
    assert fsm._armed is True

    state, reps, _ = fsm.update(_squat_frame(145.0, hip_y=0.32))
    assert state == SquatState.DESCENDING
    assert reps == 0

    state, reps, errors = fsm.update(_squat_frame(170.0, hip_y=0.25))
    assert state == SquatState.IDLE
    assert reps == 0
    assert errors == [FormErrorCode.SQUAT_DEPTH_INSUFFICIENT.value]
    assert fsm._max_hip_descent_ratio == 0.0
    assert fsm._armed is True

    # An incomplete rep must not prevent the next valid cycle from counting.
    _feed_fsm(
        fsm,
        (
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.35, 1),
            (170.0, 0.25, 1),
        ),
    )
    assert fsm.rep_count == 1


def test_partial_ascent_does_not_complete_a_rep_early() -> None:
    """Remaining below standing knee angle during ASCENDING must not count."""
    fsm = SquatFSM()
    _feed_fsm(
        fsm,
        (
            (170.0, 0.25, 3),
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.35, 1),
            (130.0, 0.33, 1),
            (150.0, 0.28, 1),
        ),
    )

    assert fsm.state == SquatState.ASCENDING
    assert fsm.rep_count == 0
    assert fsm._max_hip_descent_ratio >= fsm.config.min_hip_descent_ratio

    fsm.update(_squat_frame(170.0, hip_y=0.25))
    assert fsm.state == SquatState.IDLE
    assert fsm.rep_count == 1


@pytest.mark.parametrize(
    "bad_knee",
    [PoseLandmark.LEFT_KNEE, PoseLandmark.RIGHT_KNEE],
)
def test_degenerate_knee_angle_is_ignored_without_arming(
    bad_knee: PoseLandmark,
) -> None:
    """A zero-length joint vector is not evidence of standing or motion."""
    fsm = SquatFSM()
    landmarks = _squat_frame(170.0)
    corresponding_hip = (
        PoseLandmark.LEFT_HIP
        if bad_knee == PoseLandmark.LEFT_KNEE
        else PoseLandmark.RIGHT_HIP
    )
    landmarks[bad_knee] = landmarks[corresponding_hip]

    for _ in range(5):
        state, reps, errors = fsm.update(landmarks)
        assert state == SquatState.IDLE
        assert reps == 0
        assert errors == []

    assert fsm._armed is False
    assert fsm._standing_hip_y is None


def test_standing_arming_requires_consecutive_valid_frames() -> None:
    """A single non-standing frame resets the standing confirmation streak."""
    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 2),))
    assert fsm._armed is False

    fsm.update(_squat_frame(145.0, hip_y=0.25))
    assert fsm._standing_candidate_frames == 0

    _feed_fsm(fsm, ((170.0, 0.25, 2),))
    assert fsm._armed is False

    fsm.update(_squat_frame(170.0, hip_y=0.25))
    assert fsm._armed is True


def test_standing_arming_requires_sufficient_body_scale() -> None:
    """Do not initialize a baseline with a too-small projected leg length."""
    fsm = SquatFSM(SquatConfig(minimum_leg_scale=0.61))
    _feed_fsm(fsm, ((170.0, 0.25, 5),))

    assert fsm._armed is False
    assert fsm._standing_leg_scale is None
    assert fsm.rep_count == 0

    allowed = SquatFSM(SquatConfig(minimum_leg_scale=0.59))
    _feed_fsm(allowed, ((170.0, 0.25, 3),))
    assert allowed._armed is True
    assert allowed._standing_leg_scale == pytest.approx(0.6)


def test_abandoned_cycle_cannot_reuse_missing_baseline() -> None:
    """Defensive displacement helpers must reject a cleared standing baseline."""
    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 3),))
    assert fsm._armed is True

    fsm.abandon_cycle()
    fsm._record_hip_descent(0.45)
    assert fsm._max_hip_descent_ratio == 0.0
    assert fsm._returned_to_standing(0.25) is False
    assert fsm._standing_hip_y is None
    assert fsm._standing_leg_scale is None
    assert fsm.rep_count == 0


def test_nonfinite_descent_cannot_poison_cycle_peak() -> None:
    """A non-finite displacement must not overwrite valid cycle evidence."""
    fsm = SquatFSM()
    _feed_fsm(fsm, ((170.0, 0.25, 3),))
    fsm._record_hip_descent(float("nan"))
    assert fsm._max_hip_descent_ratio == 0.0

    _feed_fsm(
        fsm,
        (
            (145.0, 0.32, 1),
            (85.0, 0.43, 1),
            (120.0, 0.35, 1),
            (170.0, 0.25, 1),
        ),
    )
    assert fsm.rep_count == 1
