"""QRC mock-server behavior tests (D4/D5, Fable review 2026-07-06).

Covers the two defect classes the protocol-only tests never exercised:
  D4 — record -> playback round-trip (the two halves used incompatible
       file shapes: save_recording wrote "interactions", load_playback read
       "responses", so a recorded session could never be played back).
  D5 — ChangeGroup delta semantics (real Cores return only CHANGED controls
       from Poll — ChangeGroup.Invalidate exists precisely to force a full
       resend, per help.qsys.com QRC_Commands) and AutoPoll emission (the
       old handler stored the rate and never sent a frame).

Handler-level tests are used where determinism matters; two live-socket
tests cover the wire path (null-terminated framing, background emission).
"""

import asyncio
import json

import pytest

from qpdk.qrc_tester.protocol import QRCRequest
from qpdk.qrc_tester.server import QRCMockServer


def _req(method, params, req_id=1):
    return QRCRequest(method=method, params=params, id=req_id)


@pytest.fixture
def server():
    srv = QRCMockServer(host="127.0.0.1", port=0)
    srv.add_control("gain", value=0.0, string="0dB")
    srv.add_control("mute", value=0.0, string="off")
    return srv


async def _add_group(server, group_id="g1", controls=("gain", "mute")):
    resp = await server._handle_cg_add_control(
        "test-client",
        _req("ChangeGroup.AddControl",
             {"Id": group_id, "Controls": list(controls)}),
    )
    assert resp.error is None
    return group_id


class TestChangeGroupDeltas:
    async def test_first_poll_returns_all_controls(self, server):
        gid = await _add_group(server)
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        names = {c["Name"] for c in resp.result["Changes"]}
        assert names == {"gain", "mute"}

    async def test_second_poll_with_no_change_is_empty(self, server):
        gid = await _add_group(server)
        await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        assert resp.result["Changes"] == []

    async def test_changed_control_is_reported_once(self, server):
        gid = await _add_group(server)
        await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        server.state.controls["gain"].value = -12.0
        server.state.controls["gain"].string = "-12dB"
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        assert [c["Name"] for c in resp.result["Changes"]] == ["gain"]
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        assert resp.result["Changes"] == []

    async def test_invalidate_forces_full_resend(self, server):
        """help.qsys.com: Invalidate 'causes all controls to be resent'."""
        gid = await _add_group(server)
        await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        resp = await server._handle_cg_invalidate(
            "test-client", _req("ChangeGroup.Invalidate", {"Id": gid}))
        assert resp.error is None
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        names = {c["Name"] for c in resp.result["Changes"]}
        assert names == {"gain", "mute"}

    async def test_clear_removes_all_controls(self, server):
        gid = await _add_group(server)
        resp = await server._handle_cg_clear(
            "test-client", _req("ChangeGroup.Clear", {"Id": gid}))
        assert resp.error is None
        resp = await server._handle_cg_poll(
            "test-client", _req("ChangeGroup.Poll", {"Id": gid}))
        assert resp.result["Changes"] == []


# --- live-socket helpers ----------------------------------------------------

async def _start(server):
    await server.start_listening()
    return server._server.sockets[0].getsockname()[1]


async def _rpc(reader, writer, method, params, req_id):
    frame = json.dumps({
        "jsonrpc": "2.0", "method": method, "params": params, "id": req_id,
    }).encode() + b"\x00"
    writer.write(frame)
    await writer.drain()
    # Skip unsolicited notifications (e.g. the EngineStatus pushed on connect) —
    # a real QRC client keys responses by id, exactly as done here.
    while True:
        raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
        obj = json.loads(raw[:-1])
        if obj.get("id") == req_id:
            return obj


class TestRecordPlaybackRoundTrip:
    async def test_recorded_session_plays_back(self, server, tmp_path):
        """D4: a session recorded by this server must be replayable by it."""
        port = await _start(server)
        server.state.record_enabled = True

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        live_status = await _rpc(reader, writer, "StatusGet", {}, 1)
        live_noop = await _rpc(reader, writer, "NoOp", {}, 2)
        writer.close()
        await writer.wait_closed()
        await server.stop()

        rec_file = tmp_path / "session.json"
        server.save_recording(rec_file)

        # Fresh server with NO state: playback must supply the answers.
        replay = QRCMockServer(host="127.0.0.1", port=0)
        replay.load_playback(rec_file)
        port2 = await _start(replay)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port2)
        replayed_status = await _rpc(reader2, writer2, "StatusGet", {}, 1)
        replayed_noop = await _rpc(reader2, writer2, "NoOp", {}, 2)
        writer2.close()
        await writer2.wait_closed()
        await replay.stop()

        assert replayed_status["result"] == live_status["result"]
        assert replayed_noop["result"] == live_noop["result"]

    def test_legacy_responses_format_still_loads(self, server, tmp_path):
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({
            "responses": {"NoOp": [{"result": True, "error": None}]}
        }), encoding="utf-8")
        server.load_playback(legacy)
        assert server._playback_responses["NoOp"] == [
            {"result": True, "error": None}]


