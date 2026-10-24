"""The dashboard's Branding dialog: what it sends, and what it keeps.

Runs the shipped dashboard script in Node against a stub DOM, like
test_picker_js.py. Skipped when node is not installed.
"""
import pytest

from tests.test_picker_js import _run, node

pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


async def test_request_body_is_null_when_nothing_is_set(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const empty = brandingRequestBody(emptyBranding());
    const s = emptyBranding(); s.eyebrow = 'Be-Nith'; s.height = 60;
    const own = emptyBranding(); own.useGlobal = false; own.colors = { brand_primary: '#112233', brand_bg: '#FFFFFF' };
    console.log(JSON.stringify({ empty, eyebrow: brandingRequestBody(s), own: brandingRequestBody(own) }));
    """)
    assert out["empty"] is None
    assert out["eyebrow"] == {"eyebrow": "Be-Nith", "title": "", "logo_height": 60, "colors": None}
    assert out["own"]["colors"]["brand_primary"] == "#112233"


async def test_state_from_a_token_and_duplicate_source(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const s = brandingFromToken({ branding: { eyebrow: 'E', title: 'T', logo_height: 52,
      colors: { brand_primary: '#112233', brand_bg: '#FFFFFF' }, logo: { version: 'v', width: 64, height: 32 } } }, 'src-id');
    const none = brandingFromToken({ branding: { eyebrow: '', title: '', logo_height: 40, colors: null, logo: null } }, null);
    console.log(JSON.stringify({ s, none, flag: hasBranding({ branding: { eyebrow: '', title: '', colors: null, logo: null } }) }));
    """)
    assert out["s"]["hasLogo"] is True and out["s"]["copyFrom"] == "src-id"
    assert out["s"]["useGlobal"] is False and out["s"]["height"] == 52
    assert out["none"]["useGlobal"] is True and out["none"]["hasLogo"] is False
    assert out["flag"] is False


async def test_saving_a_link_patches_then_uploads_or_removes(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const calls = [];
    globalThis.fetch = async (url, opts = {}) => {
      const method = opts.method || 'GET';
      // The dashboard reloads its list after a save; that read is not under test.
      if (method !== 'GET') calls.push({ url, method, body: typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts.body ? 'form' : null) });
      return { ok: true, json: async () => (method === 'GET' ? [] : {}) };
    };
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = ' Be-Nith '; el('br-title').value = 'Welcome'; el('br-height').value = '60';
    el('br-global').checked = true;
    const state = emptyBranding(); state.hasLogo = true; state.removeLogo = true;
    brandCtx = { mode: 'edit', tokenId: 'tok1', state };
    await submitBranding();
    const withFile = emptyBranding();
    withFile.file = { name: 'a.png' };
    brandCtx = { mode: 'edit', tokenId: 'tok2', state: withFile };
    await submitBranding();
    console.log(JSON.stringify(calls));
    """)
    patch, delete, patch2, upload = out
    assert patch["method"] == "PATCH" and patch["url"].endswith("/admin/tokens/tok1/branding")
    assert patch["body"] == {"eyebrow": "Be-Nith", "title": "Welcome", "logo_height": 60, "colors": None}
    assert delete["method"] == "DELETE" and delete["url"].endswith("/admin/tokens/tok1/logo")
    assert patch2["method"] == "PATCH"
    assert upload["method"] == "POST" and upload["url"].endswith("/admin/tokens/tok2/logo") and upload["body"] == "form"


async def test_a_refused_logo_keeps_the_dialog_open_with_the_reason(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    globalThis.fetch = async (url, opts = {}) => (opts.method === 'POST'
      ? { ok: false, json: async () => ({ detail: 'That file is not a PNG or WebP image.' }) }
      : { ok: true, json: async () => ((opts.method || 'GET') === 'GET' ? [] : {}) });
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = ''; el('br-title').value = ''; el('br-height').value = '40'; el('br-global').checked = true;
    const s = emptyBranding(); s.file = { name: 'x.svg' };
    brandCtx = { mode: 'edit', tokenId: 't', state: s };
    await submitBranding();
    console.log(JSON.stringify({ err: el('branding-error').textContent, open: brandCtx !== null }));
    """)
    assert out["err"] == "That file is not a PNG or WebP image."
    assert out["open"] is True


async def test_create_mode_stages_without_calling_the_server(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    let called = false;
    globalThis.fetch = async () => { called = true; return { ok: true, json: async () => ({}) }; };
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = 'Hi'; el('br-title').value = ''; el('br-height').value = '40'; el('br-global').checked = true;
    brandCtx = { mode: 'create', tokenId: null, state: emptyBranding() };
    await submitBranding();
    console.log(JSON.stringify({ called, eyebrow: createBranding.eyebrow }));
    """)
    assert out == {"called": False, "eyebrow": "Hi"}
