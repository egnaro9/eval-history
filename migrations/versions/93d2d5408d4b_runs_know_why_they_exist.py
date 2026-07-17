"""runs know why they exist

Adds `runs.source` so the store can tell a CI run from an ablation.

Without it, `latest-comparison` picks the two newest runs by wall clock and
compares whatever it finds. That produced a real, live bug: a hand-seeded
retrieval sweep (k=3 vs k=2) sat next to a genuine CI run, so the endpoint
compared a config change against a commit and returned `verdict: regressed`
with five precision drops — attributing them to whoever pushed. Every number
was correct. The question was wrong.

The backfill is the part autogenerate can't write. Defaulting every existing
row to 'ci' is what makes the column useless: the two ablation rows already in
the deployed database are exactly the rows the column exists to exclude. They're
identified by the label their seeding script wrote ('retrieval k=…'), which is
narrow on purpose — a one-time repair of known rows, not a heuristic anyone
should rely on later. New runs declare their own source at POST time.

Revision ID: 93d2d5408d4b
Revises: e1eeff7e129c
Create Date: 2026-07-17 05:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '93d2d5408d4b'
down_revision: Union[str, None] = 'e1eeff7e129c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'runs',
        sa.Column('source', sa.String(length=16), server_default='ci', nullable=False),
    )
    # Repair the known ablation rows. Everything else predates the column and
    # came from CI, so the 'ci' default is right for it.
    op.execute(
        """
        UPDATE runs
           SET source = 'ablation'
         WHERE label LIKE 'retrieval k=%'
        """
    )


def downgrade() -> None:
    op.drop_column('runs', 'source')
