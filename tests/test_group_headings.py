"""Per-link section headings: storage, validation and the admin and public APIs.

group_headings renames the headings the guest page draws over each section of
a link's entities ("Lights", "Switches"), or locks a section so the guest
cannot collapse it. It is keyed by group key
(groupKey() in static/domains.js), holds only the headings the admin changed,
and {} is every heading as default. The guest-side rendering and the dashboard
rows are in tests/test_group_headings_js.py.

Real DB (migrated per test), real routing; only ha_client is mocked.
"""
import json
import sqlite3
import time
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from app import database as db
from app import ha_client
from app.config import settings
from app.models import (
    DISPLAY_NAME_MAX,
    GROUP_HEADINGS_MAX,
    ApiTokenUpdateRequest,
    TokenCreateRequest,
    TokenUpdateEntitiesRequest,
)

# Taken before any fixture patches ha_client: the `client` fixture mocks the
# broadcasts, and the payload tests below need the real one.
REAL_BROADCAST = ha_client.broadcast_schedule_changed

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}

LAMPS = {"light": {"name": "Lamps", "locked": False}}


@pytest.fixture
def api_on():
    with patch.object(settings, "api_enabled", True), patch.object(settings, "api_token", KEY):
        yield


async def _token(slug="headings", **kwargs):
    kwargs.setdefault("expires_at", int(time.time()) + 86400)
    return await db.create_token(
        label="Cottage", slug=slug, entity_ids=["light.living_room", "switch.fan"],
        ip_allowlist=None, **kwargs,
    )


def _create_body(**overrides):
    body = {
        "label": "Cottage",
        "entity_ids": ["light.living_room"],
        "expires_in_seconds": 3600,
    }
    body.update(overrides)
    return body


async def _entities(client, admin_session, token_id, **body):
    body.setdefault("entity_ids", ["light.living_room", "switch.fan"])
    return await client.patch(
        f"/admin/tokens/{token_id}/entities", json=body, cookies=admin_session,
    )


async def _stored(token_id):
    return db.group_headings_from_row((await db.get_token_by_id(token_id))["group_headings"])


async def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


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


