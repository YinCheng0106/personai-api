"""allow workout calories to be unknown"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workout_records") as batch_op:
        batch_op.alter_column(
            "calories_burned",
            existing_type=sa.Float(),
            nullable=True,
        )


def downgrade() -> None:
    null_count = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM workout_records WHERE calories_burned IS NULL"
        )
    )
    if null_count:
        raise RuntimeError(
            "Cannot downgrade while workout records contain unknown calories"
        )
    with op.batch_alter_table("workout_records") as batch_op:
        batch_op.alter_column(
            "calories_burned",
            existing_type=sa.Float(),
            nullable=False,
        )
