"""Opt-in PostgreSQL checks; all mutations stay in fixture-owned local databases."""

import os
from collections.abc import Generator
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import personai_api.database as database_module
from alembic import command
from personai_api.auth import CurrentUser, get_current_user
from personai_api.database import get_db
from personai_api.db_models import InBodyProfileModel, WorkoutRecordModel
from personai_api.main import app

pytestmark = pytest.mark.postgres
TABLES = ("inbody_profiles", "workout_records", "body_profiles", "body_measurements")


@pytest.fixture
def postgres_case(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[Engine, Config]]:
    raw = os.getenv("PERSONAI_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("set PERSONAI_TEST_POSTGRES_URL to opt into local PostgreSQL")
    url = make_url(raw)
    if not (
        url.drivername in {"postgresql", "postgresql+psycopg"}
        and url.host in {"127.0.0.1", "localhost", "::1"}
        and url.database == "personai_test"
        and not url.query
    ):
        pytest.fail(
            "PostgreSQL tests require a loopback personai_test DB with no query options"
        )
    url = url.set(drivername="postgresql+psycopg")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "personai_test_" + uuid4().hex
    engine = create_engine(url.set(database=name))
    created_roles: list[str] = []
    created_database = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created_database = True
            for role in ("anon", "authenticated"):
                if not connection.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
                ):
                    connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
                    created_roles.append(role)
        # Simulate pre-existing Data API default grants. Migrations must revoke
        # these, not merely succeed because the roles are absent in vanilla PG.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL ON TABLES TO anon, authenticated"
                )
            )
        monkeypatch.setattr(
            database_module,
            "DATABASE_URL",
            url.set(database=name).render_as_string(hide_password=False),
        )
        config = Config(str(database_module.PROJECT_ROOT / "alembic.ini"))
        yield engine, config
    finally:
        engine.dispose()
        with admin.connect() as connection:
            if created_database:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            for role in created_roles:
                connection.execute(text(f'DROP ROLE "{role}"'))
        admin.dispose()


def test_postgres_legacy_backfill_and_schema_match(
    postgres_case: tuple[Engine, Config],
) -> None:
    from datetime import UTC, datetime

    engine, config = postgres_case
    command.upgrade(config, "0002")
    measured_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            InBodyProfileModel(
                user_id="legacy-user",
                weight_kg=72.25,
                height_cm=176.5,
                age=31,
                gender="female",
                body_fat_pct=22.75,
                skeletal_muscle_mass_kg=29.125,
                body_fat_mass_kg=16.4375,
                total_body_water_kg=39.875,
                visceral_fat_level=8,
                measured_at=measured_at,
            )
        )
        session.commit()
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        legacy = (
            connection.execute(text("SELECT * FROM inbody_profiles")).mappings().one()
        )
        measurement = (
            connection.execute(text("SELECT * FROM body_measurements")).mappings().one()
        )
        profile = (
            connection.execute(text("SELECT * FROM body_profiles")).mappings().one()
        )
        for key in (
            "height_cm",
            "weight_kg",
            "body_fat_pct",
            "skeletal_muscle_mass_kg",
            "body_fat_mass_kg",
            "total_body_water_kg",
            "visceral_fat_level",
            "measured_at",
        ):
            assert measurement[key] == legacy[key]
        assert measurement["measured_at"] == measured_at
        assert measurement["legacy_age"] == 31
        assert measurement["legacy_gender"] == "female"
        assert profile["weight_kg"] == 72.25
        assert profile["height_cm"] == 176.5
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO body_measurements (id, user_id, source, measured_at) "
                "VALUES ('invalid', 'legacy-user', 'manual', CURRENT_TIMESTAMP)"
            )
        )


