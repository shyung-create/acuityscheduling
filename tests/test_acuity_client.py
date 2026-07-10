import json

import pytest

import acuity_client as acuity_client_mod
from acuity_client import AcuityClient, AcuityError, AcuityHTTPError, AcuitySchemaError


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(acuity_client_mod.time, "sleep", lambda *_a, **_k: None)


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text if text else (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON")
        return self._json_data


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.last_params = None
        self.all_params = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        self.last_params = params
        self.all_params.append(params)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def make_client(session):
    return AcuityClient(
        owner="dc1e29cb",
        appointment_type_id="82222707",
        calendar_id="any",
        timezone="America/Los_Angeles",
        session=session,
        max_retries=2,
    )


def test_get_month_success():
    data = {"2026-09-25": True, "2026-09-26": True}
    session = FakeSession([FakeResponse(200, data)])
    client = make_client(session)
    result = client.get_month("2026-09")
    assert result == data


def test_get_month_sends_full_date_not_bare_year_month():
    # Regression test: the live esharphair.as.me API returns HTTP 422
    # ("must not be before the current month") for a bare "YYYY-MM" month
    # param -- it needs a full "YYYY-MM-DD" date. Confirmed against the real
    # API by a user running this outside the network-restricted dev sandbox.
    session = FakeSession([FakeResponse(200, {"2026-09-01": True})])
    client = make_client(session)
    client.get_month("2026-09")
    assert session.last_params["month"] == "2026-09-01"


def test_get_month_rejects_bad_year_month():
    client = make_client(FakeSession([]))
    with pytest.raises(ValueError):
        client.get_month("2026/09")


def test_get_month_schema_error_on_non_dict():
    session = FakeSession([FakeResponse(200, ["not", "a", "dict"])])
    client = make_client(session)
    with pytest.raises(AcuitySchemaError):
        client.get_month("2026-09")


def test_get_month_schema_error_on_bad_value_type():
    session = FakeSession([FakeResponse(200, {"2026-09-25": "yes"})])
    client = make_client(session)
    with pytest.raises(AcuitySchemaError):
        client.get_month("2026-09")


def test_get_month_schema_error_on_html_bot_challenge_page():
    session = FakeSession([FakeResponse(200, None, text="<html>Attention Required! Cloudflare</html>")])
    client = make_client(session)
    with pytest.raises(AcuitySchemaError):
        client.get_month("2026-09")


def test_get_times_success_returns_only_requested_date():
    data = {
        "2026-09-26": [{"time": "2026-09-26T10:00:00-0700", "slotsAvailable": 1}],
        "2026-09-27": [{"time": "2026-09-27T10:00:00-0700", "slotsAvailable": 1}],
    }
    session = FakeSession([FakeResponse(200, data)])
    client = make_client(session)
    slots = client.get_times("2026-09-26")
    assert slots == data["2026-09-26"]


def test_get_times_empty_when_date_absent():
    session = FakeSession([FakeResponse(200, {})])
    client = make_client(session)
    assert client.get_times("2026-09-26") == []


def test_get_times_schema_error_missing_keys():
    session = FakeSession([FakeResponse(200, {"2026-09-26": [{"time": "2026-09-26T10:00:00-0700"}]})])
    client = make_client(session)
    with pytest.raises(AcuitySchemaError):
        client.get_times("2026-09-26")


def test_429_retries_then_succeeds():
    data = {"2026-09-25": True}
    session = FakeSession([FakeResponse(429, text="rate limited"), FakeResponse(200, data)])
    client = make_client(session)
    result = client.get_month("2026-09")
    assert result == data
    assert session.calls == 2


def test_non_retryable_403_raises_http_error_immediately():
    session = FakeSession([FakeResponse(403, text="Forbidden")])
    client = make_client(session)
    with pytest.raises(AcuityHTTPError) as exc_info:
        client.get_month("2026-09")
    assert exc_info.value.status_code == 403
    assert session.calls == 1


def test_exhausts_retries_and_raises():
    session = FakeSession([FakeResponse(503, text="down")] * 3)
    client = make_client(session)
    with pytest.raises(AcuityHTTPError):
        client.get_month("2026-09")
    assert session.calls == 3  # max_retries=2 -> 3 total attempts
