from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import personai_api.database as database_module
from alembic import command


def test_upgrade_from_previous_head_backfills_legacy_rows(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setattr(database_module, "DATABASE_URL", database_url)
    config = Config(str(database_module.PROJECT_ROOT / "alembic.ini"))

    command.upgrade(config, "0002")
    measured_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO inbody_profiles (
                    user_id,
                    weight_kg,
                    height_cm,
                    age,
                    gender,
                    body_fat_pct,
                    skeletal_muscle_mass_kg,
                    body_fat_mass_kg,
                    total_body_water_kg,
                    visceral_fat_level,
                    measured_at
                ) VALUES (
                    :user_id,
                    :weight_kg,
                    :height_cm,
                    :age,
                    :gender,
                    :body_fat_pct,
                    :skeletal_muscle_mass_kg,
                    :body_fat_mass_kg,
                    :total_body_water_kg,
                    :visceral_fat_level,
                    :measured_at
                )
                """
            ),
            {
                "user_id": "legacy-user",
                "weight_kg": 72.25,
                "height_cm": 176.5,
                "age": 31,
                "gender": "female",
                "body_fat_pct": 22.75,
                "skeletal_muscle_mass_kg": 29.125,
                "body_fat_mass_kg": 16.4375,
                "total_body_water_kg": 39.875,
                "visceral_fat_level": 8,
                "measured_at": measured_at,
            },
        )

    command.upgrade(config, "head")

    table_names = set(inspect(engine).get_table_names())
    assert {"inbody_profiles", "body_profiles", "body_measurements"} <= table_names
    calorie_column = next(
        column
        for column in inspect(engine).get_columns("workout_records")
        if column["name"] == "calories_burned"
    )
    assert calorie_column["nullable"] is True

    with engine.connect() as connection:
        legacy = (
            connection.execute(
                text("SELECT * FROM inbody_profiles WHERE user_id = 'legacy-user'")
            )
            .mappings()
            .one()
        )
        profile = (
            connection.execute(
                text("SELECT * FROM body_profiles WHERE user_id = 'legacy-user'")
            )
            .mappings()
            .one()
        )
        measurement = (
            connection.execute(
                text("SELECT * FROM body_measurements WHERE user_id = 'legacy-user'")
            )
            .mappings()
            .one()
        )

    assert profile["height_cm"] == legacy["height_cm"]
    assert profile["weight_kg"] == legacy["weight_kg"]
    assert measurement["height_cm"] == legacy["height_cm"]
    assert measurement["weight_kg"] == legacy["weight_kg"]
    assert measurement["body_fat_pct"] == legacy["body_fat_pct"]
    assert measurement["skeletal_muscle_mass_kg"] == legacy["skeletal_muscle_mass_kg"]
    assert measurement["body_fat_mass_kg"] == legacy["body_fat_mass_kg"]
    assert measurement["total_body_water_kg"] == legacy["total_body_water_kg"]
    assert measurement["visceral_fat_level"] == legacy["visceral_fat_level"]
    assert measurement["legacy_age"] == legacy["age"]
    assert measurement["legacy_gender"] == legacy["gender"]
    assert measurement["measured_at"] == legacy["measured_at"]
    engine.dispose()
