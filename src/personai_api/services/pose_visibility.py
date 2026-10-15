"""Authoritative pose visibility and landmark dependency policy."""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


class PoseLandmark(enum.IntEnum):
    """MediaPipe Pose landmark indices used by PersonAI."""

    NOSE = 0
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_ELBOW = 13
    RIGHT_ELBOW = 14
    LEFT_WRIST = 15
    RIGHT_WRIST = 16
    LEFT_HIP = 23
    RIGHT_HIP = 24
    LEFT_KNEE = 25
    RIGHT_KNEE = 26
    LEFT_ANKLE = 27
    RIGHT_ANKLE = 28


class TrackingState(enum.StrEnum):
    ACQUIRING = "ACQUIRING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    LOST = "LOST"


class FormErrorCode(enum.StrEnum):
    SQUAT_DEPTH_INSUFFICIENT = "SQUAT_DEPTH_INSUFFICIENT"
    SQUAT_KNEE_VALGUS = "SQUAT_KNEE_VALGUS"
    SQUAT_TORSO_LEAN = "SQUAT_TORSO_LEAN"
    PUSHUP_DEPTH_INSUFFICIENT = "PUSHUP_DEPTH_INSUFFICIENT"
    PUSHUP_HIP_SAG = "PUSHUP_HIP_SAG"
    PUSHUP_HIP_PIKE = "PUSHUP_HIP_PIKE"


class TrackingHintCode(enum.StrEnum):
    POSE_NOT_FOUND = "POSE_NOT_FOUND"
    SQUAT_KNEES_NOT_VISIBLE = "SQUAT_KNEES_NOT_VISIBLE"
    PUSHUP_ARMS_NOT_VISIBLE = "PUSHUP_ARMS_NOT_VISIBLE"
    REACQUIRING_POSE = "REACQUIRING_POSE"


class VisibleLandmark(Protocol):
    @property
    def visibility(self) -> float: ...


@dataclass(frozen=True)
class VisibilityThresholds:
    """Provisional thresholds to be tuned only with future pilot data."""

    enter_minimum: float = 0.65
    enter_mean: float = 0.75
    stay_minimum: float = 0.50
    stay_mean: float = 0.65
    consecutive_valid_frames: int = 3
    lost_after_seconds: float = 1.0


@dataclass(frozen=True)
class ExerciseVisibilityPolicy:
    counting: frozenset[PoseLandmark]
    form_errors: Mapping[FormErrorCode, frozenset[PoseLandmark]]
    angles: Mapping[str, frozenset[PoseLandmark]]
    unavailable_hint: TrackingHintCode


SQUAT_COUNTING = frozenset(
    {
        PoseLandmark.LEFT_HIP,
        PoseLandmark.LEFT_KNEE,
        PoseLandmark.LEFT_ANKLE,
        PoseLandmark.RIGHT_HIP,
        PoseLandmark.RIGHT_KNEE,
        PoseLandmark.RIGHT_ANKLE,
    }
)

PUSHUP_COUNTING = frozenset(
    {
        PoseLandmark.LEFT_SHOULDER,
        PoseLandmark.LEFT_ELBOW,
        PoseLandmark.LEFT_WRIST,
        PoseLandmark.RIGHT_SHOULDER,
        PoseLandmark.RIGHT_ELBOW,
        PoseLandmark.RIGHT_WRIST,
    }
)

VISIBILITY_THRESHOLDS = VisibilityThresholds()

POSE_VISIBILITY_POLICIES: Mapping[str, ExerciseVisibilityPolicy] = MappingProxyType(
    {
        "squat": ExerciseVisibilityPolicy(
            counting=SQUAT_COUNTING,
            form_errors=MappingProxyType(
                {
                    FormErrorCode.SQUAT_DEPTH_INSUFFICIENT: SQUAT_COUNTING,
                    FormErrorCode.SQUAT_KNEE_VALGUS: frozenset(
                        {
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.LEFT_KNEE,
                            PoseLandmark.RIGHT_KNEE,
                        }
                    ),
                    FormErrorCode.SQUAT_TORSO_LEAN: frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.RIGHT_HIP,
                        }
                    ),
                }
            ),
            angles=MappingProxyType(
                {
                    "left_knee": frozenset(
                        {
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.LEFT_KNEE,
                            PoseLandmark.LEFT_ANKLE,
                        }
                    ),
                    "right_knee": frozenset(
                        {
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.RIGHT_KNEE,
                            PoseLandmark.RIGHT_ANKLE,
                        }
                    ),
                    "left_hip": frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.LEFT_KNEE,
                        }
                    ),
                    "right_hip": frozenset(
                        {
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.RIGHT_KNEE,
                        }
                    ),
                }
            ),
            unavailable_hint=TrackingHintCode.SQUAT_KNEES_NOT_VISIBLE,
        ),
        "pushup": ExerciseVisibilityPolicy(
            counting=PUSHUP_COUNTING,
            form_errors=MappingProxyType(
                {
                    FormErrorCode.PUSHUP_DEPTH_INSUFFICIENT: PUSHUP_COUNTING,
                    FormErrorCode.PUSHUP_HIP_SAG: frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.LEFT_ANKLE,
                            PoseLandmark.RIGHT_ANKLE,
                        }
                    ),
                    FormErrorCode.PUSHUP_HIP_PIKE: frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.LEFT_ANKLE,
                            PoseLandmark.RIGHT_ANKLE,
                        }
                    ),
                }
            ),
            angles=MappingProxyType(
                {
                    "left_elbow": frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.LEFT_ELBOW,
                            PoseLandmark.LEFT_WRIST,
                        }
                    ),
                    "right_elbow": frozenset(
                        {
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.RIGHT_ELBOW,
                            PoseLandmark.RIGHT_WRIST,
                        }
                    ),
                    "left_body": frozenset(
                        {
                            PoseLandmark.LEFT_SHOULDER,
                            PoseLandmark.LEFT_HIP,
                            PoseLandmark.LEFT_ANKLE,
                        }
                    ),
                    "right_body": frozenset(
                        {
                            PoseLandmark.RIGHT_SHOULDER,
                            PoseLandmark.RIGHT_HIP,
                            PoseLandmark.RIGHT_ANKLE,
                        }
                    ),
                }
            ),
            unavailable_hint=TrackingHintCode.PUSHUP_ARMS_NOT_VISIBLE,
        ),
    }
)


def visibility_statistics(
    landmarks: Sequence[VisibleLandmark], dependencies: frozenset[PoseLandmark]
) -> tuple[float, float]:
    """Return minimum and mean visibility for exactly the supplied dependencies."""

    values = [float(landmarks[index].visibility) for index in dependencies]
    return min(values), sum(values) / len(values)


def meets_visibility_threshold(
    landmarks: Sequence[VisibleLandmark],
    dependencies: frozenset[PoseLandmark],
    *,
    minimum: float,
    mean: float,
) -> bool:
    observed_minimum, observed_mean = visibility_statistics(landmarks, dependencies)
    return observed_minimum >= minimum and observed_mean >= mean
