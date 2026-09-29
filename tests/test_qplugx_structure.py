"""QPDK048 — structural validation of encrypted .qplugx/.qplugx2 envelopes."""

import base64
import json

from qpdk.validator import PluginValidator


def _b64(n: int) -> str:
    return base64.b64encode(b"\x00" * n).decode()


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_valid_v1_envelope(tmp_path):
    p = _write(tmp_path, "good.qplugx", {"key": _b64(256), "iv": _b64(16), "data": _b64(32)})
    r = PluginValidator().validate_file(p)
    assert r.valid and not r.errors
    assert any("v1" in i.message for i in r.infos)


def test_valid_v2_envelope(tmp_path):
    env = {"key": _b64(256), "iv": _b64(16), "data": _b64(48)}
    p = _write(tmp_path, "good.qplugx2", {
        "mode": "CoreAndEmulate", "version": 2, "language": "Lua",
        "designtime": env, "runtime": env, "signature": _b64(256)})
    r = PluginValidator().validate_file(p)
    assert r.valid and not r.errors
    assert any("v2" in i.message for i in r.infos)


def test_v1_wrong_key_length(tmp_path):
    p = _write(tmp_path, "bad.qplugx", {"key": _b64(100), "iv": _b64(16), "data": _b64(32)})
    r = PluginValidator().validate_file(p)
    assert not r.valid
    assert any("key" in e.message and "256" in e.message for e in r.errors)


def test_v1_bad_data_block(tmp_path):
    p = _write(tmp_path, "bad.qplugx", {"key": _b64(256), "iv": _b64(16), "data": _b64(17)})
    r = PluginValidator().validate_file(p)
    assert not r.valid
    assert any("data" in e.message and "16" in e.message for e in r.errors)


def test_v1_extra_key_rejected(tmp_path):
    p = _write(tmp_path, "bad.qplugx", {"key": _b64(256), "iv": _b64(16), "data": _b64(32), "junk": "x"})
    r = PluginValidator().validate_file(p)
    assert not r.valid
    assert any("unexpected" in e.message for e in r.errors)


def test_v2_missing_signature(tmp_path):
    env = {"key": _b64(256), "iv": _b64(16), "data": _b64(48)}
    p = _write(tmp_path, "bad.qplugx2", {
        "mode": "x", "version": 2, "language": "Lua", "designtime": env, "runtime": env})
    r = PluginValidator().validate_file(p)
    assert not r.valid
    assert any("signature" in e.message for e in r.errors)


def test_not_json(tmp_path):
    p = tmp_path / "bad.qplugx"
    p.write_text("this is not json", encoding="utf-8")
    r = PluginValidator().validate_file(str(p))
    assert not r.valid
    assert any("not valid JSON" in e.message for e in r.errors)
