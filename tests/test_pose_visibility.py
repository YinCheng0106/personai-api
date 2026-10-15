from __future__ import annotations

import pytest

from personai_api.services.biomechanics import (
    BiomechanicsAnalyzer,
    Point,
    SquatState,
)
from personai_api.services.pose_visibility import (
    POSE_VISIBILITY_POLICIES,
    FormErrorCode,
    PoseLandmark,
    TrackingHintCode,
    TrackingState,
)


def _landmarks(exercise: str, visibility: float = 1.0) -> list[Point]:
    points = [Point(0.5, 0.5, visibility=visibility) for _ in range(33)]
    if exercise == "squat":
        points[PoseLandmark.LEFT_SHOULDER] = Point(0.4, 0.1, visibility=visibility)
        points[PoseLandmark.RIGHT_SHOULDER] = Point(0.6, 0.1, visibility=visibility)
        points[PoseLandmark.LEFT_HIP] = Point(0.4, 0.35, visibility=visibility)
        points[PoseLandmark.RIGHT_HIP] = Point(0.6, 0.35, visibility=visibility)
        points[PoseLandmark.LEFT_KNEE] = Point(0.4, 0.6, visibility=visibility)
        points[PoseLandmark.RIGHT_KNEE] = Point(0.6, 0.6, visibility=visibility)
        points[PoseLandmark.LEFT_ANKLE] = Point(0.4, 0.9, visibility=visibility)
        points[PoseLandmark.RIGHT_ANKLE] = Point(0.6, 0.9, visibility=visibility)
    else:
        points[PoseLandmark.LEFT_SHOULDER] = Point(0.2, 0.4, visibility=visibility)
        points[PoseLandmark.LEFT_ELBOW] = Point(0.5, 0.4, visibility=visibility)
        points[PoseLandmark.LEFT_WRIST] = Point(0.8, 0.4, visibility=visibility)
        points[PoseLandmark.RIGHT_SHOULDER] = Point(0.2, 0.6, visibility=visibility)
        points[PoseLandmark.RIGHT_ELBOW] = Point(0.5, 0.6, visibility=visibility)
        points[PoseLandmark.RIGHT_WRIST] = Point(0.8, 0.6, visibility=visibility)
        points[PoseLandmark.LEFT_HIP] = Point(0.5, 0.4, visibility=visibility)
        points[PoseLandmark.RIGHT_HIP] = Point(0.5, 0.6, visibility=visibility)
        points[PoseLandmark.LEFT_ANKLE] = Point(0.8, 0.4, visibility=visibility)
        points[PoseLandmark.RIGHT_ANKLE] = Point(0.8, 0.6, visibility=visibility)
    return points


def _acquire(analyzer: BiomechanicsAnalyzer, landmarks: list[Point]) -> None:
    assert analyzer.analyze(landmarks, 0.0).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(landmarks, 0.1).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(landmarks, 0.2).tracking_state == TrackingState.ACTIVE


def _filter_snapshot(analyzer: BiomechanicsAnalyzer) -> object:
    return tuple(
        tuple(
            (
                filter_.freq,
                filter_._last_time,
                filter_._x_filter.alpha,
                filter_._x_filter._prev,
                filter_._dx_filter.alpha,
                filter_._dx_filter._prev,
            )
            for filter_ in pair
        )
        for pair in analyzer._smoother._filters
    )


def test_authoritative_bilateral_counting_dependencies() -> None:
    squat = POSE_VISIBILITY_POLICIES["squat"].counting
    pushup = POSE_VISIBILITY_POLICIES["pushup"].counting

    assert squat == {
        PoseLandmark.LEFT_HIP,
        PoseLandmark.LEFT_KNEE,
        PoseLandmark.LEFT_ANKLE,
        PoseLandmark.RIGHT_HIP,
        PoseLandmark.RIGHT_KNEE,
        PoseLandmark.RIGHT_ANKLE,
    }
    assert pushup == {
        PoseLandmark.LEFT_SHOULDER,
        PoseLandmark.LEFT_ELBOW,
        PoseLandmark.LEFT_WRIST,
        PoseLandmark.RIGHT_SHOULDER,
        PoseLandmark.RIGHT_ELBOW,
        PoseLandmark.RIGHT_WRIST,
    }


@pytest.mark.parametrize(
    "missing",
    [PoseLandmark.LEFT_KNEE, PoseLandmark.RIGHT_ANKLE],
)
def test_squat_critical_knee_or_ankle_pauses_counting(
    missing: PoseLandmark,
) -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    valid = _landmarks("squat")
    _acquire(analyzer, valid)
    invalid = valid.copy()
    invalid[missing] = invalid[missing]._replace(visibility=0.0)

    before_state = analyzer._fsm.state
    before_reps = analyzer._fsm.rep_count
    result = analyzer.analyze(invalid, 0.3)

    assert result.tracking_state == TrackingState.PAUSED
    assert result.tracking_hints == [TrackingHintCode.SQUAT_KNEES_NOT_VISIBLE]
    assert analyzer._fsm.state == before_state
    assert analyzer._fsm.rep_count == before_reps


