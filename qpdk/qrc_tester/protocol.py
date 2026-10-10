"""
QRC Protocol Definitions

JSON-RPC 2.0 implementation for Q-SYS Remote Control protocol.

Reference: Q-SYS Help > External Control > Q-SYS Remote Control Protocol
"""

import json
from dataclasses import dataclass, field
from typing import Any


# QRC Error Codes
class QRCErrorCode:
    """Q-SYS QRC error codes — values match the official "QRC Commands >
    Error Code Reference" table (help.qsys.com, verified identical across the
    9.13 / 10.0 / 10.1 help pages).

    Corrected in qpdk 0.11.0: the domain codes 2-10 previously held invented
    values that matched neither the spec nor real Cores."""
    # JSON-RPC transport codes
    PARSE_ERROR = -32700       # Parse error — invalid JSON received
    INVALID_REQUEST = -32600   # Invalid request — not a valid Request object
    METHOD_NOT_FOUND = -32601  # Method not found
    INVALID_PARAMS = -32602    # Invalid params
    INTERNAL_ERROR = -32603    # doc: "Server error"
    CORE_STANDBY = -32604      # Core is on Standby (not the active Core in a redundant pair)

    # Q-SYS domain codes (official table, 2-10)
    INVALID_PAGE_REQUEST_ID = 2   # Invalid Page Request ID
    BAD_PAGE_REQUEST = 3          # Bad Page Request — could not create it
    MISSING_FILE = 4              # Missing file
    CHANGE_GROUPS_EXHAUSTED = 5   # Change Groups exhausted
    UNKNOWN_CHANGE_GROUP = 6      # Unknown change group
    UNKNOWN_COMPONENT = 7         # Unknown component name
    UNKNOWN_CONTROL = 8           # Unknown control
    ILLEGAL_MIXER_CHANNEL = 9     # Illegal mixer channel index
    LOGON_REQUIRED = 10           # Logon required

    # Backward-compatible semantic aliases (names kept; values now correct)
    BAD_LOGON = LOGON_REQUIRED            # 10
    BAD_COMPONENT = UNKNOWN_COMPONENT     # 7
    BAD_CHANGE_GROUP = UNKNOWN_CHANGE_GROUP  # 6
    BAD_CONTROL = UNKNOWN_CONTROL         # 8
    BAD_SNAPSHOT = MISSING_FILE           # 4 — official table has no snapshot-specific code; a missing snapshot bank is file-like


@dataclass
class QRCRequest:
    """A QRC JSON-RPC request."""
    method: str
    params: dict[str, Any] | list[Any] | None = None
    id: int | str | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_json(cls, data: str | bytes) -> "QRCRequest":
        """Parse a JSON-RPC request."""
        if isinstance(data, bytes):
            data = data.decode("utf-8")

        obj = json.loads(data)

        return cls(
            method=obj.get("method", ""),
            params=obj.get("params"),
            id=obj.get("id"),
            jsonrpc=obj.get("jsonrpc", "2.0"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params is not None:
            result["params"] = self.params
        if self.id is not None:
            result["id"] = self.id
        return result


@dataclass
class QRCError:
    """A QRC JSON-RPC error."""
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        return result


@dataclass
class QRCResponse:
    """A QRC JSON-RPC response."""
    id: int | str | None = None
    result: Any = None
    error: QRCError | None = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        """Serialize to JSON string with null terminator."""
        result: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            result["error"] = self.error.to_dict()
        else:
            result["result"] = self.result

        return json.dumps(result) + "\x00"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            result["error"] = self.error.to_dict()
        else:
            result["result"] = self.result
        return result

    @classmethod
    def success(cls, request_id: int | str | None, result: Any = None) -> "QRCResponse":
        """Create a success response."""
        return cls(id=request_id, result=result)

    @classmethod
    def make_error(cls, request_id: int | str | None, code: int, message: str,
                   data: Any = None) -> "QRCResponse":
        """Create an error response."""
        return cls(id=request_id, error=QRCError(code, message, data))


@dataclass
class QRCControl:
    """A mock Q-SYS control."""
    name: str
    value: float = 0.0
    string: str = ""
    position: float = 0.0
    ramp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "Name": self.name,
            "Value": self.value,
            "String": self.string,
            "Position": self.position,
            "Ramp": self.ramp,
        }


@dataclass
class QRCChangeGroup:
    """A change group for polling control changes."""
    id: str
    controls: list[str] = field(default_factory=list)
    auto_poll: float = 0.0  # Auto-poll interval in seconds (0 = disabled)
    # Last (value, string, position) reported per control. Poll returns only
    # controls whose state differs from this snapshot — real Cores return
    # deltas (ChangeGroup.Invalidate exists to force a full resend, per
    # help.qsys.com QRC_Commands).
    last_sent: dict[str, tuple] = field(default_factory=dict)
    # Controls added through ChangeGroup.AddComponentControl: internal key
    # ("Comp.ctl") -> (component name, control name). Poll reports these
    # with separate Component and Name fields (QRC_Commands.htm).
    component_controls: dict[str, tuple[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "Id": self.id,
            "Controls": self.controls,
            "AutoPoll": self.auto_poll,
        }


# Common QRC methods
QRC_METHODS = {
    # Authentication
    "Logon": "Authenticate with the Core",
    "ChangeMyPassword": "Change current user password",

    # Control operations
    "Control.Get": "Get control value(s)",
    "Control.Set": "Set control value(s)",

    # Change Groups
    "ChangeGroup.AddControl": "Add control to change group",
    "ChangeGroup.AddComponentControl": "Add component control to change group",
    "ChangeGroup.Remove": "Remove control from change group",
    "ChangeGroup.Destroy": "Destroy change group",
    "ChangeGroup.Invalidate": "Invalidate change group",
    "ChangeGroup.Clear": "Clear all controls from change group",
    "ChangeGroup.Poll": "Poll for changes",
    "ChangeGroup.AutoPoll": "Set auto-poll interval",

    # Component operations
    "Component.Get": "Get component control(s)",
    "Component.Set": "Set component control(s)",
    "Component.GetComponents": "List all components",
    "Component.GetControls": "Get all controls for component",

    # Status
    "StatusGet": "Get Core status",
    "EngineStatus": "Get engine status",

    # Snapshots
    "Snapshot.Load": "Load a snapshot",
    "Snapshot.Save": "Save a snapshot",

    # Keepalive
    "NoOp": "Keepalive (must be sent within 60 seconds)",
}
