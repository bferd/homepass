"""Deployment settings editable from the dashboard (app/settings_store.py).

Precedence under test: a saved override beats the add-on option, reverting puts
the option back, and only the allow-listed keys can be touched at all. The
overrides mutate the process-wide `settings` object, so every test here starts
and ends with the configured options restored.
"""
import asyncio
import logging
import time

import pytest

from app import database as db
from app import ha_client
from app import settings_store
from app.config import settings

# Taken before any fixture patches ha_client: the `client` fixture mocks the
# broadcasts, and the stream tests at the end need the real one.
REAL_BROADCAST_ALL = ha_client.broadcast_schedule_changed_all


@pytest.fixture(autouse=True)
def _restore_settings():
    settings_store.reset_state()
    yield
    settings_store.reset_state()


async def _patch(client, admin_session, body):
    return await client.patch("/admin/settings", json=body, cookies=admin_session)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def test_settings_require_admin(client):
    assert (await client.get("/admin/settings")).status_code == 401
    assert (await client.patch("/admin/settings", json={"app_name": "X"})).status_code == 401
    assert (await client.delete("/admin/settings/app_name")).status_code == 401
    assert settings.app_name == settings_store._option_values["app_name"]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def test_read_lists_exactly_the_editable_keys(client, admin_session):
    resp = await client.get("/admin/settings", cookies=admin_session)
    assert resp.status_code == 200
    data = resp.json()["settings"]
    assert set(data) == {
        "app_name", "contact_message", "welcome_text", "brand_bg", "brand_primary",
        "guest_url", "access_log_retention_days", "phone_region",
    }
    assert data["app_name"] == {
        "value": settings.app_name, "option": settings.app_name, "overridden": False,
    }


async def test_read_never_exposes_credentials(client, admin_session):
    body = (await client.get("/admin/settings", cookies=admin_session)).text
    assert settings.admin_password not in body
    assert settings.ha_token not in body


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

async def test_save_applies_live_and_persists(client, admin_session):
    resp = await _patch(client, admin_session, {"app_name": "  Lake House  "})
    assert resp.status_code == 200
    entry = resp.json()["settings"]["app_name"]
    assert entry["value"] == "Lake House"
    assert entry["overridden"] is True
    assert entry["option"] == settings_store._option_values["app_name"]
    assert settings.app_name == "Lake House"
    assert (await db.get_app_settings()) == {"app_name": "Lake House"}


async def test_save_is_partial(client, admin_session):
    before = settings.contact_message
    await _patch(client, admin_session, {"app_name": "Lake House"})
    assert settings.contact_message == before
    assert set(await db.get_app_settings()) == {"app_name"}


async def test_saving_the_option_value_clears_the_override(client, admin_session):
    """Otherwise the row would pin today's option and a later Supervisor edit
    would silently do nothing."""
    option = settings_store._option_values["app_name"]
    await _patch(client, admin_session, {"app_name": "Lake House"})
    resp = await _patch(client, admin_session, {"app_name": option})
    assert resp.json()["settings"]["app_name"]["overridden"] is False
    assert await db.get_app_settings() == {}


async def test_empty_save_is_refused(client, admin_session):
    assert (await _patch(client, admin_session, {})).status_code == 400


@pytest.mark.parametrize("key", [
    "admin_username", "admin_password", "ha_base_url", "ha_token",
    "db_path", "supervisor_token",
])
async def test_credentials_and_connection_settings_are_not_editable(client, admin_session, key):
    before = getattr(settings, key)
    resp = await _patch(client, admin_session, {key: "http://attacker.example"})
    assert resp.status_code == 422
    assert getattr(settings, key) == before
    assert await db.get_app_settings() == {}


async def test_one_bad_field_rejects_the_whole_save(client, admin_session):
    before = settings.app_name
    resp = await _patch(client, admin_session, {"app_name": "Fine", "brand_bg": "red"})
    assert resp.status_code == 422
    assert settings.app_name == before
    assert await db.get_app_settings() == {}


