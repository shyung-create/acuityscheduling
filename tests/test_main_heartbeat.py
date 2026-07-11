import os
from datetime import datetime

import main


def test_write_heartbeat_creates_file_with_parseable_timestamp(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    main.write_heartbeat(path)

    assert os.path.exists(path)
    with open(path) as fh:
        contents = fh.read().strip()
    datetime.fromisoformat(contents)  # raises if not a valid ISO timestamp


def test_write_heartbeat_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    main.write_heartbeat(path)
    with open(path) as fh:
        first = fh.read()

    main.write_heartbeat(path)
    with open(path) as fh:
        second = fh.read()

    # Both are valid timestamps; the point is the second call succeeds and
    # leaves no leftover .tmp file (atomic replace, not a plain append/write).
    datetime.fromisoformat(first.strip())
    datetime.fromisoformat(second.strip())
    assert not os.path.exists(path + ".tmp")


def test_write_heartbeat_leaves_no_tmp_file_on_success(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    main.write_heartbeat(path)
    assert not os.path.exists(path + ".tmp")
