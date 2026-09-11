"""create preferred body profile architecture and backfill legacy data"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "body_profiles",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "body_measurements",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_label", sa.String(length=128), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("body_fat_pct", sa.Float(), nullable=True),
        sa.Column("skeletal_muscle_mass_kg", sa.Float(), nullable=True),
        sa.Column("body_fat_mass_kg", sa.Float(), nullable=True),
        sa.Column("total_body_water_kg", sa.Float(), nullable=True),
        sa.Column("visceral_fat_level", sa.Integer(), nullable=True),
        sa.Column("legacy_age", sa.Integer(), nullable=True),
        sa.Column("legacy_gender", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "body_fat_pct IS NOT NULL "
            "OR skeletal_muscle_mass_kg IS NOT NULL "
            "OR body_fat_mass_kg IS NOT NULL "
            "OR total_body_water_kg IS NOT NULL "
            "OR visceral_fat_level IS NOT NULL",
            name="ck_bm_has_composition",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_body_measurements_user_measured_at",
        "body_measurements",
        ["user_id", "measured_at"],
    )

    op.execute(
        """
        INSERT INTO body_profiles (user_id, height_cm, weight_kg, updated_at)
        SELECT user_id, height_cm, weight_kg, measured_at
        FROM inbody_profiles
        """
    )
    op.execute(
        """
        INSERT INTO body_measurements (
            id,
            user_id,
            source,
            source_label,
            measured_at,
            height_cm,
            weight_kg,
            body_fat_pct,
            skeletal_muscle_mass_kg,
            body_fat_mass_kg,
            total_body_water_kg,
            visceral_fat_level,
            legacy_age,
            legacy_gender
        )
        SELECT
            'legacy-inbody:' || user_id,
            user_id,
            'inbody',
            'Legacy InBody profile',
            measured_at,
            height_cm,
            weight_kg,
            body_fat_pct,
            skeletal_muscle_mass_kg,
            body_fat_mass_kg,
            total_body_water_kg,
            visceral_fat_level,
            age,
            gender
        FROM inbody_profiles
        """
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE public.body_profiles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE public.body_measurements ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON TABLE public.body_profiles FROM anon;
                    REVOKE ALL ON TABLE public.body_measurements FROM anon;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'
                ) THEN
                    REVOKE ALL ON TABLE public.body_profiles FROM authenticated;
                    REVOKE ALL ON TABLE public.body_measurements FROM authenticated;
                END IF;
            END
            $$
            """
        )


def downgrade() -> None:
    op.drop_index(
        "ix_body_measurements_user_measured_at", table_name="body_measurements"
    )
    op.drop_table("body_measurements")
    op.drop_table("body_profiles")