@pytest.mark.parametrize("body", [
    {"app_name": "   "},
    {"app_name": "x" * 65},
    {"app_name": None},
    {"contact_message": ""},
    {"contact_message": "x" * 501},
    {"brand_bg": "#FFF"},
    {"brand_primary": "D9523C"},
    {"brand_primary": "#D9523C;}</style><script>"},
    {"guest_url": "guest.example.com"},
    {"guest_url": "javascript:alert(1)"},
    {"guest_url": "https://guest.example.com/?x=1"},
    {"guest_url": "https://guest.example.com/#frag"},
    {"guest_url": "https://user:pass@guest.example.com"},
    {"guest_url": "https://guest.example.com/';alert(1);'"},
    {"guest_url": "https://guest.example.com:99999"},
    {"access_log_retention_days": 0},
    {"access_log_retention_days": 3651},
    {"access_log_retention_days": True},
    {"access_log_retention_days": "30"},
    {"welcome_text": "x" * 1001},
    {"welcome_text": None},
    {"phone_region": "XX"},
    {"phone_region": "CAN"},
    {"phone_region": "1"},
    {"phone_region": "C A"},
    {"phone_region": None},
])
async def test_invalid_values_are_refused(client, admin_session, body):
    resp = await _patch(client, admin_session, body)
    assert resp.status_code == 422
    assert await db.get_app_settings() == {}


async def test_guest_url_is_normalised(client, admin_session):
    resp = await _patch(client, admin_session, {"guest_url": " https://guest.example.com/ "})
    assert resp.status_code == 200
    assert settings.guest_url == "https://guest.example.com"


async def test_colours_are_stored_in_one_spelling(client, admin_session):
    await _patch(client, admin_session, {"brand_primary": "#00aa11"})
    assert settings.brand_primary == "#00AA11"


# ---------------------------------------------------------------------------
# Welcome text and phone region: empty is a value, not an error
# ---------------------------------------------------------------------------

async def test_welcome_text_line_endings_are_stored_as_newlines(client, admin_session):
    resp = await _patch(client, admin_session, {"welcome_text": "  Hi!\r\nWi-Fi: Cottage\rCall us  "})
    assert resp.status_code == 200
    assert settings.welcome_text == "Hi!\nWi-Fi: Cottage\nCall us"
    assert await db.get_app_settings() == {"welcome_text": "Hi!\nWi-Fi: Cottage\nCall us"}


async def test_welcome_text_limit_counts_the_stored_text(client, admin_session):
    """1000 characters typed on Windows arrive with CRLFs; the limit is on
    what is kept, so they still fit."""
    text = "\r\n".join(["x" * 9] * 100)  # 999 characters once stored
    resp = await _patch(client, admin_session, {"welcome_text": text})
    assert resp.status_code == 200
    assert len(settings.welcome_text) == 999


async def test_welcome_text_markup_is_kept_as_typed(client, admin_session):
    """Sanitising happens where the text is rendered, not here: even a tag the
    page will never interpret is stored exactly as typed."""
    typed = '<b>Hi</b> & welcome <script>x</script><font color="nope">'
    await _patch(client, admin_session, {"welcome_text": typed})
    assert settings.welcome_text == typed
    assert await db.get_app_settings() == {"welcome_text": typed}


async def test_welcome_text_limit_counts_tags(client, admin_session):
    resp = await _patch(client, admin_session, {"welcome_text": "<b>" + "x" * 994 + "</b>"})
    assert resp.status_code == 422


async def test_clearing_welcome_text_removes_the_override(client, admin_session):
    await _patch(client, admin_session, {"welcome_text": "Welcome!"})
    assert await db.get_app_settings() == {"welcome_text": "Welcome!"}
    resp = await _patch(client, admin_session, {"welcome_text": "   "})
    assert resp.status_code == 200
    entry = resp.json()["settings"]["welcome_text"]
    assert entry == {"value": "", "option": "", "overridden": False}
    assert settings.welcome_text == ""
    assert await db.get_app_settings() == {}


async def test_phone_region_is_upper_cased(client, admin_session):
    resp = await _patch(client, admin_session, {"phone_region": " ca "})
    assert resp.status_code == 200
    assert settings.phone_region == "CA"
    assert resp.json()["settings"]["phone_region"]["value"] == "CA"


