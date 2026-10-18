"""Default phone region (app/region.py).

The precedence is override, then Home Assistant's country, then the time
zone's country. The time zone cases include backward names that zone.tab does
not list, and zones that tzdata links across a border, which must still give
their own country.
"""
import pytest

from app.region import country_for_time_zone, resolve_region


@pytest.mark.parametrize("zone, country", [
    # Linked to Europe/Brussels in tzdata.zi: zone.tab must win.
    ("Europe/Amsterdam", "NL"),
    # Backward names zone.tab does not list, resolved through the links.
    ("US/Eastern", "US"),
    ("Asia/Calcutta", "IN"),
    ("America/Montreal", "CA"),
    ("Canada/Pacific", "CA"),
    ("America/Toronto", "CA"),
])
def test_time_zone_gives_its_country(zone, country):
    assert country_for_time_zone(zone) == country
    assert resolve_region(None, None, zone) == country


@pytest.mark.parametrize("zone", ["UTC", "Etc/UTC", "Etc/GMT", "Nowhere/Atlantis", "", None])
def test_zones_without_a_country_give_none(zone):
    assert resolve_region(None, None, zone) is None


def test_override_beats_ha_country():
    assert resolve_region("ES", "CA", "Europe/Amsterdam") == "ES"


def test_ha_country_beats_time_zone():
    assert resolve_region(None, "CA", "Europe/Amsterdam") == "CA"


def test_codes_are_upper_cased():
    assert resolve_region("es", None, None) == "ES"
    assert resolve_region(None, "ca", None) == "CA"


@pytest.mark.parametrize("bad", ["", "   ", "X", "XX", "CAN", "1A", "ZZ"])
def test_invalid_override_is_ignored(bad):
    assert resolve_region(bad, "CA", "Europe/Amsterdam") == "CA"


@pytest.mark.parametrize("bad", ["", "XX", "CAN", "ZZ"])
def test_invalid_ha_country_is_ignored(bad):
    assert resolve_region(None, bad, "Europe/Amsterdam") == "NL"


def test_nothing_known_gives_none():
    assert resolve_region(None, None, None) is None
    assert resolve_region("XX", "ZZ", "UTC") is None
