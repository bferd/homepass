"""Per-link info text: storage, validation and the admin and public APIs.

info_text is text an admin writes for one link, stored exactly as typed
(formatting tags included; the guest page sanitises it on every render, see
tests/test_richtext.py); info_text_in_footer says whether the guest page shows
it at the top or in a box below the controls. Empty is "none".

Real DB (migrated per test), real routing; only ha_client is mocked.
"""
import sqlite3
import time
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app import database as db
from app import ha_client
from app.config import settings
from app.models import INFO_TEXT_MAX

# Taken before any fixture patches ha_client: the `client` fixture mocks the
# broadcasts, and the payload tests below need the real one.
REAL_BROADCAST = ha_client.broadcast_schedule_changed

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def api_on():
    with patch.object(settings, "api_enabled", True), patch.object(settings, "api_token", KEY):
        yield


async def _token(slug="info", **kwargs):
    return await db.create_token(
        label="Cottage", slug=slug, entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 86400, ip_allowlist=None, **kwargs,
    )


def _create_body(**overrides):
    body = {
        "label": "Cottage",
        "entity_ids": ["light.living_room"],
        "expires_in_seconds": 3600,
    }
    body.update(overrides)
    return body


async def _info(client, admin_session, token_id, body):
    return await client.patch(
        f"/admin/tokens/{token_id}/info", json=body, cookies=admin_session,
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _alembic(db_file, monkeypatch) -> Config:
    # migrations/env.py builds its URL from settings.db_path.
    monkeypatch.setattr(settings, "db_path", str(db_file))
    return Config("alembic.ini")


def _columns(db_file) -> list[str]:
    with sqlite3.connect(db_file) as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(tokens)")]


def test_head_is_015():
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_current_head() == "016"


def test_upgrade_gives_existing_tokens_no_info_text(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    cfg = _alembic(db_file, monkeypatch)
    command.upgrade(cfg, "013")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO tokens (id, slug, label, created_at, expires_at) "
            "VALUES ('t1', 'old', 'Old link', 1, 4102444800)"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            "SELECT info_text, info_text_in_footer FROM tokens WHERE id = 't1'"
        ).fetchone()
    assert row == ("", 0)


def test_downgrade_removes_both_columns(tmp_path, monkeypatch):
    db_file = tmp_path / "down.db"
    cfg = _alembic(db_file, monkeypatch)
    command.upgrade(cfg, "head")
    assert {"info_text", "info_text_in_footer"} <= set(_columns(db_file))
    command.downgrade(cfg, "013")
    cols = _columns(db_file)
    assert "info_text" not in cols and "info_text_in_footer" not in cols
    assert "show_remaining_uses" in cols
    # And back up again, so the guarded add runs on a table that lost them.
    command.upgrade(cfg, "head")
    assert {"info_text", "info_text_in_footer"} <= set(_columns(db_file))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

async def test_create_defaults_to_no_info_text(test_db):
    row = await db.get_token_by_id((await _token())["id"])
    assert row["info_text"] == ""
    assert row["info_text_in_footer"] == 0


async def test_set_token_info_none_keeps_the_stored_value(test_db):
    t = await _token(info_text="Door code 1234", info_text_in_footer=True)
    await db.set_token_info(t["id"], None, None)
    row = await db.get_token_by_id(t["id"])
    assert (row["info_text"], row["info_text_in_footer"]) == ("Door code 1234", 1)
    await db.set_token_info(t["id"], "", None)
    row = await db.get_token_by_id(t["id"])
    assert (row["info_text"], row["info_text_in_footer"]) == ("", 1)
    await db.set_token_info(t["id"], None, False)
    row = await db.get_token_by_id(t["id"])
    assert (row["info_text"], row["info_text_in_footer"]) == ("", 0)


# ---------------------------------------------------------------------------
# Dashboard create and validation
# ---------------------------------------------------------------------------

async def test_dashboard_create_without_the_fields(client, admin_session):
    resp = await client.post("/admin/tokens", json=_create_body(), cookies=admin_session)
    assert resp.status_code == 201
    data = resp.json()
    assert data["info_text"] == ""
    assert data["info_text_in_footer"] is False


async def test_dashboard_create_with_the_fields(client, admin_session):
    resp = await client.post(
        "/admin/tokens",
        json=_create_body(info_text="Park on the left.", info_text_in_footer=True),
        cookies=admin_session,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["info_text"] == "Park on the left."
    assert data["info_text_in_footer"] is True
    listed = (await client.get("/admin/tokens", cookies=admin_session)).json()
    assert listed[0]["info_text"] == "Park on the left."
    assert listed[0]["info_text_in_footer"] is True


@pytest.mark.parametrize("raw, stored", [
    ("x" * INFO_TEXT_MAX, "x" * INFO_TEXT_MAX),
    ("Line one\r\nLine two\rLine three", "Line one\nLine two\nLine three"),
    ("   Park on the left.  \n ", "Park on the left."),
    (" \r\n\t ", ""),
    # 999 characters once stored, more than 1000 as sent.
    ("\r\n".join(["x" * 9] * 100), "\n".join(["x" * 9] * 100)),
    ("<b>kept as typed</b> & sanitised on render", "<b>kept as typed</b> & sanitised on render"),
    ('<script>alert(1)</script><font color="x">', '<script>alert(1)</script><font color="x">'),
    # The limit counts the tags too.
    ("<b>" + "x" * (INFO_TEXT_MAX - 7) + "</b>", "<b>" + "x" * (INFO_TEXT_MAX - 7) + "</b>"),
])
async def test_info_text_is_cleaned_before_the_length_check(client, admin_session, raw, stored):
    resp = await client.post(
        "/admin/tokens", json=_create_body(info_text=raw), cookies=admin_session,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["info_text"] == stored


async def test_tags_count_toward_the_limit(client, admin_session):
    resp = await client.post(
        "/admin/tokens", json=_create_body(info_text="<b>" + "x" * (INFO_TEXT_MAX - 6) + "</b>"),
        cookies=admin_session,
    )
    assert resp.status_code == 422
    assert await db.list_tokens() == []


async def test_info_text_over_the_limit_is_refused(client, admin_session):
    resp = await client.post(
        "/admin/tokens", json=_create_body(info_text="x" * (INFO_TEXT_MAX + 1)),
        cookies=admin_session,
    )
    assert resp.status_code == 422
    assert await db.list_tokens() == []


# ---------------------------------------------------------------------------
# Dashboard PATCH /admin/tokens/{id}/info
# ---------------------------------------------------------------------------

async def test_patch_text_only_keeps_the_placement(client, admin_session):
    t = await _token(info_text="Old", info_text_in_footer=True)
    resp = await _info(client, admin_session, t["id"], {"info_text": " New\r\ntext "})
    assert resp.status_code == 200
    assert resp.json()["info_text"] == "New\ntext"
    assert resp.json()["info_text_in_footer"] is True


async def test_patch_placement_only_keeps_the_text(client, admin_session):
    t = await _token(info_text="Keep me")
    resp = await _info(client, admin_session, t["id"], {"info_text_in_footer": True})
    assert resp.status_code == 200
    assert resp.json()["info_text"] == "Keep me"
    assert resp.json()["info_text_in_footer"] is True


async def test_patch_empty_text_clears_it(client, admin_session):
    t = await _token(info_text="Gone soon")
    resp = await _info(client, admin_session, t["id"], {"info_text": ""})
    assert resp.status_code == 200
    assert resp.json()["info_text"] == ""
    assert (await db.get_token_by_id(t["id"]))["info_text"] == ""


async def test_patch_both_fields(client, admin_session):
    t = await _token()
    resp = await _info(
        client, admin_session, t["id"], {"info_text": "Hi", "info_text_in_footer": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert (data["info_text"], data["info_text_in_footer"]) == ("Hi", True)
    assert data["id"] == t["id"]


async def test_patch_over_the_limit_is_refused(client, admin_session):
    t = await _token(info_text="Keep")
    resp = await _info(client, admin_session, t["id"], {"info_text": "x" * (INFO_TEXT_MAX + 1)})
    assert resp.status_code == 422
    assert (await db.get_token_by_id(t["id"]))["info_text"] == "Keep"


async def test_patch_unknown_token_is_404(client, admin_session):
    resp = await _info(client, admin_session, "no-such-token", {"info_text": "Hi"})
    assert resp.status_code == 404


async def test_patch_needs_an_admin(client, test_db):
    t = await _token(info_text="Keep")
    resp = await client.patch(f"/admin/tokens/{t['id']}/info", json={"info_text": "Hacked"})
    assert resp.status_code == 401
    assert (await db.get_token_by_id(t["id"]))["info_text"] == "Keep"


async def test_patch_refuses_a_revoked_token(client, admin_session):
    """As the schedule PATCH does: Renew is what undoes a revocation."""
    t = await _token(info_text="Keep")
    await db.revoke_token(t["id"])
    resp = await _info(client, admin_session, t["id"], {"info_text": "Changed"})
    assert resp.status_code == 400
    assert (await db.get_token_by_id(t["id"]))["info_text"] == "Keep"


# ---------------------------------------------------------------------------
# Open guest pages are told to reload, with no text in the event
# ---------------------------------------------------------------------------

async def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


async def test_patch_tells_open_pages_to_reload(client, admin_session, mock_ha_client):
    t = await _token(info_text="Old")
    resp = await _info(client, admin_session, t["id"], {"info_text": "New"})
    assert resp.status_code == 200
    mock_ha_client["broadcast_schedule_changed"].assert_awaited_once_with(t["id"])


async def test_patch_placement_only_tells_open_pages_to_reload(client, admin_session, mock_ha_client):
    t = await _token(info_text="Keep")
    resp = await _info(client, admin_session, t["id"], {"info_text_in_footer": True})
    assert resp.status_code == 200
    mock_ha_client["broadcast_schedule_changed"].assert_awaited_once_with(t["id"])


async def test_empty_patch_changes_nothing_and_tells_no_one(client, admin_session, mock_ha_client):
    t = await _token(info_text="Keep", info_text_in_footer=True)
    resp = await _info(client, admin_session, t["id"], {})
    assert resp.status_code == 200
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("Keep", True)
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_refused_patch_tells_no_one(client, admin_session, mock_ha_client):
    revoked = await _token(slug="revoked", info_text="Keep")
    await db.revoke_token(revoked["id"])
    live = await _token(slug="live", info_text="Keep")
    assert (await _info(client, admin_session, revoked["id"], {"info_text": "x"})).status_code == 400
    assert (await _info(client, admin_session, "no-such-token", {"info_text": "x"})).status_code == 404
    too_long = {"info_text": "x" * (INFO_TEXT_MAX + 1)}
    assert (await _info(client, admin_session, live["id"], too_long)).status_code == 422
    assert (await _info(client, admin_session, live["id"], {"info_text": 5})).status_code == 422
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_patch_event_carries_no_text(client, admin_session, test_db, monkeypatch):
    """The real broadcast, read from a stream's own queue: the event a guest
    page receives says only "reload"."""
    monkeypatch.setattr(ha_client, "broadcast_schedule_changed", REAL_BROADCAST)
    t = await _token(info_text="Old")
    q = await ha_client.subscribe(t["id"])
    try:
        resp = await _info(client, admin_session, t["id"], {"info_text": "Door code 4821"})
        assert resp.status_code == 200
        events = await _drain(q)
    finally:
        await ha_client.unsubscribe(t["id"], q)
    assert events == [{"type": "schedule_changed"}]
    assert "4821" not in repr(events)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def test_api_create_and_read(client, api_on):
    resp = await client.post(
        "/api/v1/tokens",
        json=_create_body(info_text="Call +44 20 7946 0958", info_text_in_footer=True),
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["info_text"] == "Call +44 20 7946 0958"
    assert created["info_text_in_footer"] is True
    got = (await client.get(f"/api/v1/tokens/{created['id']}", headers=HEADERS)).json()
    assert (got["info_text"], got["info_text_in_footer"]) == ("Call +44 20 7946 0958", True)
    listed = (await client.get("/api/v1/tokens", headers=HEADERS)).json()
    assert listed[0]["info_text"] == "Call +44 20 7946 0958"


async def test_api_create_defaults(client, api_on):
    resp = await client.post("/api/v1/tokens", json=_create_body(), headers=HEADERS)
    assert resp.status_code == 201
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("", False)


async def test_api_update_sets_and_omission_keeps(client, api_on, test_db):
    t = await _token(info_text="Old", info_text_in_footer=True)
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}", json={"info_text": "New\r\n"}, headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("New", True)

    # A PATCH about something else leaves both alone.
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json={"label": "Renamed"}, headers=HEADERS)
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("New", True)

    # Explicit nulls are the same as leaving them out.
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"info_text": None, "info_text_in_footer": None}, headers=HEADERS,
    )
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("New", True)

    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"info_text": "", "info_text_in_footer": False}, headers=HEADERS,
    )
    assert (resp.json()["info_text"], resp.json()["info_text_in_footer"]) == ("", False)


