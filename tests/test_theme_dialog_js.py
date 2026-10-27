"""The dashboard's colour section: Auto toggles and the live preview.

Runs the shipped inline script in node, like tests/test_runtime_settings.py.
"""
import pytest

from tests.test_picker_js import _run, node

pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

_SEED = """
const colour = (value, option = value, overridden = false) => ({ value, option, overridden });
settingsState = {
  app_name: colour('Home Access'),
  brand_primary: colour('#D9523C'),
  brand_secondary: colour(''),
  brand_accent: colour('#445566', '', true),
  brand_bg: colour('#F2F0E9'),
  brand_surface: colour(''),
  brand_text: colour(''),
  brand_dark_bg: colour(''),
  brand_dark_surface: colour(''),
  brand_dark_text: colour(''),
};
// The runtime never gets to a real timer in these probes.
globalThis.fetch = async () => ({ ok: false });
"""


async def test_colour_section_groups_the_fields_and_marks_blank_ones_auto(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const html = document.getElementById('settings-fields').innerHTML;
    const input = key => html.match(new RegExp('<input id="set-' + key + '" type="color"[^>]*>'))[0];
    const auto = key => html.match(new RegExp('<input id="set-' + key + '-auto"[^>]*>'))[0];
    console.log(JSON.stringify({
      preview: html.includes('id="theme-preview"'),
      warnings: html.includes('id="theme-warnings"'),
      titles: ['Brand', 'Light mode', 'Dark mode'].map(t => html.includes('>' + t + '</h4>')),
      secondaryDisabled: input('brand_secondary').includes(' disabled'),
      secondaryAuto: auto('brand_secondary').includes(' checked'),
      accentDisabled: input('brand_accent').includes(' disabled'),
      accentAuto: auto('brand_accent').includes(' checked'),
      accentValue: input('brand_accent').includes('value="#445566"'),
      primaryHasAuto: html.includes('id="set-brand_primary-auto"'),
      bgHasAuto: html.includes('id="set-brand_bg-auto"'),
      sections: (html.match(/id="theme-preview"/g) || []).length,
    }));
    """)
    assert out["preview"] and out["warnings"]
    assert out["titles"] == [True, True, True]
    # An Auto swatch is not disabled: clicking it opens the picker.
    assert not out["secondaryDisabled"] and out["secondaryAuto"]
    assert not out["accentDisabled"] and not out["accentAuto"] and out["accentValue"]
    # The two required colours cannot be blank, so they get no Auto.
    assert not out["primaryHasAuto"] and not out["bgHasAuto"]
    assert out["sections"] == 1


async def test_collect_sends_blank_for_auto_and_a_colour_otherwise(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    const el = id => document.getElementById(id);
    const reset = () => {
      el('set-app_name').value = 'Home Access';
      el('set-brand_primary').value = '#d9523c';
      el('set-brand_bg').value = '#f2f0e9';
      for (const k of ['secondary', 'surface', 'text', 'dark_bg', 'dark_surface', 'dark_text']) {
        el('set-brand_' + k).value = '#808080';       // what an Auto picker displays
        el('set-brand_' + k + '-auto').checked = true;
      }
      el('set-brand_accent').value = '#445566';
      el('set-brand_accent-auto').checked = false;
    };
    reset();
    const untouched = collectSettings();

    el('set-brand_dark_bg-auto').checked = false;
    el('set-brand_dark_bg').value = '#0f172a';
    const custom = collectSettings();

    reset();
    el('set-brand_accent-auto').checked = true;      // hand an overridden one back
    const handedBack = collectSettings();
    console.log(JSON.stringify({ untouched, custom, handedBack }));
    """)
    assert out["untouched"] == {}
    assert out["custom"] == {"brand_dark_bg": "#0F172A"}
    assert out["handedBack"] == {"brand_accent": ""}


async def test_preview_request_carries_only_the_colours(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const el = id => document.getElementById(id);
    el('set-brand_primary').value = '#2d6a4f';
    el('set-brand_secondary-auto').checked = true;
    el('set-brand_accent-auto').checked = false;
    const values = currentThemeValues();
    console.log(JSON.stringify({ values, keys: Object.keys(values).sort() }));
    """)
    assert out["values"]["brand_primary"] == "#2D6A4F"
    assert out["values"]["brand_secondary"] == ""          # Auto
    assert out["values"]["brand_accent"] == out["values"]["brand_accent"].upper()
    assert "app_name" not in out["values"]
    assert len(out["keys"]) == 9


async def test_preview_draws_both_modes_and_fills_auto_fields(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const el = id => document.getElementById(id);
    el('set-brand_secondary-auto').checked = true;
    const sample = mode => ({
      bg: '#111111', surface: '#222222', border: '#333333', text: '#EEEEEE',
      muted: '#AAAAAA', primary: '#D9523C', on_primary: '#FFFFFF',
      accent: '#D4A373', secondary: '#4A6FA5',
    });
    drawThemePreview({
      light: sample('light'), dark: sample('dark'),
      resolved: { brand_secondary: '#4A6FA5' },
      warnings: [['dark', 'text'], ['both', 'button']],
    });
    const box = el('theme-preview').innerHTML;
    console.log(JSON.stringify({
      panels: (box.match(/border-radius|rounded-xl p-2\\.5/g) || []).length,
      hasLight: box.includes('Light mode'), hasDark: box.includes('Dark mode'),
      primary: box.includes('background:#D9523C'),
      filled: el('set-brand_secondary').value,
      warnings: el('theme-warnings').innerHTML,
    }));
    """)
    assert out["hasLight"] and out["hasDark"] and out["primary"]
    assert out["filled"] == "#4a6fa5"
    assert "Dark mode: text is hard to read" in out["warnings"]
    assert "Button text is hard to read" in out["warnings"]


