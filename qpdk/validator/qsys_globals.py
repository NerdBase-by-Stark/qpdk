"""
Q-SYS Global API Reference

Comprehensive list of Q-SYS globals for different versions.
Used for validation and mock runtime setup.
"""

# Required fields in PluginInfo
REQUIRED_PLUGIN_FIELDS = {
    "Name": True,      # Required - displayed in Designer
    "Version": True,   # Required - semantic version
    "Id": True,        # Required - unique plugin identifier
    "Author": False,   # Optional - plugin author
    "Description": False,  # Optional - plugin description
    "ShowDebug": False,    # Optional - show debug tab
    "BuildVersion": False, # Optional - build number
}

# Q-SYS v9.x globals (current)
QSYS_GLOBALS_V9 = {
    # Core Runtime
    "Controls": "table - Access to plugin controls (Inputs, Outputs)",
    "Properties": "table - Access to plugin properties",
    "System": "table - System-level functions and info",
    "Design": "table - Design information",
    # Not Status / StatusState: neither is a Q-SYS global (absent from
    # help.qsys.com "Q-SYS Extensions to Lua"); plugins define their own.
    "Log": "table - Logging functions",

    # Timers
    "Timer": "table - Timer creation and management",

    # Network
    "TcpSocket": "table - TCP socket operations",
    "UdpSocket": "table - UDP socket operations",
    "HttpClient": "table - HTTP client operations",
    "Ssh": "table - SSH connection operations",

    # Components
    "Component": "table - Named component access",
    "NamedControl": "table - Named control access",

    # UI
    "Uci": "table - UCI control operations",
    "Snapshot": "table - Snapshot operations",

    # Serial/IO
    "SerialPorts": "table - Serial port access",
    "Gpio": "table - GPIO access",

    # Audio
    "Mixer": "table - Mixer operations",
    "Audio": "table - Audio routing operations",

    # Plugin structure functions (defined by plugins)
    "PluginInfo": "table - Plugin metadata",
    "GetProperties": "function - Returns property definitions",
    "GetControls": "function - Returns control definitions",
    "GetControlLayout": "function - Returns control layout",
    "GetPages": "function - Returns page definitions",
    "GetPins": "function - Returns pin definitions",
    "GetComponents": "function - Returns component definitions",
    "GetWiring": "function - Returns wiring definitions",
    "GetPrettyName": "function - Returns display name",
    "GetColor": "function - Returns plugin color",
    "RectifyProperties": "function - Validates/adjusts properties",
    "HandleDebug": "function - Debug message handler",

    # Q-SYS v9+ additions
    "Notifications": "table - Notification system (v9.8+)",
    "IsManaged": "boolean - Whether design is managed (v9.10+)",

    # Added later:
    "Network": "table - Network interface information",
    # NOTE: Reflect is the Q-SYS Reflect / Enterprise Manager integration global
    # (used as `Reflect and X or Y`), NOT "cross-plugin control binding". Its
    # version provenance is unverified, so no (v9+) marker — QPDK009 must not
    # assert an unverified version gate.
    "Reflect": "userdata - Q-SYS Reflect / Enterprise Manager integration global",
    "WebSocket": "table - WebSocket client (v9.5+)",
    "TcpSocketServer": "table - TCP server socket",
    "SerialServerPorts": "table - Serial server ports",
    "SNMP": "table - SNMP operations",
    "SNMPTrap": "table - SNMP trap operations",
    "json": "table - JSON encode/decode (built-in, simpler than rapidjson)",
    "rapidjson": "table - Fast JSON library (requires explicit require())",
    "Crypto": "table - Cryptographic operations",
    "dir": "function - Directory listing",
    "EzSVG": "table - SVG generation",
    "QRCode": "table - QR code generation",
    "Ping": "table - Network ping operations",
    "Email": "table - Email sending",
    "LuaXML": "table - XML parsing",
    "LPeg": "table - Pattern matching library",
    "LuaDate": "table - Date/time operations",
    "PageNames": "table - author-defined page-name convention (NOT a Q-SYS-provided global; whitelisted to avoid luacheck noise)",
    # Plugin-defined but universally expected:
    "SSH": "userdata - SSH connection instance (common plugin-level global name)",
}

