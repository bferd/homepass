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
    assert out["eyebrow"] == {
        "eyebrow": "Be-Nith", "title": "", "logo_height": 60,
        "title_size": 16, "eyebrow_size": 12, "colors": None,
    }
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
    # The text goes first and quietly when a logo call follows; the logo call is
    # the one that tells open guest pages to reload.
    assert patch["method"] == "PATCH" and patch["url"].endswith("/admin/tokens/tok1/branding?notify=false")
    assert patch["body"] == {
        "eyebrow": "Be-Nith", "title": "Welcome", "logo_height": 60,
        "title_size": 16, "eyebrow_size": 12, "colors": None,
    }
    assert delete["method"] == "DELETE" and delete["url"].endswith("/admin/tokens/tok1/logo")
    assert patch2["method"] == "PATCH" and patch2["url"].endswith("/admin/tokens/tok2/branding?notify=false")
    assert upload["method"] == "POST" and upload["url"].endswith("/admin/tokens/tok2/logo") and upload["body"] == "form"


async def test_text_only_save_notifies_from_the_patch(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const calls = [];
    globalThis.fetch = async (url, opts = {}) => {
      const method = opts.method || 'GET';
      if (method !== 'GET') calls.push(url);
      return { ok: true, json: async () => (method === 'GET' ? [] : {}) };
    };
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = 'Hi'; el('br-title').value = ''; el('br-height').value = '40'; el('br-global').checked = true;
    brandCtx = { mode: 'edit', tokenId: 'tok3', state: emptyBranding() };
    await submitBranding();
    console.log(JSON.stringify(calls));
    """)
    assert len(out) == 1 and out[0].endswith("/admin/tokens/tok3/branding")


async def test_a_refused_logo_keeps_the_dialog_open_and_still_tells_guest_pages(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const calls = [];
    globalThis.fetch = async (url, opts = {}) => {
      const method = opts.method || 'GET';
      if (method !== 'GET') calls.push(method + ' ' + url);
      return method === 'POST'
        ? { ok: false, json: async () => ({ detail: 'That file is not a PNG or WebP image.' }) }
        : { ok: true, json: async () => (method === 'GET' ? [] : {}) };
    };
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = ''; el('br-title').value = ''; el('br-height').value = '40'; el('br-global').checked = true;
    const s = emptyBranding(); s.file = { name: 'x.svg' };
    brandCtx = { mode: 'edit', tokenId: 't', state: s };
    await submitBranding();
    console.log(JSON.stringify({ err: el('branding-error').textContent, open: brandCtx !== null, calls }));
    """)
    assert out["err"] == "That file is not a PNG or WebP image."
    assert out["open"] is True
    # The text went quietly, the upload failed, so the notice is sent by the last call.
    assert out["calls"][-1].endswith("/admin/tokens/t/branding?force=true")


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


async def test_revert_on_primary_and_background(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const el = id => document.getElementById(id);
    // A link's dialog goes back to the global colour.
    brandGlobal = { brand_primary: '#D9523C', brand_bg: '#F2F0E9' };
    el('br-c-brand_primary').value = '#000000';
    await resetBrandColor('brand_primary');
    // Settings goes back to the built-in colour, whatever the add-on option is.
    settingsState.brand_bg = { value: '#101010', option: '#112233', overridden: true };
    el('set-brand_bg').value = '#101010';
    resetSettingColor('brand_bg');
    console.log(JSON.stringify({ link: el('br-c-brand_primary').value, setting: el('set-brand_bg').value }));
    """)
    assert out == {"link": "#d9523c", "setting": "#f2f0e9"}


async def test_required_colour_rows_offer_revert_and_auto_greys_the_label(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const f = SETTINGS_FIELDS.find(x => x.key === 'brand_primary');
    const g = SETTINGS_FIELDS.find(x => x.key === 'brand_secondary');
    const primary = settingRowHtml(f, { value: '#D9523C', option: '#D9523C', overridden: false });
    const secondary = settingRowHtml(g, { value: '', option: '', overridden: false });
    const form = brandingFormHtml(emptyBranding());
    console.log(JSON.stringify({
      primaryRevert: primary.includes('data-action="reset-color"'),
      secondaryRevert: secondary.includes('data-action="reset-color"'),
      labelGreyed: /id="set-brand_secondary-label"[^>]*opacity-50/.test(secondary),
      swatchGreyed: /type="color"[^>]*opacity-60/.test(secondary),
      linkRevert: form.includes('data-action="reset-brand-color" data-key="brand_primary"'),
      linkAutoLabel: /id="br-l-brand_secondary"[^>]*opacity-50/.test(form),
    }));
    """)
    assert out == {
        "primaryRevert": True, "secondaryRevert": False, "labelGreyed": True,
        "swatchGreyed": False, "linkRevert": True, "linkAutoLabel": True,
    }