async def test_blank_phone_region_is_accepted(client, admin_session):
    await _patch(client, admin_session, {"phone_region": "CA"})
    resp = await _patch(client, admin_session, {"phone_region": ""})
    assert resp.status_code == 200
    assert settings.phone_region == ""
    assert await db.get_app_settings() == {}


async def test_new_keys_are_in_the_snapshot(client, admin_session):
    data = (await client.get("/admin/settings", cookies=admin_session)).json()["settings"]
    assert data["welcome_text"] == {"value": "", "option": "", "overridden": False}
    assert data["phone_region"] == {"value": "", "option": "", "overridden": False}


async def test_new_keys_leave_the_others_alone(client, admin_session):
    await _patch(client, admin_session, {"app_name": "Lake House"})
    resp = await _patch(client, admin_session, {"welcome_text": "Hi", "phone_region": "es"})
    assert resp.status_code == 200
    assert settings.app_name == "Lake House"
    assert await db.get_app_settings() == {
        "app_name": "Lake House", "welcome_text": "Hi", "phone_region": "ES",
    }
    snap = resp.json()["settings"]
    assert {k for k, v in snap.items() if v["overridden"]} == {
        "app_name", "welcome_text", "phone_region",
    }


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

async def test_revert_restores_the_option(client, admin_session):
    option = settings_store._option_values["app_name"]
    await _patch(client, admin_session, {"app_name": "Lake House"})
    resp = await client.delete("/admin/settings/app_name", cookies=admin_session)
    assert resp.status_code == 200
    assert resp.json()["settings"]["app_name"] == {
        "value": option, "option": option, "overridden": False,
    }
    assert settings.app_name == option
    assert await db.get_app_settings() == {}


@pytest.mark.parametrize("key", ["admin_password", "ha_token", "nonsense"])
async def test_revert_refuses_non_editable_keys(client, admin_session, key):
    resp = await client.delete(f"/admin/settings/{key}", cookies=admin_session)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# The overrides actually reach what they configure
# ---------------------------------------------------------------------------

async def test_app_name_reaches_guest_pages_and_manifest(client, admin_session, sample_token):
    await _patch(client, admin_session, {"app_name": "Lake House"})
    page = await client.get("/g/test-token")
    assert "Lake House" in page.text
    manifest = (await client.get("/g/test-token/manifest.json")).json()
    assert manifest["name"] == "Lake House"


async def test_contact_message_reaches_the_expired_page(client, admin_session):
    await _patch(client, admin_session, {"contact_message": "Text Sam on 555-0100."})
    resp = await client.get("/g/no-such-link")
    assert resp.status_code == 410
    assert "Text Sam on 555-0100." in resp.text


async def test_brand_colours_change_the_palette_without_a_restart(client, admin_session, sample_token):
    await _patch(client, admin_session, {"brand_primary": "#112233"})
    page = await client.get("/g/test-token")
    assert "--color-primary: 17 34 51;" in page.text
    manifest = (await client.get("/g/test-token/manifest.json")).json()
    assert manifest["theme_color"] == "#112233"

    await client.delete("/admin/settings/brand_primary", cookies=admin_session)
    page = await client.get("/g/test-token")
    assert "--color-primary: 17 34 51;" not in page.text


async def test_guest_url_reaches_the_dashboard(client, admin_session):
    await _patch(client, admin_session, {"guest_url": "https://guest.example.com"})
    resp = await client.get("/admin/dashboard", cookies=admin_session)
    assert 'const GUEST_URL = "https://guest.example.com";' in resp.text


async def test_retention_is_what_cleanup_uses(client, admin_session):
    await _patch(client, admin_session, {"access_log_retention_days": 7})
    assert settings.access_log_retention_days == 7
    token = await db.create_token(
        label="t", slug="retention", entity_ids=["light.x"],
        expires_at=int(time.time()) + 3600, ip_allowlist=None,
    )
    await db.log_access(token_id=token["id"], event_type="page_load")
    conn = await db.get_db()
    await conn.execute("UPDATE access_log SET timestamp = ?", (int(time.time()) - 8 * 86400,))
    await conn.commit()
    await db.cleanup_old_data(settings.access_log_retention_days)
    assert await db.list_access_logs() == []


