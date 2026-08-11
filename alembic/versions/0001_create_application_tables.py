"""create application tables"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbody_profiles",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("body_fat_pct", sa.Float(), nullable=False),
        sa.Column("skeletal_muscle_mass_kg", sa.Float(), nullable=False),
        sa.Column("body_fat_mass_kg", sa.Float(), nullable=False),
        sa.Column("total_body_water_kg", sa.Float(), nullable=True),
        sa.Column("visceral_fat_level", sa.Integer(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "workout_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("exercise_type", sa.String(length=32), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=False),
        sa.Column("calories_burned", sa.Float(), nullable=False),
        sa.Column("avg_intensity", sa.String(length=16), nullable=False),
        sa.Column("errors_count", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_records_exercise_type", "workout_records", ["exercise_type"])
    op.create_index("ix_workout_records_timestamp", "workout_records", ["timestamp"])
    op.create_index("ix_workout_records_user_id", "workout_records", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workout_records_user_id", table_name="workout_records")
    op.drop_index("ix_workout_records_timestamp", table_name="workout_records")
    op.drop_index("ix_workout_records_exercise_type", table_name="workout_records")
    op.drop_table("workout_records")
    op.drop_table("inbody_profiles")
