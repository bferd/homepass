"""Section headings in the browser: the guest page's sections and the dashboard rows.

Same harness as test_picker_js.py and test_guest_cards_js.py: the real page is
rendered through its route, its inline script is run in node against the stub
DOM, and the shipped functions are called directly. One addition: the
document's event listeners are recorded, so a click or an input reaches the
page's own delegated handlers rather than a function called around them.

Skipped when node is not installed.
"""
import json
import re
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from app import database as db
from tests.test_guest_cards_js import GUEST_STUB
from tests.test_picker_js import DOM_STUB, REPO_SCRIPTS, node
from tests.test_pin_access_ui_js import _FETCH_RECORDER

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

# Keeps the listeners the page adds to document, so a probe can fire them.
# localStorage records every touch, so a test can show the sections never
# reach for it.
LISTENERS = """
const _listeners = {};
document.addEventListener = (type, fn) => { (_listeners[type] ||= []).push(fn); };
function fire(type, target) { for (const fn of _listeners[type] || []) fn({ target, preventDefault() {} }); }
const storageLog = [];
globalThis.localStorage = {
  getItem(k) { storageLog.push(['get', k]); return null; },
  setItem(k) { storageLog.push(['set', k]); },
  removeItem(k) { storageLog.push(['remove', k]); },
};
window.localStorage = globalThis.localStorage;
"""


def _last_inline_script(html: str) -> str:
    blocks = re.findall(r'<script nonce="[^"]*">(.*?)</script>', html, re.S)
    assert blocks, "no inline script on the page"
    return blocks[-1]


