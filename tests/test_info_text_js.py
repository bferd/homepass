"""The dashboard side of per-link guest info text.

Same harness as test_picker_js.py: the real dashboard is rendered, its inline
script is run in Node against a stub DOM, and the shipped functions are called
directly. The markup checks at the end need no node. The node tests are
skipped when node is not installed.
"""
import json

import pytest

from tests.test_picker_js import _run, node
from tests.test_pin_access_ui_js import _FETCH_RECORDER

needs_node = pytest.mark.skipif(node is None, reason="node is not installed")

HOSTILE = "</textarea><script>alert(1)</script><img src=x onerror=alert(2)>"

TOKEN = {
    "id": "t1", "slug": "abc", "label": "Stay", "entity_ids": ["light.a"], "entity_count": 1,
    "ip_allowlist": None, "country_allowlist": None, "created_at": 1700000000,
    "starts_at": None, "expires_at": 4102444800, "revoked": False, "last_accessed": None,
    "max_uses": None, "use_count": 0, "uses_remaining": None, "access_windows": None,
    "remember_pin": True, "has_pin": False, "device_binding": False, "device_bound_at": None,
    "show_remaining_uses": False,
    "info_text": "Door code 1234\nCall +1 613 555 0100", "info_text_in_footer": True,
}

_SEED = _FETCH_RECORDER + f"""
const TOKEN = {json.dumps(TOKEN)};
const HOSTILE = {json.dumps(HOSTILE)};
allEntities = [{{ entity_id: 'light.a', friendly_name: 'A', domain: 'light', state: 'on' }}];
const els = id => document.getElementById(id);
"""


# ---------------------------------------------------------------------------
# Create modal
# ---------------------------------------------------------------------------

@needs_node
async def test_create_sends_both_fields(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    openCreateModal();
    els('f-label').value = 'Guest';
    els('f-info-text').value = 'Park on the left';
    els('f-info-footer').checked = true;
    createPicker.selected.add('light.a');
    replies.push({ ok: true, body: { slug: 'abc' } });
    await submitCreate();
    const create = calls.filter(c => c.url.endsWith('/admin/tokens') && c.method === 'POST').pop();
    console.log(JSON.stringify(create.body));
    """)
    assert out["info_text"] == "Park on the left"
    assert out["info_text_in_footer"] is True


@needs_node
async def test_create_without_text_sends_empty_and_unchecked(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    openCreateModal();
    els('f-label').value = 'Guest';
    createPicker.selected.add('light.a');
    replies.push({ ok: true, body: { slug: 'abc' } });
    await submitCreate();
    const create = calls.filter(c => c.url.endsWith('/admin/tokens') && c.method === 'POST').pop();
    console.log(JSON.stringify(create.body));
    """)
    assert out["info_text"] == ""
    assert out["info_text_in_footer"] is False


@needs_node
async def test_reopening_the_create_modal_resets_both(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    openCreateModal();
    els('f-info-text').value = 'left over';
    els('f-info-footer').checked = true;
    openCreateModal();
    console.log(JSON.stringify({ text: els('f-info-text').value, footer: els('f-info-footer').checked }));
    """)
    assert out == {"text": "", "footer": False}


@needs_node
async def test_duplicate_prefills_both(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    replies.push({ ok: true, body: TOKEN });
    await duplicateToken('t1');
    console.log(JSON.stringify({ text: els('f-info-text').value, footer: els('f-info-footer').checked }));
    """)
    assert out == {"text": TOKEN["info_text"], "footer": True}


@needs_node
async def test_duplicate_of_a_token_without_text_leaves_the_form_blank(
    client, admin_session, mock_ha_client
):
    out = await _run(client, admin_session, _SEED + """
    openCreateModal();
    els('f-info-text').value = 'left over';
    els('f-info-footer').checked = true;
    replies.push({ ok: true, body: { ...TOKEN, info_text: '', info_text_in_footer: false } });
    await duplicateToken('t1');
    console.log(JSON.stringify({ text: els('f-info-text').value, footer: els('f-info-footer').checked }));
    """)
    assert out == {"text": "", "footer": False}


# ---------------------------------------------------------------------------
# Guest info dialog
# ---------------------------------------------------------------------------

