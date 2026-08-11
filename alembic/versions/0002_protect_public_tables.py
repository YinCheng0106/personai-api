"""protect application tables from Supabase Data API roles"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.inbody_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.workout_records ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON TABLE public.inbody_profiles FROM anon;
                REVOKE ALL ON TABLE public.workout_records FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON TABLE public.inbody_profiles FROM authenticated;
                REVOKE ALL ON TABLE public.workout_records FROM authenticated;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.inbody_profiles DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.workout_records DISABLE ROW LEVEL SECURITY")
