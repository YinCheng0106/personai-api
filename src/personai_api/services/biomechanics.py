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


# ============================================================
# 常數：MediaPipe Pose 33 個關鍵點索引（僅列出本專案常用點位）
# ============================================================
class PoseLandmark(enum.IntEnum):
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
def calculate_angle(a: tuple, b: tuple, c: tuple) -> float:
    """
    計算 a→b→c 三點所構成的夾角（以 b 為頂點）

    原理：利用向量內積公式 cos(θ) = (BA·BC) / (|BA|×|BC|)

    Parameters
    ----------
    a, b, c : tuple(x, y)
        三個關鍵點座標，b 為角度頂點

    Returns
    -------
    float : 角度 (0~180 度)
    """
    a, b, c = np.array(a[:2]), np.array(b[:2]), np.array(c[:2])

    ba = a - b  # 向量 b→a
    bc = c - b  # 向量 b→c

    # 避免零向量造成除以零
    norm_ba = np.linalg.norm(ba)
    norm_bc = np.linalg.norm(bc)
    if norm_ba == 0 or norm_bc == 0:
        return 0.0

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

    def smooth(self, landmarks: list[Point], t: float | None = None) -> list[Point]:
        smoothed = []
        for i, lm in enumerate(landmarks):
            if i < len(self._filters):
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

    def update(self, landmarks: list[Point]) -> tuple[SquatState, int, list[str]]:
        """根據當前幀的關鍵點更新狀態機，回傳 (state, rep_count, errors)"""
        self._errors = []
        cfg = self.config

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
        knee_angle = (
            calculate_angle(
                (l_hip.x, l_hip.y), (l_knee.x, l_knee.y), (l_ankle.x, l_ankle.y)
            )
            + calculate_angle(
                (r_hip.x, r_hip.y), (r_knee.x, r_knee.y), (r_ankle.x, r_ankle.y)
            )
        ) / 2

        # --- 3. 錯誤偵測 ---
        # 膝蓋內扣：膝蓋間距 vs 髖部間距
        knee_dist = abs(l_knee.x - r_knee.x)
        hip_dist = abs(l_hip.x - r_hip.x)
        if hip_dist > 0 and knee_dist / hip_dist < cfg.knee_valgus_ratio:
            self._errors.append("膝蓋內扣：請將膝蓋對齊腳尖方向")

        # 軀幹前傾：肩膀中點-髖部中點 vs 垂直線
        mid_sh = ((l_shoulder.x + r_shoulder.x) / 2, (l_shoulder.y + r_shoulder.y) / 2)
        mid_hp = ((l_hip.x + r_hip.x) / 2, (l_hip.y + r_hip.y) / 2)
        vertical = (mid_hp[0], mid_hp[1] - 0.3)  # 正上方虛擬點
        torso_angle = calculate_angle(mid_sh, mid_hp, vertical)
        if torso_angle > cfg.torso_lean_max:
            self._errors.append("軀幹過度前傾：請保持挺胸")

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
                self._errors.append("深度不足：請蹲得再低一些")
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

    def reset(self):
        self.state = SquatState.IDLE
        self.rep_count = 0
        self._reached_bottom = False
        self._errors = []


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

    def update(self, landmarks: list[Point]) -> tuple[PushUpState, int, list[str]]:
        self._errors = []
        cfg = self.config

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
        elbow_angle = (
            calculate_angle((l_sh.x, l_sh.y), (l_el.x, l_el.y), (l_wr.x, l_wr.y))
            + calculate_angle((r_sh.x, r_sh.y), (r_el.x, r_el.y), (r_wr.x, r_wr.y))
        ) / 2

        # --- 身體直線度（肩-髖-踝，左右平均）---
        body_angle = (
            calculate_angle((l_sh.x, l_sh.y), (l_hp.x, l_hp.y), (l_ak.x, l_ak.y))
            + calculate_angle((r_sh.x, r_sh.y), (r_hp.x, r_hp.y), (r_ak.x, r_ak.y))
        ) / 2

        # --- 錯誤偵測 ---
        if body_angle < cfg.body_alignment_min:
            mid_sh_y = (l_sh.y + r_sh.y) / 2
            mid_hp_y = (l_hp.y + r_hp.y) / 2
            mid_ak_y = (l_ak.y + r_ak.y) / 2
            expected_y = (mid_sh_y + mid_ak_y) / 2
            # MediaPipe y 軸向下遞增
            if mid_hp_y > expected_y:
                self._errors.append("身體下沉：請收緊核心保持身體成一直線")
            else:
                self._errors.append("臀部過高：請放低臀部保持身體成一直線")

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
                self._errors.append("深度不足：請再往下壓低一些")
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

    def reset(self):
        self.state = PushUpState.UP
        self.rep_count = 0
        self._reached_bottom = False
        self._errors = []