def _node(parts: list[str]):
    proc = subprocess.run(
        [node, "--input-type=module"], input="\n".join(parts),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def _guest(client, probe: str, slug: str = "test-token"):
    resp = await client.get(f"/g/{slug}")
    assert resp.status_code == 200, resp.text
    parts = [DOM_STUB, GUEST_STUB, LISTENERS]
    parts += [(ROOT / rel).read_text() for rel in REPO_SCRIPTS]
    parts += [_last_inline_script(resp.text), textwrap.dedent(probe)]
    return _node(parts)


async def _dashboard(client, admin_session, probe: str):
    resp = await client.get("/admin/dashboard", cookies=admin_session)
    assert resp.status_code == 200
    parts = [DOM_STUB, LISTENERS]
    parts += [(ROOT / rel).read_text() for rel in REPO_SCRIPTS]
    parts += [_last_inline_script(resp.text), _FETCH_RECORDER, textwrap.dedent(probe)]
    return _node(parts)


def _headings(html: str) -> list[str]:
    """The text of every section heading, toggle or plain, in page order."""
    out = []
    for inner in re.findall(r"<h2 [^>]*>(.*?)</h2>", html, re.S):
        inner = re.sub(r'<span aria-hidden="true"[^>]*>[^<]*</span>', "", inner)
        out.append(re.sub(r"<[^>]+>", "", inner))
    return out


def _toggles(html: str) -> list[str]:
    """Group keys of the headings drawn as collapse toggles."""
    return re.findall(r'data-action="group-toggle" data-group="([^"]+)"', html)


# Four sections' worth of entities: input_boolean shares "Switches" with switch.
GUEST_SEED = """
entityMeta = {};
entityOrder = ['light.a', 'switch.b', 'input_boolean.c', 'lock.d'];
states = {
  'light.a': { entity_id: 'light.a', state: 'off', attributes: { friendly_name: 'Lamp' } },
  'switch.b': { entity_id: 'switch.b', state: 'off', attributes: { friendly_name: 'Plug' } },
  'input_boolean.c': { entity_id: 'input_boolean.c', state: 'on', attributes: { friendly_name: 'Guest mode' } },
  'lock.d': { entity_id: 'lock.d', state: 'locked', attributes: { friendly_name: 'Door' } },
};
const html = () => document.getElementById('cards-container').innerHTML;
// A stand-in for the rendered heading button: the delegated handler only
// reads its dataset and aria-expanded.
function click(key) {
  const attrs = { 'aria-expanded': String(!collapsedGroups.has(key)) };
  const btn = {
    dataset: { action: 'group-toggle', group: key }, tagName: 'BUTTON', attrs,
    getAttribute: k => attrs[k] ?? null,
    setAttribute: (k, v) => { attrs[k] = String(v); },
    closest: sel => (sel === '[data-action]' ? btn : null),
  };
  fire('click', btn);
  return btn;
}
"""


# ---------------------------------------------------------------------------
# Guest page
# ---------------------------------------------------------------------------

async def test_default_headings_are_toggles_in_section_order(client, sample_token, mock_ha_client):
    out = await _guest(client, GUEST_SEED + """
    groupHeadings = {};
    renderAll();
    console.log(JSON.stringify(html()));
    """)
    assert _headings(out) == ["Lights", "Switches", "Locks"]
    for key in ("light", "switch", "lock"):
        button = re.search(rf'<button [^>]*data-group="{key}"[^>]*>', out).group(0)
        assert 'data-action="group-toggle"' in button
        assert 'aria-expanded="true"' in button
        assert f'aria-controls="group-{key}"' in button
        assert f'<div id="group-{key}" data-group="{key}">' in out
    assert out.count("expand_more") == 3


async def test_renamed_and_locked_headings(client, sample_token, mock_ha_client):
    out = await _guest(client, GUEST_SEED + """
    groupHeadings = {
      light: { name: 'Lamps <b>', locked: false },
      switch: { name: 'Plugs', locked: true },
      fan: { name: 'Not on this link', locked: false },
    };
    renderAll();
    console.log(JSON.stringify(html()));
    """)
    assert _headings(out) == ["Lamps &lt;b&gt;", "Plugs", "Locks"]
    assert _toggles(out) == ["light", "lock"]
    # The locked heading is plain text: no button, no chevron, no aria state.
    assert '<h2 class="pt-5 pb-2 px-1 text-[13px] font-semibold text-muted">Plugs</h2>' in out
    assert out.count("expand_more") == 2 and out.count("aria-expanded") == 2
    assert 'aria-controls="group-switch"' not in out
    # Its cards, both domains of the section, sit under it.
    assert '<div data-group="switch">' in out
    assert "card-switch-b" in out and "card-input_boolean-c" in out
    assert "Not on this link" not in out


async def test_a_heading_is_never_hidden(client, sample_token, mock_ha_client):
    """There is no hide option. A stray "hidden" field is not one either."""
    out = await _guest(client, GUEST_SEED + """
    groupHeadings = { light: { name: '', hidden: true }, switch: { name: 'Plugs', hidden: true } };
    renderAll();
    console.log(JSON.stringify(html()));
    """)
    assert _headings(out) == ["Lights", "Plugs", "Locks"]
    assert _toggles(out) == ["light", "switch", "lock"]


async def test_click_collapses_and_expands_a_section(client, sample_token, mock_ha_client):
    out = await _guest(client, GUEST_SEED + """
    groupHeadings = {};
    renderAll();
    const body = document.getElementById('group-light');
    const shut = click('light');
    const afterShut = { expanded: shut.attrs['aria-expanded'], hidden: body.hidden,
                        collapsed: [...collapsedGroups] };
    // A re-render (a reconnect's refetch) keeps the section the way it was left.
    renderAll();
    const rendered = html();
    const open = click('light');
    console.log(JSON.stringify({
      afterShut, rendered,
      afterOpen: { expanded: open.attrs['aria-expanded'], hidden: body.hidden,
                   collapsed: [...collapsedGroups] },
    }));
    """)
    assert out["afterShut"] == {"expanded": "false", "hidden": True, "collapsed": ["light"]}
    assert '<div id="group-light" data-group="light" hidden>' in out["rendered"]
    assert re.search(r'data-group="light" aria-expanded="false"', out["rendered"])
    assert '<div id="group-switch" data-group="switch">' in out["rendered"]
    assert out["afterOpen"] == {"expanded": "true", "hidden": False, "collapsed": []}


async def test_a_locked_section_always_shows(client, sample_token, mock_ha_client):
    out = await _guest(client, GUEST_SEED + """
    groupHeadings = { switch: { name: '', locked: true } };
    // Even a stale collapsed entry (the section was locked after the guest
    // shut it) leaves the cards showing.
    collapsedGroups.add('switch');
    renderAll();
    const locked = html();
    // An unlocked section beside it still collapses and opens.
    const body = document.getElementById('group-light');
    const shut = click('light');
    const shutState = { expanded: shut.attrs['aria-expanded'], hidden: body.hidden };
    renderAll();
    const afterShut = html();
    click('light');
    console.log(JSON.stringify({ locked, shutState, afterShut, reopened: !body.hidden }));
    """)
    section = re.search(r'<div data-group="switch"[^>]*>', out["locked"]).group(0)
    assert section == '<div data-group="switch">'
    assert "switch" not in _toggles(out["locked"])
    assert out["shutState"] == {"expanded": "false", "hidden": True}
    assert '<div id="group-light" data-group="light" hidden>' in out["afterShut"]
    assert '<div data-group="switch">' in out["afterShut"]
    assert out["reopened"] is True


async def test_sections_never_touch_storage(client, sample_token, mock_ha_client):
    out = await _guest(client, GUEST_SEED + """
    storageLog.length = 0;
    groupHeadings = { light: { name: 'Lamps', locked: false } };
    renderAll();
    click('light');
    renderAll();
    click('light');
    console.log(JSON.stringify(storageLog));
    """)
    assert out == []
    page = (ROOT / "templates" / "guest_pwa.html").read_text()
    toggle = re.search(r"function toggleGroup\(button\) \{.*?\n\}", page, re.S).group(0)
    assert "Storage" not in toggle and "localStorage" not in toggle


async def test_init_and_refetch_carry_the_headings(client, sample_token, mock_ha_client):
    out = await _guest(client, """
    const html = () => document.getElementById('cards-container').innerHTML;
    const reply = headings => ({
      ok: true, status: 200, clone() { return this; },
      json: async () => ({
        entities: ['light.a'],
        states: { 'light.a': { entity_id: 'light.a', state: 'off', attributes: { friendly_name: 'Lamp' } } },
        entity_meta: {},
        group_headings: headings,
      }),
    });
    globalThis.fetch = async () => reply({ light: { name: 'First', locked: false } });
    await init();
    const first = html();
    globalThis.fetch = async () => reply({ light: { name: 'Second', locked: false } });
    await refetchState();
    const second = html();
    globalThis.fetch = async () => reply(undefined);
    await refetchState();
    console.log(JSON.stringify({ first, second, third: html() }));
    """)
    assert _headings(out["first"]) == ["First"]
    assert _headings(out["second"]) == ["Second"]
    # A reply without the field falls back to the defaults, not to the last value.
    assert _headings(out["third"]) == ["Lights"]


async def test_pending_preview_uses_the_headings(client, test_db, mock_ha_client):
    now = int(time.time())
    await db.create_token(
        label="Later", slug="later", entity_ids=["light.a", "input_boolean.b", "lock.c"],
        expires_at=now + 86400, ip_allowlist=None, starts_at=now + 3600,
        group_headings={"light": {"name": "Lamps", "locked": False},
                        "lock": {"name": "", "locked": True}},
    )
    out = await _guest(client, """
    console.log(JSON.stringify({ html: document.getElementById('cards-container').innerHTML,
                                 headings: groupHeadings }));
    """, slug="later")
    assert out["headings"]["light"]["name"] == "Lamps"
    assert _headings(out["html"]) == ["Lamps", "Switches", "Locks"]
    assert _toggles(out["html"]) == ["light", "switch"]
    assert '<div data-group="lock">' in out["html"]


# ---------------------------------------------------------------------------
# Dashboard picker
# ---------------------------------------------------------------------------

DASH_SEED = """
allEntities = [
  { entity_id: 'light.a', friendly_name: 'Lamp', domain: 'light', state: 'on' },
  { entity_id: 'switch.b', friendly_name: 'Plug', domain: 'switch', state: 'off' },
  { entity_id: 'input_boolean.c', friendly_name: 'Guest mode', domain: 'input_boolean', state: 'off' },
  { entity_id: 'lock.d', friendly_name: 'Door', domain: 'lock', state: 'locked' },
];
const els = id => document.getElementById(id);
const typeName = (container, key, value) =>
  fire('input', { dataset: { action: 'group-heading-name', container, group: key }, value, closest: () => null });
const tickLock = (container, key, checked) =>
  fire('change', { dataset: { action: 'group-heading-locked', container, group: key }, checked, closest: () => null });
const TOKEN = {
  id: 't1', slug: 'abc', label: 'Stay', entity_ids: ['light.a', 'input_boolean.c'], entity_count: 2,
  entity_meta: {}, ip_allowlist: null, country_allowlist: null, created_at: 1700000000,
  starts_at: null, expires_at: 4102444800, revoked: false, last_accessed: null,
  max_uses: null, use_count: 0, uses_remaining: null, access_windows: null,
  remember_pin: true, has_pin: false, device_binding: false, device_bound_at: null,
  show_remaining_uses: false, info_text: '', info_text_in_footer: false,
  group_headings: { switch: { name: 'Toggles', locked: false }, light: { name: '', locked: true } },
};
"""


async def test_rows_follow_the_selected_sections(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    openCreateModal();
    const empty = els('create-picker').innerHTML;
    ['lock.d', 'input_boolean.c', 'light.a', 'switch.b'].forEach(id => createPicker.selected.add(id));
    renderPicker('create-picker', createPicker);
    const full = els('create-picker').innerHTML;
    console.log(JSON.stringify({ empty, full }));
    """)
    assert "group-heading-name" not in out["empty"]
    # One row per section, in the guest page's order; input_boolean is a switch.
    rows = re.findall(
        r'data-action="group-heading-name" data-container="create-picker" data-group="([^"]+)"',
        out["full"],
    )
    assert rows == ["light", "switch", "lock"]
    assert 'placeholder="Switches"' in out["full"] and 'placeholder="Lights"' in out["full"]
    assert out["full"].count('maxlength="64"') >= 3
    assert "Section headings" in out["full"] and "allow collapsing" in out["full"]
    assert out["full"].count('data-action="group-heading-locked"') == 3
    assert "Hide heading" not in out["full"] and "group-heading-hidden" not in out["full"]


async def test_create_sends_the_headings_and_prunes(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    openCreateModal();
    els('f-label').value = 'Guest';
    ['light.a', 'input_boolean.c', 'lock.d'].forEach(id => createPicker.selected.add(id));
    renderPicker('create-picker', createPicker);
    typeName('create-picker', 'light', '  Lamps  ');
    tickLock('create-picker', 'switch', true);
    typeName('create-picker', 'lock', 'Doors');
    typeName('create-picker', 'lock', '');
    // A section that loses its last entity takes its row, and its heading, with it.
    typeName('create-picker', 'switch', 'Gone');
    pickerRemove('create-picker', 'input_boolean.c');
    replies.push({ ok: true, body: { slug: 'abc' } });
    await submitCreate();
    const create = calls.filter(c => c.url.endsWith('/admin/tokens') && c.method === 'POST').pop();
    console.log(JSON.stringify(create.body.group_headings));
    """)
    assert out == {"light": {"name": "Lamps", "locked": False}}


async def test_reopening_the_create_modal_resets_the_rows(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    openCreateModal();
    createPicker.selected.add('light.a');
    typeName('create-picker', 'light', 'Lamps');
    openCreateModal();
    console.log(JSON.stringify(createPicker.groupHeadings));
    """)
    assert out == {}


async def test_edit_loads_and_saves_the_headings(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    replies.push({ ok: true, body: TOKEN });
    await openEditModal('t1');
    const html = els('edit-picker').innerHTML;
    typeName('edit-picker', 'switch', 'Plugs');
    tickLock('edit-picker', 'light', false);
    replies.push({ ok: true, body: TOKEN });
    await submitEditEntities();
    const patch = calls.filter(c => c.url.endsWith('/admin/tokens/t1/entities')).pop();
    console.log(JSON.stringify({ html, body: patch.body }));
    """)
    assert 'value="Toggles"' in out["html"]
    assert re.search(r'<input type="checkbox" checked\s+data-action="group-heading-locked" '
                     r'data-container="edit-picker" data-group="light"', out["html"])
    assert out["body"]["group_headings"] == {"switch": {"name": "Plugs", "locked": False}}
    assert out["body"]["entity_ids"] == ["light.a", "input_boolean.c"]


async def test_duplicate_carries_the_headings(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    replies.push({ ok: true, body: TOKEN });
    await duplicateToken('t1');
    const held = JSON.parse(JSON.stringify(createPicker.groupHeadings));
    // A copy, so editing it leaves the source token's object alone.
    typeName('create-picker', 'switch', 'Changed');
    replies.push({ ok: true, body: { slug: 'abd' } });
    await submitCreate();
    const create = calls.filter(c => c.url.endsWith('/admin/tokens') && c.method === 'POST').pop();
    console.log(JSON.stringify({ held, sent: create.body.group_headings, source: TOKEN.group_headings }));
    """)
    assert out["held"] == {"switch": {"name": "Toggles", "locked": False},
                           "light": {"name": "", "locked": True}}
    assert out["sent"] == {"light": {"name": "", "locked": True},
                           "switch": {"name": "Changed", "locked": False}}
    assert out["source"]["switch"]["name"] == "Toggles"


async def test_picker_chips_match_by_group_key(client, admin_session, mock_ha_client):
    out = await _dashboard(client, admin_session, DASH_SEED + """
    const ids = filter => pickerFiltered(
      { selected: new Set(), filter, label: '', search: '', meta: {}, groupHeadings: {} }
    ).map(e => e.entity_id);
    console.log(JSON.stringify({
      chips: PICKER_DOMAINS,
      switches: ids('switch'),
      keys: ['input_boolean', 'input_button', 'input_datetime', 'light', 'nope'].map(groupKey),
    }));
    """)
    assert "input_boolean" not in out["chips"] and "switch" in out["chips"]
    assert "input_button" not in out["chips"] and "input_datetime" not in out["chips"]
    assert out["switches"] == ["switch.b", "input_boolean.c"]
    assert out["keys"] == ["switch", "button", "datetime", "light", "nope"]
