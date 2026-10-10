"""QRC mock response shapes checked against the QRC_Commands.htm examples."""

import asyncio
import json

import pytest

from qpdk.qrc_tester.protocol import QRCErrorCode, QRCRequest
from qpdk.qrc_tester.server import QRCMockServer


def _req(method, params, req_id=1):
    return QRCRequest(method=method, params=params, id=req_id)


@pytest.fixture
def server():
    srv = QRCMockServer(host="127.0.0.1", port=0)
    srv.add_control("some control", value=-12.0, string="-12dB")
    srv.add_component("My Component", ["gain"])
    return srv


async def _connect(server):
    await server.start_listening()
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)  # EngineStatus
    return reader, writer


async def _send(reader, writer, obj):
    writer.write(json.dumps(obj).encode() + b"\x00")
    await writer.drain()
    raw = await asyncio.wait_for(reader.readuntil(b"\x00"), timeout=5)
    return json.loads(raw[:-1])


async def test_poll_reports_component_and_short_name(server):
    """Doc Poll example: a named control entry has Name only; a component
    control entry has Component plus the control's own Name."""
    reader, writer = await _connect(server)
    try:
        await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 1, "method": "ChangeGroup.AddControl",
            "params": {"Id": "my change group", "Controls": ["some control"]}})
        await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 2, "method": "ChangeGroup.AddComponentControl",
            "params": {"Id": "my change group",
                       "Component": {"Name": "My Component",
                                     "Controls": [{"Name": "gain"}]}}})
        await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 3, "method": "Component.Set",
            "params": {"Name": "My Component",
                       "Controls": [{"Name": "gain", "Value": -12}]}})
        resp = await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 4, "method": "ChangeGroup.Poll",
            "params": {"Id": "my change group"}})
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
    assert resp["result"]["Id"] == "my change group"
    changes = {(c.get("Component"), c["Name"]): c for c in resp["result"]["Changes"]}
    assert set(changes) == {(None, "some control"), ("My Component", "gain")}
    assert "Component" not in changes[(None, "some control")]
    assert changes[("My Component", "gain")]["Value"] == -12.0


async def test_control_get_unknown_name_is_an_error(server):
    resp = await server._handle_control_get(
        "c", _req("Control.Get", ["some control", "Nope"]))
    assert resp.result is None
    assert resp.error.code == QRCErrorCode.UNKNOWN_CONTROL
    assert "Nope" in resp.error.message


async def test_control_get_known_names_still_succeed(server):
    resp = await server._handle_control_get("c", _req("Control.Get", ["some control"]))
    assert resp.error is None
    assert resp.result[0]["Name"] == "some control"


async def test_status_get_includes_status_block(server):
    resp = await server._handle_status_get("c", _req("StatusGet", 0))
    assert resp.result["Status"] == {"Code": 0, "String": "OK"}
    assert {"Platform", "State", "DesignName", "DesignCode",
            "IsRedundant", "IsEmulator"} <= set(resp.result)


async def test_component_set_response_values(server):
    """Doc Component.Set Example 3: ResponseValues true returns the values."""
    resp = await server._handle_component_set("c", _req("Component.Set", {
        "Name": "My Component", "ResponseValues": True,
        "Controls": [{"Name": "gain", "Value": -10, "Ramp": 0}]}))
    assert resp.error is None
    (entry,) = resp.result
    assert entry["Component"] == "My Component"
    assert entry["Name"] == "gain"
    assert entry["Value"] == -10.0
    assert {"String", "Position"} <= set(entry)


async def test_component_set_without_response_values_returns_true(server):
    resp = await server._handle_component_set("c", _req("Component.Set", {
        "Name": "My Component", "Controls": [{"Name": "gain", "Value": 1}]}))
    assert resp.result is True


async def test_snapshot_save_is_handled(server):
    resp = await server._handle_snapshot_save(
        "c", _req("Snapshot.Save", {"Name": "My Snapshots", "Bank": 1}))
    assert resp.error is None


async def test_array_params_get_invalid_params_not_a_dropped_connection(server):
    reader, writer = await _connect(server)
    try:
        resp = await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 1, "method": "Logon", "params": ["u", "p"]})
        assert resp["id"] == 1
        assert resp["error"]["code"] == QRCErrorCode.INVALID_PARAMS == -32602
        follow_up = await _send(reader, writer, {
            "jsonrpc": "2.0", "id": 2, "method": "NoOp", "params": {}})
        assert follow_up["id"] == 2
        assert "error" not in follow_up
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