class TestAutoPoll:
    async def test_autopoll_emits_frames(self, server):
        """D5: AutoPoll must actually push poll-shaped frames at the rate
        (the old handler stored the rate and never emitted anything)."""
        port = await _start(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await _rpc(reader, writer, "ChangeGroup.AddControl",
                   {"Id": "ap", "Controls": ["gain"]}, 1)
        ack = await _rpc(reader, writer, "ChangeGroup.AutoPoll",
                         {"Id": "ap", "Rate": 0.05}, 42)
        assert ack.get("error") is None

        # First periodic frame carries the initial (never-sent) control state
        # and reuses the AutoPoll request id, per the QRC doc's example.
        raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
        frame = json.loads(raw[:-1])
        assert frame["id"] == 42
        assert frame["result"]["Id"] == "ap"
        assert [c["Name"] for c in frame["result"]["Changes"]] == ["gain"]

        # Change the value; a later frame must report it exactly once.
        server.state.controls["gain"].value = -6.0
        server.state.controls["gain"].string = "-6dB"
        deadline = asyncio.get_event_loop().time() + 5
        seen_change = False
        while asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
            frame = json.loads(raw[:-1])
            changes = frame["result"]["Changes"]
            if any(c["Name"] == "gain" and c["Value"] == -6.0 for c in changes):
                seen_change = True
                break
        assert seen_change, "AutoPoll never reported the changed control"

        writer.close()
        await writer.wait_closed()
        await server.stop()

    async def test_destroy_cancels_autopoll(self, server):
        port = await _start(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await _rpc(reader, writer, "ChangeGroup.AddControl",
                   {"Id": "ap2", "Controls": ["gain"]}, 1)
        await _rpc(reader, writer, "ChangeGroup.AutoPoll",
                   {"Id": "ap2", "Rate": 0.05}, 2)
        assert server._autopoll_tasks, "task should be registered"
        await _rpc_drain_until_response(reader, writer,
                                        "ChangeGroup.Destroy", {"Id": "ap2"}, 3)
        await asyncio.sleep(0.1)
        assert not server._autopoll_tasks, "Destroy must cancel the autopoll task"
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def _rpc_drain_until_response(reader, writer, method, params, req_id):
    """Send a request and read frames until the response with req_id arrives
    (autopoll frames may interleave)."""
    frame = json.dumps({
        "jsonrpc": "2.0", "method": method, "params": params, "id": req_id,
    }).encode() + b"\x00"
    writer.write(frame)
    await writer.drain()
    deadline = asyncio.get_event_loop().time() + 5
    while asyncio.get_event_loop().time() < deadline:
        raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
        obj = json.loads(raw[:-1])
        if obj.get("id") == req_id:
            return obj
    raise AssertionError(f"no response for id={req_id}")


class TestComponentChangeGroup:
    async def test_add_component_control_registers(self, server):
        """G3: ChangeGroup.AddComponentControl was advertised but unimplemented
        (fell through to METHOD_NOT_FOUND). It must register component-scoped
        controls, the dominant real-world change-group path."""
        port = await _start(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        resp = await _rpc(reader, writer, "ChangeGroup.AddComponentControl",
                          {"Id": "cg", "Component": {"Name": "MyGain",
                           "Controls": [{"Name": "gain"}, {"Name": "mute"}]}}, 7)
        assert resp.get("error") is None
        assert resp["result"]["Id"] == "cg"
        group = server.state.change_groups["cg"]
        assert "MyGain.gain" in group.controls
        assert "MyGain.mute" in group.controls
        writer.close()
        await writer.wait_closed()
        await server.stop()

    async def test_engine_status_pushed_on_connect(self, server):
        """G2: a real Core pushes an unsolicited EngineStatus on connect."""
        port = await _start(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
        frame = json.loads(raw[:-1])
        assert frame.get("method") == "EngineStatus"
        assert frame["params"]["State"] == "Active"
        assert "id" not in frame  # notification, not a response
        writer.close()
        await writer.wait_closed()
        await server.stop()


class TestComponentSetStringValues:
    """V5 regression (2026-07-12): stock Component.Set float()ed every Value,
    so a string payload (the code-push lane sets the "code" control's Value
    to Lua SOURCE) raised ValueError and killed the client connection. The
    fixed handler stores non-numeric string Values on .string instead.
    """

    LUA_PAYLOAD = 'print("hello from pushed code")\nlocal x = 1 + 1\n'

    async def test_string_value_lands_on_string_not_crash(self, server):
        server.add_component("Scripty", ["code"])
        resp = await server._handle_component_set(
            "test-client",
            _req("Component.Set", {
                "Name": "Scripty",
                "Controls": [{"Name": "code", "Value": self.LUA_PAYLOAD}],
            }))
        assert resp.error is None
        assert resp.result is True
        ctrl = server.state.controls["Scripty.code"]
        assert ctrl.string == self.LUA_PAYLOAD

    async def test_string_value_round_trips_via_component_get(self, server):
        server.add_component("Scripty", ["code"])
        await server._handle_component_set(
            "test-client",
            _req("Component.Set", {
                "Name": "Scripty",
                "Controls": [{"Name": "code", "Value": self.LUA_PAYLOAD}],
            }))
        resp = await server._handle_component_get(
            "test-client",
            _req("Component.Get", {
                "Name": "Scripty",
                "Controls": [{"Name": "code"}],
            }, req_id=2))
        assert resp.error is None
        (ctrl,) = resp.result["Controls"]
        assert ctrl["String"] == self.LUA_PAYLOAD

    async def test_numeric_and_numeric_string_values_unchanged(self, server):
        """Pre-fix behavior preserved: numbers AND numeric strings still
        coerce to float on .value/.position."""
        server.add_component("Amp", ["gain", "trim"])
        resp = await server._handle_component_set(
            "test-client",
            _req("Component.Set", {
                "Name": "Amp",
                "Controls": [{"Name": "gain", "Value": -6.5},
                             {"Name": "trim", "Value": "3.5"}],
            }))
        assert resp.error is None
        assert server.state.controls["Amp.gain"].value == -6.5
        assert server.state.controls["Amp.gain"].position == -6.5
        assert server.state.controls["Amp.trim"].value == 3.5


class TestControlSetStringValues:
    """0.14.0 regression: _handle_control_set had the SAME defect class the
    V5 Component.Set fix closed — float()ing every Value unconditionally, so
    a non-numeric string Value raised ValueError, escaped the handler, and
    killed the client connection (_handle_client's generic except closes the
    socket). Non-numeric string Values now land on .string, mirroring
    Component.Set.
    """

    TEXT_PAYLOAD = "Meeting Room A — Presentation"

    async def test_string_value_lands_on_string_not_crash(self, server):
        resp = await server._handle_control_set(
            "test-client",
            _req("Control.Set", {"Name": "label", "Value": self.TEXT_PAYLOAD}))
        assert resp.error is None
        assert resp.result is True
        assert server.state.controls["label"].string == self.TEXT_PAYLOAD

    async def test_string_value_round_trips_via_control_get(self, server):
        await server._handle_control_set(
            "test-client",
            _req("Control.Set", {"Name": "label", "Value": self.TEXT_PAYLOAD}))
        resp = await server._handle_control_get(
            "test-client", _req("Control.Get", ["label"], req_id=2))
        assert resp.error is None
        (ctrl,) = resp.result
        assert ctrl["String"] == self.TEXT_PAYLOAD

    async def test_numeric_and_numeric_string_values_unchanged(self, server):
        """Pre-fix behavior preserved: numbers AND numeric strings still
        coerce to float on .value/.position (list-of-params form)."""
        resp = await server._handle_control_set(
            "test-client",
            _req("Control.Set", [{"Name": "gain", "Value": -6.5},
                                 {"Name": "trim", "Value": "3.5"}]))
        assert resp.error is None
        assert server.state.controls["gain"].value == -6.5
        assert server.state.controls["gain"].position == -6.5
        assert server.state.controls["trim"].value == 3.5

    async def test_string_value_does_not_kill_connection(self, server):
        """The wire-level symptom: pre-fix, the ValueError escaped the handler
        and _handle_client closed the socket, so the NEXT rpc on the same
        connection died. Post-fix the same connection keeps answering."""
        port = await _start(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            resp = await _rpc(reader, writer, "Control.Set",
                              {"Name": "label", "Value": self.TEXT_PAYLOAD}, 1)
            assert resp.get("error") is None
            follow_up = await _rpc(reader, writer, "NoOp", {}, 2)
            assert follow_up["id"] == 2
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()