def test_postgres_rls_and_data_api_grants(postgres_case: tuple[Engine, Config]) -> None:
    engine, config = postgres_case
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO body_profiles (user_id, weight_kg) "
                "VALUES ('private-user', 70)"
            )
        )
        protected = {
            row[0]: row[1]
            for row in connection.execute(
                text(
                    "SELECT relname, relrowsecurity FROM pg_class "
                    "JOIN pg_namespace ON pg_namespace.oid = relnamespace "
                    "WHERE nspname = 'public' AND relname IN "
                    "('inbody_profiles', 'workout_records', "
                    "'body_profiles', 'body_measurements')"
                )
            )
        }
        assert protected == {table: True for table in TABLES}
        for role in ("anon", "authenticated"):
            for table in TABLES:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    assert (
                        connection.scalar(
                            text(
                                "SELECT has_table_privilege(:role, :table, :privilege)"
                            ),
                            {
                                "role": role,
                                "table": f"public.{table}",
                                "privilege": privilege,
                            },
                        )
                        is False
                    )
    for role in ("anon", "authenticated"):
        with pytest.raises(DBAPIError) as exc, engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            connection.execute(text("SELECT * FROM body_profiles"))
        assert getattr(exc.value.orig, "sqlstate", None) == "42501"
        # Prove RLS still denies rows if SELECT is accidentally granted. Roll back
        # the temporary grant so the next role tests the migrated ACL unchanged.
        with engine.connect() as connection, connection.begin() as transaction:
            assert connection.scalar(text("SELECT count(*) FROM body_profiles")) == 1
            connection.execute(text(f'GRANT SELECT ON body_profiles TO "{role}"'))
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert connection.scalar(text("SELECT count(*) FROM body_profiles")) == 0
            transaction.rollback()


def test_postgres_unknown_calories_survive_commit_and_block_unsafe_downgrade(
    postgres_case: tuple[Engine, Config],
) -> None:
    engine, config = postgres_case
    command.upgrade(config, "head")
    with Session(engine) as session:
        session.add(
            WorkoutRecordModel(
                id="unknown-calories",
                user_id="owner",
                exercise_type="squat",
                reps=10,
                sets=1,
                duration_sec=60,
                calories_burned=None,
                avg_intensity="moderate",
                errors_count=0,
            )
        )
        session.commit()
    with Session(engine) as session:
        saved = session.get(WorkoutRecordModel, "unknown-calories")
        assert saved is not None
        assert saved.calories_burned is None
    with pytest.raises(RuntimeError, match="unknown calories"):
        command.downgrade(config, "0003")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        )
        assert connection.scalar(text("SELECT count(*) FROM workout_records")) == 1
    assert (
        next(
            c
            for c in inspect(engine).get_columns("workout_records")
            if c["name"] == "calories_burned"
        )["nullable"]
        is True
    )
    # Remove test data; never substitute a fabricated physiological value.
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM workout_records WHERE id = 'unknown-calories'")
        )
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")
    command.check(config)


def test_postgres_api_commits_nulls_and_isolates_users(
    postgres_case: tuple[Engine, Config],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, config = postgres_case
    command.upgrade(config, "head")
    identity = {"id": "owner"}

    def request_session() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_user,
        lambda: CurrentUser(id=identity["id"]),
    )
    payload = {
        "exercise_type": "squat",
        "reps": 10,
        "sets": 1,
        "duration_sec": 60,
        "avg_intensity": "moderate",
        "errors_count": 0,
    }
    with TestClient(app) as client:
        saved = client.post("/wk/me/record", json=payload)
        assert saved.status_code == 201
        assert saved.json()["calories_burned"] is None
        assert (
            client.post(
                "/wk/me/record", json={**payload, "calories_burned": 0}
            ).status_code
            == 201
        )
        measurement = client.post(
            "/body-profile/me/measurements",
            json={
                "source": "manual",
                "measured_at": "2026-10-01T08:00:00+08:00",
                "body_fat_pct": 20,
            },
        )
        assert measurement.status_code == 201
        path = f"/body-profile/me/measurements/{measurement.json()['id']}"
        identity["id"] = "other-user"
        assert client.get("/wk/me").json() == []
        assert client.get("/wk/me/daily").json() == []
        assert client.get("/body-profile/me/measurements").json() == []
        assert client.get(path).status_code == 404
        assert client.patch(path, json={"body_fat_pct": 10}).status_code == 404
        identity["id"] = "owner"
        summary = client.get("/wk/me/summary").json()[0]
        assert summary["session_count"] == 2
        assert summary["calorie_session_count"] == 1
        assert summary["total_calories"] is None
        assert client.get(path).json()["body_fat_pct"] == 20
        profile = client.get("/body-profile/me").json()
        assert profile["state"] == "measured"
        assert profile["height_cm"] is profile["weight_kg"] is profile["bmi"] is None
    with Session(engine) as session:
        stored = session.get(WorkoutRecordModel, saved.json()["id"])
        assert stored is not None
        assert stored.calories_burned is None