async def test_api_update_over_the_limit_writes_nothing(client, api_on, test_db):
    t = await _token(info_text="Keep")
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"label": "Renamed", "info_text": "x" * (INFO_TEXT_MAX + 1)}, headers=HEADERS,
    )
    assert resp.status_code == 422
    row = await db.get_token_by_id(t["id"])
    assert (row["label"], row["info_text"]) == ("Cottage", "Keep")


async def test_api_update_refuses_info_on_a_revoked_token(client, api_on, test_db):
    t = await _token(info_text="Keep")
    await db.revoke_token(t["id"])
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"label": "Renamed", "info_text": "Changed"}, headers=HEADERS,
    )
    assert resp.status_code == 400
    row = await db.get_token_by_id(t["id"])
    # Refused before anything is written, the label included.
    assert (row["label"], row["info_text"]) == ("Cottage", "Keep")


@pytest.mark.parametrize("body", [
    {"info_text": "New"},
    {"info_text": ""},
    {"info_text_in_footer": True},
    {"show_remaining_uses": True},
    {"show_remaining_uses": True, "info_text": "New", "info_text_in_footer": True},
    {"label": "Renamed", "info_text": "New"},
], ids=["text", "cleared-text", "footer", "uses-only", "uses-and-info", "label-and-text"])
async def test_api_update_broadcasts_once(client, api_on, test_db, mock_ha_client, body):
    t = await _token(info_text="Old", max_uses=3)
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    mock_ha_client["broadcast_schedule_changed"].assert_awaited_once_with(t["id"])