# Q-SYS v7.x globals (legacy)
QSYS_GLOBALS_V7 = {
    # Core Runtime - same as v9
    "Controls": "table - Access to plugin controls",
    "Properties": "table - Access to plugin properties",
    "System": "table - System-level functions and info",
    "Design": "table - Design information",
    "Log": "table - Logging functions",

    # Timers
    "Timer": "table - Timer creation and management",

    # Network
    "TcpSocket": "table - TCP socket operations",
    "UdpSocket": "table - UDP socket operations",
    "HttpClient": "table - HTTP client operations",
    "Ssh": "table - SSH connection operations",

    # Components
    "Component": "table - Named component access",
    "NamedControl": "table - Named control access",

    # UI
    "Uci": "table - UCI control operations",
    "Snapshot": "table - Snapshot operations",

    # Serial/IO
    "SerialPorts": "table - Serial port access",
    "Gpio": "table - GPIO access",

    # Audio
    "Mixer": "table - Mixer operations",

    # Plugin structure functions
    "PluginInfo": "table - Plugin metadata",
    "GetProperties": "function - Returns property definitions",
    "GetControls": "function - Returns control definitions",
    "GetControlLayout": "function - Returns control layout",
    "GetPages": "function - Returns page definitions",
    "GetPins": "function - Returns pin definitions",
    "GetComponents": "function - Returns component definitions",
    "GetWiring": "function - Returns wiring definitions",
    "GetPrettyName": "function - Returns display name",
    "GetColor": "function - Returns plugin color",
    "RectifyProperties": "function - Validates/adjusts properties",
    "HandleDebug": "function - Debug message handler",
}

# Values of a Status-type indicator control (not a Q-SYS global table)
STATUS_STATES = {
    "OK": 0,
    "Compromised": 1,
    "Fault": 2,
    "NotPresent": 3,
    "Missing": 4,
    "Initializing": 5,
}

# Q-SYS plugin control TYPES — the `ControlType` field in GetControls.
# Per help.qsys.com Reserved_Functions, ONLY these four are valid ControlType
# values. LED/Meter/Header/GroupBox are layout `Style`/graphics, NOT ControlType
# (the old list conflated them). Reference data — not yet enforced by a rule.
CONTROL_TYPES = [
    "Button",
    "Knob",
    "Indicator",
    "Text",
]

# Per-field option sets from the Q-SYS plugin control schema (Reserved_Functions).
# These are DISTINCT fields, not a single "style" — the previous CONTROL_STYLES
# table conflated them and used wrong values (e.g. Knob "Gain", Button "On/Off").
# Reference data — not yet enforced by a rule.
BUTTON_TYPES = ["Toggle", "Momentary", "Trigger", "StateTrigger"]
KNOB_CONTROL_UNITS = ["dB", "Hz", "Float", "Integer", "Pan", "Percent", "Position", "Seconds"]
INDICATOR_TYPES = ["Led", "Meter", "Text", "Status"]
LAYOUT_STYLES = ["Fader", "Knob", "Button", "Text", "Meter", "Led", "ListBox", "ComboBox", "Media", "None"]

# Backwards-compatible alias (kept so any external import does not break). The
# values are now the corrected per-field sets keyed by the field they belong to.
CONTROL_STYLES = {
    "ButtonType": BUTTON_TYPES,
    "ControlUnit": KNOB_CONTROL_UNITS,
    "IndicatorType": INDICATOR_TYPES,
    "Style": LAYOUT_STYLES,
}

# Property types
PROPERTY_TYPES = [
    "integer",
    "double",
    "string",
    "boolean",
    "enum",
]

# Pin styles (the `PinStyle` field). Per Reserved_Functions: Input/Output/Both/None.
PIN_TYPES = [
    "Input",
    "Output",
    "Both",
    "None",
]

# Pin directions
PIN_DIRECTIONS = [
    "input",
    "output",
]

# Timer methods
TIMER_METHODS = [
    "Create",
    "CallAfter",
    "Call",
    "Stop",
]

# TcpSocket events
TCP_SOCKET_EVENTS = [
    "Connected",
    "Reconnect",
    "Data",
    "Closed",
    "Error",
    "Timeout",
]

# HttpClient methods
HTTP_CLIENT_METHODS = [
    "Download",
    "Upload",
    "Request",
]
