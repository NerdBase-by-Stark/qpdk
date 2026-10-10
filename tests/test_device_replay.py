"""qpdk.testing.device_replay — fixture loading + replay matching."""
import json

import pytest

from qpdk.mock_runtime.ssh import MockSsh
from qpdk.testing.device_replay import (
    DeviceRecording, ReplayDevice, UnrecordedCommandError, load_bank,
)


def _fixture(exchanges) -> DeviceRecording:
    return DeviceRecording({
        "format": "qsys-device-recording/1",
        "device": {"vendor": "cisco", "product": "Room Bar",
                   "firmware": "ce26.3.1.3", "transport": "ssh-cli"},
        "exchanges": exchanges,
    })


STANDBY = {"type": "cmd", "tx": "xStatus Standby",
           "rx": "*s Standby State: Off\n** end\n\nOK\n", "elapsed_ms": 1000}


def test_rejects_wrong_format():
    with pytest.raises(ValueError):
        DeviceRecording({"format": "something-else/9"})


def test_fifo_then_last_repeats():
    rec = _fixture([
        {"type": "cmd", "tx": "xStatus Standby", "rx": "first", "elapsed_ms": 1},
        {"type": "cmd", "tx": "xStatus Standby", "rx": "second", "elapsed_ms": 1},
    ])
    dev = ReplayDevice([rec])
    assert dev.response_for("xStatus Standby") == "first"
    assert dev.response_for("xStatus Standby") == "second"
    # steady-state polling: last recorded response repeats
    assert dev.response_for("xStatus Standby") == "second"


def test_overrides_win():
    dev = ReplayDevice([_fixture([STANDBY])],
                       overrides={"xStatus Standby": "*s Standby State: Halfwake\n** end\nOK\n"})
    assert "Halfwake" in dev.response_for("xStatus Standby")


def test_strict_raises_on_unrecorded():
    dev = ReplayDevice([_fixture([STANDBY])])
    sock = MockSsh()
    sock.simulate_connect()
    sock.Write("xStatus Audio Volume\n")
    with pytest.raises(UnrecordedCommandError):
        dev.pump(sock)


def test_lenient_records_unmatched_and_stays_silent():
    dev = ReplayDevice([_fixture([STANDBY])], strict=False)
    sock = MockSsh()
    sock.simulate_connect()
    sock.Write("xStatus Audio Volume\n")
    assert dev.pump(sock) == 0
    assert dev.unmatched == ["xStatus Audio Volume"]
    assert sock.Read() == ""


def test_pump_answers_into_socket_buffer():
    dev = ReplayDevice([_fixture([STANDBY])])
    sock = MockSsh()
    sock.simulate_connect()
    sock.Write("xStatus Standby\n")
    assert dev.pump(sock) == 1
    assert sock.Read() == "*s Standby State: Off\n** end\n\nOK\n"
    assert dev.answered == ["xStatus Standby"]


def test_pump_handles_split_and_batched_writes():
    dev = ReplayDevice([_fixture([STANDBY, {"type": "cmd", "tx": "echo off",
                                            "rx": "OK\n", "elapsed_ms": 1}])])
    sock = MockSsh()
    sock.simulate_connect()
    sock.Write("echo of")          # split mid-command
    assert dev.pump(sock) == 0     # no complete line yet
    sock.Write("f\nxStatus Standby\n")  # completion + second command batched
    assert dev.pump(sock) == 2
    assert dev.answered == ["echo off", "xStatus Standby"]


def test_load_bank_skips_non_fixtures(tmp_path):
    good = tmp_path / "a.json"
    good.write_text(json.dumps({"format": "qsys-device-recording/1",
                                "device": {}, "exchanges": [STANDBY]}))
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"other": True}))
    bank = load_bank(tmp_path)
    assert len(bank) == 1


def test_real_converted_fixture_loads_if_present():
    """Sanity link to an optional recorded-fixture bank (skips when absent)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "device-recordings" / "cisco-room-bar"
    if not root.is_dir():
        pytest.skip("optional device-recording fixtures not on disk")
    bank = load_bank(root)
    assert bank, "fixture bank present but empty"
    rec = bank[0]
    assert rec.device.get("vendor") == "cisco"
    assert any(e["type"] == "cmd" for e in rec.exchanges)
