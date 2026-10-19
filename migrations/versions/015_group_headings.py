"""Let a link rename each section heading on its guest page, or lock it open.

The guest page sorts a link's entities into sections ("Lights", "Switches")
by kind. group_headings is a per-token JSON object keyed by the section's
group key (the first domain in static/domains.js DOMAIN_ORDER that shares the
label, so input_boolean files under "switch"):

    {"light": {"name": "Lamps", "locked": false}, "switch": {"name": "", "locked": true}}

An empty name is the translated default heading. A locked section cannot be
collapsed by the guest: its heading is plain text and its entities always
show. Entries that change nothing are never stored, so "{}" is every heading
as it was before.

NOT NULL DEFAULT '{}' so every existing token reads as "all defaults" and no
reader has to reason about a NULL, the same rule 013 and 014 follow.

Revision ID: 015
Revises: 014
Create Date: 2026-10-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[str]:
    return [r[1] for r in op.get_bind().exec_driver_sql("PRAGMA table_info(tokens)")]


def upgrade() -> None:
    if "group_headings" not in _columns():
        op.execute("ALTER TABLE tokens ADD COLUMN group_headings TEXT NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    if "group_headings" in _columns():
        op.execute("ALTER TABLE tokens DROP COLUMN group_headings")
