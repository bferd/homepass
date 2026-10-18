"""Which country national phone numbers in the info text are read in.

"613-996-2000" is a number in Canada and nothing at all in Spain, so linking a
phone number written without a leading + needs a default region. The admin
can set one; otherwise Home Assistant usually knows the house's country; and
failing that, the house's time zone almost always names it. With none of
those, the answer is None and only +-prefixed numbers are linked, which is
better than guessing a country and dialling the wrong one.

This module only decides. It reads no settings and makes no Home Assistant
calls: the caller passes in what it has, so the order of precedence can be
tested without either.

The time zone lookup uses zone.tab from the tzdata package, which is already
a dependency and ships the same table on every platform. A name is looked up
in zone.tab directly first, and only a name zone.tab does not list is
resolved through the link lines in tzdata.zi. The other way round gives wrong
answers: tzdata merges zones that have agreed since 1970, so Europe/Amsterdam
is a link to Europe/Brussels there, and resolving it first would make a Dutch
house Belgian.
"""
from functools import lru_cache
from importlib import resources

import phonenumbers


def valid_region(code: str | None) -> str | None:
    """An upper-cased region libphonenumber knows, or None for anything else.

    Blank, malformed and unknown codes all count as absent so the next source
    gets its turn, rather than one bad value hiding a good one below it.
    """
    if not code:
        return None
    code = code.strip().upper()
    if len(code) == 2 and code in phonenumbers.SUPPORTED_REGIONS:
        return code
    return None


def _zoneinfo_text(name: str) -> str:
    return resources.files("tzdata").joinpath("zoneinfo", name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _zone_countries() -> dict[str, str]:
    """zone.tab as {zone name: country code}. Read once per process."""
    table = {}
    for line in _zoneinfo_text("zone.tab").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3:
            table[fields[2]] = fields[0]
    return table


@lru_cache(maxsize=1)
def _zone_links() -> dict[str, str]:
    """The backward names in tzdata.zi, as {link name: target zone}."""
    links = {}
    for line in _zoneinfo_text("tzdata.zi").splitlines():
        if line.startswith("L "):
            fields = line.split()
            if len(fields) == 3:
                links[fields[2]] = fields[1]
    return links


def country_for_time_zone(time_zone: str | None) -> str | None:
    """The country a time zone belongs to, or None.

    UTC, the Etc/* zones and unknown names have no country and return None.
    """
    if not time_zone:
        return None
    countries = _zone_countries()
    code = countries.get(time_zone)
    if code is None:
        target = _zone_links().get(time_zone)
        code = countries.get(target) if target else None
    return valid_region(code)


def resolve_region(
    override: str | None, ha_country: str | None, time_zone: str | None
) -> str | None:
    """The default phone region: the admin's override, then Home Assistant's
    country, then the country of the time zone, then None."""
    return (
        valid_region(override)
        or valid_region(ha_country)
        or country_for_time_zone(time_zone)
    )