@pytest.mark.parametrize("body", [
    {"label": "Renamed"},
    {"remember_pin": True},
    {"info_text": None, "info_text_in_footer": None, "show_remaining_uses": None},
], ids=["label", "remember-pin", "explicit-nulls"])
async def test_api_update_without_info_or_uses_does_not_broadcast(
    client, api_on, test_db, mock_ha_client, body
):
    t = await _token(info_text="Old")
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_api_refused_info_change_does_not_broadcast(client, api_on, test_db, mock_ha_client):
    t = await _token(info_text="Keep")
    await db.revoke_token(t["id"])
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"info_text": "Changed", "show_remaining_uses": True}, headers=HEADERS,
    )
    assert resp.status_code == 400
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_api_update_event_carries_no_text(client, api_on, test_db, monkeypatch):
    monkeypatch.setattr(ha_client, "broadcast_schedule_changed", REAL_BROADCAST)
    t = await _token(info_text="Old", max_uses=3)
    q = await ha_client.subscribe(t["id"])
    try:
        resp = await client.patch(
            f"/api/v1/tokens/{t['id']}",
            json={"info_text": "Door code 4821", "show_remaining_uses": True}, headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        events = await _drain(q)
    finally:
        await ha_client.unsubscribe(t["id"], q)
    # One reload for the whole request, and nothing but the event's name.
    assert events == [{"type": "schedule_changed"}]
    assert "4821" not in repr(events)


async def test_api_duplicate_copies_both_fields(client, api_on, test_db):
    t = await _token(info_text="Wi-Fi: Cottage", info_text_in_footer=True)
    resp = await client.post(f"/api/v1/tokens/{t['id']}/duplicate", json={}, headers=HEADERS)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["id"] != t["id"]
    assert (copy["info_text"], copy["info_text_in_footer"]) == ("Wi-Fi: Cottage", True)


# ---------------------------------------------------------------------------
# Guest page: the stored text reaches it (layout in test_info_text_page.py)
# ---------------------------------------------------------------------------

async def test_guest_page_shows_info_text(client, test_db, mock_ha_client):
    await _token(slug="info-page", info_text="Door code 4821")
    resp = await client.get("/g/info-page")
    assert resp.status_code == 200
    assert "Door code 4821" in resp.text
