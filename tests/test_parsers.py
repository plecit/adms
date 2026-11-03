"""Unit test untuk parser ATTLOG, OPERLOG, dan Options iClock."""
import pytest
from datetime import datetime
from app.parsers.attlog import parse_attlog_line, parse_attlog_payload
from app.parsers.operlog import parse_operlog_payload
from app.parsers.options import format_options_response, parse_device_info_string


def test_parse_attlog_line_standard():
    line = "101\t2026-11-03 08:00:15\t0\t1\t0\t0\t0"
    rec = parse_attlog_line(line)
    assert rec is not None
    assert rec.pin == "101"
    assert rec.punched_at_local == datetime(2026, 11, 3, 8, 0, 15)
    assert rec.status == 0
    assert rec.verify_type == 1
    assert rec.workcode == "0"


def test_parse_attlog_line_short_columns():
    line = "202\t2026-11-03 17:30:00\t1"
    rec = parse_attlog_line(line)
    assert rec is not None
    assert rec.pin == "202"
    assert rec.punched_at_local == datetime(2026, 11, 3, 17, 30, 0)
    assert rec.status == 1
    assert rec.verify_type == 0


def test_parse_attlog_payload_with_malformed_line():
    payload = (
        "101\t2026-11-03 08:00:00\t0\t1\n"
        "MALFORMED_LINE_WITHOUT_TIME\n"
        "102\t2026-11-03 08:05:00\t0\t15\n"
    )
    result = parse_attlog_payload(payload)
    assert len(result.records) == 2
    assert len(result.malformed_lines) == 1
    assert result.records[0].pin == "101"
    assert result.records[1].pin == "102"
    assert result.records[1].verify_type == 15  # Face


def test_parse_operlog_user():
    payload = "USER PIN=105\tName=Ahmad Dahlan\tPri=14\tPasswd=1234\tCard=889900\tGrp=1\tTZ=0000000100000000\n"
    res = parse_operlog_payload(payload)
    assert len(res.users) == 1
    u = res.users[0]
    assert u.pin == "105"
    assert u.name == "Ahmad Dahlan"
    assert u.privilege == 14
    assert u.card_no == "889900"


def test_parse_operlog_biodata_face_type_9():
    payload = "BIODATA Pin=105\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=12\tMinorVer=0\tFormat=0\tTmp=dGVzdF9mYWNlX2Jhc2U2NA==\n"
    res = parse_operlog_payload(payload)
    assert len(res.biometrics) == 1
    bio = res.biometrics[0]
    assert bio.pin == "105"
    assert bio.kind == "face"
    assert bio.type_code == 9
    assert bio.format_version == "12.0"
    assert bio.template_b64 == "dGVzdF9mYWNlX2Jhc2U2NA=="


def test_parse_operlog_fingerprint_legacy():
    payload = "FP PIN=105\tFID=2\tSize=1560\tValid=1\tTMP=ZmluZ2VycHJpbnRfdGVtcGxhdGU=\n"
    res = parse_operlog_payload(payload)
    assert len(res.biometrics) == 1
    bio = res.biometrics[0]
    assert bio.pin == "105"
    assert bio.kind == "finger"
    assert bio.slot == 2
    assert bio.template_b64 == "ZmluZ2VycHJpbnRfdGVtcGxhdGU="


def test_options_response_format():
    resp = format_options_response(sn="TESTSN123", delay=30, tz_offset_minutes=420)
    assert "GET OPTION FROM: TESTSN123" in resp
    assert "Delay=30" in resp
    assert "TimeZone=420" in resp
    assert "Realtime=1" in resp


def test_parse_device_info_string():
    info = "DeviceName=SenseFace 4A,FirmwareVersion=Ver 2.4.1,FingerCount=500,FaceCount=1000"
    caps = parse_device_info_string(info)
    assert caps["DeviceName"] == "SenseFace 4A"
    assert caps["FirmwareVersion"] == "Ver 2.4.1"
    assert caps["FaceCount"] == "1000"