# ============================================================
# BiomechanicsAnalyzer：整合分析引擎（供 WebSocket handler 呼叫）
# ============================================================
class BiomechanicsAnalyzer:
    """
    生物力學分析引擎 — 接收前端 keypoints，進行角度計算與 FSM 判定

    Parameters
    ----------
    exercise_type : "squat" 或 "pushup"
    min_confidence : 最低信心分數，低於此值提示調整位置
    """

    def __init__(
        self,
        exercise_type: str = "squat",
        min_confidence: float = 0.5,
    ):
        self.exercise_type = exercise_type
        self.min_confidence = min_confidence
        self._smoother = LandmarkSmoother(freq=30.0, min_cutoff=1.0, beta=0.007)

        if exercise_type == "squat":
            self._fsm: SquatFSM | PushUpFSM = SquatFSM()
        elif exercise_type == "pushup":
            self._fsm = PushUpFSM()
        else:
            raise ValueError(f"不支援的運動類型: {exercise_type}")

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
        t = timestamp if timestamp is not None else time.time()

        # Step 1: 信心分數
        confidence = self._get_average_confidence(landmarks)
        is_visible = self._is_fully_visible(landmarks, self.min_confidence)

        # Step 2: One-Euro Filter 平滑
        landmarks = self._smoother.smooth(landmarks, t)

        # Step 3: FSM 更新
        state, rep_count, errors = self._fsm.update(landmarks)

        if not is_visible:
            errors.append("請調整位置，確保全身在畫面中")

        # Step 4: 計算角度供前端 HUD
        angles = self._compute_angles(landmarks)

        return AnalysisResult(
            rep_count=rep_count,
            state=state.value,
            angles=angles,
            errors=errors,
            confidence=round(confidence, 2),
            is_visible=is_visible,
        )

    def _compute_angles(self, lm: list[Point]) -> dict[str, float]:
        """計算相關角度（依運動類型不同）"""
        angles: dict[str, float] = {}

        if self.exercise_type == "squat":
            angles["left_knee"] = calculate_angle(
                (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                (lm[PoseLandmark.LEFT_KNEE].x, lm[PoseLandmark.LEFT_KNEE].y),
                (lm[PoseLandmark.LEFT_ANKLE].x, lm[PoseLandmark.LEFT_ANKLE].y),
            )
            angles["right_knee"] = calculate_angle(
                (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                (lm[PoseLandmark.RIGHT_KNEE].x, lm[PoseLandmark.RIGHT_KNEE].y),
                (lm[PoseLandmark.RIGHT_ANKLE].x, lm[PoseLandmark.RIGHT_ANKLE].y),
            )
            angles["left_hip"] = calculate_angle(
                (lm[PoseLandmark.LEFT_SHOULDER].x, lm[PoseLandmark.LEFT_SHOULDER].y),
                (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                (lm[PoseLandmark.LEFT_KNEE].x, lm[PoseLandmark.LEFT_KNEE].y),
            )
            angles["right_hip"] = calculate_angle(
                (lm[PoseLandmark.RIGHT_SHOULDER].x, lm[PoseLandmark.RIGHT_SHOULDER].y),
                (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                (lm[PoseLandmark.RIGHT_KNEE].x, lm[PoseLandmark.RIGHT_KNEE].y),
            )
        elif self.exercise_type == "pushup":
            angles["left_elbow"] = calculate_angle(
                (lm[PoseLandmark.LEFT_SHOULDER].x, lm[PoseLandmark.LEFT_SHOULDER].y),
                (lm[PoseLandmark.LEFT_ELBOW].x, lm[PoseLandmark.LEFT_ELBOW].y),
                (lm[PoseLandmark.LEFT_WRIST].x, lm[PoseLandmark.LEFT_WRIST].y),
            )
            angles["right_elbow"] = calculate_angle(
                (lm[PoseLandmark.RIGHT_SHOULDER].x, lm[PoseLandmark.RIGHT_SHOULDER].y),
                (lm[PoseLandmark.RIGHT_ELBOW].x, lm[PoseLandmark.RIGHT_ELBOW].y),
                (lm[PoseLandmark.RIGHT_WRIST].x, lm[PoseLandmark.RIGHT_WRIST].y),
            )
            angles["left_body"] = calculate_angle(
                (lm[PoseLandmark.LEFT_SHOULDER].x, lm[PoseLandmark.LEFT_SHOULDER].y),
                (lm[PoseLandmark.LEFT_HIP].x, lm[PoseLandmark.LEFT_HIP].y),
                (lm[PoseLandmark.LEFT_ANKLE].x, lm[PoseLandmark.LEFT_ANKLE].y),
            )
            angles["right_body"] = calculate_angle(
                (lm[PoseLandmark.RIGHT_SHOULDER].x, lm[PoseLandmark.RIGHT_SHOULDER].y),
                (lm[PoseLandmark.RIGHT_HIP].x, lm[PoseLandmark.RIGHT_HIP].y),
                (lm[PoseLandmark.RIGHT_ANKLE].x, lm[PoseLandmark.RIGHT_ANKLE].y),
            )

        return {k: round(v, 1) for k, v in angles.items()}

    @staticmethod
    def _get_average_confidence(landmarks: list[Point]) -> float:
        """計算所有關鍵點的平均信心分數"""
        if not landmarks:
            return 0.0
        return float(np.mean([lm.visibility for lm in landmarks]))

    @staticmethod
    def _is_fully_visible(landmarks: list[Point], min_conf: float = 0.5) -> bool:
        """
        檢查使用者是否全身入鏡

        判斷核心關鍵點（肩、髖、膝、踝）的 visibility 是否皆達標
        """
        key_indices = [
            PoseLandmark.LEFT_SHOULDER,
            PoseLandmark.RIGHT_SHOULDER,
            PoseLandmark.LEFT_HIP,
            PoseLandmark.RIGHT_HIP,
            PoseLandmark.LEFT_KNEE,
            PoseLandmark.RIGHT_KNEE,
            PoseLandmark.LEFT_ANKLE,
            PoseLandmark.RIGHT_ANKLE,
        ]
        return all(landmarks[i].visibility >= min_conf for i in key_indices)

    def reset(self):
        """重置狀態（開始新的一組訓練）"""
        self._fsm.reset()
        self._smoother.reset()


@dataclass
class AnalysisResult:
    """單幀分析結果（內部使用）"""

    rep_count: int
    state: str
    angles: dict[str, float]
    errors: list[str]
    confidence: float
    is_visible: bool