@needs_node
async def test_dialog_opens_prefilled_from_the_token(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    els('info-error').textContent = 'stale';
    replies.push({ ok: true, body: TOKEN });
    await openInfoModal('t1');
    console.log(JSON.stringify({
      url: calls[0].url, id: infoTokenId, label: els('info-token-label').textContent,
      text: els('i-info-text').value, footer: els('i-info-footer').checked,
      error: els('info-error').textContent,
    }));
    """)
    assert out["url"].endswith("/admin/tokens/t1")
    assert out["id"] == "t1"
    assert out["label"] == "Stay"
    assert out["text"] == TOKEN["info_text"]
    assert out["footer"] is True
    assert out["error"] == ""


@needs_node
async def test_dialog_does_not_open_when_the_token_cannot_be_read(
    client, admin_session, mock_ha_client
):
    out = await _run(client, admin_session, _SEED + """
    replies.push({ ok: false, body: {} });
    await openInfoModal('t1');
    console.log(JSON.stringify({ id: infoTokenId }));
    """)
    assert out["id"] is None


@needs_node
async def test_save_patches_both_fields_and_updates_the_held_token(
    client, admin_session, mock_ha_client
):
    out = await _run(client, admin_session, _SEED + """
    tokens = [{ ...TOKEN, info_text: '', info_text_in_footer: false }];
    replies.push({ ok: true, body: tokens[0] });
    await openInfoModal('t1');
    els('i-info-text').value = 'Bins go out on Tuesday';
    els('i-info-footer').checked = true;
    // The PATCH reply is the bare row: no entity count, as the server sends it.
    replies.push({ ok: true, body: { ...TOKEN, entity_count: 0,
      info_text: 'Bins go out on Tuesday', info_text_in_footer: true } });
    await submitInfo();
    const patch = calls.pop();
    console.log(JSON.stringify({ patch, held: tokens[0], id: infoTokenId,
      rendered: els('token-grid').innerHTML.includes('open-info-modal') }));
    """)
    assert out["patch"]["url"].endswith("/admin/tokens/t1/info")
    assert out["patch"]["method"] == "PATCH"
    assert out["patch"]["body"] == {
        "info_text": "Bins go out on Tuesday", "info_text_in_footer": True,
    }
    assert out["held"]["info_text"] == "Bins go out on Tuesday"
    assert out["held"]["info_text_in_footer"] is True
    # Only the two fields are taken from the reply.
    assert out["held"]["entity_count"] == 1
    assert out["id"] is None
    assert out["rendered"]


@needs_node
async def test_clearing_the_text_sends_an_empty_string(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    tokens = [{ ...TOKEN }];
    replies.push({ ok: true, body: TOKEN });
    await openInfoModal('t1');
    els('i-info-text').value = '';
    els('i-info-footer').checked = false;
    replies.push({ ok: true, body: { ...TOKEN, info_text: '', info_text_in_footer: false } });
    await submitInfo();
    console.log(JSON.stringify({ body: calls.pop().body, held: tokens[0].info_text }));
    """)
    assert out["body"] == {"info_text": "", "info_text_in_footer": False}
    assert out["held"] == ""


@needs_node
@pytest.mark.parametrize("reply, shown", [
    ({"detail": "Cannot edit the info text of a revoked token"},
     "Cannot edit the info text of a revoked token"),
    # A 422 carries a list, which falls back to the dialog's own message.
    ({"detail": [{"loc": ["body", "info_text"], "msg": "too long"}]}, "Error updating guest info."),
    ({}, "Error updating guest info."),
])
async def test_an_error_is_shown_and_the_dialog_stays_open(
    client, admin_session, mock_ha_client, reply, shown
):
    out = await _run(client, admin_session, _SEED + f"""
    tokens = [{{ ...TOKEN }}];
    replies.push({{ ok: true, body: TOKEN }});
    await openInfoModal('t1');
    els('i-info-text').value = 'changed';
    replies.push({{ ok: false, body: {json.dumps(reply)} }});
    await submitInfo();
    console.log(JSON.stringify({{ error: els('info-error').textContent, id: infoTokenId,
      held: tokens[0].info_text }}));
    """)
    assert out["error"] == shown
    assert out["id"] == "t1"
    assert out["held"] == TOKEN["info_text"]


# ---------------------------------------------------------------------------
# Hostile text
# ---------------------------------------------------------------------------

@needs_node
async def test_hostile_text_only_ever_travels_as_a_value(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    const hostile = { ...TOKEN, label: HOSTILE, info_text: HOSTILE };
    tokens = [hostile];
    renderTokens();
    const card = els('token-grid').innerHTML;
    replies.push({ ok: true, body: hostile });
    await openInfoModal('t1');
    const dialog = { text: els('i-info-text').value, html: els('i-info-text').innerHTML,
      label: els('info-token-label').textContent, labelHtml: els('info-token-label').innerHTML };
    replies.push({ ok: true, body: hostile });
    await duplicateToken('t1');
    const create = { text: els('f-info-text').value, html: els('f-info-text').innerHTML };
    console.log(JSON.stringify({ card, dialog, create }));
    """)
    # The text is set as a value, untouched, and never written as markup.
    assert out["dialog"]["text"] == HOSTILE and out["dialog"]["html"] == ""
    assert out["create"]["text"] == HOSTILE and out["create"]["html"] == ""
    assert out["dialog"]["label"] == HOSTILE and out["dialog"]["labelHtml"] == ""
    # The card does not show the text, and the label it does show is escaped.
    assert "<script" not in out["card"] and "<img" not in out["card"]
    assert "</textarea" not in out["card"]


async def test_dashboard_has_the_modal_and_both_sets_of_controls(
    client, admin_session, mock_ha_client
):
    resp = await client.get("/admin/dashboard", cookies=admin_session)
    html = resp.text
    assert 'id="info-modal"' in html
    assert 'data-action="submit-info"' in html
    assert 'data-modal="info"' in html
    for p in ("f", "i"):
        assert f'<textarea id="{p}-info-text" rows="4" maxlength="1000"' in html
        assert f'id="{p}-info-footer" type="checkbox"' in html
    # Both textareas are empty in the markup; script fills them through .value.
    assert html.count('focus:ring-primary/20"></textarea>') >= 2
    for text in (
        "Guest info text",
        "Optional. Shown to this link&#39;s guests. Web links, email addresses and phone numbers become tappable.",
        "Show in the footer instead",
        "Moves this link&#39;s text to a box at the bottom of the page that is always visible.",
    ):
        assert html.count(text) == 2, text
    assert '<h2 id="info-modal-title" class="text-lg font-serif">Guest info</h2>' in html


async def test_dashboard_is_translated_into_spanish(client, admin_session, mock_ha_client):
    resp = await client.get(
        "/admin/dashboard", cookies=admin_session, headers={"Accept-Language": "es"}
    )
    assert "Información para invitados" in resp.text
    assert resp.text.count("Mostrarlo en el pie de página") == 2
