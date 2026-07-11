"""Client for Acuity Scheduling's real API v1 (owner-side), used by the
appointment alarm to see the account's own bookings.

Unlike acuity_client.py (which scrapes the public, unauthenticated
availability endpoints of one specific business's booking page), this talks
to https://acuityscheduling.com/api/v1/appointments with HTTP Basic Auth --
this requires an Acuity API key, available on any paid Acuity plan (webhooks
and OAuth-based API access require Premium/Powerhouse; this polling
endpoint does not).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests
from dateutil import parser as dateutil_parser

logger = logging.getLogger("appointment_alarm.client")

BASE_URL = "https://acuityscheduling.com/api/v1"
STATUS_ACTIVE = "active"
STATUS_CANCELED = "canceled"


class AcuityAppointmentsError(Exception):
    """Raised for any failure fetching appointments -- network error, auth
    failure, rate limit, or an unexpected response shape. Callers treat this
    as "skip this poll cycle", not a crash."""


@dataclass
class Appointment:
    id: str
    status: str  # STATUS_ACTIVE or STATUS_CANCELED
    start_time: datetime  # tz-aware
    client_name: str
    service_name: str


def _parse_appointment(raw: dict, status: str) -> Appointment:
    try:
        appt_id = str(raw["id"])
        start_time = dateutil_parser.isoparse(raw["datetime"])
    except (KeyError, ValueError) as exc:
        raise AcuityAppointmentsError(f"malformed appointment record: {raw!r}") from exc

    first = (raw.get("firstName") or "").strip()
    last = (raw.get("lastName") or "").strip()
    client_name = f"{first} {last}".strip() or "Unknown client"
    service_name = raw.get("type") or "appointment"

    return Appointment(
        id=appt_id,
        status=status,
        start_time=start_time,
        client_name=client_name,
        service_name=service_name,
    )


class AcuityAppointmentsClient:
    def __init__(
        self,
        user_id: str,
        api_key: str,
        base_url: str = BASE_URL,
        timeout: float = 15,
        session: Optional[requests.Session] = None,
    ):
        self.user_id = user_id
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict) -> list:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(
                url,
                params=params,
                auth=(self.user_id, self.api_key),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise AcuityAppointmentsError(f"network error calling {path}: {exc}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise AcuityAppointmentsError(f"Acuity auth failed (HTTP {resp.status_code}) -- check ACUITY_USER_ID/ACUITY_API_KEY")
        if resp.status_code != 200:
            raise AcuityAppointmentsError(f"Acuity API error: HTTP {resp.status_code}: {resp.text[:300]!r}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise AcuityAppointmentsError(f"non-JSON response from {path}: {resp.text[:300]!r}") from exc

        if not isinstance(data, list):
            raise AcuityAppointmentsError(f"expected a JSON list from {path}, got {type(data).__name__}")
        return data

    def get_appointments(self, min_date: date, max_date: date, max_results: int = 500) -> list[Appointment]:
        """Fetches all appointments (active + canceled) whose date falls in
        [min_date, max_date]. Acuity's `canceled` query param is a filter,
        not a field on every record returned unfiltered, so this issues two
        requests -- one for active, one for canceled -- and merges them.
        Assumes both fit within max_results in a single page; fine for a
        single-business 7-ish day rolling window, not built for accounts
        with hundreds of bookings per week.
        """
        base_params = {
            "minDate": min_date.isoformat(),
            "maxDate": max_date.isoformat(),
            "max": max_results,
        }

        active_raw = self._get("/appointments", {**base_params, "canceled": "false"})
        canceled_raw = self._get("/appointments", {**base_params, "canceled": "true"})

        appointments = [_parse_appointment(r, STATUS_ACTIVE) for r in active_raw]
        appointments += [_parse_appointment(r, STATUS_CANCELED) for r in canceled_raw]
        return appointments