# ---------------------------------------------------------------------------
# Startup load
# ---------------------------------------------------------------------------

async def test_load_applies_stored_overrides(test_db):
    await db.set_app_settings({"app_name": "Lake House", "access_log_retention_days": 14})
    await settings_store.load()
    assert settings.app_name == "Lake House"
    assert settings.access_log_retention_days == 14
    snap = settings_store.snapshot()
    assert snap["app_name"]["overridden"] is True
    assert snap["contact_message"]["overridden"] is False


async def test_load_skips_unknown_and_invalid_rows(test_db):
    """A row planted in the table outside the API is still validated before it
    is rendered into a page."""
    await db.set_app_settings({
        "ha_token": "stolen",
        "brand_bg": "#000;}</style><script>alert(1)</script>",
        "app_name": "Lake House",
    })
    await settings_store.load()
    assert settings.ha_token != "stolen"
    assert settings.brand_bg == settings_store._option_values["brand_bg"]
    assert settings.app_name == "Lake House"
    assert set(k for k, v in settings_store.snapshot().items() if v["overridden"]) == {"app_name"}


async def test_load_skips_an_invalid_stored_phone_region(test_db, caplog):
    await db.set_app_settings({"phone_region": "XX", "welcome_text": "Hi"})
    with caplog.at_level(logging.WARNING, logger="app.settings_store"):
        await settings_store.load()
    assert settings.phone_region == settings_store._option_values["phone_region"]
    assert settings.welcome_text == "Hi"
    assert set(k for k, v in settings_store.snapshot().items() if v["overridden"]) == {"welcome_text"}
    assert any(
        "phone_region" in r.getMessage() and "no longer valid" in r.getMessage()
        for r in caplog.records
    )


async def test_load_with_an_empty_table_is_the_old_behaviour(test_db):
    await settings_store.load()
    for name, entry in settings_store.snapshot().items():
        assert entry["overridden"] is False
        assert entry["value"] == settings_store._option_values[name]


# ---------------------------------------------------------------------------
# Dashboard dialog (the shipped inline script, run in node)
# ---------------------------------------------------------------------------

from tests.test_picker_js import _run, node  # noqa: E402

_needs_node = pytest.mark.skipif(node is None, reason="node is not installed")

_SETTINGS_SEED = """
settingsState = {
  app_name: { value: 'Home Access', option: 'Home Access', overridden: false },
  contact_message: { value: 'Ask Sam', option: 'Please request a new link.', overridden: true },
  guest_url: { value: '', option: '', overridden: false },
  brand_primary: { value: '#D9523C', option: '#D9523C', overridden: false },
  brand_bg: { value: '#F2F0E9', option: '#F2F0E9', overridden: false },
  access_log_retention_days: { value: 90, option: 90, overridden: false },
  welcome_text: { value: '', option: '', overridden: false },
  phone_region: { value: '', option: '', overridden: false },
};
const put = (key, v) => { document.getElementById('set-' + key).value = v; };
const fill = () => {
  put('app_name', 'Home Access'); put('contact_message', 'Ask Sam'); put('guest_url', '');
  put('brand_primary', '#d9523c'); put('brand_bg', '#f2f0e9');
  put('access_log_retention_days', '90');
  put('welcome_text', ''); put('phone_region', '');
};
"""


@_needs_node
async def test_dialog_sends_only_what_changed(client, admin_session, mock_ha_client):
    """An untouched field must not become an override just because the dialog
    was saved with it on screen — including a colour the picker lower-cased."""
    out = await _run(client, admin_session, _SETTINGS_SEED + """
    fill();
    const untouched = collectSettings();
    put('app_name', '  Lake House '); put('access_log_retention_days', '30');
    const edited = collectSettings();
    put('access_log_retention_days', '3.5');
    let error = null;
    try { collectSettings(); } catch (e) { error = e.message; }
    console.log(JSON.stringify({ untouched, edited, error }));
    """)
    assert out["untouched"] == {}
    assert out["edited"] == {"app_name": "Lake House", "access_log_retention_days": 30}
    assert "whole number" in out["error"]


