"""The guest page's info card: Collapse, More, and remembering which per link.

The card's script is the small nonce'd block rendered right after the card in
templates/guest_pwa.html. As in test_guest_cards_js.py the page is rendered
through the real guest route and that block is run in node against the stub
DOM, with a Map-backed localStorage swapped in, so the assertions are on the
shipped code. The markup checks at the end need no node.
"""
import json
import re
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from app import database as db
from app.config import settings
from tests.test_guest_cards_js import GUEST_STUB, _guest_script
from tests.test_picker_js import DOM_STUB, REPO_SCRIPTS, node

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(node is None, reason="node is not installed")

SLUG = "info"
KEY = f"hp-info:{SLUG}"

# Storage backed by a Map, the card's data attributes as the server renders
# them, focus calls recorded, and every timer counted.
_PRELUDE = """
const store = new Map();
const storage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: k => { store.delete(k); },
};
globalThis.window.localStorage = storage;
globalThis.localStorage = storage;
const card = document.getElementById('info-card');
card.dataset.infoHash = HASH;
card.dataset.collapsed = 'false';
const focused = [];
for (const id of ['info-collapse', 'info-more']) {
  document.getElementById(id).focus = () => focused.push(id);
}
const timers = { setTimeout: 0, setInterval: 0, requestAnimationFrame: 0 };
for (const name of Object.keys(timers)) {
  const real = globalThis[name];
  globalThis[name] = (...a) => { timers[name] += 1; return real(...a); };
}
const state = () => card.dataset.collapsed;
"""


async def _token(slug=SLUG, info_text="Door code 4821"):
    return await db.create_token(
        label=f"Token {slug}", slug=slug, entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 86400, ip_allowlist=None, info_text=info_text,
    )


def _info_script(html: str) -> str:
    blocks = [b for b in re.findall(r'<script nonce="[^"]*">(.*?)</script>', html, re.S)
              if "hp-info:" in b]
    assert len(blocks) == 1, "expected exactly one info card script"
    return blocks[0]


def _hash(html: str) -> str:
    return re.search(r'data-info-hash="([0-9a-f]{12})"', html).group(1)


