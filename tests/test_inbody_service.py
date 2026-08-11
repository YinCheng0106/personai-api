import pytest

from personai_api.services.inbody import (
    InBodyProfile,
    InBodyService,
    calculate_bmi,
    calculate_bmr_harris_benedict,
    calculate_bmr_katch_mcardle,
    calculate_calories,
    calculate_form_score,
    calculate_lean_body_mass,
)


def test_body_composition_calculations() -> None:
    lean_mass = calculate_lean_body_mass(70, 20)
    assert lean_mass == 56
    assert calculate_bmi(70, 175) == 22.86
    assert calculate_bmr_katch_mcardle(lean_mass) == pytest.approx(1579.6)
    assert calculate_bmr_harris_benedict(70, 175, 22, "male") == pytest.approx(1741.083)
    assert calculate_calories(5, 70, 30) == 175
    assert calculate_form_score(10, 2) == 80


def test_inbody_summary_uses_katch_mcardle() -> None:
    service = InBodyService(
        InBodyProfile(
            weight_kg=70,
            height_cm=175,
            age=22,
            gender="male",
            body_fat_pct=20,
            skeletal_muscle_mass_kg=31,
            body_fat_mass_kg=14,
        )
    )
    assert service.get_profile_summary() == {
        "weight_kg": 70,
        "height_cm": 175,
        "bmi": 22.86,
        "bmi_category": "正常",
        "body_fat_pct": 20,
        "skeletal_muscle_mass_kg": 31,
        "lean_body_mass_kg": 56.0,
        "bmr_kcal_day": 1579.6,
    }
