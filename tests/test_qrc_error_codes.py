"""Pin QRC error codes to the official spec so they can never silently drift.

Values are the official "QRC Commands > Error Code Reference" table
(help.qsys.com, identical across 9.13/10.0/10.1 KB mirrors). See
internal notes
"""

from qpdk.qrc_tester.protocol import QRCErrorCode


def test_domain_codes_match_official_table():
    assert QRCErrorCode.INVALID_PAGE_REQUEST_ID == 2
    assert QRCErrorCode.BAD_PAGE_REQUEST == 3
    assert QRCErrorCode.MISSING_FILE == 4
    assert QRCErrorCode.CHANGE_GROUPS_EXHAUSTED == 5
    assert QRCErrorCode.UNKNOWN_CHANGE_GROUP == 6
    assert QRCErrorCode.UNKNOWN_COMPONENT == 7
    assert QRCErrorCode.UNKNOWN_CONTROL == 8
    assert QRCErrorCode.ILLEGAL_MIXER_CHANNEL == 9
    assert QRCErrorCode.LOGON_REQUIRED == 10


def test_jsonrpc_transport_codes():
    assert QRCErrorCode.PARSE_ERROR == -32700
    assert QRCErrorCode.INVALID_REQUEST == -32600
    assert QRCErrorCode.METHOD_NOT_FOUND == -32601
    assert QRCErrorCode.INVALID_PARAMS == -32602
    assert QRCErrorCode.INTERNAL_ERROR == -32603
    assert QRCErrorCode.CORE_STANDBY == -32604


def test_backward_compat_aliases_have_correct_values():
    # Names kept for existing server.py/test call sites; values corrected.
    assert QRCErrorCode.BAD_LOGON == 10
    assert QRCErrorCode.BAD_COMPONENT == 7
    assert QRCErrorCode.BAD_CONTROL == 8
    assert QRCErrorCode.BAD_CHANGE_GROUP == 6


def test_invented_names_are_gone():
    # The old invented constants must not resurface.
    for gone in ("INVALID_CALL_ID", "REDUNDANT_CORE", "BAD_COMMAND_ID", "BAD_DESIGN"):
        assert not hasattr(QRCErrorCode, gone), f"{gone} should have been removed"