@_needs_node
async def test_dialog_offers_revert_only_on_overridden_fields(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SETTINGS_SEED + """
    renderSettings();
    const html = document.getElementById('settings-fields').innerHTML;
    console.log(JSON.stringify({
      reverts: (html.match(/data-action="revert-setting"/g) || []).length,
      key: html.includes('data-key="contact_message"'),
      option: html.includes('Please request a new link.'),
    }));
    """)
    assert out == {"reverts": 1, "key": True, "option": True}


@_needs_node
async def test_welcome_text_dialog_field(client, admin_session, mock_ha_client):
    """A roomier box than the contact message, and clearing it saves "" (off)
    rather than leaving the old text in place."""
    out = await _run(client, admin_session, _SETTINGS_SEED + """
    settingsState.welcome_text = { value: 'Welcome!', option: '', overridden: true };
    renderSettings();
    const html = document.getElementById('settings-fields').innerHTML;
    const box = html.match(/<textarea id="set-welcome_text"[^>]*>/)[0];
    const region = html.match(/<input id="set-phone_region"[^>]*>/)[0];
    fill();
    put('welcome_text', '');
    console.log(JSON.stringify({
      rows: (box.match(/rows="(\\d+)"/) || [])[1],
      maxlength: (box.match(/maxlength="(\\d+)"/) || [])[1],
      contactRows: (html.match(/<textarea id="set-contact_message"[^>]*rows="(\\d+)"/) || [])[1],
      regionMax: (region.match(/maxlength="(\\d+)"/) || [])[1],
      saved: collectSettings(),
      option: html.includes('Overriding the add-on option (empty).'),
    }));
    """)
    assert out == {
        "rows": "5", "maxlength": "1000", "contactRows": "2", "regionMax": "2",
        "saved": {"welcome_text": ""}, "option": True,
    }


# ---------------------------------------------------------------------------
# Welcome text and phone region reload every open guest page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"welcome_text": "Welcome!"},
    {"phone_region": "CA"},
    {"welcome_text": "Welcome!", "phone_region": "CA"},
    {"welcome_text": "Welcome!", "app_name": "Cottage"},
], ids=["welcome", "region", "both", "welcome-and-other"])
async def test_guest_page_setting_change_reloads_every_page_once(
    client, admin_session, mock_ha_client, body
):
    resp = await _patch(client, admin_session, body)
    assert resp.status_code == 200, resp.text
    mock_ha_client["broadcast_schedule_changed_all"].assert_awaited_once_with()
    mock_ha_client["broadcast_schedule_changed"].assert_not_awaited()


async def test_saving_the_same_value_again_does_not_reload(client, admin_session, mock_ha_client):
    await _patch(client, admin_session, {"welcome_text": "Welcome!", "phone_region": "CA"})
    broadcast = mock_ha_client["broadcast_schedule_changed_all"]
    broadcast.reset_mock()
    # "ca" is stored as "CA": the value in effect does not change.
    resp = await _patch(client, admin_session, {"welcome_text": "  Welcome!  ", "phone_region": "ca"})
    assert resp.status_code == 200
    broadcast.assert_not_awaited()


async def test_other_settings_do_not_reload(client, admin_session, mock_ha_client):
    resp = await _patch(client, admin_session, {"app_name": "Cottage", "brand_primary": "#00aa11"})
    assert resp.status_code == 200
    mock_ha_client["broadcast_schedule_changed_all"].assert_not_awaited()


