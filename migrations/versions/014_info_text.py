"""Let a link carry its own info text for the guest.

info_text is plain text the admin writes for one link (a door code, where to
park, who to call), shown on that link's guest page alongside the global
welcome text. info_text_in_footer moves it from the top of the page to a box
below the controls, for text a guest needs once rather than every visit.

Both are NOT NULL with defaults ('' and 0) so every existing token reads as
"no info text, shown at the top" and no reader has to reason about a NULL.
An empty string, not NULL, is "none", the same way the global welcome text
works.

Revision ID: 014
Revises: 013
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[str]:
    return [r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(tokens)")]


def upgrade() -> None:
    cols = _columns()
    if "info_text" not in cols:
        op.execute("ALTER TABLE tokens ADD COLUMN info_text TEXT NOT NULL DEFAULT ''")
    if "info_text_in_footer" not in cols:
        op.execute(
            "ALTER TABLE tokens ADD COLUMN info_text_in_footer INTEGER NOT NULL DEFAULT 0"
        )


def downgrade() -> None:
    cols = _columns()
    if "info_text_in_footer" in cols:
        op.execute("ALTER TABLE tokens DROP COLUMN info_text_in_footer")
    if "info_text" in cols:
        op.execute("ALTER TABLE tokens DROP COLUMN info_text")
