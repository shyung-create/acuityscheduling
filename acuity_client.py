"""Thin client for Acuity Scheduling's first-party public JSON endpoints.

Confirmed by capturing network traffic on the live booking page
(https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707) -- same
origin, no auth headers, no CORS proxy needed:

  GET /api/scheduling/v1/availability/month?owner=..&appointmentTypeId=..&calendarId=..&timezone=..[&month=YYYY-MM]
      -> {"YYYY-MM-DD": bool, ...}

  GET /api/scheduling/v1/availability/times?owner=..&appointmentTypeId=..&calendarId=..&startDate=YYYY-MM-DD&timezone=..
      -> {"YYYY-MM-DD": [{"time": "2026-07-10T17:15:00-0700", "slotsAvailable": 1}, ...]}
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

import requests

logger = logging.getLogger("haircut_alarm.acuity_client")

BASE_URL = "https://esharphair.as.me/api/scheduling/v1/availability"
USER_AGENT = "Mozilla/5.0 (compatible; haircut-availability-alarm/1.0; personal-use; monitors booking availability)"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AcuityError(Exception):
    """Base class for all client failures. Callers treat these as one poll failure."""


class AcuityHTTPError(AcuityError):
    def __init__(self, status_code: int, body_snippet: str):
        self.status_code = status_code
        self.body_snippet = body_snippet
        super().__init__(f"HTTP {status_code}: {body_snippet[:200]!r}")


class AcuitySchemaError(AcuityError):
    """Response was 200 but not the JSON shape we expect -- Acuity may have
    changed their API, or we got a bot-detection/Cloudflare HTML challenge
    page back with a 200 status."""


class AcuityClient:
    def __init__(
        self,
        owner: str,
        appointment_type_id: str,
        calendar_id: str,
        timezone: str,
        base_url: str = BASE_URL,
        timeout: float = 15,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self.owner = owner
        self.appointment_type_id = appointment_type_id
        self.calendar_id = calendar_id
        self.timezone = timezone
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    def _base_params(self) -> dict:
        return {
            "owner": self.owner,
            "appointmentTypeId": self.appointment_type_id,
            "calendarId": self.calendar_id,
            "timezone": self.timezone,
        }

    def _get(self, path: str, params: dict) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    _sleep_backoff(attempt)
                    continue
                raise AcuityError(f"network error after {attempt + 1} attempts: {exc}") from exc

            if resp.status_code == 200:
                return resp

            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                logger.warning(
                    "acuity request got HTTP %s (attempt %d/%d), backing off: %s",
                    resp.status_code, attempt + 1, self.max_retries + 1, url,
                )
                _sleep_backoff(attempt)
                continue

            # Not retryable, or retries exhausted: log the raw body once for
            # diagnosis (could be a Cloudflare/bot-detection HTML page) and
            # surface a distinct failure mode rather than crash-looping.
            snippet = resp.text[:500]
            logger.error("acuity request failed permanently: HTTP %s body=%r", resp.status_code, snippet)
            raise AcuityHTTPError(resp.status_code, snippet)

        raise AcuityError(f"exhausted retries: {last_exc}")

    def get_month(self, year_month: Optional[str] = None) -> dict[str, bool]:
        """Fetch the month-view availability map. `year_month` is "YYYY-MM";
        omitting it relies on the API's default (observed to be the current
        month) -- callers that need a specific future month should always
        pass it explicitly."""
        params = self._base_params()
        if year_month is not None:
            if not _YEAR_MONTH_RE.match(year_month):
                raise ValueError(f"year_month must look like YYYY-MM, got {year_month!r}")
            params["month"] = year_month

        resp = self._get("/month", params)
        try:
            data = resp.json()
        except ValueError as exc:
            raise AcuitySchemaError(
                f"non-JSON response from /month (possible bot-detection page): {resp.text[:300]!r}"
            ) from exc

        if not isinstance(data, dict) or not data:
            raise AcuitySchemaError(f"/month returned unexpected shape: {type(data).__name__}")
        for k, v in data.items():
            if not _DATE_RE.match(k) or not isinstance(v, bool):
                raise AcuitySchemaError(f"/month entry does not match expected schema: {k!r}: {v!r}")
        return data

    def get_times(self, date_str: str) -> list[dict]:
        if not _DATE_RE.match(date_str):
            raise ValueError(f"date_str must look like YYYY-MM-DD, got {date_str!r}")

        params = self._base_params()
        params["startDate"] = date_str

        resp = self._get("/times", params)
        try:
            data = resp.json()
        except ValueError as exc:
            raise AcuitySchemaError(
                f"non-JSON response from /times (possible bot-detection page): {resp.text[:300]!r}"
            ) from exc

        if not isinstance(data, dict):
            raise AcuitySchemaError(f"/times returned unexpected shape: {type(data).__name__}")

        slots = data.get(date_str, [])
        if not isinstance(slots, list):
            raise AcuitySchemaError(f"/times[{date_str}] is not a list: {type(slots).__name__}")
        for slot in slots:
            if not isinstance(slot, dict) or "time" not in slot or "slotsAvailable" not in slot:
                raise AcuitySchemaError(f"/times slot missing expected keys: {slot!r}")
        return slots


def _sleep_backoff(attempt: int, base: float = 1.5, cap: float = 30.0) -> None:
    delay = min(cap, base * (2**attempt))
    delay += random.uniform(0, delay * 0.25)  # jitter so we don't retry in lockstep
    time.sleep(delay)
