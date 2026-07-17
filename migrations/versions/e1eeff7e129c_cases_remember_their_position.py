"""cases remember their position

Adds `cases.ordinal` so a run reads back as the list that was stored rather
than the same cases in an order the database chose. Ids are random uuids, so
before this there was nothing to sort by and `GET` could hand back a different
order than `POST` sent.

The backfill is the part autogenerate can't write. Rows that already exist have
a position that was never recorded, and defaulting every one of them to 0 makes
every row a tie — which leaves their order exactly as arbitrary as it was. This
freezes whatever order the database is currently returning: not the original
submission order (that information is gone and inventing it would be a lie),
but *an* order, stable from now on, which is the property the column exists to
provide.

Revision ID: e1eeff7e129c
Revises: 803ec46adee9
Create Date: 2026-07-17 00:48:33.648145

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1eeff7e129c'
down_revision: Union[str, None] = '803ec46adee9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'cases',
        sa.Column('ordinal', sa.Integer(), server_default='0', nullable=False),
    )
    # Freeze the current order of rows that predate the column, per run.
    # ctid is Postgres' physical row location — not stable in general, but it is
    # exactly "the order a bare SELECT returns today", which is what's being
    # preserved. Skipped on SQLite, which has no ctid and no deployed data.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE cases AS c
               SET ordinal = ranked.rn - 1
              FROM (
                    SELECT id,
                           row_number() OVER (PARTITION BY run_id ORDER BY ctid) AS rn
                      FROM cases
                   ) AS ranked
             WHERE c.id = ranked.id
            """
        )


def downgrade() -> None:
    op.drop_column('cases', 'ordinal')
