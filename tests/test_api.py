from fastapi.testclient import TestClient

INBODY = {
    "weight_kg": 70,
    "height_cm": 175,
    "age": 22,
    "gender": "male",
    "body_fat_pct": 20,
    "skeletal_muscle_mass_kg": 31,
    "body_fat_mass_kg": 14,
}


def test_inbody_create_update_and_calories(client: TestClient) -> None:
    created = client.post("/inbody/me", json=INBODY)
    assert created.status_code == 201
    assert created.json()["bmi"] == 22.86
    assert client.get("/inbody/me").json()["weight_kg"] == 70
    created_profile = client.get("/body-profile/me").json()
    assert created_profile["height_cm"] == 175
    assert created_profile["weight_kg"] == 70

    updated = client.post("/inbody/me", json={**INBODY, "weight_kg": 72})
    assert updated.status_code == 201
    assert client.get("/inbody/me").json()["weight_kg"] == 72
    history = client.get("/body-profile/me/measurements").json()
    assert len(history) == 2
    assert history[0]["source"] == "inbody"
    preferred = client.get("/body-profile/me").json()
    assert preferred["state"] == "measured"
    assert preferred["height_cm"] == 175
    assert preferred["weight_kg"] == 72

    calories = client.post(
        "/inbody/me/calories",
        json={"exercise_type": "squat", "duration_min": 30, "intensity": "moderate"},
    )
    assert calories.status_code == 200
    assert calories.json()["calories_burned"] == 180


def test_inbody_validation_and_empty_state(client: TestClient) -> None:
    assert client.get("/inbody/me").status_code == 404
    invalid = client.post("/inbody/me", json={**INBODY, "weight_kg": -1})
    assert invalid.status_code == 422
    impossible = client.post(
        "/inbody/me", json={**INBODY, "skeletal_muscle_mass_kg": 80}
    )
    assert impossible.status_code == 422


def test_inbody_update_preserves_editable_fields_and_isolates_users(
    client: TestClient, current_identity: dict[str, str]
) -> None:
    first_user = current_identity["id"]
    created = client.post(
        "/inbody/me",
        json={**INBODY, "age": 31, "gender": "female", "total_body_water_kg": 40},
    )
    assert created.status_code == 201
    assert created.json()["age"] == 31
    assert created.json()["gender"] == "female"
    assert created.json()["body_fat_mass_kg"] == 14
    assert created.json()["total_body_water_kg"] == 40

    current_identity["id"] = "00000000-0000-4000-8000-000000000002"
    assert client.get("/inbody/me").status_code == 404
    assert (
        client.post("/inbody/me", json={**INBODY, "weight_kg": 60}).status_code == 201
    )

    current_identity["id"] = first_user
    assert client.get("/inbody/me").json()["weight_kg"] == 70


def test_workout_storage_summary_and_user_isolation(
    client: TestClient, current_identity: dict[str, str]
) -> None:
    payload = {
        "exercise_type": "squat",
        "reps": 10,
        "sets": 1,
        "duration_sec": 60,
        "calories_burned": 12.5,
        "avg_intensity": "moderate",
        "errors_count": 2,
    }
    saved = client.post("/wk/me/record", json=payload)
    assert saved.status_code == 201
    assert saved.json()["form_score"] == 80
    assert len(client.get("/wk/me").json()) == 1
    assert client.get("/wk/me/summary").json()[0]["total_reps"] == 10
    assert client.get("/wk/me/daily").json()[0]["workout_count"] == 1

    current_identity["id"] = "00000000-0000-4000-8000-000000000002"
    assert client.get("/wk/me").json() == []
    assert client.get("/wk/me/summary").json() == []


def test_user_me_comes_from_verified_identity(client: TestClient) -> None:
    response = client.get("/user/me")
    assert response.status_code == 200
    assert response.json()["email"] == "test@personai.test"


def test_legacy_user_id_routes_are_removed(client: TestClient) -> None:
    assert client.get("/wk/u001").status_code == 404
    assert client.get("/inbody/u001").status_code == 404


def test_workout_rejects_unreasonable_values(client: TestClient) -> None:
    response = client.post(
        "/wk/me/record",
        json={
            "exercise_type": "squat",
            "reps": 10_001,
            "sets": 1,
            "duration_sec": 60,
            "calories_burned": 10,
            "avg_intensity": "moderate",
            "errors_count": 0,
        },
    )
    assert response.status_code == 422


def test_nullable_workout_calories_and_aggregate_coverage(client: TestClient) -> None:
    base = {
        "exercise_type": "squat",
        "reps": 10,
        "sets": 1,
        "duration_sec": 60,
        "avg_intensity": "moderate",
        "errors_count": 0,
    }
    known = client.post("/wk/me/record", json={**base, "calories_burned": 12.5})
    unknown = client.post("/wk/me/record", json=base)
    assert known.status_code == 201
    assert unknown.status_code == 201
    assert unknown.json()["calories_burned"] is None

    summary = client.get("/wk/me/summary").json()[0]
    assert summary["session_count"] == 2
    assert summary["calorie_session_count"] == 1
    assert summary["total_calories"] is None

    daily = client.get("/wk/me/daily").json()[0]
    assert daily["workout_count"] == 2
    assert daily["calorie_workout_count"] == 1
    assert daily["total_calories"] is None

    historical_zero = client.post(
        "/wk/me/record",
        json={**base, "exercise_type": "pushup", "calories_burned": 0},
    )
    assert historical_zero.json()["calories_burned"] == 0
    pushup_summary = next(
        item
        for item in client.get("/wk/me/summary").json()
        if item["exercise_type"] == "pushup"
    )
    assert pushup_summary["total_calories"] == 0
    assert pushup_summary["calorie_session_count"] == 1