def test_squat_noncritical_face_and_wrist_do_not_block_counting() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    landmarks = _landmarks("squat")
    for index in (PoseLandmark.NOSE, PoseLandmark.LEFT_WRIST, PoseLandmark.RIGHT_WRIST):
        landmarks[index] = landmarks[index]._replace(visibility=0.0)

    _acquire(analyzer, landmarks)
    result = analyzer.analyze(landmarks, 0.3)

    assert result.tracking_state == TrackingState.ACTIVE
    assert result.is_visible is True


def test_squat_torso_rule_and_angle_are_suppressed_without_shoulders() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    landmarks = _landmarks("squat")
    landmarks[PoseLandmark.LEFT_SHOULDER] = Point(0.9, 0.35, visibility=0.0)
    landmarks[PoseLandmark.RIGHT_SHOULDER] = Point(0.9, 0.35, visibility=0.0)

    _acquire(analyzer, landmarks)
    result = analyzer.analyze(landmarks, 0.3)

    assert result.tracking_state == TrackingState.ACTIVE
    assert FormErrorCode.SQUAT_TORSO_LEAN not in result.form_errors
    assert result.angles["left_hip"] is None
    assert result.angles["right_hip"] is None


def test_squat_enter_and_stay_visibility_hysteresis() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    below_enter_mean = _landmarks("squat", visibility=0.70)
    assert (
        analyzer.analyze(below_enter_mean, 0.0).tracking_state
        == TrackingState.ACQUIRING
    )

    enter_valid = _landmarks("squat", visibility=0.75)
    assert analyzer.analyze(enter_valid, 0.1).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(enter_valid, 0.2).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(enter_valid, 0.3).tracking_state == TrackingState.ACTIVE

    stay_valid = _landmarks("squat", visibility=0.65)
    assert analyzer.analyze(stay_valid, 0.4).tracking_state == TrackingState.ACTIVE
    below_stay = _landmarks("squat", visibility=0.64)
    assert analyzer.analyze(below_stay, 0.5).tracking_state == TrackingState.PAUSED


@pytest.mark.parametrize(
    "missing",
    [PoseLandmark.LEFT_ELBOW, PoseLandmark.RIGHT_WRIST],
)
def test_pushup_critical_elbow_or_wrist_pauses_counting(
    missing: PoseLandmark,
) -> None:
    analyzer = BiomechanicsAnalyzer("pushup")
    valid = _landmarks("pushup")
    _acquire(analyzer, valid)
    invalid = valid.copy()
    invalid[missing] = invalid[missing]._replace(visibility=0.0)

    result = analyzer.analyze(invalid, 0.3)

    assert result.tracking_state == TrackingState.PAUSED
    assert result.tracking_hints == [TrackingHintCode.PUSHUP_ARMS_NOT_VISIBLE]


def test_pushup_knees_do_not_block_counting() -> None:
    analyzer = BiomechanicsAnalyzer("pushup")
    landmarks = _landmarks("pushup")
    landmarks[PoseLandmark.LEFT_KNEE] = landmarks[PoseLandmark.LEFT_KNEE]._replace(
        visibility=0.0
    )
    landmarks[PoseLandmark.RIGHT_KNEE] = landmarks[PoseLandmark.RIGHT_KNEE]._replace(
        visibility=0.0
    )

    _acquire(analyzer, landmarks)
    assert analyzer.analyze(landmarks, 0.3).tracking_state == TrackingState.ACTIVE


def test_pushup_body_alignment_rules_are_suppressed_without_hips() -> None:
    analyzer = BiomechanicsAnalyzer("pushup")
    landmarks = _landmarks("pushup")
    landmarks[PoseLandmark.LEFT_HIP] = Point(0.5, 0.9, visibility=0.0)
    landmarks[PoseLandmark.RIGHT_HIP] = Point(0.5, 0.9, visibility=0.0)

    _acquire(analyzer, landmarks)
    result = analyzer.analyze(landmarks, 0.3)

    assert result.tracking_state == TrackingState.ACTIVE
    assert FormErrorCode.PUSHUP_HIP_SAG not in result.form_errors
    assert FormErrorCode.PUSHUP_HIP_PIKE not in result.form_errors
    assert result.angles["left_body"] is None
    assert result.angles["right_body"] is None


def test_pushup_visibility_hysteresis() -> None:
    analyzer = BiomechanicsAnalyzer("pushup")
    enter_valid = _landmarks("pushup", visibility=0.75)
    _acquire(analyzer, enter_valid)

    assert (
        analyzer.analyze(_landmarks("pushup", 0.65), 0.3).tracking_state
        == TrackingState.ACTIVE
    )
    assert (
        analyzer.analyze(_landmarks("pushup", 0.64), 0.4).tracking_state
        == TrackingState.PAUSED
    )


