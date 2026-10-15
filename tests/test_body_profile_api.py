from datetime import UTC, datetime

from fastapi.testclient import TestClient


def _measurement(**overrides: object) -> dict[str, object]:
    return {
        "source": "inbody",
        "source_label": "Clinic A",
        "measured_at": "2026-09-10T08:30:00Z",
        "height_cm": 175,
        "weight_kg": 70,
        "body_fat_pct": 20,
        **overrides,
    }


def test_unknown_and_basic_profile_states(client: TestClient) -> None:
    unknown = client.get("/body-profile/me")
    assert unknown.status_code == 200
    assert unknown.json() == {
        "state": "unknown",
        "height_cm": None,
        "weight_kg": None,
        "bmi": None,
        "updated_at": None,
    }

    height_only = client.patch("/body-profile/me", json={"height_cm": 175})
    assert height_only.status_code == 200
    assert height_only.json()["state"] == "basic"
    assert height_only.json()["weight_kg"] is None
    assert height_only.json()["bmi"] is None

    weight_only = client.patch(
        "/body-profile/me", json={"height_cm": None, "weight_kg": 70}
    )
    assert weight_only.status_code == 200
    assert weight_only.json()["height_cm"] is None
    assert weight_only.json()["weight_kg"] == 70

    full = client.patch("/body-profile/me", json={"height_cm": 175})
    assert full.json()["bmi"] == 22.86


def test_basic_patch_omission_clear_and_empty_row_removal(client: TestClient) -> None:
    client.patch("/body-profile/me", json={"height_cm": 180, "weight_kg": 75})

    omitted_is_unchanged = client.patch("/body-profile/me", json={"weight_kg": 76})
    assert omitted_is_unchanged.json()["height_cm"] == 180

    cleared_height = client.patch("/body-profile/me", json={"height_cm": None})
    assert cleared_height.json()["state"] == "basic"
    assert cleared_height.json()["height_cm"] is None

    cleared_all = client.patch("/body-profile/me", json={"weight_kg": None})
    assert cleared_all.json()["state"] == "unknown"
    assert cleared_all.json()["updated_at"] is None


def test_basic_profile_rejects_invalid_ranges(client: TestClient) -> None:
    assert client.patch("/body-profile/me", json={"height_cm": 0}).status_code == 422
    assert client.patch("/body-profile/me", json={"height_cm": 301}).status_code == 422
    assert client.patch("/body-profile/me", json={"weight_kg": -1}).status_code == 422
    assert client.patch("/body-profile/me", json={"weight_kg": 501}).status_code == 422


def test_partial_and_full_measurements_do_not_mutate_basic_profile(
    client: TestClient,
) -> None:
    client.patch("/body-profile/me", json={"height_cm": 170, "weight_kg": 65})

    partial = client.post(
        "/body-profile/me/measurements",
        json=_measurement(height_cm=None, weight_kg=None, body_fat_pct=18),
    )
    assert partial.status_code == 201
    assert partial.json()["bmi"] is None
    assert partial.json()["lean_body_mass_kg"] is None
    assert partial.json()["bmr_kcal_day"] is None
    assert partial.json()["bmr_is_formula_estimate"] is False

    full = client.post(
        "/body-profile/me/measurements",
        json=_measurement(
            skeletal_muscle_mass_kg=31,
            body_fat_mass_kg=14,
            total_body_water_kg=40,
            visceral_fat_level=7,
        ),
    )
    assert full.status_code == 201
    assert full.json()["bmi"] == 22.86
    assert full.json()["lean_body_mass_kg"] == 56.0
    assert full.json()["bmr_kcal_day"] == 1579.6
    assert full.json()["bmr_is_formula_estimate"] is True

    profile = client.get("/body-profile/me").json()
    assert profile["state"] == "measured"
    assert profile["height_cm"] == 170
    assert profile["weight_kg"] == 65


def test_measurement_requires_composition_and_valid_ranges(client: TestClient) -> None:
    height_weight_only = client.post(
        "/body-profile/me/measurements",
        json=_measurement(body_fat_pct=None),
    )
    assert height_weight_only.status_code == 422

    invalid_range = client.post(
        "/body-profile/me/measurements",
        json=_measurement(body_fat_pct=81),
    )
    assert invalid_range.status_code == 422

    impossible_component = client.post(
        "/body-profile/me/measurements",
        json=_measurement(weight_kg=60, skeletal_muscle_mass_kg=61),
    )
    assert impossible_component.status_code == 422


def test_measurement_history_order_and_correction(client: TestClient) -> None:
    older = client.post(
        "/body-profile/me/measurements",
        json=_measurement(measured_at="2026-01-01T00:00:00Z", body_fat_pct=21),
    ).json()
    newer = client.post(
        "/body-profile/me/measurements",
        json=_measurement(measured_at="2026-02-01T00:00:00Z", body_fat_pct=19),
    ).json()

    history = client.get("/body-profile/me/measurements")
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [newer["id"], older["id"]]

    corrected = client.patch(
        f"/body-profile/me/measurements/{older['id']}",
        json={"body_fat_pct": 20, "source_label": None},
    )
    assert corrected.status_code == 200
    assert corrected.json()["body_fat_pct"] == 20
    assert corrected.json()["source_label"] is None
    assert client.get(f"/body-profile/me/measurements/{older['id']}").status_code == 200

    cannot_clear_last_composition = client.patch(
        f"/body-profile/me/measurements/{older['id']}",
        json={"body_fat_pct": None},
    )
    assert cannot_clear_last_composition.status_code == 422


def test_measurement_ownership_isolation(
    client: TestClient, current_identity: dict[str, str]
) -> None:
    first_user = current_identity["id"]
    first = client.post("/body-profile/me/measurements", json=_measurement()).json()

    current_identity["id"] = "00000000-0000-4000-8000-000000000002"
    assert client.get("/body-profile/me/measurements").json() == []
    assert client.get(f"/body-profile/me/measurements/{first['id']}").status_code == 404
    assert (
        client.patch(
            f"/body-profile/me/measurements/{first['id']}",
            json={"body_fat_pct": 10},
        ).status_code
        == 404
    )
    second = client.post(
        "/body-profile/me/measurements",
        json=_measurement(measured_at=datetime.now(UTC).isoformat()),
    )
    assert second.status_code == 201

    current_identity["id"] = first_user
    assert [
        item["id"] for item in client.get("/body-profile/me/measurements").json()
    ] == [first["id"]]