def _node(parts):
    proc = subprocess.run(
        [node, "--input-type=module"], input="\n".join(parts),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def _run(client, before: str, probe: str, slug=SLUG):
    """Run the card script with `before` (storage set-up) ahead of it, then `probe`."""
    html = (await client.get(f"/g/{slug}")).text
    return _node([
        DOM_STUB, f"const HASH = {json.dumps(_hash(html))};", _PRELUDE,
        textwrap.dedent(before), _info_script(html), textwrap.dedent(probe),
    ]), _hash(html)


_REPORT = """
console.log(JSON.stringify({ state: state(), stored: store.get(KEY) ?? null,
  keys: [...store.keys()], focused, timers }));
"""


def _with_key(slug=SLUG):
    return f"const KEY = {json.dumps(f'hp-info:{slug}')};"


@pytest.fixture
async def info_token(test_db, mock_ha_client, monkeypatch):
    monkeypatch.setattr(settings, "welcome_text", "")
    return await _token()


# ---------------------------------------------------------------------------
# On load
# ---------------------------------------------------------------------------

@needs_node
async def test_no_stored_key_stays_expanded(client, info_token):
    out, _ = await _run(client, _with_key(), _REPORT)
    assert out["state"] == "false"
    assert out["keys"] == []
    assert out["focused"] == []


@needs_node
async def test_stored_matching_hash_collapses_on_load(client, info_token):
    out, _ = await _run(client, _with_key() + "store.set(KEY, HASH);", _REPORT)
    assert out["state"] == "true"
    assert out["stored"] is not None
    # Applying a remembered state is not a tap: focus stays where it was.
    assert out["focused"] == []


@needs_node
async def test_stored_stale_hash_expands_and_drops_the_key(client, info_token):
    out, _ = await _run(
        client, _with_key() + "store.set(KEY, 'aaaaaaaaaaaa'); card.dataset.collapsed = 'true';",
        _REPORT,
    )
    assert out["state"] == "false"
    assert out["keys"] == []


@needs_node
async def test_another_links_key_does_not_apply(client, info_token):
    out, _ = await _run(
        client, _with_key() + "store.set('hp-info:other', HASH);", _REPORT,
    )
    assert out["state"] == "false"
    assert out["keys"] == ["hp-info:other"]


# ---------------------------------------------------------------------------
# Taps
# ---------------------------------------------------------------------------

@needs_node
async def test_collapse_stores_the_hash_and_more_removes_it(client, info_token):
    out, page_hash = await _run(client, _with_key(), """
    collapseInfoCard();
    const shut = { state: state(), stored: store.get(KEY) ?? null, focused: [...focused] };
    expandInfoCard();
    console.log(JSON.stringify({ shut, state: state(), keys: [...store.keys()], focused, timers }));
    """)
    assert out["shut"] == {"state": "true", "stored": page_hash, "focused": ["info-more"]}
    assert out["state"] == "false"
    assert out["keys"] == []
    assert out["focused"] == ["info-more", "info-collapse"]


@needs_node
async def test_collapsing_one_link_leaves_another_alone(client, info_token):
    out, _ = await _run(client, _with_key() + "store.set('hp-info:other', 'bbbbbbbbbbbb');", """
    collapseInfoCard();
    expandInfoCard();
    console.log(JSON.stringify({ other: store.get('hp-info:other') }));
    """)
    assert out["other"] == "bbbbbbbbbbbb"


@needs_node
async def test_throwing_storage_still_toggles_in_memory(client, info_token):
    out, _ = await _run(client, _with_key() + """
    const boom = () => { throw new Error('SecurityError'); };
    storage.getItem = boom; storage.setItem = boom; storage.removeItem = boom;
    const errors = [];
    console.error = (...a) => errors.push(a.join(' '));
    console.warn = (...a) => errors.push(a.join(' '));
    """, """
    collapseInfoCard();
    const shut = state();
    expandInfoCard();
    console.log(JSON.stringify({ shut, state: state(), errors }));
    """)
    assert out == {"shut": "true", "state": "false", "errors": []}


@needs_node
async def test_missing_storage_does_not_throw(client, info_token):
    out, _ = await _run(client, _with_key() + """
    Object.defineProperty(globalThis.window, 'localStorage', {
      get() { throw new Error('denied'); },
    });
    """, """
    collapseInfoCard();
    console.log(JSON.stringify({ state: state() }));
    """)
    assert out["state"] == "true"


@needs_node
async def test_the_feature_starts_no_timers(client, info_token):
    out, _ = await _run(client, _with_key() + "store.set(KEY, HASH);", """
    collapseInfoCard();
    expandInfoCard();
    collapseInfoCard();
    """ + _REPORT)
    assert out["timers"] == {"setTimeout": 0, "setInterval": 0, "requestAnimationFrame": 0}


@needs_node
async def test_the_page_buttons_reach_the_card_script(client, info_token):
    """The page's delegated click handler dispatches both actions."""
    html = (await client.get(f"/g/{SLUG}")).text
    capture = """
    const clickHandlers = [];
    const realAdd = document.addEventListener;
    document.addEventListener = (type, fn) => { if (type === 'click') clickHandlers.push(fn); };
    """
    probe = """
    const click = id => {
      const btn = document.getElementById(id);
      btn.dataset.action = id;
      clickHandlers.forEach(fn => fn({ target: { closest: () => btn } }));
    };
    click('info-collapse');
    const shut = { state: state(), stored: store.get(KEY) ?? null };
    click('info-more');
    console.log(JSON.stringify({ shut, state: state(), keys: [...store.keys()] }));
    """
    out = _node(
        [DOM_STUB, GUEST_STUB, f"const HASH = {json.dumps(_hash(html))};", _PRELUDE,
         _with_key(), capture]
        + [(ROOT / rel).read_text() for rel in REPO_SCRIPTS]
        + [_info_script(html), _guest_script(html), textwrap.dedent(probe)]
    )
    assert out["shut"] == {"state": "true", "stored": _hash(html)}
    assert out["state"] == "false" and out["keys"] == []


# ---------------------------------------------------------------------------
# The collapsed strip
# ---------------------------------------------------------------------------

MULTI = "<b>Welcome</b> to the <i>cottage</i>\nWi-Fi password: hunter2\nCall 613-996-2000"


@needs_node
async def test_collapsed_strip_shows_line_one_and_never_line_two(client, test_db, mock_ha_client, monkeypatch):
    """The stub DOM is seeded with what the server rendered for the strip and
    the full text; the script collapses the card from storage and on a tap,
    and must leave both untouched: the strip is server-rendered, so nothing
    from line two can reach it."""
    monkeypatch.setattr(settings, "welcome_text", "")
    await _token(info_text=MULTI)
    html = (await client.get(f"/g/{SLUG}")).text
    line = re.search(r'<div id="info-card-line"[^>]*>(.*?)</div>', html, re.S).group(1)
    full = re.search(r'<div id="info-card-text"[^>]*>(.*?)</div>', html, re.S).group(1)
    seed = f"""
    document.getElementById('info-card-line').innerHTML = {json.dumps(line)};
    document.getElementById('info-card-text').innerHTML = {json.dumps(full)};
    store.set(KEY, HASH);
    """
    probe = """
    const loaded = state();
    expandInfoCard();
    collapseInfoCard();
    console.log(JSON.stringify({ loaded, state: state(),
      line: document.getElementById('info-card-line').innerHTML,
      lineText: document.getElementById('info-card-line').textContent,
      full: document.getElementById('info-card-text').innerHTML }));
    """
    out = _node([DOM_STUB, f"const HASH = {json.dumps(_hash(html))};", _PRELUDE,
                 _with_key(), textwrap.dedent(seed), _info_script(html), textwrap.dedent(probe)])
    assert out["loaded"] == "true" and out["state"] == "true"
    # The server's first line, formatting kept, untouched by the script.
    assert out["line"] == line == "<b>Welcome</b> to the <i>cottage</i>"
    assert "hunter2" not in out["line"] and "Wi-Fi" not in out["line"]
    assert out["lineText"] == ""
    # The expanded text is still all there, formatting included.
    assert out["full"] == full and "hunter2" in full and "<b>Welcome</b>" in full


# ---------------------------------------------------------------------------
# Markup
# ---------------------------------------------------------------------------

def _card(html: str) -> str:
    return re.search(r'<section id="info-card".*?</section>', html, re.S).group(0)


async def test_card_has_both_buttons(client, info_token):
    html = (await client.get(f"/g/{SLUG}")).text
    card = _card(html)
    assert 'data-collapsed="false"' in card
    for action, label, expanded in (("info-collapse", "Collapse", "true"),
                                    ("info-more", "More", "false")):
        button = re.search(rf'<button[^>]*data-action="{action}"[^>]*>(.*?)</button>', card, re.S)
        assert button, action
        assert 'aria-controls="info-card-text"' in button.group(0)
        assert f'aria-expanded="{expanded}"' in button.group(0)
        assert 'type="button"' in button.group(0)
        assert label in button.group(1)
    assert 'id="info-card-text"' in card
    assert re.search(r'<div id="info-card-line"[^>]*>Door code 4821</div>', card)
    # No inline handlers: the CSP forbids them.
    assert "onclick" not in card


async def test_buttons_are_translated(client, info_token):
    html = (await client.get(f"/g/{SLUG}", headers={"Accept-Language": "de"})).text
    card = _card(html)
    assert "Einklappen" in card and "Mehr" in card


async def test_card_script_carries_the_csp_nonce_and_slug(client, info_token):
    resp = await client.get(f"/g/{SLUG}")
    nonce = re.search(r"'nonce-([^']+)'", resp.headers["content-security-policy"]).group(1)
    assert re.search(rf'<script nonce="{re.escape(nonce)}">\s*// Per link', resp.text)
    script = _info_script(resp.text)
    assert "'hp-info:' + \"info\"" in script
    assert info_token["id"] not in resp.text


async def test_no_card_means_no_buttons_or_script(client, test_db, mock_ha_client, monkeypatch):
    monkeypatch.setattr(settings, "welcome_text", "")
    await _token(info_text="")
    html = (await client.get(f"/g/{SLUG}")).text
    assert 'data-action="info-collapse"' not in html
    assert 'data-action="info-more"' not in html
    assert "hp-info:" not in html


async def test_footer_box_has_no_buttons(client, test_db, mock_ha_client, monkeypatch):
    monkeypatch.setattr(settings, "welcome_text", "")
    await db.create_token(
        label="Token info", slug=SLUG, entity_ids=["light.living_room"],
        expires_at=int(time.time()) + 86400, ip_allowlist=None,
        info_text="Door code 4821", info_text_in_footer=True,
    )
    html = (await client.get(f"/g/{SLUG}")).text
    footer = re.search(r'<footer id="info-footer".*?</footer>', html, re.S).group(0)
    assert "<button" not in footer
    assert 'id="info-card"' not in html and "hp-info:" not in html