def test_tracking_acquires_pauses_and_reacquires_after_three_valid_frames() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    valid = _landmarks("squat")
    invalid = valid.copy()
    invalid[PoseLandmark.LEFT_KNEE] = invalid[PoseLandmark.LEFT_KNEE]._replace(
        visibility=0.0
    )

    _acquire(analyzer, valid)
    assert analyzer.analyze(invalid, 0.3).tracking_state == TrackingState.PAUSED
    assert analyzer.analyze(valid, 0.4).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(valid, 0.5).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(valid, 0.6).tracking_state == TrackingState.ACTIVE


def test_lost_abandons_unfinished_cycle_once_and_preserves_completed_reps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    valid = _landmarks("squat")
    _acquire(analyzer, valid)
    analyzer._fsm.rep_count = 2
    analyzer._fsm.state = SquatState.BOTTOM
    analyzer._fsm._reached_bottom = True
    calls = 0
    original = analyzer._fsm.abandon_cycle

    def counted_abandon() -> None:
        nonlocal calls
        calls += 1
        original()

    monkeypatch.setattr(analyzer._fsm, "abandon_cycle", counted_abandon)
    assert analyzer.pose_missing(0.3).tracking_state == TrackingState.PAUSED
    lost = analyzer.pose_missing(1.31)

    assert lost.tracking_state == TrackingState.LOST
    assert lost.rep_count == 2
    assert lost.state == SquatState.IDLE.value
    assert calls == 1
    assert analyzer.pose_missing(1.5).tracking_state == TrackingState.LOST
    assert calls == 1


def test_short_visibility_loss_cannot_complete_a_ghost_repetition() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    standing = _landmarks("squat")
    _acquire(analyzer, standing)
    analyzer._fsm.rep_count = 4
    analyzer._fsm.state = SquatState.BOTTOM
    analyzer._fsm._reached_bottom = True

    analyzer.pose_missing(0.3)
    analyzer.analyze(standing, 0.4)
    analyzer.analyze(standing, 0.5)
    result = analyzer.analyze(standing, 0.6)

    assert result.tracking_state == TrackingState.ACTIVE
    assert result.rep_count == 4
    assert result.state == SquatState.IDLE.value


def test_pose_missing_and_recovery_from_lost() -> None:
    analyzer = BiomechanicsAnalyzer("pushup")
    valid = _landmarks("pushup")
    _acquire(analyzer, valid)

    paused = analyzer.pose_missing(0.3)
    assert paused.tracking_hints == [TrackingHintCode.POSE_NOT_FOUND]
    assert analyzer.pose_missing(1.4).tracking_state == TrackingState.LOST
    assert analyzer.analyze(valid, 1.5).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(valid, 1.6).tracking_state == TrackingState.ACQUIRING
    assert analyzer.analyze(valid, 1.7).tracking_state == TrackingState.ACTIVE


def test_rejected_frame_does_not_change_filter_state_or_fsm() -> None:
    analyzer = BiomechanicsAnalyzer("squat")
    valid = _landmarks("squat")
    _acquire(analyzer, valid)
    before_filters = _filter_snapshot(analyzer)
    before_state = analyzer._fsm.state
    before_reps = analyzer._fsm.rep_count
    invalid = valid.copy()
    invalid[PoseLandmark.LEFT_ANKLE] = Point(0.0, 0.0, visibility=0.0)

    result = analyzer.analyze(invalid, 0.3)

    assert _filter_snapshot(analyzer) == before_filters
    assert analyzer._fsm.state == before_state
    assert analyzer._fsm.rep_count == before_reps
    assert result.form_errors == []
    assert all(value is None for value in result.angles.values())


def test_filter_replay_matches_sequence_where_invalid_frame_never_existed() -> None:
    guarded = BiomechanicsAnalyzer("squat")
    reference = BiomechanicsAnalyzer("squat")
    first = _landmarks("squat")
    _acquire(guarded, first)
    _acquire(reference, first)
    guarded.analyze(first, 0.3)
    reference.analyze(first, 0.3)

    invalid = _landmarks("squat")
    invalid[PoseLandmark.LEFT_KNEE] = Point(0.0, 0.0, visibility=0.0)
    guarded.analyze(invalid, 0.4)

    next_frame = _landmarks("squat")
    next_frame[PoseLandmark.LEFT_HIP] = Point(0.42, 0.35, visibility=1.0)
    guarded.analyze(next_frame, 0.5)
    guarded.analyze(next_frame, 0.6)
    guarded.analyze(next_frame, 0.7)
    reference.analyze(next_frame, 0.7)

    assert _filter_snapshot(guarded) == _filter_snapshot(reference)
