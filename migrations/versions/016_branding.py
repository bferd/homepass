"""Per-link branding: an eyebrow, a title, a logo and optional colours.

branding is a per-token JSON object (see app/branding.py), NOT NULL DEFAULT '{}'
so every existing token reads as unbranded and no reader has to reason about a
NULL, the same rule 013, 014 and 015 follow.

The logo image itself lives in token_logos, one row per token, so listing links
never loads image bytes. It is removed with its token.

Revision ID: 016
Revises: 015
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[str]:
    return [r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(tokens)")]


def upgrade() -> None:
    if "branding" not in _columns():
        op.execute("ALTER TABLE tokens ADD COLUMN branding TEXT NOT NULL DEFAULT '{}'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS token_logos (
            token_id     TEXT PRIMARY KEY REFERENCES tokens(id) ON DELETE CASCADE,
            content_type TEXT NOT NULL,
            data         BLOB NOT NULL,
            width        INTEGER NOT NULL,
            height       INTEGER NOT NULL,
            created_at   INTEGER NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS token_logos")
    if "branding" in _columns():
        op.execute("ALTER TABLE tokens DROP COLUMN branding")