def test_upgrade_gives_existing_tokens_default_headings(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    cfg = _alembic(db_file, monkeypatch)
    command.upgrade(cfg, "014")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO tokens (id, slug, label, created_at, expires_at) "
            "VALUES ('t1', 'old', 'Old link', 1, 4102444800)"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT group_headings FROM tokens WHERE id = 't1'").fetchone()
    assert row == ("{}",)


def test_downgrade_removes_the_column_and_upgrade_restores_it(tmp_path, monkeypatch):
    db_file = tmp_path / "down.db"
    cfg = _alembic(db_file, monkeypatch)
    command.upgrade(cfg, "head")
    assert "group_headings" in _columns(db_file)
    command.downgrade(cfg, "014")
    cols = _columns(db_file)
    assert "group_headings" not in cols
    assert {"info_text", "info_text_in_footer"} <= set(cols)
    # And back up again, so the guarded add runs on a table that lost it.
    command.upgrade(cfg, "head")
    assert "group_headings" in _columns(db_file)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

async def test_create_defaults_to_no_headings(test_db):
    t = await _token()
    assert t["group_headings"] == "{}"
    assert await _stored(t["id"]) == {}


async def test_create_stores_the_headings(test_db):
    t = await _token(group_headings=LAMPS)
    assert await _stored(t["id"]) == LAMPS


async def test_setter_none_keeps_and_empty_resets(test_db):
    t = await _token(group_headings=LAMPS)
    await db.set_token_group_headings(t["id"], None)
    assert await _stored(t["id"]) == LAMPS
    await db.set_token_group_headings(t["id"], {})
    assert await _stored(t["id"]) == {}


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"text"'])
def test_an_unreadable_value_reads_as_defaults(raw):
    assert db.group_headings_from_row(raw) == {}


# ---------------------------------------------------------------------------
# Validation (one shared validator on every request model)
# ---------------------------------------------------------------------------

MODELS = [
    lambda gh: TokenCreateRequest(label="x", entity_ids=["light.a"], group_headings=gh),
    lambda gh: TokenUpdateEntitiesRequest(entity_ids=["light.a"], group_headings=gh),
    lambda gh: ApiTokenUpdateRequest(group_headings=gh),
]
MODEL_IDS = ["create", "entities", "api-update"]


@pytest.mark.parametrize("make", MODELS, ids=MODEL_IDS)
@pytest.mark.parametrize("bad", [
    {"Light": {"name": "x"}},
    {"light.x": {"name": "x"}},
    {"light-1": {"name": "x"}},
    {"light1": {"name": "x"}},
    {"": {"name": "x"}},
    {"light": "Lamps"},
    {"light": {"name": 5}},
    {"light": {"name": "x" * (DISPLAY_NAME_MAX + 1)}},
    {f"k{'_' * i}": {"locked": True} for i in range(GROUP_HEADINGS_MAX + 1)},
    ["light"],
    "light",
], ids=["upper-case", "dot", "hyphen", "digit", "empty-key", "entry-not-object",
        "name-not-string", "name-too-long", "too-many-keys", "list", "string"])
def test_bad_headings_are_refused(make, bad):
    with pytest.raises(ValidationError):
        make(bad)


@pytest.mark.parametrize("make", MODELS, ids=MODEL_IDS)
def test_headings_are_normalised(make):
    model = make({
        "light": {"name": "  Lamps  "},
        "switch": {"name": "", "locked": 1},
        "fan": {"name": "   ", "locked": False},
        "cover": {"name": None},
        "lock": {},
        "camera": {"name": "x" * DISPLAY_NAME_MAX, "locked": True, "extra": "ignored"},
        # There is no hide option: a stray "hidden" is ignored like any extra.
        "climate": {"hidden": True},
    })
    assert model.group_headings == {
        "light": {"name": "Lamps", "locked": False},
        "switch": {"name": "", "locked": True},
        "camera": {"name": "x" * DISPLAY_NAME_MAX, "locked": True},
    }


@pytest.mark.parametrize("make", MODELS, ids=MODEL_IDS)
def test_none_stays_none_and_the_limit_itself_is_allowed(make):
    assert make(None).group_headings is None
    full = {f"k{'_' * i}": {"locked": True} for i in range(GROUP_HEADINGS_MAX)}
    assert len(make(full).group_headings) == GROUP_HEADINGS_MAX


def test_keys_are_not_checked_against_the_entities():
    model = TokenCreateRequest(
        label="x", entity_ids=["light.a"], group_headings={"climate": {"name": "Heat"}},
    )
    assert model.group_headings == {"climate": {"name": "Heat", "locked": False}}


async def test_a_refusal_never_echoes_the_pin(client, admin_session):
    resp = await client.post(
        "/admin/tokens",
        json=_create_body(pin="482913", group_headings={"Bad Key": {"name": "x"}}),
        cookies=admin_session,
    )
    assert resp.status_code == 422
    assert "482913" not in resp.text


# ---------------------------------------------------------------------------
# Dashboard: create, read, and the entities PATCH
# ---------------------------------------------------------------------------

async def test_dashboard_create_without_the_field(client, admin_session):
    resp = await client.post("/admin/tokens", json=_create_body(), cookies=admin_session)
    assert resp.status_code == 201, resp.text
    assert resp.json()["group_headings"] == {}


async def test_dashboard_create_get_and_list(client, admin_session):
    body = _create_body(group_headings={
        "light": {"name": " Lamps "}, "switch": {"locked": True}, "fan": {"name": ""},
    })
    resp = await client.post("/admin/tokens", json=body, cookies=admin_session)
    assert resp.status_code == 201, resp.text
    want = {"light": {"name": "Lamps", "locked": False}, "switch": {"name": "", "locked": True}}
    assert resp.json()["group_headings"] == want
    token_id = resp.json()["id"]
    got = (await client.get(f"/admin/tokens/{token_id}", cookies=admin_session)).json()
    assert got["group_headings"] == want
    listed = (await client.get("/admin/tokens", cookies=admin_session)).json()
    assert listed[0]["group_headings"] == want


async def test_dashboard_create_over_the_limit_is_refused(client, admin_session, test_db):
    body = _create_body(group_headings={"light": {"name": "x" * (DISPLAY_NAME_MAX + 1)}})
    resp = await client.post("/admin/tokens", json=body, cookies=admin_session)
    assert resp.status_code == 422
    assert await db.list_tokens() == []


async def test_patch_sets_keeps_and_resets(client, admin_session, mock_ha_client):
    t = await _token()
    resp = await _entities(client, admin_session, t["id"], group_headings=LAMPS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["group_headings"] == LAMPS

    # Left out: the entity list changes, the headings stay.
    resp = await _entities(client, admin_session, t["id"], entity_ids=["light.living_room"])
    assert resp.status_code == 200
    assert resp.json()["group_headings"] == LAMPS
    assert resp.json()["entity_ids"] == ["light.living_room"]

    resp = await _entities(client, admin_session, t["id"], group_headings={})
    assert resp.json()["group_headings"] == {}
    assert await _stored(t["id"]) == {}


async def test_patch_change_tells_open_pages_to_reload_once(client, admin_session, mock_ha_client):
    t = await _token()
    resp = await _entities(client, admin_session, t["id"], group_headings=LAMPS)
    assert resp.status_code == 200
    mock_ha_client["broadcast_schedule_changed"].assert_awaited_once_with(t["id"])


@pytest.mark.parametrize("body", [
    {},
    {"group_headings": None},
    {"group_headings": LAMPS},
    {"group_headings": {"light": {"name": " Lamps "}, "fan": {"name": ""}}},
], ids=["omitted", "null", "same", "same-after-cleaning"])
async def test_patch_without_a_change_tells_no_one(client, admin_session, mock_ha_client, body):
    t = await _token(group_headings=LAMPS)
    resp = await _entities(client, admin_session, t["id"], **body)
    assert resp.status_code == 200, resp.text
    assert await _stored(t["id"]) == LAMPS
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_patch_refuses_a_revoked_token(client, admin_session, mock_ha_client):
    t = await _token(group_headings=LAMPS)
    await db.revoke_token(t["id"])
    resp = await _entities(client, admin_session, t["id"], group_headings={})
    assert resp.status_code == 400
    assert await _stored(t["id"]) == LAMPS
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_patch_invalid_headings_change_nothing(client, admin_session, mock_ha_client):
    t = await _token(group_headings=LAMPS)
    resp = await _entities(
        client, admin_session, t["id"], entity_ids=["light.living_room"],
        group_headings={"BAD": {"name": "x"}},
    )
    assert resp.status_code == 422
    assert await _stored(t["id"]) == LAMPS
    assert await db.get_token_entities(t["id"]) == ["light.living_room", "switch.fan"]
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_patch_needs_an_admin(client, test_db):
    t = await _token()
    resp = await client.patch(
        f"/admin/tokens/{t['id']}/entities",
        json={"entity_ids": ["light.living_room"], "group_headings": LAMPS},
    )
    assert resp.status_code == 401
    assert await _stored(t["id"]) == {}


async def test_patch_event_carries_no_heading(client, admin_session, test_db, monkeypatch):
    """The real broadcast, read from a stream's own queue: the event a guest
    page receives says only "reload"."""
    monkeypatch.setattr(ha_client, "broadcast_schedule_changed", REAL_BROADCAST)
    t = await _token()
    q = await ha_client.subscribe(t["id"])
    try:
        resp = await _entities(
            client, admin_session, t["id"], group_headings={"light": {"name": "Secret Lamps"}},
        )
        assert resp.status_code == 200
        events = await _drain(q)
    finally:
        await ha_client.unsubscribe(t["id"], q)
    assert events == [{"type": "schedule_changed"}]
    assert "Secret" not in repr(events)


async def test_entity_templates_do_not_carry_headings(client, admin_session, test_db):
    resp = await client.post(
        "/admin/templates",
        json={"name": "Kit", "entity_ids": ["light.a"], "group_headings": LAMPS},
        cookies=admin_session,
    )
    assert resp.status_code in (200, 201), resp.text
    assert "group_headings" not in resp.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def test_api_create_and_read(client, api_on):
    resp = await client.post(
        "/api/v1/tokens", json=_create_body(group_headings=LAMPS), headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["group_headings"] == LAMPS
    got = (await client.get(f"/api/v1/tokens/{created['id']}", headers=HEADERS)).json()
    assert got["group_headings"] == LAMPS
    listed = (await client.get("/api/v1/tokens", headers=HEADERS)).json()
    assert listed[0]["group_headings"] == LAMPS


async def test_api_create_defaults_and_shape(client, api_on):
    resp = await client.post("/api/v1/tokens", json=_create_body(), headers=HEADERS)
    assert resp.status_code == 201
    assert resp.json()["group_headings"] == {}
    schema = (await client.get("/api/openapi.json", headers=HEADERS)).json()
    props = schema["components"]["schemas"]["ApiTokenResponse"]["properties"]
    assert "group_headings" in props
    assert "group_headings" in schema["components"]["schemas"]["ApiTokenResponse"]["required"]
    heading = schema["components"]["schemas"]["ApiGroupHeading"]["properties"]
    assert set(heading) == {"name", "locked"}


async def test_api_update_sets_and_omission_keeps(client, api_on, test_db):
    t = await _token()
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}", json={"group_headings": LAMPS}, headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["group_headings"] == LAMPS
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json={"label": "New"}, headers=HEADERS)
    assert resp.json()["group_headings"] == LAMPS
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}", json={"group_headings": {}}, headers=HEADERS,
    )
    assert resp.json()["group_headings"] == {}


