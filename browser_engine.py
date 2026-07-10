"""Fallback engine that drives the real booking page with a headless browser
and intercepts the same two JSON responses AcuityClient calls directly.

Only needed if sustained plain-`requests` polling gets blocked by
bot-detection (Cloudflare challenge, etc). Same method signatures as
AcuityClient (get_month/get_times) so monitor.py can swap engines via
--engine=browser without touching orchestration logic.

Requires the `playwright` extra: pip install -r requirements-browser.txt && playwright install chromium
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from acuity_client import AcuityError, AcuitySchemaError, BASE_URL, USER_AGENT

logger = logging.getLogger("haircut_alarm.browser_engine")

BOOKING_PAGE_URL = "https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707"


class BrowserAcuityClient:
    def __init__(
        self,
        owner: str,
        appointment_type_id: str,
        calendar_id: str,
        timezone: str,
        booking_page_url: str = BOOKING_PAGE_URL,
        base_url: str = BASE_URL,
        timeout: float = 30,
    ):
        self.owner = owner
        self.appointment_type_id = appointment_type_id
        self.calendar_id = calendar_id
        self.timezone = timezone
        self.booking_page_url = booking_page_url
        self.base_url = base_url
        self.timeout_ms = timeout * 1000

    def _base_params(self) -> dict:
        return {
            "owner": self.owner,
            "appointmentTypeId": self.appointment_type_id,
            "calendarId": self.calendar_id,
            "timezone": self.timezone,
        }

    def _fetch_via_browser(self, path: str, params: dict) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AcuityError(
                "playwright is not installed; run `pip install -r requirements-browser.txt "
                "&& playwright install chromium` to use --engine=browser"
            ) from exc

        target_url = f"{self.base_url}{path}?{urlencode(params)}"
        result: dict = {}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)

                def handle_response(response):
                    if response.url.startswith(f"{self.base_url}{path}") and response.status == 200:
                        try:
                            result["json"] = response.json()
                        except Exception:  # noqa: BLE001 - best effort capture
                            pass

                page.on("response", handle_response)
                page.goto(self.booking_page_url, wait_until="networkidle")

                # The page itself only fires the requests we need in response
                # to real navigation/date-picker interaction; requesting the
                # endpoint URL directly inside the authenticated browser
                # session is simpler and gets us the same JSON.
                resp = page.request.get(target_url, headers={"Accept": "application/json"})
                if resp.status != 200:
                    body = resp.text()
                    raise AcuityError(f"browser engine got HTTP {resp.status}: {body[:300]!r}")
                result["json"] = resp.json()
            finally:
                browser.close()

        if "json" not in result:
            raise AcuityError(f"browser engine never captured a response for {path}")
        return result["json"]

    def get_month(self, year_month: Optional[str] = None) -> dict[str, bool]:
        params = self._base_params()
        if year_month is not None:
            params["month"] = year_month
        data = self._fetch_via_browser("/month", params)
        if not isinstance(data, dict) or not data:
            raise AcuitySchemaError(f"/month returned unexpected shape: {type(data).__name__}")
        return data

    def get_times(self, date_str: str) -> list[dict]:
        params = self._base_params()
        params["startDate"] = date_str
        data = self._fetch_via_browser("/times", params)
        if not isinstance(data, dict):
            raise AcuitySchemaError(f"/times returned unexpected shape: {type(data).__name__}")
        return data.get(date_str, [])
