"""
生理數據層 (Physio Layer)
InBody 身體組成數據處理、Katch-McArdle BMR 計算、METs 動態卡路里精算與 Pandas 統計報表

對應系統模組圖：
  3. 生理數據層 (Physio Layer): 用戶資料庫 → METs 動態強度對照 → 卡路里算法
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd


# ============================================================
# 運動類型 & 強度定義
# ============================================================
class ExerciseType(StrEnum):
    """系統支援的運動類型"""

    SQUAT = "squat"  # 深蹲
    PUSH_UP = "pushup"  # 伏地挺身


class ExerciseIntensity(StrEnum):
    """運動強度等級（根據動作速度與角度範圍動態判定）"""

    LIGHT = "light"  # 輕度（慢速 / 淺幅度）
    MODERATE = "moderate"  # 中度（標準速度與幅度）
    VIGOROUS = "vigorous"  # 高強度（快速 / 大幅度）


# ============================================================
# InBody 身體組成資料模型
# ============================================================
@dataclass
class InBodyProfile:
    """
    InBody 身體組成數據（由使用者從 InBody 報告手動輸入）

    欄位說明：
    - weight_kg              : 體重 (公斤)
    - height_cm              : 身高 (公分)
    - age                    : 年齡
    - gender                 : 性別 ("male" / "female")
    - body_fat_pct           : 體脂率 (%)，如 20.5 表示 20.5%
    - skeletal_muscle_mass_kg: 骨骼肌重 (公斤)
    - body_fat_mass_kg       : 體脂肪重 (公斤)
    - total_body_water_kg    : 身體水分 (公斤)，可選
    - visceral_fat_level     : 內臟脂肪等級 (1~20)，可選
    """

    weight_kg: float
    height_cm: float
    age: int
    gender: str
    body_fat_pct: float
    skeletal_muscle_mass_kg: float
    body_fat_mass_kg: float
    total_body_water_kg: float | None = None
    visceral_fat_level: int | None = None


# ============================================================
# 運動紀錄
# ============================================================
@dataclass
class WorkoutRecord:
    """單次運動紀錄（運動結束後由前端組裝傳送至後端儲存）"""

    user_id: str
    exercise_type: ExerciseType
    reps: int
    sets: int = 1
    duration_sec: float = 0.0
    calories_burned: float = 0.0
    avg_intensity: ExerciseIntensity = ExerciseIntensity.MODERATE
    errors_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# METs 代謝當量對照表
#
# 資料來源：Ainsworth BE, et al.
#           "Compendium of Physical Activities" (2011)
#
# 調整指南：數值越大 = 強度越高 = 消耗熱量越多
# ============================================================
METS_TABLE: dict[str, dict[str, float]] = {
    ExerciseType.SQUAT: {
        ExerciseIntensity.LIGHT: 3.5,  # 慢速淺蹲
        ExerciseIntensity.MODERATE: 5.0,  # 標準深蹲
        ExerciseIntensity.VIGOROUS: 8.0,  # 快速 / 負重深蹲
    },
    ExerciseType.PUSH_UP: {
        ExerciseIntensity.LIGHT: 3.8,  # 膝蓋撐地
        ExerciseIntensity.MODERATE: 5.5,  # 標準伏地挺身
        ExerciseIntensity.VIGOROUS: 8.0,  # 爆發式
    },
}


# ============================================================
# 身體組成計算
# ============================================================
def calculate_lean_body_mass(weight_kg: float, body_fat_pct: float) -> float:
    """
    計算淨體重 (Lean Body Mass, LBM)

    公式：LBM = 體重 × (1 - 體脂率 / 100)

    >>> calculate_lean_body_mass(70.0, 20.0)
    56.0
    """
    return weight_kg * (1.0 - body_fat_pct / 100.0)


def calculate_bmi(weight_kg: float, height_cm: float) -> float:
    """
    BMI = 體重(kg) / 身高(m)²
    """
    height_m = height_cm / 100.0
    if height_m <= 0:
        return 0.0
    return round(weight_kg / (height_m**2), 2)


def get_bmi_category(bmi: float) -> str:
    """
    依衛福部台灣成人 BMI 標準分類

    < 18.5: 過輕 / 18.5~23.9: 正常 / 24.0~26.9: 過重 / ≥ 27.0: 肥胖
    """
    if bmi < 18.5:
        return "過輕"
    elif bmi < 24.0:
        return "正常"
    elif bmi < 27.0:
        return "過重"
    return "肥胖"


# ============================================================
# BMR 基礎代謝率計算
# ============================================================
def calculate_bmr_katch_mcardle(lean_body_mass_kg: float) -> float:
    """
    Katch-McArdle 公式（推薦：搭配 InBody 體脂率資料使用）

    公式：BMR = 370 + 21.6 × LBM(kg)

    優點：使用淨體重而非體重，比 Harris-Benedict 更精準

    >>> lbm = calculate_lean_body_mass(70.0, 20.0)  # 56 kg
    >>> calculate_bmr_katch_mcardle(lbm)
    1579.6
    """
    return 370.0 + 21.6 * lean_body_mass_kg


def calculate_bmr_harris_benedict(
    weight_kg: float, height_cm: float, age: int, gender: str
) -> float:
    """
    Harris-Benedict 公式（備用：當無 InBody 體脂率資料時使用）

    男性：BMR = 88.362 + 13.397×體重 + 4.799×身高 - 5.677×年齡
    女性：BMR = 447.593 + 9.247×體重 + 3.098×身高 - 4.330×年齡
    """
    if gender == "male":
        return 88.362 + 13.397 * weight_kg + 4.799 * height_cm - 5.677 * age
    return 447.593 + 9.247 * weight_kg + 3.098 * height_cm - 4.330 * age


# ============================================================
# METs 查表 & 卡路里計算
# ============================================================
def get_mets(
    exercise_type: ExerciseType | str,
    intensity: ExerciseIntensity | str = ExerciseIntensity.MODERATE,
) -> float:
    """
    查表取得 METs 值

    若查無對應項目，預設回傳 5.0
    """
    ex = (
        ExerciseType(exercise_type) if isinstance(exercise_type, str) else exercise_type
    )
    inten = ExerciseIntensity(intensity) if isinstance(intensity, str) else intensity
    return METS_TABLE.get(ex, {}).get(inten, 5.0)


def calculate_calories(mets: float, weight_kg: float, duration_min: float) -> float:
    """
    計算運動消耗卡路里

    公式：kcal = METs × 體重(kg) × 時間(hr)

    >>> calculate_calories(mets=5.0, weight_kg=70.0, duration_min=30.0)
    175.0
    """
    return round(mets * weight_kg * (duration_min / 60.0), 1)


def estimate_calories_per_rep(
    exercise_type: ExerciseType | str,
    weight_kg: float,
    intensity: ExerciseIntensity | str = ExerciseIntensity.MODERATE,
    seconds_per_rep: float = 4.0,
) -> float:
    """
    估算單次動作消耗卡路里（即時更新用：每完成一次動作呼叫一次）

    Parameters
    ----------
    seconds_per_rep : float — 每次動作平均秒數，預設 4 秒
    """
    mets = get_mets(exercise_type, intensity)
    return calculate_calories(mets, weight_kg, seconds_per_rep / 60.0)


# ============================================================
# 動態強度判定
# ============================================================
def determine_intensity(
    rep_duration_sec: float,
    angle_range: float,
    exercise_type: ExerciseType | str,
) -> ExerciseIntensity:
    """
    根據動作速度與角度變化幅度，動態判定運動強度

    深蹲判定：
      VIGOROUS : 每次 < 2s 且 角度範圍 > 80°
      LIGHT    : 每次 > 5s 或 角度範圍 < 40°
      MODERATE : 其他

    伏地挺身判定：
      VIGOROUS : 每次 < 2s 且 角度範圍 > 70°
      LIGHT    : 每次 > 5s 或 角度範圍 < 30°
      MODERATE : 其他
    """
    ex = (
        ExerciseType(exercise_type) if isinstance(exercise_type, str) else exercise_type
    )

    if ex == ExerciseType.SQUAT:
        if rep_duration_sec < 2.0 and angle_range > 80.0:
            return ExerciseIntensity.VIGOROUS
        if rep_duration_sec > 5.0 or angle_range < 40.0:
            return ExerciseIntensity.LIGHT
    elif ex == ExerciseType.PUSH_UP:
        if rep_duration_sec < 2.0 and angle_range > 70.0:
            return ExerciseIntensity.VIGOROUS
        if rep_duration_sec > 5.0 or angle_range < 30.0:
            return ExerciseIntensity.LIGHT

    return ExerciseIntensity.MODERATE


# ============================================================
# Pandas 統計報表
# ============================================================
def generate_workout_summary(records: list[WorkoutRecord]) -> pd.DataFrame:
    """
    依運動類型分組統計

    回傳欄位：
      exercise_type, total_reps, total_sets, total_calories,
      total_duration_min, avg_reps_per_set, error_rate

    範例：
      exercise_type  total_reps  total_calories  avg_reps_per_set  error_rate
      squat          45          120.5           15.0              0.04
      pushup         30          85.2            10.0              0.02
    """
    empty_cols = [
        "exercise_type",
        "total_reps",
        "total_sets",
        "total_calories",
        "total_duration_min",
        "avg_reps_per_set",
        "error_rate",
        "session_count",
        "avg_form_score",
    ]
    if not records:
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(
        [
            {
                "exercise_type": r.exercise_type.value
                if isinstance(r.exercise_type, ExerciseType)
                else r.exercise_type,
                "reps": r.reps,
                "sets": r.sets,
                "calories": r.calories_burned,
                "duration_sec": r.duration_sec,
                "errors": r.errors_count,
                "form_score": calculate_form_score(r.reps, r.errors_count),
            }
            for r in records
        ]
    )

    summary = (
        df.groupby("exercise_type")
        .agg(
            total_reps=("reps", "sum"),
            total_sets=("sets", "sum"),
            total_calories=("calories", "sum"),
            total_duration_sec=("duration_sec", "sum"),
            total_errors=("errors", "sum"),
            session_count=("reps", "count"),
            avg_form_score=("form_score", "mean"),
        )
        .reset_index()
    )

    summary["total_duration_min"] = (summary["total_duration_sec"] / 60.0).round(1)
    summary["total_calories"] = summary["total_calories"].round(1)
    summary["avg_reps_per_set"] = np.where(
        summary["total_sets"] > 0,
        (summary["total_reps"] / summary["total_sets"]).round(1),
        0.0,
    )
    summary["error_rate"] = np.where(
        summary["total_reps"] > 0,
        (summary["total_errors"] / summary["total_reps"]).round(3),
        0.0,
    )
    summary["avg_form_score"] = summary["avg_form_score"].round(1)
    return summary.drop(columns=["total_duration_sec", "total_errors"])


def generate_daily_summary(records: list[WorkoutRecord]) -> pd.DataFrame:
    """
    依日期分組的每日摘要（供前端熱力圖 & 統計圖表使用）

    回傳欄位：date, total_calories, total_duration_min, workout_count
    """
    if not records:
        return pd.DataFrame(
            columns=[
                "date",
                "total_calories",
                "total_duration_min",
                "workout_count",
                "total_reps",
            ]
        )

    df = pd.DataFrame(
        [
            {
                "date": r.timestamp[:10],  # "YYYY-MM-DD"
                "calories": r.calories_burned,
                "duration_sec": r.duration_sec,
                "reps": r.reps,
            }
            for r in records
        ]
    )

    daily = (
        df.groupby("date")
        .agg(
            total_calories=("calories", "sum"),
            total_duration_sec=("duration_sec", "sum"),
            workout_count=("calories", "count"),
            total_reps=("reps", "sum"),
        )
        .reset_index()
    )

    daily["total_duration_min"] = (daily["total_duration_sec"] / 60.0).round(1)
    daily["total_calories"] = daily["total_calories"].round(1)
    return daily.drop(columns=["total_duration_sec"])


def calculate_form_score(reps: int, errors_count: int) -> int:
    """以錯誤事件占完成次數的比例產生 0-100 的 MVP 姿勢分數。"""
    if reps <= 0:
        return 100 if errors_count == 0 else 0
    return max(0, min(100, round(100 * (1.0 - errors_count / reps))))


# ============================================================
# InBodyService：整合服務類別
#
# 使用方式：
#     profile = InBodyProfile(
#         weight_kg=70.0, height_cm=175.0, age=22, gender="male",
#         body_fat_pct=18.5, skeletal_muscle_mass_kg=32.0,
#         body_fat_mass_kg=13.0,
#     )
#     service = InBodyService(profile)
#     bmr = service.bmr
#     kcal = service.calculate_exercise_calories("squat", duration_min=5.0)
# ============================================================
class InBodyService:
    def __init__(self, profile: InBodyProfile):
        self.profile = profile

        # 預先計算常用數值
        self.lean_body_mass = calculate_lean_body_mass(
            profile.weight_kg, profile.body_fat_pct
        )
        self.bmi = calculate_bmi(profile.weight_kg, profile.height_cm)
        self.bmi_category = get_bmi_category(self.bmi)
        self.bmr = calculate_bmr_katch_mcardle(self.lean_body_mass)

    def calculate_exercise_calories(
        self,
        exercise_type: ExerciseType | str,
        duration_min: float,
        intensity: ExerciseIntensity | str = ExerciseIntensity.MODERATE,
    ) -> float:
        """計算特定運動消耗卡路里"""
        mets = get_mets(exercise_type, intensity)
        return calculate_calories(mets, self.profile.weight_kg, duration_min)

    def calories_per_rep(
        self,
        exercise_type: ExerciseType | str,
        intensity: ExerciseIntensity | str = ExerciseIntensity.MODERATE,
        seconds_per_rep: float = 4.0,
    ) -> float:
        """單次動作消耗卡路里（即時更新用）"""
        return estimate_calories_per_rep(
            exercise_type, self.profile.weight_kg, intensity, seconds_per_rep
        )

    def get_profile_summary(self) -> dict:
        """取得生理數據摘要（供前端個人頁面）"""
        return {
            "weight_kg": self.profile.weight_kg,
            "height_cm": self.profile.height_cm,
            "bmi": self.bmi,
            "bmi_category": self.bmi_category,
            "body_fat_pct": self.profile.body_fat_pct,
            "skeletal_muscle_mass_kg": self.profile.skeletal_muscle_mass_kg,
            "lean_body_mass_kg": round(self.lean_body_mass, 1),
            "bmr_kcal_day": round(self.bmr, 1),
        }