async def test_api_update_invalid_writes_nothing(client, api_on, test_db):
    t = await _token(group_headings=LAMPS)
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"label": "Changed", "group_headings": {"x": {"name": "y" * 65}}}, headers=HEADERS,
    )
    assert resp.status_code == 422
    row = await db.get_token_by_id(t["id"])
    assert row["label"] == "Cottage"
    assert db.group_headings_from_row(row["group_headings"]) == LAMPS


@pytest.mark.parametrize("body", [
    {"group_headings": LAMPS},
    {"group_headings": LAMPS, "info_text": "New"},
    {"group_headings": LAMPS, "show_remaining_uses": True},
    {"group_headings": LAMPS, "entity_ids": ["light.living_room"]},
], ids=["headings", "with-info", "with-uses", "with-entities"])
async def test_api_update_broadcasts_once(client, api_on, test_db, mock_ha_client, body):
    t = await _token(max_uses=3)
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    mock_ha_client["broadcast_schedule_changed"].assert_awaited_once_with(t["id"])


@pytest.mark.parametrize("body", [
    {"label": "Renamed"},
    {"group_headings": None},
    {"group_headings": LAMPS},
    {"group_headings": {"light": {"name": "Lamps "}}, "label": "Renamed"},
], ids=["omitted", "null", "same", "same-after-cleaning"])
async def test_api_update_without_a_change_does_not_broadcast(
    client, api_on, test_db, mock_ha_client, body
):
    t = await _token(group_headings=LAMPS)
    resp = await client.patch(f"/api/v1/tokens/{t['id']}", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_api_refuses_headings_on_a_revoked_token(client, api_on, test_db, mock_ha_client):
    t = await _token(group_headings=LAMPS)
    await db.revoke_token(t["id"])
    resp = await client.patch(
        f"/api/v1/tokens/{t['id']}",
        json={"label": "Changed", "group_headings": {}}, headers=HEADERS,
    )
    assert resp.status_code == 400
    row = await db.get_token_by_id(t["id"])
    assert row["label"] == "Cottage"
    assert db.group_headings_from_row(row["group_headings"]) == LAMPS
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_api_update_event_carries_no_heading(client, api_on, test_db, monkeypatch):
    monkeypatch.setattr(ha_client, "broadcast_schedule_changed", REAL_BROADCAST)
    t = await _token(max_uses=3)
    q = await ha_client.subscribe(t["id"])
    try:
        resp = await client.patch(
            f"/api/v1/tokens/{t['id']}",
            json={"group_headings": {"light": {"name": "Secret Lamps"}}, "info_text": "Hi"},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        events = await _drain(q)
    finally:
        await ha_client.unsubscribe(t["id"], q)
    # One reload for the whole request, and nothing but the event's name.
    assert events == [{"type": "schedule_changed"}]
    assert "Secret" not in repr(events)


async def test_api_duplicate_copies_the_headings(client, api_on, test_db):
    stored = {"light": {"name": "Lamps", "locked": False}, "switch": {"name": "", "locked": True}}
    t = await _token(group_headings=stored)
    resp = await client.post(f"/api/v1/tokens/{t['id']}/duplicate", json={}, headers=HEADERS)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["id"] != t["id"]
    assert copy["group_headings"] == stored
    assert await _stored(copy["id"]) == stored


# ---------------------------------------------------------------------------
# Guest page: /state and the pending preview carry the headings
# ---------------------------------------------------------------------------

async def test_guest_state_carries_the_headings(client, test_db, mock_ha_client):
    await _token(slug="state-page", group_headings=LAMPS)
    resp = await client.get("/g/state-page/state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["group_headings"] == LAMPS


async def test_guest_state_defaults_to_empty(client, test_db, mock_ha_client):
    await _token(slug="state-default")
    resp = await client.get("/g/state-default/state")
    assert resp.json()["group_headings"] == {}


async def test_pending_page_carries_the_headings(client, test_db, mock_ha_client):
    now = int(time.time())
    await _token(
        slug="pending-page", starts_at=now + 3600, expires_at=now + 86400,
        group_headings={"switch": {"name": "Plugs", "locked": False}},
    )
    resp = await client.get("/g/pending-page")
    assert resp.status_code == 200, resp.text
    want = json.dumps({"switch": {"name": "Plugs", "locked": False}}, sort_keys=True)
    assert f"const PREVIEW_GROUP_HEADINGS = {want};" in resp.text


async def test_live_page_carries_no_preview_headings(client, test_db, mock_ha_client):
    await _token(slug="live-page", group_headings=LAMPS)
    resp = await client.get("/g/live-page")
    assert resp.status_code == 200
    assert "const PREVIEW_GROUP_HEADINGS = {};" in resp.text
