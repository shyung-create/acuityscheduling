import json
from datetime import date

import pytest

from appointment_client import (
    STATUS_ACTIVE,
    STATUS_CANCELED,
    AcuityAppointmentsClient,
    AcuityAppointmentsError,
)


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
        self.calls = []

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "auth": auth})
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


ACTIVE_FIXTURE = [
    {
        "id": 101,
        "firstName": "Jane",
        "lastName": "Doe",
        "type": "Haircut",
        "datetime": "2026-07-16T10:00:00-0400",
        "canceled": False,
    },
]

CANCELED_FIXTURE = [
    {
        "id": 202,
        "firstName": "John",
        "lastName": "Smith",
        "type": "Beard Trim",
        "datetime": "2026-07-17T14:30:00-0400",
        "canceled": True,
    },
]


def make_client(session):
    return AcuityAppointmentsClient(user_id="12345", api_key="secret-key", session=session)


def test_get_appointments_merges_active_and_canceled():
    session = FakeSession([FakeResponse(200, ACTIVE_FIXTURE), FakeResponse(200, CANCELED_FIXTURE)])
    client = make_client(session)
    appointments = client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))

    assert len(appointments) == 2
    active = next(a for a in appointments if a.id == "101")
    canceled = next(a for a in appointments if a.id == "202")
    assert active.status == STATUS_ACTIVE
    assert active.client_name == "Jane Doe"
    assert active.service_name == "Haircut"
    assert canceled.status == STATUS_CANCELED


def test_get_appointments_sends_basic_auth():
    session = FakeSession([FakeResponse(200, []), FakeResponse(200, [])])
    client = make_client(session)
    client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))
    assert session.calls[0]["auth"] == ("12345", "secret-key")


def test_get_appointments_sends_min_max_date_and_canceled_filter():
    session = FakeSession([FakeResponse(200, []), FakeResponse(200, [])])
    client = make_client(session)
    client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))
    active_params, canceled_params = session.calls[0]["params"], session.calls[1]["params"]
    assert active_params["minDate"] == "2026-07-15"
    assert active_params["maxDate"] == "2026-07-22"
    assert active_params["canceled"] == "false"
    assert canceled_params["canceled"] == "true"


def test_auth_failure_raises_clear_error():
    session = FakeSession([FakeResponse(401, text="Unauthorized")])
    client = make_client(session)
    with pytest.raises(AcuityAppointmentsError, match="auth failed"):
        client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))


def test_server_error_raises():
    session = FakeSession([FakeResponse(500, text="oops")])
    client = make_client(session)
    with pytest.raises(AcuityAppointmentsError):
        client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))


def test_non_list_response_raises_schema_error():
    session = FakeSession([FakeResponse(200, {"not": "a list"})])
    client = make_client(session)
    with pytest.raises(AcuityAppointmentsError):
        client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))


def test_missing_client_name_falls_back():
    fixture = [{"id": 5, "type": "Haircut", "datetime": "2026-07-16T10:00:00-0400", "canceled": False}]
    session = FakeSession([FakeResponse(200, fixture), FakeResponse(200, [])])
    client = make_client(session)
    appointments = client.get_appointments(date(2026, 7, 15), date(2026, 7, 22))
    assert appointments[0].client_name == "Unknown client"
