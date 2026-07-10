from datetime import datetime, timezone

import monitor
import state_store
from config import ChannelsConfig, Config, PollConfig, Secrets, TimeWindow


class FakeClient:
    """Always reports the queried date as open, with a fixed set of slot
    times spanning outside and inside a 17:00-18:00 window."""

    SLOTS = {
        "2026-08-28": [
            {"time": "2026-08-28T10:00:00-0700", "slotsAvailable": 1},
            {"time": "2026-08-28T17:30:00-0700", "slotsAvailable": 1},
        ],
        "2026-09-18": [
            {"time": "2026-09-18T10:00:00-0700", "slotsAvailable": 1},
            {"time": "2026-09-18T17:30:00-0700", "slotsAvailable": 1},
        ],
    }

    def get_month(self, year_month=None):
        return {d: True for d in self.SLOTS}

    def get_times(self, date_str):
        return self.SLOTS.get(date_str, [])


def make_cfg():
    return Config(
        target_dates=["2026-08-28", "2026-09-18"],
        time_window=TimeWindow(),  # default: any time
        time_windows={"2026-09-18": TimeWindow(start="17:00", end="18:00")},
        channels=ChannelsConfig(telegram=False, email=False),
        poll=PollConfig(),
        dry_run=True,
    )


def test_aug28_gets_any_time_sep18_gets_window_only():
    cfg = make_cfg()
    secrets = Secrets()
    state = state_store.default_state()
    client = FakeClient()
    now = datetime.now(timezone.utc)

    monitor.process_target("2026-08-28", cfg, secrets, client, state, now)
    monitor.process_target("2026-09-18", cfg, secrets, client, state, now)

    aug28_times = state["targets"]["2026-08-28"]["seen_slot_times"]
    sep18_times = state["targets"]["2026-09-18"]["seen_slot_times"]

    assert sorted(aug28_times) == sorted(
        ["2026-08-28T10:00:00-0700", "2026-08-28T17:30:00-0700"]
    ), "Aug 28 has no override -> both slots (any time) should be kept"

    assert sep18_times == ["2026-09-18T17:30:00-0700"], (
        "Sep 18 has a 17:00-18:00 override -> only the 17:30 slot should survive, "
        "the 10:00 one must be filtered out"
    )