async def test_saving_back_to_the_option_reloads_only_when_the_value_changes(
    client, admin_session, mock_ha_client, monkeypatch
):
    monkeypatch.setitem(settings_store._option_values, "welcome_text", "From the add-on")
    monkeypatch.setattr(settings, "welcome_text", "From the add-on")
    broadcast = mock_ha_client["broadcast_schedule_changed_all"]

    # Saving the option's own value with no override in place deletes a row
    # that is not there and changes nothing a guest sees.
    resp = await _patch(client, admin_session, {"welcome_text": "From the add-on"})
    assert resp.status_code == 200
    broadcast.assert_not_awaited()

    await _patch(client, admin_session, {"welcome_text": "Override"})
    assert broadcast.await_count == 1

    # Back to the option: the override row goes, and the text in effect changes.
    resp = await _patch(client, admin_session, {"welcome_text": "From the add-on"})
    assert resp.json()["settings"]["welcome_text"]["overridden"] is False
    assert broadcast.await_count == 2


async def test_revert_reloads_only_when_the_value_changes(client, admin_session, mock_ha_client):
    broadcast = mock_ha_client["broadcast_schedule_changed_all"]
    # Nothing overridden: reverting changes nothing.
    assert (await client.delete("/admin/settings/welcome_text", cookies=admin_session)).status_code == 200
    broadcast.assert_not_awaited()

    await _patch(client, admin_session, {"phone_region": "CA"})
    broadcast.reset_mock()
    assert (await client.delete("/admin/settings/phone_region", cookies=admin_session)).status_code == 200
    broadcast.assert_awaited_once_with()

    broadcast.reset_mock()
    await _patch(client, admin_session, {"app_name": "Cottage"})
    await client.delete("/admin/settings/app_name", cookies=admin_session)
    broadcast.assert_not_awaited()


@pytest.mark.parametrize("body", [
    {"welcome_text": "x" * 1001},
    {"phone_region": "XX"},
    {"welcome_text": "Welcome!", "phone_region": "XX"},
    {},
])
async def test_refused_save_does_not_reload(client, admin_session, mock_ha_client, body):
    resp = await _patch(client, admin_session, body)
    assert resp.status_code in (400, 422)
    mock_ha_client["broadcast_schedule_changed_all"].assert_not_awaited()


async def test_save_without_a_session_does_not_reload(client, mock_ha_client):
    resp = await client.patch("/admin/settings", json={"welcome_text": "Hacked"})
    assert resp.status_code == 401
    resp = await client.delete("/admin/settings/welcome_text")
    assert resp.status_code == 401
    mock_ha_client["broadcast_schedule_changed_all"].assert_not_awaited()


async def _token(slug):
    return await db.create_token(
        label=slug, slug=slug, entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 86400, ip_allowlist=None,
    )


async def test_the_reload_reaches_every_link_with_no_text(
    client, admin_session, monkeypatch
):
    """The real broadcast, read from the streams' own queues for two links."""
    monkeypatch.setattr(ha_client, "broadcast_schedule_changed_all", REAL_BROADCAST_ALL)
    first, second = await _token("first"), await _token("second")
    queues = [
        (first["id"], await ha_client.subscribe(first["id"])),
        (second["id"], await ha_client.subscribe(second["id"])),
        (second["id"], await ha_client.subscribe(second["id"])),
    ]
    try:
        resp = await _patch(client, admin_session, {"welcome_text": "Door code 4821"})
        assert resp.status_code == 200
        received = []
        for _token_id, q in queues:
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            received.append(events)
    finally:
        for token_id, q in queues:
            await ha_client.unsubscribe(token_id, q)
    assert received == [[{"type": "schedule_changed"}]] * 3
    assert "4821" not in repr(received)


async def test_the_all_links_reload_tolerates_no_streams(monkeypatch):
    monkeypatch.setattr(ha_client, "_subscriptions", {})
    await REAL_BROADCAST_ALL()


async def test_the_all_links_reload_skips_a_full_queue(monkeypatch):
    full = asyncio.Queue(maxsize=1)
    full.put_nowait({"type": "older"})
    other = asyncio.Queue(maxsize=1)
    monkeypatch.setattr(ha_client, "_subscriptions", {"a": {full}, "b": {other}})
    await REAL_BROADCAST_ALL()
    assert full.get_nowait() == {"type": "older"}
    assert other.get_nowait() == {"type": "schedule_changed"}
