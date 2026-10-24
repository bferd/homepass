"""Tests for runtime theme generation (app/theme.py) and the colour settings."""
import re
from pathlib import Path

import pytest

from app import settings_store
from app.config import settings
from app.theme import (
    DEFAULT_COLORS,
    MUTED_CONTRAST,
    TEXT_CONTRAST,
    Brand,
    brand_theme,
    contrast,
    dark_bg,
    ensure_contrast,
    palette_css,
    preview,
    resolve,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_settings():
    settings_store.reset_state()
    yield
    settings_store.reset_state()


# ── shipped palette ───────────────────────────────────────────────────────

def test_palette_css_default_colors_returns_empty():
    """Default colors produce no CSS override (the CSS file already has them)."""
    assert palette_css("#F2F0E9", "#D9523C") == ""
    assert palette_css(Brand()) == ""


def test_default_colors_mirror_input_css():
    """DEFAULT_COLORS is a copy of static/input.css; the two must not drift."""
    css = (ROOT / "static" / "input.css").read_text()
    var_for = {
        "primary": "--color-primary", "primary_hover": "--color-primary-hover",
        "on_primary": "--color-on-primary", "surface_light": "--color-surface-light",
        "surface_dark": "--color-surface-dark", "bg_light": "--color-bg-light",
        "bg_dark": "--color-bg-dark", "border_light": "--color-border-light",
        "border_dark": "--color-border-dark", "accent": "--color-accent",
        "ink": "--color-ink", "muted_light": "--color-muted", "soot": "--color-soot",
        "fg_light": "--color-fg-light", "fg_dark": "--color-fg-dark",
    }
    for key, var in var_for.items():
        m = re.search(re.escape(var) + r":\s*(\d+) (\d+) (\d+);", css)
        assert m, f"{var} missing from input.css"
        assert tuple(int(g) for g in m.groups()) == DEFAULT_COLORS[key], var
    dark_muted = re.search(r"\.dark\s*\{[^}]*--color-muted:\s*(\d+) (\d+) (\d+);", css)
    assert tuple(int(g) for g in dark_muted.groups()) == DEFAULT_COLORS["muted_dark"]


def test_tailwind_knows_the_new_colours():
    config = (ROOT / "tailwind.config.js").read_text()
    assert "--color-fg-light" in config and "--color-fg-dark" in config
    assert "--color-on-primary" in config


# ── derivation ────────────────────────────────────────────────────────────

def test_palette_css_custom_colors_returns_css():
    """Non-default colors produce a :root block with CSS variable overrides."""
    css = palette_css("#1a1a2e", "#e94560")
    assert ":root {" in css
    assert "--color-primary:" in css


@pytest.mark.parametrize("bg", ["#1A2A1A", "#0B1020", "#F2F0E9", "#FFFFFF", "#FFF4D6", "#808080", "#E8F0E8"])
@pytest.mark.parametrize("primary", ["#D9523C", "#F5D90A", "#2D6A4F", "#7B2CBF"])
def test_text_is_readable_in_both_modes(bg, primary):
    c = resolve(Brand(bg=bg, primary=primary))
    for mode in ("light", "dark"):
        page, card, text = c[f"bg_{mode}"], c[f"surface_{mode}"], c[f"fg_{mode}"]
        assert contrast(text, page) >= MUTED_CONTRAST, (mode, "text on page")
        assert contrast(text, card) >= MUTED_CONTRAST, (mode, "text on card")
        assert contrast(c[f"muted_{mode}"], page) >= 3.0, (mode, "muted on page")


@pytest.mark.parametrize("bg", ["#1A2A1A", "#F2F0E9", "#E8F0E8", "#FFF4D6"])
def test_dark_mode_has_distinct_page_card_and_border(bg):
    """The old derivation made the three near-identical blacks."""
    c = resolve(Brand(bg=bg))
    assert contrast(c["surface_dark"], c["bg_dark"]) >= 1.10
    assert contrast(c["border_dark"], c["bg_dark"]) >= 1.35
    assert c["bg_dark"] != c["surface_dark"] != c["border_dark"]


def test_dark_background_setting_gets_light_text_and_visible_cards():
    c = resolve(Brand(bg="#1A2A1A"))
    # A dark "light mode" background must not keep dark text.
    assert sum(c["fg_light"]) > 600
    assert contrast(c["fg_light"], c["bg_light"]) >= TEXT_CONTRAST
    assert contrast(c["surface_light"], c["bg_light"]) > 1.1


def test_light_background_keeps_dark_text():
    c = resolve(Brand(bg="#E8F0E8"))
    assert sum(c["fg_light"]) < 200


def test_explicit_dark_background_drives_dark_card_border_and_text():
    c = resolve(Brand(dark_bg="#0F172A"))
    assert c["bg_dark"] == (15, 23, 42)
    assert c["surface_dark"] != DEFAULT_COLORS["surface_dark"]
    assert contrast(c["surface_dark"], c["bg_dark"]) >= 1.1
    # Light mode is untouched by a dark-only setting.
    assert c["bg_light"] == DEFAULT_COLORS["bg_light"]
    assert c["surface_light"] == DEFAULT_COLORS["surface_light"]
    assert dark_bg(Brand(dark_bg="#0F172A")) == "#0F172A"
    css = palette_css(Brand(dark_bg="#0F172A"))
    assert "--color-bg-dark: 15 23 42;" in css
    assert "--color-bg-light" not in css


def test_explicit_colours_win_over_derived_ones():
    c = resolve(Brand(
        bg="#1A2A1A", secondary="#112233", accent="#445566", surface="#010203",
        text="#FAFBFC", dark_surface="#0A0B0C", dark_text="#DDEEFF",
    ))
    assert c["ink"] == (0x11, 0x22, 0x33)
    assert c["accent"] == (0x44, 0x55, 0x66)
    assert c["surface_light"] == (1, 2, 3)
    assert c["fg_light"] == (0xFA, 0xFB, 0xFC)
    assert c["surface_dark"] == (10, 11, 12)
    assert c["fg_dark"] == (0xDD, 0xEE, 0xFF)


def test_muted_follows_an_explicit_text_or_card_colour():
    base = resolve(Brand(bg="#E8F0E8"))["muted_light"]
    changed = resolve(Brand(bg="#E8F0E8", text="#000000"))["muted_light"]
    assert changed != base


def test_derived_accent_and_secondary_read_on_both_page_colours():
    c = resolve(Brand(primary="#2D6A4F"))
    for key in ("accent", "ink"):
        assert contrast(c[key], c["bg_light"]) >= 2.9
        assert contrast(c[key], c["bg_dark"]) >= 2.9
    assert c["accent"] != c["ink"]


def test_button_text_turns_dark_on_a_pale_primary():
    assert resolve(Brand())["on_primary"] == (255, 255, 255)
    assert resolve(Brand(primary="#F5D90A"))["on_primary"] != (255, 255, 255)
    assert resolve(Brand(primary="#2D6A4F"))["on_primary"] == (255, 255, 255)


def test_malformed_colours_fall_back_instead_of_raising():
    c = resolve(Brand(bg="nope", primary="#12", dark_bg="zzzzzz", text="#GGGGGG"))
    assert c["bg_light"] == DEFAULT_COLORS["bg_light"]
    assert c["primary"] == DEFAULT_COLORS["primary"]
    assert palette_css(Brand(bg="nope", dark_bg="zzzzzz")) == ""


def test_emitted_css_only_contains_numbers_and_hex():
    css = palette_css(Brand(bg="#1A2A1A", primary="#e94560", dark_bg="#101820"))
    assert re.fullmatch(r"[\s\w:;#.{}\-]*", css)
    assert "<" not in css and "url(" not in css


def test_ensure_contrast_reaches_the_ratio():
    out = ensure_contrast((120, 120, 120), (128, 128, 128), 4.5)
    assert contrast(out, (128, 128, 128)) >= 4.5 or out in ((0, 0, 0), (255, 255, 255))
    assert ensure_contrast((0, 0, 0), (255, 255, 255), 7.0) == (0, 0, 0)


def test_brand_theme_is_cached_per_colour_set():
    a = brand_theme(Brand(bg="#1A2A1A"))
    b = brand_theme(Brand(bg="#1A2A1A"))
    c = brand_theme(Brand(bg="#1A2A1A", dark_bg="#101820"))
    assert a == b and a != c


# ── preview data ──────────────────────────────────────────────────────────

def test_preview_has_a_sample_per_mode_and_resolved_colours():
    p = preview(Brand(bg="#1A2A1A"))
    assert set(p) == {"light", "dark", "resolved", "warnings"}
    for mode in ("light", "dark"):
        assert set(p[mode]) == {
            "bg", "surface", "border", "text", "muted", "primary", "on_primary",
            "accent", "secondary",
        }
        assert all(re.fullmatch(r"#[0-9A-F]{6}", v) for v in p[mode].values())
    assert set(p["resolved"]) == {
        "brand_primary", "brand_secondary", "brand_accent", "brand_bg",
        "brand_surface", "brand_text", "brand_dark_bg", "brand_dark_surface",
        "brand_dark_text",
    }
    assert p["warnings"] == []


def test_preview_warns_about_unreadable_explicit_text():
    p = preview(Brand(text="#F2F0E9"))
    assert ["light", "text"] in p["warnings"]
    p = preview(Brand(dark_text="#211411"))
    assert ["dark", "text"] in p["warnings"]


# ── settings and pages ────────────────────────────────────────────────────

async def test_new_colour_settings_save_blank_and_revert(client, admin_session):
    resp = await client.patch("/admin/settings", cookies=admin_session,
                              json={"brand_dark_bg": "#0f172a", "brand_secondary": "#112233"})
    assert resp.status_code == 200
    assert settings.brand_dark_bg == "#0F172A"
    # Blank is "Auto", equal to the option, so it is a revert rather than an override.
    resp = await client.patch("/admin/settings", cookies=admin_session,
                              json={"brand_dark_bg": ""})
    assert resp.status_code == 200
    assert settings.brand_dark_bg == ""
    assert resp.json()["settings"]["brand_dark_bg"]["overridden"] is False
    assert resp.json()["settings"]["brand_secondary"]["overridden"] is True


@pytest.mark.parametrize("body", [
    {"brand_dark_bg": "red"},
    {"brand_text": "#12345"},
    {"brand_accent": "#GGGGGG"},
    {"brand_bg": ""},          # the two required colours cannot be blank
    {"brand_primary": ""},
])
async def test_bad_colours_are_refused(client, admin_session, body):
    resp = await client.patch("/admin/settings", cookies=admin_session, json=body)
    assert resp.status_code == 422


async def test_preview_requires_admin(client):
    resp = await client.post("/admin/settings/theme-preview", json={"brand_bg": "#1A2A1A"})
    assert resp.status_code == 401


async def test_preview_resolves_unsaved_colours_and_stores_nothing(client, admin_session):
    resp = await client.post("/admin/settings/theme-preview", cookies=admin_session,
                             json={"brand_bg": "#1A2A1A", "brand_dark_bg": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["resolved"]["brand_bg"] == "#1A2A1A"
    assert data["dark"]["bg"] != data["dark"]["surface"]
    assert settings.brand_bg == settings_store._option_values["brand_bg"]


async def test_preview_rejects_unknown_fields_and_bad_values(client, admin_session):
    for body in ({"app_name": "x"}, {"brand_bg": "blue"}, {"brand_text": "#12"}):
        resp = await client.post("/admin/settings/theme-preview", cookies=admin_session, json=body)
        assert resp.status_code == 422


async def test_preview_with_no_body_uses_the_colours_in_effect(client, admin_session):
    resp = await client.post("/admin/settings/theme-preview", cookies=admin_session, json={})
    assert resp.status_code == 200
    assert resp.json()["resolved"]["brand_primary"] == settings.brand_primary


async def test_saved_dark_colours_reach_the_guest_page(client, admin_session, sample_token):
    await client.patch("/admin/settings", cookies=admin_session,
                       json={"brand_dark_bg": "#0F172A"})
    page = await client.get("/g/test-token")
    assert "--color-bg-dark: 15 23 42;" in page.text
    assert 'content="#0F172A" media="(prefers-color-scheme: dark)"' in page.text
    await client.delete("/admin/settings/brand_dark_bg", cookies=admin_session)
    page = await client.get("/g/test-token")
    assert "--color-bg-dark: 15 23 42;" not in page.text


async def test_pages_use_the_configurable_text_and_button_colours(client, sample_token):
    page = await client.get("/g/test-token")
    assert "text-fg-light dark:text-fg-dark" in page.text
    assert "text-on-primary" in page.text
    assert "dark:text-gray-100" not in page.text
