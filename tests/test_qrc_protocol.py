"""Tests for QRC Protocol"""

import json

from qpdk.qrc_tester.protocol import (
    QRCControl,
    QRCErrorCode,
    QRCRequest,
    QRCResponse,
)


class TestQRCRequest:
    """Test cases for QRC request parsing."""

    def test_parse_simple_request(self):
        """Parse a simple JSON-RPC request."""
        data = '{"jsonrpc": "2.0", "method": "NoOp", "id": 1}'
        request = QRCRequest.from_json(data)

        assert request.method == "NoOp"
        assert request.id == 1
        assert request.jsonrpc == "2.0"
        assert request.params is None

    def test_parse_request_with_params(self):
        """Parse request with parameters."""
        data = json.dumps({
            "jsonrpc": "2.0",
            "method": "Control.Get",
            "params": [{"Name": "Volume"}],
            "id": 2
        })
        request = QRCRequest.from_json(data)

        assert request.method == "Control.Get"
        assert request.params == [{"Name": "Volume"}]
        assert request.id == 2

    def test_parse_logon_request(self):
        """Parse Logon request."""
        data = json.dumps({
            "jsonrpc": "2.0",
            "method": "Logon",
            "params": {"User": "admin", "Password": "secret"},
            "id": 0
        })
        request = QRCRequest.from_json(data)

        assert request.method == "Logon"
        assert request.params["User"] == "admin"
        assert request.params["Password"] == "secret"

    def test_request_to_dict(self):
        """Request should serialize to dict."""
        request = QRCRequest(
            method="Control.Set",
            params={"Name": "Volume", "Value": 0.5},
            id=10
        )
        d = request.to_dict()

        assert d["method"] == "Control.Set"
        assert d["params"]["Value"] == 0.5
        assert d["id"] == 10


class TestQRCResponse:
    """Test cases for QRC response generation."""

    def test_success_response(self):
        """Create success response."""
        response = QRCResponse.success(1, {"status": "ok"})

        assert response.id == 1
        assert response.result == {"status": "ok"}
        assert response.error is None

    def test_error_response(self):
        """Create error response."""
        response = QRCResponse.make_error(
            2, QRCErrorCode.BAD_CONTROL, "Control not found"
        )

        assert response.id == 2
        assert response.result is None
        assert response.error.code == QRCErrorCode.BAD_CONTROL
        assert response.error.message == "Control not found"

    def test_response_to_json(self):
        """Response should serialize to JSON with null terminator."""
        response = QRCResponse.success(1, True)
        json_str = response.to_json()

        assert json_str.endswith("\x00")
        data = json.loads(json_str.rstrip("\x00"))
        assert data["id"] == 1
        assert data["result"] is True

    def test_error_response_json(self):
        """Error response should serialize correctly."""
        response = QRCResponse.make_error(3, -32601, "Method not found")
        json_str = response.to_json()

        data = json.loads(json_str.rstrip("\x00"))
        assert "error" in data
        assert data["error"]["code"] == -32601
        assert data["error"]["message"] == "Method not found"


class TestQRCControl:
    """Test cases for QRC control representation."""

    def test_control_to_dict(self):
        """Control should serialize to dict."""
        ctrl = QRCControl(
            name="Volume",
            value=0.75,
            string="75%",
            position=0.75
        )
        d = ctrl.to_dict()

        assert d["Name"] == "Volume"
        assert d["Value"] == 0.75
        assert d["String"] == "75%"
        assert d["Position"] == 0.75

    def test_control_defaults(self):
        """Control should have sensible defaults."""
        ctrl = QRCControl(name="Test")

        assert ctrl.value == 0.0
        assert ctrl.string == ""
        assert ctrl.position == 0.0
        assert ctrl.ramp == 0.0
