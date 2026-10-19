import httpx
import pytest

from app import ha_client


@pytest.fixture
async def mock_http_client(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        base_url="http://homeassistant.local:8123",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(ha_client, "_client", client)
    yield requests
    await client.aclose()


async def test_fire_event_posts_to_home_assistant_event_endpoint(mock_http_client):
    result = await ha_client.fire_event("homepass_activity", {"activity": "command"})

    assert result == {"ok": True}
    assert len(mock_http_client) == 1
    request = mock_http_client[0]
    assert request.method == "POST"
    assert request.url.path == "/api/events/homepass_activity"
    assert request.read() == b'{"activity":"command"}'


async def test_fire_event_does_not_retry_failed_posts(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    client = httpx.AsyncClient(
        base_url="http://homeassistant.local:8123",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(ha_client, "_client", client)

    with pytest.raises(httpx.HTTPStatusError):
        await ha_client.fire_event("homepass_activity", {"activity": "command"})

    assert len(requests) == 1
    await client.aclose()


async def test_logbook_log_posts_to_home_assistant_service_endpoint(mock_http_client):
    result = await ha_client.logbook_log({
        "name": "HomePass",
        "message": "Guest used light.turn_on",
    })

    assert result == {"ok": True}
    assert len(mock_http_client) == 1
    request = mock_http_client[0]
    assert request.method == "POST"
    assert request.url.path == "/api/services/logbook/log"
    assert request.read() == b'{"name":"HomePass","message":"Guest used light.turn_on"}'


# ---------------------------------------------------------------------------
# /api/config: time zone and country from one cached read
# ---------------------------------------------------------------------------

class _Clock:
    """Stands in for the time module inside ha_client only.

    Patching time.monotonic itself would also move the event loop's clock;
    replacing the module reference ha_client holds keeps the fake local.
    """
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        import time
        return getattr(time, name)


@pytest.fixture
async def ha_config(monkeypatch):
    """A fake HA answering /api/config with whatever `state` says.

    `state["body"]` is the JSON sent back (raw bytes are sent as they are);
    `state["status"]` the HTTP status, or None to fail at the network level.
    """
    state = {"body": {"time_zone": "America/Toronto", "country": "CA"}, "status": 200, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/config"
        state["calls"] += 1
        if state["status"] is None:
            raise httpx.ConnectError("down", request=request)
        if isinstance(state["body"], bytes):
            return httpx.Response(state["status"], content=state["body"])
        return httpx.Response(state["status"], json=state["body"])

    client = httpx.AsyncClient(
        base_url="http://homeassistant.local:8123",
        transport=httpx.MockTransport(handler),
    )
    clock = _Clock()
    monkeypatch.setattr(ha_client, "_client", client)
    monkeypatch.setattr(ha_client, "time", clock)
    monkeypatch.setattr(ha_client, "_time_zone", None)
    monkeypatch.setattr(ha_client, "_time_zone_next_read", 0.0)
    monkeypatch.setattr(ha_client, "_country", None)
    state["clock"] = clock
    yield state
    await client.aclose()


async def test_one_request_serves_zone_and_country(ha_config):
    assert await ha_client.get_time_zone() == "America/Toronto"
    assert await ha_client.get_country() == "CA"
    assert ha_config["calls"] == 1


async def test_country_first_also_fills_the_zone(ha_config):
    assert await ha_client.get_country() == "CA"
    assert await ha_client.get_time_zone() == "America/Toronto"
    assert ha_config["calls"] == 1


async def test_lower_case_country_is_upper_cased(ha_config):
    ha_config["body"]["country"] = " ca "
    assert await ha_client.get_country() == "CA"


@pytest.mark.parametrize("value", [None, "", "Canada", 12, ["CA"], "C1", "É1"])
async def test_anything_but_a_two_letter_code_is_no_country(ha_config, value):
    ha_config["body"]["country"] = value
    assert await ha_client.get_country() is None
    # A bad country does not cost the zone.
    assert await ha_client.get_time_zone() == "America/Toronto"


async def test_missing_country_key_is_no_country(ha_config):
    del ha_config["body"]["country"]
    assert await ha_client.get_country() is None


async def test_cache_window_is_respected(ha_config):
    await ha_client.get_country()
    ha_config["body"] = {"time_zone": "Europe/Madrid", "country": "ES"}
    ha_config["clock"].now += ha_client.TIME_ZONE_CACHE_TTL - 1
    assert await ha_client.get_country() == "CA"
    assert await ha_client.get_time_zone() == "America/Toronto"
    assert ha_config["calls"] == 1

    ha_config["clock"].now += 1
    assert await ha_client.get_country() == "ES"
    assert await ha_client.get_time_zone() == "Europe/Madrid"
    assert ha_config["calls"] == 2


@pytest.mark.parametrize("failure", ["network", "status", "garbled", "not_an_object"])
async def test_failed_read_keeps_the_last_good_values(ha_config, failure):
    await ha_client.get_country()
    ha_config["clock"].now += ha_client.TIME_ZONE_CACHE_TTL
    if failure == "network":
        ha_config["status"] = None
    elif failure == "status":
        ha_config["status"] = 500
    elif failure == "garbled":
        ha_config["body"] = b"<html>502 Bad Gateway</html>"
    else:
        ha_config["body"] = ["not", "a", "config"]

    assert await ha_client.get_country() == "CA"
    assert await ha_client.get_time_zone() == "America/Toronto"
    assert ha_config["calls"] == 2


async def test_retry_delay_applies_after_a_failure(ha_config):
    await ha_client.get_country()
    ha_config["clock"].now += ha_client.TIME_ZONE_CACHE_TTL
    ha_config["status"] = 500
    await ha_client.get_country()
    assert ha_config["calls"] == 2

    # HA is back, but the retry is not due yet.
    ha_config["status"] = 200
    ha_config["body"] = {"time_zone": "Europe/Madrid", "country": "ES"}
    ha_config["clock"].now += ha_client.TIME_ZONE_RETRY_SECONDS - 1
    assert await ha_client.get_country() == "CA"
    assert ha_config["calls"] == 2

    # Once it is, one successful read updates both.
    ha_config["clock"].now += 1
    assert await ha_client.get_country() == "ES"
    assert await ha_client.get_time_zone() == "Europe/Madrid"
    assert ha_config["calls"] == 3


async def test_cleared_country_replaces_the_cached_one(ha_config):
    assert await ha_client.get_country() == "CA"
    ha_config["clock"].now += ha_client.TIME_ZONE_CACHE_TTL
    ha_config["body"]["country"] = None
    assert await ha_client.get_country() is None
    assert await ha_client.get_time_zone() == "America/Toronto"