async def test_preview_never_puts_a_non_hex_value_in_a_style(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const evil = '#FFF;} </style><script>alert(1)</script>';
    drawThemePreview({
      light: { bg: evil, surface: 'red', border: 'url(x)', text: '', muted: null,
               primary: '#12', on_primary: '#FFFFFF', accent: '#D4A373', secondary: '#4A6FA5' },
      dark: {}, resolved: {}, warnings: [],
    });
    console.log(JSON.stringify({ html: document.getElementById('theme-preview').innerHTML }));
    """)
    assert "<script" not in out["html"] and "url(" not in out["html"] and "alert" not in out["html"]


async def test_a_slow_reply_to_an_older_edit_does_not_overwrite_a_newer_sample(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const waiters = [];
    globalThis.fetch = () => new Promise(resolve => waiters.push(resolve));
    const reply = tag => ({ ok: true, json: async () => ({
      light: { bg: tag, surface: tag, border: tag, text: tag, muted: tag, primary: tag, on_primary: tag, accent: tag, secondary: tag },
      dark: {}, resolved: {}, warnings: [] }) });
    const first = runThemePreview();
    const second = runThemePreview();
    waiters[1](reply('#222222'));   // the newer edit answers first
    await second;
    waiters[0](reply('#111111'));   // the older one answers late
    await first;
    console.log(JSON.stringify({ html: document.getElementById('theme-preview').innerHTML }));
    """)
    assert "#222222" in out["html"] and "#111111" not in out["html"]


async def test_colors_show_a_faint_default_instead_of_the_add_on_note(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    settingsState.brand_primary = colour('#2D6A4F', '#D9523C', true);
    settingsState.brand_bg = colour('#101010', '#F2F0E9', true);
    settingsState.app_name = colour('Mine', 'Home Access', true);
    renderSettings();
    const html = document.getElementById('settings-fields').innerHTML;
    console.log(JSON.stringify({
      overriding: (html.match(/Overriding the add-on option/g) || []).length,
      defaults: html.match(/Default #[0-9A-F]{6}\\./g),
    }));
    """)
    # Only the app name, which is not a colour, still names its add-on option.
    assert out["overriding"] == 1
    assert out["defaults"] == ["Default #D9523C.", "Default #F2F0E9."]


async def test_reverting_an_optional_colour_only_moves_the_field(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    const keys = ['secondary', 'accent', 'surface', 'text', 'dark_bg', 'dark_surface', 'dark_text'].map(k => 'brand_' + k);
    for (const k of keys) settingsState[k] = colour('#445566', '', true);
    const calls = [];
    globalThis.fetch = async (url, opts) => { calls.push((opts && opts.method) || 'GET'); return { ok: false }; };
    renderSettings();
    const el = id => document.getElementById(id);
    const html = el('settings-fields').innerHTML;
    const result = {};
    calls.length = 0;
    for (const k of keys) {
      const button = html.match(new RegExp('<button[^>]*data-key="' + k + '"[^>]*>'))[0];
      resetSettingColor(k);
      result[k] = { local: button.includes('data-action="reset-color"'), auto: el('set-' + k + '-auto').checked,
        collected: collectSettings()[k] };
    }
    console.log(JSON.stringify({ result, deletes: calls.filter(m => m === 'DELETE').length }));
    """)
    # Every optional colour's Revert is the dialog-only one, not DELETE-and-reload,
    # and Save is what stores the hand-back to Auto.
    assert len(out["result"]) == 7
    for key, row in out["result"].items():
        assert row == {"local": True, "auto": True, "collected": ""}, key
    assert out["deletes"] == 0


async def test_picking_a_colour_in_settings_takes_the_field_off_auto(client, admin_session, mock_ha_client):
    out = await _run(client, admin_session, _SEED + """
    renderSettings();
    const el = id => document.getElementById(id);
    const seen = [];
    el('set-brand_secondary-label').classList = { remove: c => seen.push(c) };   // the stub's classList does nothing
    el('set-brand_secondary-auto').checked = true;       // the stub does not read the attribute
    el('set-brand_secondary').value = '#112233';
    onColorEvent({ target: { id: 'set-brand_secondary', type: 'color', dataset: {} } });
    console.log(JSON.stringify({ auto: el('set-brand_secondary-auto').checked, seen,
      collected: collectSettings().brand_secondary }));
    """)
    assert out["auto"] is False
    assert out["seen"] == ["opacity-50"]
    assert out["collected"] == "#112233"
