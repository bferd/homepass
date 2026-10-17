"""Let a use-limited link show its remaining uses to the guest.

show_remaining_uses is a per-token switch: when set on a link that has a
max_uses limit, the guest page keeps a "N uses left" counter on screen instead
of only flashing it in a toast after each tap. Off by default, so every
existing token looks exactly as it did before. It does nothing on a link with
no use limit.

NOT NULL DEFAULT 0 so reads never have to reason about a NULL.

Revision ID: 013
Revises: 012
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = [r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(tokens)")]
    if "show_remaining_uses" not in cols:
        op.execute("ALTER TABLE tokens ADD COLUMN show_remaining_uses INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    cols = [r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(tokens)")]
    if "show_remaining_uses" in cols:
        op.execute("ALTER TABLE tokens DROP COLUMN show_remaining_uses")