async def test_built_in_colours_match_the_defaults(client, admin_session, mock_ha_client):
    from app.config import Settings
    out = await _run(client, admin_session, "console.log(JSON.stringify(BUILT_IN_COLORS));")
    assert out == {
        "brand_primary": Settings.model_fields["brand_primary"].default,
        "brand_bg": Settings.model_fields["brand_bg"].default,
    }


async def test_text_size_sliders_are_read_and_sent(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const el = id => document.getElementById(id);
    el('br-eyebrow').value = 'E'; el('br-title').value = 'T'; el('br-height').value = '40';
    el('br-title-size').value = '24'; el('br-eyebrow-size').value = '15'; el('br-global').checked = true;
    brandCtx = { mode: 'edit', tokenId: 't', state: emptyBranding() };
    const s = readBrandingForm();
    const fromToken = brandingFromToken({ branding: { title_size: 20, eyebrow_size: 11 } }, null);
    console.log(JSON.stringify({ body: brandingRequestBody(s), titleSize: fromToken.titleSize, eyebrowSize: fromToken.eyebrowSize,
      form: brandingFormHtml(fromToken).includes('id="br-title-size" type="range" min="14" max="28"') }));
    """)
    assert out["body"]["title_size"] == 24 and out["body"]["eyebrow_size"] == 15
    assert (out["titleSize"], out["eyebrowSize"], out["form"]) == (20, 11, True)


async def test_link_revert_works_when_the_link_has_its_own_colours(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    // A link with colours of its own: nothing has asked for the global ones yet.
    brandGlobal = {};
    globalThis.fetch = async (url, opts = {}) => ({
      ok: true,
      json: async () => ({ resolved: { brand_primary: '#D9523C', brand_bg: '#F2F0E9' } }),
    });
    const el = id => document.getElementById(id);
    el('br-c-brand_bg').value = '#000000';
    await resetBrandColor('brand_bg');
    console.log(JSON.stringify({ bg: el('br-c-brand_bg').value }));
    """)
    assert out == {"bg": "#f2f0e9"}


async def test_picking_a_colour_in_a_link_takes_the_field_off_auto(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    brandCtx = { state: Object.assign(emptyBranding(), { useGlobal: false }) };
    document.getElementById('branding-body').innerHTML = brandingFormHtml(brandCtx.state);
    scheduleBrandPreview = () => {};
    const el = id => document.getElementById(id);
    const seen = [];
    el('br-l-brand_accent').classList = { remove: c => seen.push(c) };           // the stub's classList does nothing
    el('br-a-brand_accent').checked = true;               // the stub does not read the attribute
    const disabled = /id="br-c-brand_accent"[^>]*disabled/.test(document.getElementById('branding-body').innerHTML);
    el('br-c-brand_accent').value = '#112233';
    onBrandEvent({ type: 'input', target: { id: 'br-c-brand_accent', type: 'color', dataset: {}, closest: () => true } });
    console.log(JSON.stringify({ disabled, auto: el('br-a-brand_accent').checked, seen,
      read: readBrandingForm().colors.brand_accent }));
    """)
    assert out["disabled"] is False                        # an Auto swatch can be clicked
    assert out["auto"] is False and out["seen"] == ["opacity-50"]
    assert out["read"] == "#112233"


async def test_current_logo_is_shown_in_the_dialog(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, """
    const tok = { id: 'abc', branding: { eyebrow: '', title: '', logo_height: 40, colors: null,
      logo: { version: 'v1', width: 64, height: 32 } } };
    const own = brandingFromToken(tok, null);
    const dup = brandingFromToken(tok, 'src id');
    const none = brandingFromToken({ id: 'x', branding: { eyebrow: '', title: '', logo_height: 40, colors: null, logo: null } }, null);
    const picked = emptyBranding(); picked.fileUrl = 'data:image/png;base64,AAAA'; picked.logoUrl = '/old';
    console.log(JSON.stringify({ own: brandLogoSrc(own), dup: brandLogoSrc(dup), none: brandLogoSrc(none),
      picked: brandLogoSrc(picked) }));
    """)
    assert out["own"].endswith("/admin/tokens/abc/logo?v=v1")
    assert "/admin/tokens/src%20id/logo?v=v1" in out["dup"]
    assert not out["none"]
    assert out["picked"].startswith("data:image/png")
