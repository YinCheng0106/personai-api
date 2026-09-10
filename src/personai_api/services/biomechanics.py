"""
生物力學運算層 (Biomechanics Layer)
角度計算、One-Euro Filter 平滑濾波、FSM 狀態機與錯誤偵測

模組流程：keypoints 接收 → 平滑濾波 → 角度計算 → FSM 判定 → 錯誤偵測

前端（Next.js）負責 MediaPipe 姿態偵測，後端僅接收 33 個 keypoints JSON
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from personai_api.services.pose_visibility import (
    POSE_VISIBILITY_POLICIES,
    VISIBILITY_THRESHOLDS,
    ExerciseVisibilityPolicy,
    FormErrorCode,
    PoseLandmark,
    TrackingHintCode,
    TrackingState,
    VisibilityThresholds,
    meets_visibility_threshold,
    visibility_statistics,
)


# ============================================================
# 資料結構
# ============================================================
class Point(NamedTuple):
    """2D/3D 關鍵點座標（正規化：x, y 範圍 0~1）"""

    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.0


# ============================================================
# 數學工具：三點角度計算 (NumPy 向量運算)
# ============================================================
def calculate_angle(a: tuple, b: tuple, c: tuple) -> float | None:
    """
    計算 a→b→c 三點所構成的夾角（以 b 為頂點）

    原理：利用向量內積公式 cos(θ) = (BA·BC) / (|BA|×|BC|)

    Parameters
    ----------
    a, b, c : tuple(x, y)
        三個關鍵點座標，b 為角度頂點

    Returns
    -------
    float | None : 角度 (0~180 度)；退化向量無法計算時為 None
    """
    av, bv, cv = np.array(a[:2]), np.array(b[:2]), np.array(c[:2])

    ba = av - bv  # 向量 b→a
    bc = cv - bv  # 向量 b→c

    # 避免零向量造成除以零
    norm_ba = np.linalg.norm(ba)
    norm_bc = np.linalg.norm(bc)
    if norm_ba == 0 or norm_bc == 0:
        return None

    cosine = np.dot(ba, bc) / (norm_ba * norm_bc)
    # np.clip 防止浮點誤差超出 arccos 定義域 [-1, 1]
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


# ============================================================
# One-Euro Filter：低延遲即時訊號平滑濾波器
# 論文：Casiez et al. "1€ Filter" (CHI 2012)
# ============================================================
class _LowPassFilter:
    """一階低通濾波器（內部使用）"""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self._prev: float | None = None

    def __call__(self, value: float) -> float:
        if self._prev is None:
            self._prev = value
        else:
            self._prev = self.alpha * value + (1.0 - self.alpha) * self._prev
        return self._prev

    def reset(self):
        self._prev = None


class OneEuroFilter:
    """
    One-Euro Filter — 自適應低通濾波器

    Parameters（調整指南）
    ----------
    freq       : 取樣頻率 (Hz)，通常等於 webcam FPS（如 30）
    min_cutoff : 最小截止頻率，值越小平滑越強但延遲越高（建議 0.5~2.0）
    beta       : 速度係數，值越大對快速移動響應越快（建議 0.0~1.0）
    d_cutoff   : 微分訊號截止頻率（通常不需調整）
    """

    def __init__(
        self,
        freq: float = 30.0,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_filter = _LowPassFilter()
        self._dx_filter = _LowPassFilter()
        self._last_time: float | None = None

    @staticmethod
    def _smoothing_factor(te: float, cutoff: float) -> float:
        tau = 1.0 / (2.0 * np.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x: float, t: float | None = None) -> float:
        """輸入原始值，回傳平滑後的值"""
        if self._last_time is not None and t is not None:
            dt = t - self._last_time
            if dt > 0:
                self.freq = 1.0 / dt
        self._last_time = t

        te = 1.0 / self.freq

        # 估算變化速度（微分）
        prev = self._x_filter._prev
        dx = 0.0 if prev is None else (x - prev) * self.freq
        edx = self._dx_filter(dx)

        # 根據速度自適應調整截止頻率
        cutoff = self.min_cutoff + self.beta * abs(edx)
        self._x_filter.alpha = self._smoothing_factor(te, cutoff)

        return self._x_filter(x)

    def reset(self):
        self._x_filter.reset()
        self._dx_filter.reset()
        self._last_time = None


class LandmarkSmoother:
    """
    對所有關鍵點座標套用 One-Euro Filter

    每個 landmark 的 x, y 各一個濾波器 → 33 × 2 = 66 個濾波器
    """

    def __init__(self, num_landmarks: int = 33, **filter_kwargs):
        self._filters: list[tuple[OneEuroFilter, OneEuroFilter]] = [
            (OneEuroFilter(**filter_kwargs), OneEuroFilter(**filter_kwargs))
            for _ in range(num_landmarks)
        ]

    def smooth(
        self,
        landmarks: list[Point],
        t: float | None = None,
        update_indices: frozenset[int] | None = None,
    ) -> list[Point]:
        smoothed = []
        for i, lm in enumerate(landmarks):
            should_update = update_indices is None or i in update_indices
            if i < len(self._filters) and should_update:
                fx, fy = self._filters[i]
                smoothed.append(Point(fx(lm.x, t), fy(lm.y, t), lm.z, lm.visibility))
            else:
                smoothed.append(lm)
        return smoothed

    def reset(self):
        for fx, fy in self._filters:
            fx.reset()
            fy.reset()


# ============================================================
# FSM 狀態定義
# ============================================================
class SquatState(enum.Enum):
    """深蹲動作階段"""

    IDLE = "idle"  # 站立待機
    DESCENDING = "descending"  # 下蹲中
    BOTTOM = "bottom"  # 蹲到最低點
    ASCENDING = "ascending"  # 起身中


class PushUpState(enum.Enum):
    """伏地挺身動作階段"""

    UP = "up"  # 撐起（手臂伸直）
    DESCENDING = "descending"  # 下壓中
    BOTTOM = "bottom"  # 壓到最低點
    ASCENDING = "ascending"  # 推起中


# ============================================================
# 深蹲 FSM — 計數 + 錯誤偵測
#
# 狀態轉換：IDLE → DESCENDING → BOTTOM → ASCENDING → IDLE (+1 rep)
# ============================================================
@dataclass
class SquatConfig:
    """
    深蹲閾值設定（角度單位：度）

    調整指南：
    - knee_bottom_angle  : 蹲到底的膝蓋角度上限，越小要求蹲越深
    - knee_standing_angle: 判定站立的膝蓋角度下限
    - knee_valgus_ratio  : 膝距/臀距 < 此值 → 膝蓋內扣
    - hysteresis         : 遲滯區間（防止狀態抖動）
    """

    knee_bottom_angle: float = 100.0
    knee_standing_angle: float = 160.0
    knee_valgus_ratio: float = 0.8
    torso_lean_max: float = 45.0
    hysteresis: float = 5.0


class SquatFSM:
    def __init__(self, config: SquatConfig | None = None):
        self.config = config or SquatConfig()
        self.state = SquatState.IDLE
        self.rep_count = 0
        self._reached_bottom = False
        self._errors: list[str] = []

    def update(
        self,
        landmarks: list[Point],
        enabled_rules: frozenset[FormErrorCode] | None = None,
    ) -> tuple[SquatState, int, list[str]]:
        """根據當前幀的關鍵點更新狀態機，回傳 (state, rep_count, errors)"""
        self._errors = []
        cfg = self.config
        rules = enabled_rules if enabled_rules is not None else frozenset(FormErrorCode)

        # --- 1. 擷取關鍵點 ---
        l_hip = landmarks[PoseLandmark.LEFT_HIP]
        r_hip = landmarks[PoseLandmark.RIGHT_HIP]
        l_knee = landmarks[PoseLandmark.LEFT_KNEE]
        r_knee = landmarks[PoseLandmark.RIGHT_KNEE]
        l_ankle = landmarks[PoseLandmark.LEFT_ANKLE]
        r_ankle = landmarks[PoseLandmark.RIGHT_ANKLE]
        l_shoulder = landmarks[PoseLandmark.LEFT_SHOULDER]
        r_shoulder = landmarks[PoseLandmark.RIGHT_SHOULDER]

        # --- 2. 計算關節角度（取左右平均） ---
        left_knee_angle = calculate_angle(
            (l_hip.x, l_hip.y), (l_knee.x, l_knee.y), (l_ankle.x, l_ankle.y)
        )
        right_knee_angle = calculate_angle(
            (r_hip.x, r_hip.y), (r_knee.x, r_knee.y), (r_ankle.x, r_ankle.y)
        )
        if left_knee_angle is None or right_knee_angle is None:
            return self.state, self.rep_count, self._errors
        knee_angle = (left_knee_angle + right_knee_angle) / 2

        # --- 3. 錯誤偵測 ---
        # 膝蓋內扣：膝蓋間距 vs 髖部間距
        if FormErrorCode.SQUAT_KNEE_VALGUS in rules:
            knee_dist = abs(l_knee.x - r_knee.x)
            hip_dist = abs(l_hip.x - r_hip.x)
            if hip_dist > 0 and knee_dist / hip_dist < cfg.knee_valgus_ratio:
                self._errors.append(FormErrorCode.SQUAT_KNEE_VALGUS.value)

        # 軀幹前傾：肩膀中點-髖部中點 vs 垂直線
        mid_sh = ((l_shoulder.x + r_shoulder.x) / 2, (l_shoulder.y + r_shoulder.y) / 2)
        mid_hp = ((l_hip.x + r_hip.x) / 2, (l_hip.y + r_hip.y) / 2)
        vertical = (mid_hp[0], mid_hp[1] - 0.3)  # 正上方虛擬點
        if FormErrorCode.SQUAT_TORSO_LEAN in rules:
            torso_angle = calculate_angle(mid_sh, mid_hp, vertical)
            if torso_angle is not None and torso_angle > cfg.torso_lean_max:
                self._errors.append(FormErrorCode.SQUAT_TORSO_LEAN.value)

        # --- 4. FSM 狀態轉換 ---
        if self.state == SquatState.IDLE:
            if knee_angle < cfg.knee_standing_angle - cfg.hysteresis:
                self.state = SquatState.DESCENDING
                self._reached_bottom = False

        elif self.state == SquatState.DESCENDING:
            if knee_angle <= cfg.knee_bottom_angle:
                self.state = SquatState.BOTTOM
                self._reached_bottom = True
            elif knee_angle > cfg.knee_standing_angle:
                if FormErrorCode.SQUAT_DEPTH_INSUFFICIENT in rules:
                    self._errors.append(FormErrorCode.SQUAT_DEPTH_INSUFFICIENT.value)
                self.state = SquatState.IDLE

        elif self.state == SquatState.BOTTOM:
            if knee_angle > cfg.knee_bottom_angle + cfg.hysteresis:
                self.state = SquatState.ASCENDING

        elif self.state == SquatState.ASCENDING:
            if knee_angle >= cfg.knee_standing_angle:
                if self._reached_bottom:
                    self.rep_count += 1
                self.state = SquatState.IDLE

        return self.state, self.rep_count, self._errors

    def abandon_cycle(self) -> None:
        self.state = SquatState.IDLE
        self._reached_bottom = False
        self._errors = []

    def reset(self) -> None:
        self.abandon_cycle()
        self.rep_count = 0


# ============================================================
# 伏地挺身 FSM
#
# 狀態轉換：UP → DESCENDING → BOTTOM → ASCENDING → UP (+1 rep)
# ============================================================
@dataclass
class PushUpConfig:
    """
    伏地挺身閾值設定

    調整指南：
    - elbow_bottom_angle   : 肘部角度 < 此值 → 到達底部
    - elbow_up_angle       : 肘部角度 > 此值 → 撐起
    - body_alignment_min   : 肩-髖-踝角度 < 此值 → 身體不夠直
    """

    elbow_bottom_angle: float = 90.0
    elbow_up_angle: float = 160.0
    body_alignment_min: float = 150.0
    hysteresis: float = 5.0


class PushUpFSM:
    def __init__(self, config: PushUpConfig | None = None):
        self.config = config or PushUpConfig()
        self.state = PushUpState.UP
        self.rep_count = 0
        self._reached_bottom = False
        self._errors: list[str] = []

    def update(
        self,
        landmarks: list[Point],
        enabled_rules: frozenset[FormErrorCode] | None = None,
    ) -> tuple[PushUpState, int, list[str]]:
        self._errors = []
        cfg = self.config
        rules = enabled_rules if enabled_rules is not None else frozenset(FormErrorCode)

        # --- 擷取關鍵點 ---
        l_sh = landmarks[PoseLandmark.LEFT_SHOULDER]
        r_sh = landmarks[PoseLandmark.RIGHT_SHOULDER]
        l_el = landmarks[PoseLandmark.LEFT_ELBOW]
        r_el = landmarks[PoseLandmark.RIGHT_ELBOW]
        l_wr = landmarks[PoseLandmark.LEFT_WRIST]
        r_wr = landmarks[PoseLandmark.RIGHT_WRIST]
        l_hp = landmarks[PoseLandmark.LEFT_HIP]
        r_hp = landmarks[PoseLandmark.RIGHT_HIP]
        l_ak = landmarks[PoseLandmark.LEFT_ANKLE]
        r_ak = landmarks[PoseLandmark.RIGHT_ANKLE]

        # --- 肘部角度（左右平均）---
        left_elbow_angle = calculate_angle(
            (l_sh.x, l_sh.y), (l_el.x, l_el.y), (l_wr.x, l_wr.y)
        )
        right_elbow_angle = calculate_angle(
            (r_sh.x, r_sh.y), (r_el.x, r_el.y), (r_wr.x, r_wr.y)
        )
        if left_elbow_angle is None or right_elbow_angle is None:
            return self.state, self.rep_count, self._errors
        elbow_angle = (left_elbow_angle + right_elbow_angle) / 2

        # --- 錯誤偵測 ---
        alignment_rules = frozenset(
            {
                FormErrorCode.PUSHUP_HIP_SAG,
                FormErrorCode.PUSHUP_HIP_PIKE,
            }
        )
        if rules & alignment_rules:
            left_body_angle = calculate_angle(
                (l_sh.x, l_sh.y), (l_hp.x, l_hp.y), (l_ak.x, l_ak.y)
            )
            right_body_angle = calculate_angle(
                (r_sh.x, r_sh.y), (r_hp.x, r_hp.y), (r_ak.x, r_ak.y)
            )
            body_angle = (
                (left_body_angle + right_body_angle) / 2
                if left_body_angle is not None and right_body_angle is not None
                else None
            )
        else:
            body_angle = None

        if body_angle is not None and body_angle < cfg.body_alignment_min:
            mid_sh_y = (l_sh.y + r_sh.y) / 2
            mid_hp_y = (l_hp.y + r_hp.y) / 2
            mid_ak_y = (l_ak.y + r_ak.y) / 2
            expected_y = (mid_sh_y + mid_ak_y) / 2
            # MediaPipe y 軸向下遞增
            if mid_hp_y > expected_y:
                if FormErrorCode.PUSHUP_HIP_SAG in rules:
                    self._errors.append(FormErrorCode.PUSHUP_HIP_SAG.value)
            elif FormErrorCode.PUSHUP_HIP_PIKE in rules:
                self._errors.append(FormErrorCode.PUSHUP_HIP_PIKE.value)

        # --- FSM 狀態轉換 ---
        if self.state == PushUpState.UP:
            if elbow_angle < cfg.elbow_up_angle - cfg.hysteresis:
                self.state = PushUpState.DESCENDING
                self._reached_bottom = False

        elif self.state == PushUpState.DESCENDING:
            if elbow_angle <= cfg.elbow_bottom_angle:
                self.state = PushUpState.BOTTOM
                self._reached_bottom = True
            elif elbow_angle > cfg.elbow_up_angle:
                if FormErrorCode.PUSHUP_DEPTH_INSUFFICIENT in rules:
                    self._errors.append(FormErrorCode.PUSHUP_DEPTH_INSUFFICIENT.value)
                self.state = PushUpState.UP

        elif self.state == PushUpState.BOTTOM:
            if elbow_angle > cfg.elbow_bottom_angle + cfg.hysteresis:
                self.state = PushUpState.ASCENDING

        elif self.state == PushUpState.ASCENDING:
            if elbow_angle >= cfg.elbow_up_angle:
                if self._reached_bottom:
                    self.rep_count += 1
                self.state = PushUpState.UP

        return self.state, self.rep_count, self._errors

    def abandon_cycle(self) -> None:
        self.state = PushUpState.UP
        self._reached_bottom = False
        self._errors = []

    def reset(self) -> None:
        self.abandon_cycle()
        self.rep_count = 0


# ============================================================
# BiomechanicsAnalyzer：整合分析引擎（供 WebSocket handler 呼叫）
# ============================================================
class BiomechanicsAnalyzer:
    """
    生物力學分析引擎 — 接收前端 keypoints，進行角度計算與 FSM 判定

    The visibility guard owns whether a frame can reach filtering, geometry, or FSM.
    Visibility thresholds are provisional and centralized in ``pose_visibility``.
    """

    def __init__(
        self,
        exercise_type: str = "squat",
        visibility_thresholds: VisibilityThresholds = VISIBILITY_THRESHOLDS,
    ):
        try:
            self.policy: ExerciseVisibilityPolicy = POSE_VISIBILITY_POLICIES[
                exercise_type
            ]
        except KeyError as exc:
            raise ValueError(f"不支援的運動類型: {exercise_type}") from exc

        self.exercise_type = exercise_type
        self.visibility_thresholds = visibility_thresholds
        self._smoother = LandmarkSmoother(freq=30.0, min_cutoff=1.0, beta=0.007)
        self.tracking_state = TrackingState.ACQUIRING
        self._consecutive_valid_frames = 0
        self._invalid_since: float | None = None
        self._movement_interrupted = False

        if exercise_type == "squat":
            self._fsm: SquatFSM | PushUpFSM = SquatFSM()
        else:
            self._fsm = PushUpFSM()

    def analyze(
        self, landmarks: list[Point], timestamp: float | None = None
    ) -> AnalysisResult:
        """
        分析單幀 keypoints

        Parameters
        ----------
        landmarks : list[Point] — 33 個關鍵點（由前端 MediaPipe 偵測）
        timestamp : float | None — 時間戳記（秒），用於濾波器頻率估算
        """
        t = timestamp if timestamp is not None else time.monotonic()
        _, confidence = visibility_statistics(landmarks, self.policy.counting)
        acquiring = self.tracking_state != TrackingState.ACTIVE
        minimum = (
            self.visibility_thresholds.enter_minimum
            if acquiring
            else self.visibility_thresholds.stay_minimum
        )
        mean = (
            self.visibility_thresholds.enter_mean
            if acquiring
            else self.visibility_thresholds.stay_mean
        )
        is_count_valid = meets_visibility_threshold(
            landmarks,
            self.policy.counting,
            minimum=minimum,
            mean=mean,
        )

        if not is_count_valid:
            return self._handle_unavailable(
                t,
                confidence=confidence,
                hint=self.policy.unavailable_hint,
            )

        if self.tracking_state != TrackingState.ACTIVE:
            if self.tracking_state in {TrackingState.PAUSED, TrackingState.LOST}:
                self.tracking_state = TrackingState.ACQUIRING
            self._invalid_since = None
            self._consecutive_valid_frames += 1
            if (
                self._consecutive_valid_frames
                < self.visibility_thresholds.consecutive_valid_frames
            ):
                return self._unavailable_result(
                    confidence=confidence,
                    hint=TrackingHintCode.REACQUIRING_POSE,
                )

            if self._movement_interrupted:
                self._fsm.abandon_cycle()
                self._movement_interrupted = False
            self.tracking_state = TrackingState.ACTIVE

        self._consecutive_valid_frames = (
            self.visibility_thresholds.consecutive_valid_frames
        )
        self._invalid_since = None

        enabled_rules = frozenset(
            error_code
            for error_code, dependencies in self.policy.form_errors.items()
            if meets_visibility_threshold(
                landmarks,
                dependencies,
                minimum=self.visibility_thresholds.stay_minimum,
                mean=self.visibility_thresholds.stay_mean,
            )
        )
        enabled_angles = frozenset(
            name
            for name, dependencies in self.policy.angles.items()
            if meets_visibility_threshold(
                landmarks,
                dependencies,
                minimum=self.visibility_thresholds.stay_minimum,
                mean=self.visibility_thresholds.stay_mean,
            )
        )
        relevant_landmarks = frozenset(
            int(index)
            for dependencies in (
                [self.policy.counting]
                + list(self.policy.form_errors.values())
                + list(self.policy.angles.values())
            )
            for index in dependencies
            if landmarks[index].visibility >= self.visibility_thresholds.stay_minimum
        )

        # Authoritative order: visibility guard -> filter -> geometry/FSM/errors.
        smoothed = self._smoother.smooth(
            landmarks,
            t,
            update_indices=relevant_landmarks,
        )
        state, rep_count, form_errors = self._fsm.update(smoothed, enabled_rules)
        angles = self._compute_angles(smoothed, enabled_angles)

        return AnalysisResult(
            rep_count=rep_count,
            state=state.value,
            angles=angles,
            form_errors=form_errors,
            tracking_hints=[],
            confidence=round(confidence, 2),
            is_visible=True,
            tracking_state=self.tracking_state.value,
        )

    def pose_missing(self, timestamp: float | None = None) -> AnalysisResult:
        """Record an explicit MediaPipe no-pose observation."""

        t = timestamp if timestamp is not None else time.monotonic()
        return self._handle_unavailable(
            t,
            confidence=0.0,
            hint=TrackingHintCode.POSE_NOT_FOUND,
        )

    def _handle_unavailable(
        self,
        timestamp: float,
        *,
        confidence: float,
        hint: TrackingHintCode,
    ) -> AnalysisResult:
        self._consecutive_valid_frames = 0
        if self._invalid_since is None:
            self._invalid_since = timestamp

        if self.tracking_state == TrackingState.ACTIVE:
            self.tracking_state = TrackingState.PAUSED
            self._movement_interrupted = True

        elapsed = max(0.0, timestamp - self._invalid_since)
        if (
            self.tracking_state != TrackingState.LOST
            and elapsed >= self.visibility_thresholds.lost_after_seconds
        ):
            self.tracking_state = TrackingState.LOST
            self._fsm.abandon_cycle()
            self._smoother.reset()
            self._movement_interrupted = False

        return self._unavailable_result(confidence=confidence, hint=hint)

    def _unavailable_result(
        self,
        *,
        confidence: float,
        hint: TrackingHintCode,
    ) -> AnalysisResult:
        return AnalysisResult(
            rep_count=self._fsm.rep_count,
            state=self._fsm.state.value,
            angles={name: None for name in self.policy.angles},
            form_errors=[],
            tracking_hints=[hint.value],
            confidence=round(confidence, 2),
            is_visible=False,
            tracking_state=self.tracking_state.value,
        )

    def _compute_angles(
        self,
        lm: list[Point],
        enabled_angles: frozenset[str],
    ) -> dict[str, float | None]:
        """Compute only angles whose explicit landmark dependencies are valid."""

        angles: dict[str, float | None] = {name: None for name in self.policy.angles}

        if self.exercise_type == "squat":
            if "left_knee" in enabled_angles:
                angles["left_knee"] = calculate_angle(
                    (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                    (lm[PoseLandmark.LEFT_KNEE].x, lm[PoseLandmark.LEFT_KNEE].y),
                    (lm[PoseLandmark.LEFT_ANKLE].x, lm[PoseLandmark.LEFT_ANKLE].y),
                )
            if "right_knee" in enabled_angles:
                angles["right_knee"] = calculate_angle(
                    (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                    (lm[PoseLandmark.RIGHT_KNEE].x, lm[PoseLandmark.RIGHT_KNEE].y),
                    (lm[PoseLandmark.RIGHT_ANKLE].x, lm[PoseLandmark.RIGHT_ANKLE].y),
                )
            if "left_hip" in enabled_angles:
                angles["left_hip"] = calculate_angle(
                    (
                        lm[PoseLandmark.LEFT_SHOULDER].x,
                        lm[PoseLandmark.LEFT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                    (lm[PoseLandmark.LEFT_KNEE].x, lm[PoseLandmark.LEFT_KNEE].y),
                )
            if "right_hip" in enabled_angles:
                angles["right_hip"] = calculate_angle(
                    (
                        lm[PoseLandmark.RIGHT_SHOULDER].x,
                        lm[PoseLandmark.RIGHT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                    (lm[PoseLandmark.RIGHT_KNEE].x, lm[PoseLandmark.RIGHT_KNEE].y),
                )
        elif self.exercise_type == "pushup":
            if "left_elbow" in enabled_angles:
                angles["left_elbow"] = calculate_angle(
                    (
                        lm[PoseLandmark.LEFT_SHOULDER].x,
                        lm[PoseLandmark.LEFT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.LEFT_ELBOW].x, lm[PoseLandmark.LEFT_ELBOW].y),
                    (lm[PoseLandmark.LEFT_WRIST].x, lm[PoseLandmark.LEFT_WRIST].y),
                )
            if "right_elbow" in enabled_angles:
                angles["right_elbow"] = calculate_angle(
                    (
                        lm[PoseLandmark.RIGHT_SHOULDER].x,
                        lm[PoseLandmark.RIGHT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.RIGHT_ELBOW].x, lm[PoseLandmark.RIGHT_ELBOW].y),
                    (lm[PoseLandmark.RIGHT_WRIST].x, lm[PoseLandmark.RIGHT_WRIST].y),
                )
            if "left_body" in enabled_angles:
                angles["left_body"] = calculate_angle(
                    (
                        lm[PoseLandmark.LEFT_SHOULDER].x,
                        lm[PoseLandmark.LEFT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                    (lm[PoseLandmark.LEFT_ANKLE].x, lm[PoseLandmark.LEFT_ANKLE].y),
                )
            if "right_body" in enabled_angles:
                angles["right_body"] = calculate_angle(
                    (
                        lm[PoseLandmark.RIGHT_SHOULDER].x,
                        lm[PoseLandmark.RIGHT_SHOULDER].y,
                    ),
                    (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                    (lm[PoseLandmark.RIGHT_ANKLE].x, lm[PoseLandmark.RIGHT_ANKLE].y),
                )

        return {
            name: round(value, 1) if value is not None else None
            for name, value in angles.items()
        }

    def reset(self):
        """重置狀態（開始新的一組訓練）"""
        self._fsm.reset()
        self._smoother.reset()
        self.tracking_state = TrackingState.ACQUIRING
        self._consecutive_valid_frames = 0
        self._invalid_since = None
        self._movement_interrupted = False


@dataclass
class AnalysisResult:
    """單幀分析結果（內部使用）"""

    rep_count: int
    state: str
    angles: dict[str, float | None]
    form_errors: list[str]
    tracking_hints: list[str]
    confidence: float
    is_visible: bool
    tracking_state: str
