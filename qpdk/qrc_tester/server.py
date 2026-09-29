"""
QRC Mock Server Implementation

A TCP server that emulates a Q-SYS Core for integration testing.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .protocol import (
    QRCRequest,
    QRCResponse,
    QRCControl,
    QRCChangeGroup,
    QRCError,
    QRCErrorCode,
)


@dataclass
class QRCMockState:
    """Server state for the mock QRC."""
    # Controls storage: name -> QRCControl
    controls: dict[str, QRCControl] = field(default_factory=dict)

    # Components: component_name -> list of control names
    components: dict[str, list[str]] = field(default_factory=dict)

    # Change groups: group_id -> QRCChangeGroup
    change_groups: dict[str, QRCChangeGroup] = field(default_factory=dict)

    # Authenticated sessions: client_id -> username
    authenticated: dict[str, str] = field(default_factory=dict)

    # Design info
    design_name: str = "Mock Design"
    design_code: str = "MOCK001"
    platform: str = "Core 110f"
    firmware: str = "9.10.0"

    # Valid credentials
    valid_users: dict[str, str] = field(
        default_factory=lambda: {"admin": "", "operator": "password"}
    )

    # Auto-poll tracking
    last_poll_time: float = field(default_factory=time.time)

    # Recording
    recording: list[dict[str, Any]] = field(default_factory=list)
    record_enabled: bool = False


class QRCMockServer:
    """Mock QRC server for testing."""

    DEFAULT_PORT = 1710
    KEEPALIVE_TIMEOUT = 60  # seconds

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.state = QRCMockState()
        self._server: asyncio.Server | None = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._playback_responses: dict[str, list[dict]] = {}

        # Method handlers
        self._handlers: dict[str, Callable] = {
            "Logon": self._handle_logon,
            "NoOp": self._handle_noop,
            "StatusGet": self._handle_status_get,
            "EngineStatus": self._handle_engine_status,
            "Control.Get": self._handle_control_get,
            "Control.Set": self._handle_control_set,
            "Component.Get": self._handle_component_get,
            "Component.Set": self._handle_component_set,
            "Component.GetComponents": self._handle_get_components,
            "Component.GetControls": self._handle_get_controls,
            "ChangeGroup.AddControl": self._handle_cg_add_control,
            "ChangeGroup.AddComponentControl": self._handle_cg_add_component_control,
            "ChangeGroup.Remove": self._handle_cg_remove,
            "ChangeGroup.Destroy": self._handle_cg_destroy,
            "ChangeGroup.Poll": self._handle_cg_poll,
            "ChangeGroup.AutoPoll": self._handle_cg_autopoll,
            "ChangeGroup.Invalidate": self._handle_cg_invalidate,
            "ChangeGroup.Clear": self._handle_cg_clear,
            "Snapshot.Load": self._handle_snapshot_load,
        }
        # Running AutoPoll emitters: (client_id, group_id) -> Task
        self._autopoll_tasks: dict[tuple[str, str], asyncio.Task] = {}

    def add_control(self, name: str, value: float = 0.0, string: str = "") -> None:
        """Add a control to the mock state."""
        self.state.controls[name] = QRCControl(
            name=name,
            value=value,
            string=string,
            position=value,
        )

    def add_component(self, name: str, controls: list[str]) -> None:
        """Add a component with controls."""
        self.state.components[name] = controls
        for ctrl_name in controls:
            full_name = f"{name}.{ctrl_name}"
            if full_name not in self.state.controls:
                self.add_control(full_name)

    def set_credentials(self, username: str, password: str) -> None:
        """Set valid credentials for authentication."""
        self.state.valid_users[username] = password

    def load_playback(self, path: str | Path) -> None:
        """Load recorded responses for playback.

        Accepts both the file this server records ({"interactions": [...]})
        and the legacy hand-written format ({"responses": {method: [...]}}).
        Before the D4 fix (Fable review 2026-07-06) the two halves were
        incompatible: save_recording wrote "interactions" (requests only)
        while load_playback read "responses", so a recorded session could
        never be played back.
        """
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        responses: dict[str, list[dict]] = {}
        for entry in data.get("interactions", []):
            method = entry.get("method") or entry.get("request", {}).get("method")
            resp = entry.get("response")
            if method and resp is not None:
                responses.setdefault(method, []).append({
                    "result": resp.get("result"),
                    "error": resp.get("error"),
                })
        for method, items in data.get("responses", {}).items():
            responses.setdefault(method, []).extend(items)
        self._playback_responses = responses

    def save_recording(self, path: str | Path) -> None:
        """Save recorded interactions."""
        path = Path(path)
        with open(path, "w") as f:
            json.dump({
                "recorded_at": time.time(),
                "interactions": self.state.recording,
            }, f, indent=2)

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        """Handle a connected client."""
        addr = writer.get_extra_info("peername")
        client_id = f"{addr[0]}:{addr[1]}"
        self._clients[client_id] = writer

        print(f"Client connected: {client_id}")

        # Unsolicited EngineStatus on connect — a real Core pushes this without
        # being asked (QRC Commands doc; reproduced by qsys-mock-core, pyqsys,
        # home-assistant-qsys-qrc). Sent as a JSON-RPC notification (no id).
        try:
            notify = json.dumps({
                "jsonrpc": "2.0",
                "method": "EngineStatus",
                "params": self._engine_status_payload(),
            }) + "\x00"
            writer.write(notify.encode("utf-8"))
            await writer.drain()
        except Exception as e:
            print(f"EngineStatus push failed for {client_id}: {e}")

        buffer = b""
        try:
            while True:
                data = await asyncio.wait_for(
                    reader.read(4096),
                    timeout=self.KEEPALIVE_TIMEOUT + 5
                )
                if not data:
                    break

                buffer += data

                # QRC messages are null-terminated
                while b"\x00" in buffer:
                    msg, buffer = buffer.split(b"\x00", 1)
                    if msg:
                        await self._process_message(client_id, msg, writer)

        except asyncio.TimeoutError:
            print(f"Client timeout: {client_id}")
        except ConnectionResetError:
            print(f"Client disconnected: {client_id}")
        except Exception as e:
            print(f"Client error: {client_id}: {e}")
        finally:
            self._cancel_autopoll(client_id=client_id)
            self._clients.pop(client_id, None)
            self.state.authenticated.pop(client_id, None)
            writer.close()
            await writer.wait_closed()
            print(f"Client closed: {client_id}")

    async def _process_message(self, client_id: str, data: bytes,
                               writer: asyncio.StreamWriter) -> None:
        """Process a single message."""
        try:
            request = QRCRequest.from_json(data)
        except json.JSONDecodeError as e:
            response = QRCResponse.make_error(
                None, QRCErrorCode.PARSE_ERROR, f"Parse error: {e}"
            )
            await self._send_response(writer, response)
            return

        # Resolve the response: playback queue first, then live handlers.
        queued = self._playback_responses.get(request.method)
        if queued:
            playback = queued.pop(0)
            error = playback.get("error")
            response = QRCResponse(
                id=request.id,
                result=playback.get("result"),
                # A raw dict here would crash to_json (it calls .to_dict()).
                error=QRCError(**error) if isinstance(error, dict) else error,
            )
        else:
            handler = self._handlers.get(request.method)
            if handler:
                response = await handler(client_id, request)
            else:
                response = QRCResponse.make_error(
                    request.id,
                    QRCErrorCode.METHOD_NOT_FOUND,
                    f"Unknown method: {request.method}"
                )

        # Record request AND response so the session is replayable.
        if self.state.record_enabled:
            self.state.recording.append({
                "timestamp": time.time(),
                "client": client_id,
                "method": request.method,
                "request": request.to_dict(),
                "response": response.to_dict(),
            })

        await self._send_response(writer, response)

    async def _send_response(self, writer: asyncio.StreamWriter,
                             response: QRCResponse) -> None:
        """Send a response to the client."""
        data = response.to_json().encode("utf-8")
        writer.write(data)
        await writer.drain()

    # Handler implementations

    async def _handle_logon(self, client_id: str,
                            request: QRCRequest) -> QRCResponse:
        """Handle Logon command."""
        params = request.params or {}
        username = params.get("User", "")
        password = params.get("Password", "")

        # Check credentials
        if username in self.state.valid_users:
            if self.state.valid_users[username] == password:
                self.state.authenticated[client_id] = username
                return QRCResponse.success(request.id, True)

        return QRCResponse.make_error(
            request.id,
            QRCErrorCode.BAD_LOGON,
            "Invalid credentials"
        )

    async def _handle_noop(self, client_id: str,
                           request: QRCRequest) -> QRCResponse:
        """Handle NoOp keepalive."""
        return QRCResponse.success(request.id, None)

    async def _handle_status_get(self, client_id: str,
                                 request: QRCRequest) -> QRCResponse:
        """Handle StatusGet command."""
        return QRCResponse.success(request.id, {
            "Platform": self.state.platform,
            "State": "Active",
            "DesignName": self.state.design_name,
            "DesignCode": self.state.design_code,
            "IsRedundant": False,
            "IsEmulator": True,
        })

    def _engine_status_payload(self) -> dict:
        """The EngineStatus body — shared by the request/response handler and
        the unsolicited on-connect push (a real Core sends EngineStatus
        proactively when a client connects; QRC Commands doc + 3 client libs)."""
        return {
            "Platform": self.state.platform,
            "State": "Active",
            "DesignName": self.state.design_name,
            "DesignCode": self.state.design_code,
            "IsRedundant": False,
            "IsEmulator": True,
            "Status": {
                "Code": 0,
                "String": "OK",
            },
        }

    async def _handle_engine_status(self, client_id: str,
                                    request: QRCRequest) -> QRCResponse:
        """Handle EngineStatus command."""
        return QRCResponse.success(request.id, self._engine_status_payload())

    async def _handle_control_get(self, client_id: str,
                                  request: QRCRequest) -> QRCResponse:
        """Handle Control.Get command."""
        params = request.params or []

        if isinstance(params, dict):
            names = [params.get("Name")]
        elif isinstance(params, list):
            names = [p.get("Name") if isinstance(p, dict) else p for p in params]
        else:
            names = [params]

        results = []
        for name in names:
            if name in self.state.controls:
                results.append(self.state.controls[name].to_dict())
            else:
                # Return default for unknown controls
                results.append({
                    "Name": name,
                    "Value": 0.0,
                    "String": "",
                    "Position": 0.0,
                })

        return QRCResponse.success(request.id, results)

    async def _handle_control_set(self, client_id: str,
                                  request: QRCRequest) -> QRCResponse:
        """Handle Control.Set command.

        Value coercion is string-tolerant, mirroring _handle_component_set's
        V5 fix (2026-07-12): real string controls accept string Values over
        QRC, but this handler used to float() every Value unconditionally, so
        a non-numeric string raised ValueError, escaped the handler, and
        _handle_client's generic except closed the client connection
        (0.14.0 fix — same defect class flagged in
        internal notes). Non-numeric string Values
        now land on .string; numbers and numeric strings keep the float path.
        """
        params = request.params or []

        if isinstance(params, dict):
            params = [params]

        for param in params:
            name = param.get("Name")
            if name:
                if name not in self.state.controls:
                    self.state.controls[name] = QRCControl(name=name)

                ctrl = self.state.controls[name]
                if "Value" in param:
                    try:
                        ctrl.value = float(param["Value"])
                        ctrl.position = ctrl.value
                    except (TypeError, ValueError):
                        ctrl.string = str(param["Value"])
                if "Position" in param:
                    ctrl.position = float(param["Position"])
                if "String" in param:
                    ctrl.string = str(param["String"])
                if "Ramp" in param:
                    ctrl.ramp = float(param["Ramp"])

        return QRCResponse.success(request.id, True)

    async def _handle_component_get(self, client_id: str,
                                    request: QRCRequest) -> QRCResponse:
        """Handle Component.Get command."""
        params = request.params or {}
        name = params.get("Name", "")
        controls = params.get("Controls", [])

        if name not in self.state.components:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_COMPONENT,
                f"Component not found: {name}"
            )

        results = []
        for ctrl in controls:
            ctrl_name = ctrl.get("Name") if isinstance(ctrl, dict) else ctrl
            full_name = f"{name}.{ctrl_name}"

            if full_name in self.state.controls:
                result = self.state.controls[full_name].to_dict()
                result["Name"] = ctrl_name  # Return short name
                results.append(result)

        return QRCResponse.success(request.id, {
            "Name": name,
            "Controls": results,
        })

    async def _handle_component_set(self, client_id: str,
                                    request: QRCRequest) -> QRCResponse:
        """Handle Component.Set command.

        Value coercion is string-tolerant: real string controls accept string
        Values over QRC — the code-push lane IS a Component.Set of the "code"
        control with Lua source as Value (community evidence, see
        internal notes). The stock handler used to
        float() every Value unconditionally, so a string payload raised
        ValueError and killed the client connection (U3 open item, 2026-07-12
        fix). Non-numeric string Values now land on .string instead.
        """
        params = request.params or {}
        name = params.get("Name", "")
        controls = params.get("Controls", [])

        if name not in self.state.components:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_COMPONENT,
                f"Component not found: {name}"
            )

        for ctrl in controls:
            ctrl_name = ctrl.get("Name")
            full_name = f"{name}.{ctrl_name}"

            if full_name not in self.state.controls:
                self.state.controls[full_name] = QRCControl(name=full_name)

            c = self.state.controls[full_name]
            if "Value" in ctrl:
                try:
                    c.value = float(ctrl["Value"])
                    c.position = c.value
                except (TypeError, ValueError):
                    c.string = str(ctrl["Value"])
            if "Position" in ctrl:
                c.position = float(ctrl["Position"])
            if "String" in ctrl:
                c.string = str(ctrl["String"])

        return QRCResponse.success(request.id, True)

    async def _handle_get_components(self, client_id: str,
                                     request: QRCRequest) -> QRCResponse:
        """Handle Component.GetComponents command."""
        components = [
            {"Name": name, "Type": "MockComponent"}
            for name in self.state.components.keys()
        ]
        return QRCResponse.success(request.id, components)

    async def _handle_get_controls(self, client_id: str,
                                   request: QRCRequest) -> QRCResponse:
        """Handle Component.GetControls command."""
        params = request.params or {}
        name = params.get("Name", "")

        if name not in self.state.components:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_COMPONENT,
                f"Component not found: {name}"
            )

        controls = []
        for ctrl_name in self.state.components[name]:
            controls.append({
                "Name": ctrl_name,
                "Type": "Float",
                "Direction": "Read/Write",
            })

        return QRCResponse.success(request.id, {
            "Name": name,
            "Controls": controls,
        })

    async def _handle_cg_add_control(self, client_id: str,
                                     request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.AddControl command."""
        params = request.params or {}
        group_id = params.get("Id", "")
        controls = params.get("Controls", [])

        if group_id not in self.state.change_groups:
            self.state.change_groups[group_id] = QRCChangeGroup(id=group_id)

        group = self.state.change_groups[group_id]
        for ctrl in controls:
            name = ctrl.get("Name") if isinstance(ctrl, dict) else ctrl
            if name not in group.controls:
                group.controls.append(name)

        return QRCResponse.success(request.id, {"Id": group_id})

    async def _handle_cg_add_component_control(self, client_id: str,
                                               request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.AddComponentControl — component-scoped change-group
        registration. This is the DOMINANT real-world path (official @q-sys/qrwc,
        home-assistant, pyqsys, Bitfocus all use it); the mock previously advertised
        it in QRC_METHODS but had no handler, falling through to METHOD_NOT_FOUND.

        Params: {Id, Component: {Name, Controls: [{Name}, ...]}}. Controls are
        tracked as "Component.Control" so component-scoped and named-control
        registrations don't collide.
        """
        params = request.params or {}
        group_id = params.get("Id", "")
        component = params.get("Component", {}) or {}
        comp_name = component.get("Name", "")
        controls = component.get("Controls", [])

        if group_id not in self.state.change_groups:
            self.state.change_groups[group_id] = QRCChangeGroup(id=group_id)

        group = self.state.change_groups[group_id]
        for ctrl in controls:
            name = ctrl.get("Name") if isinstance(ctrl, dict) else ctrl
            key = f"{comp_name}.{name}" if comp_name else name
            if key not in group.controls:
                group.controls.append(key)

        return QRCResponse.success(request.id, {"Id": group_id})

    async def _handle_cg_remove(self, client_id: str,
                                request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.Remove command."""
        params = request.params or {}
        group_id = params.get("Id", "")
        controls = params.get("Controls", [])

        if group_id not in self.state.change_groups:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_CHANGE_GROUP,
                f"Change group not found: {group_id}"
            )

        group = self.state.change_groups[group_id]
        for ctrl in controls:
            name = ctrl.get("Name") if isinstance(ctrl, dict) else ctrl
            if name in group.controls:
                group.controls.remove(name)

        return QRCResponse.success(request.id, True)

    async def _handle_cg_destroy(self, client_id: str,
                                 request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.Destroy command."""
        params = request.params or {}
        group_id = params.get("Id", "")

        if group_id in self.state.change_groups:
            del self.state.change_groups[group_id]
        self._cancel_autopoll(group_id=group_id)

        return QRCResponse.success(request.id, True)

    async def _handle_cg_poll(self, client_id: str,
                              request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.Poll command."""
        params = request.params or {}
        group_id = params.get("Id", "")

        if group_id not in self.state.change_groups:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_CHANGE_GROUP,
                f"Change group not found: {group_id}"
            )

        group = self.state.change_groups[group_id]
        return QRCResponse.success(request.id, {
            "Id": group_id,
            "Changes": self._collect_changes(group),
        })

    def _collect_changes(self, group: QRCChangeGroup) -> list[dict]:
        """Return only controls whose state changed since the last report.

        Real Cores return deltas: ChangeGroup.Invalidate exists to "cause all
        controls to be resent" (help.qsys.com QRC_Commands), which is only
        meaningful if Poll normally skips unchanged controls. A never-reported
        control (fresh group or after Invalidate) always counts as changed.
        """
        changes = []
        for name in group.controls:
            ctrl = self.state.controls.get(name)
            if ctrl is None:
                continue
            snapshot = (ctrl.value, ctrl.string, ctrl.position)
            if group.last_sent.get(name) != snapshot:
                group.last_sent[name] = snapshot
                changes.append(ctrl.to_dict())
        return changes

    async def _handle_cg_invalidate(self, client_id: str,
                                    request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.Invalidate: force a full resend on next poll."""
        params = request.params or {}
        group_id = params.get("Id", "")

        if group_id not in self.state.change_groups:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_CHANGE_GROUP,
                f"Change group not found: {group_id}"
            )

        self.state.change_groups[group_id].last_sent.clear()
        return QRCResponse.success(request.id, True)

    async def _handle_cg_clear(self, client_id: str,
                               request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.Clear: remove all controls from the group."""
        params = request.params or {}
        group_id = params.get("Id", "")

        if group_id not in self.state.change_groups:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_CHANGE_GROUP,
                f"Change group not found: {group_id}"
            )

        group = self.state.change_groups[group_id]
        group.controls.clear()
        group.last_sent.clear()
        return QRCResponse.success(request.id, True)

    async def _handle_cg_autopoll(self, client_id: str,
                                  request: QRCRequest) -> QRCResponse:
        """Handle ChangeGroup.AutoPoll: emit poll frames at the given rate.

        The QRC doc's AutoPoll example shows the periodic responses reusing
        the AutoPoll request's id with a Poll-shaped result ("receive a
        response every 5 seconds"). Emission is doc-literal: one frame per
        interval, with whatever (possibly empty) delta set exists. Rate <= 0
        cancels the emitter.
        """
        params = request.params or {}
        group_id = params.get("Id", "")
        rate = params.get("Rate", 0.0)

        if group_id not in self.state.change_groups:
            return QRCResponse.make_error(
                request.id,
                QRCErrorCode.BAD_CHANGE_GROUP,
                f"Change group not found: {group_id}"
            )

        self.state.change_groups[group_id].auto_poll = rate

        key = (client_id, group_id)
        old_task = self._autopoll_tasks.pop(key, None)
        if old_task:
            old_task.cancel()
        if rate and rate > 0:
            self._autopoll_tasks[key] = asyncio.create_task(
                self._autopoll_loop(client_id, group_id, rate, request.id))
        return QRCResponse.success(request.id, True)

    async def _autopoll_loop(self, client_id: str, group_id: str,
                             rate: float, request_id) -> None:
        """Background emitter for one client's AutoPoll on one group."""
        try:
            while True:
                await asyncio.sleep(rate)
                writer = self._clients.get(client_id)
                group = self.state.change_groups.get(group_id)
                if writer is None or group is None:
                    return
                response = QRCResponse(id=request_id, result={
                    "Id": group_id,
                    "Changes": self._collect_changes(group),
                })
                await self._send_response(writer, response)
        except (asyncio.CancelledError, ConnectionResetError, OSError):
            return

    def _cancel_autopoll(self, client_id: str | None = None,
                         group_id: str | None = None) -> None:
        """Cancel autopoll emitters matching the given client and/or group."""
        for key in list(self._autopoll_tasks):
            if client_id is not None and key[0] != client_id:
                continue
            if group_id is not None and key[1] != group_id:
                continue
            self._autopoll_tasks.pop(key).cancel()

    async def _handle_snapshot_load(self, client_id: str,
                                    request: QRCRequest) -> QRCResponse:
        """Handle Snapshot.Load command."""
        params = request.params or {}
        name = params.get("Name", "")
        bank = params.get("Bank", "")
        ramp = params.get("Ramp", 0.0)

        # Mock implementation - just acknowledge
        return QRCResponse.success(request.id, True)

    async def start_listening(self, record_to: str | None = None) -> None:
        """Bind and start accepting connections without blocking.

        For async tests and embedding: the asyncio server accepts clients in
        the background of the running loop. `start()` wraps this with
        serve_forever() for the CLI. With port=0 the bound port is available
        via self._server.sockets[0].getsockname()[1].
        """
        if record_to:
            self.state.record_enabled = True

        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port
        )

    async def start(self, record_to: str | None = None) -> None:
        """Start the server (async, blocks until stopped)."""
        await self.start_listening(record_to)

        async with self._server:
            await self._server.serve_forever()

    def run(self, record_to: str | None = None) -> None:
        """Run the server (blocking)."""
        if record_to:
            self.state.record_enabled = True

        try:
            asyncio.run(self.start(record_to))
        except KeyboardInterrupt:
            pass
        finally:
            if record_to and self.state.recording:
                self.save_recording(record_to)

    async def stop(self) -> None:
        """Stop the server."""
        self._cancel_autopoll()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
