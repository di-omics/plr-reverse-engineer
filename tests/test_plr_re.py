"""Device-free tests: guards, coverage gate, contact-closure dry-run, decode."""

import os
import tempfile

import pytest

from plr_re.decode import (
  build_write_single_register,
  crc16_modbus,
  diff_frames,
  load_serial_log,
  parse_modbus_rtu,
  scan_modbus_stream,
)
from plr_re.guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from plr_re.instruments.agilent6530 import Agilent6530Remote, AgilentPinMap, summarize_scan
from plr_re.instruments.biotage_v10 import BiotageV10
from plr_re.protocolmap import Command, ProtocolMap, Transport, seed
from plr_re.replay import GuardedReplayer
from plr_re.transports import MockByteConn, MockContactClosureIO


# -- ProtocolMap + coverage --------------------------------------------------


def test_seed_all_undecoded():
  pm = seed("agilent6530")
  cov = pm.coverage()
  assert cov["decoded"] == 0
  assert cov["total"] == len(pm.commands)
  assert set(cov["missing"]) == set(pm.commands)


def test_coverage_updates_when_decoded():
  pm = seed("biotage_v10")
  pm.commands["get_status"].decoded = True
  cov = pm.coverage()
  assert cov["decoded"] == 1
  assert "get_status" not in cov["missing"]


def test_protocolmap_json_roundtrip():
  pm = seed("facsmelody")
  pm.endpoint = "usb:0x1234:0x5678"
  pm.commands["get_status"].decoded = True
  pm.commands["get_status"].frame_template = "aa01"
  with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "map.json")
    pm.to_json(path)
    back = ProtocolMap.from_json(path)
  assert back.endpoint == "usb:0x1234:0x5678"
  assert back.commands["get_status"].decoded is True
  assert back.commands["get_status"].frame_template == "aa01"


# -- guarded replay ----------------------------------------------------------


def _one_command_map(actuating: bool) -> ProtocolMap:
  pm = ProtocolMap(device="test", transport=Transport.TCP, endpoint="127.0.0.1:9")
  pm.commands["go"] = Command(
    name="go", frame_template="ab{n}", decoded=True, actuating=actuating
  )
  return pm


def test_armed_run_refuses_incomplete_map():
  pm = seed("agilent6530")  # all undecoded
  r = GuardedReplayer(pm, Guards(armed=True), conn=MockByteConn())
  with pytest.raises(ProtocolMapIncompleteError):
    r.setup()


def test_dry_run_transmits_nothing():
  conn = MockByteConn()
  r = GuardedReplayer(_one_command_map(actuating=True), Guards(armed=False), conn=conn)
  r.setup()
  assert r.send("go", n=5) is None
  assert conn.writes == []  # dry run previews, sends nothing


def test_armed_actuation_requires_permission():
  conn = MockByteConn()
  r = GuardedReplayer(
    _one_command_map(actuating=True), Guards(armed=True, allow_actuation=False), conn=conn
  )
  r.setup()
  with pytest.raises(ActuationNotAllowed):
    r.send("go", n=5)
  assert conn.writes == []


def test_armed_allowed_actuation_transmits():
  conn = MockByteConn(responses=[b"\x06"])
  r = GuardedReplayer(
    _one_command_map(actuating=True), Guards(armed=True, allow_actuation=True), conn=conn
  )
  r.setup()
  resp = r.send("go", n=5)
  assert conn.writes == [bytes.fromhex("ab05")]
  assert resp == b"\x06"


# -- agilent contact closure (dry-run + mock) --------------------------------


def test_agilent_dry_run_does_not_pulse():
  io = MockContactClosureIO(levels={17: False})
  pins = AgilentPinMap(ready=17, start=27, stop=22)
  dev = Agilent6530Remote(pins, Guards(armed=False), io=io)
  dev.start_run()  # dry-run: logs, no pulse
  assert io.pulses == []


def test_agilent_armed_pulses_start():
  io = MockContactClosureIO(levels={17: True})
  pins = AgilentPinMap(ready=17, start=27, stop=22, pulse_s=0.01)
  dev = Agilent6530Remote(pins, Guards(armed=True, allow_actuation=True), io=io)
  assert dev.is_ready() is True
  dev.start_run()
  assert io.pulses == [(27, 0.01)]


def test_agilent_armed_start_blocked_without_actuation():
  io = MockContactClosureIO()
  pins = AgilentPinMap(ready=17, start=27, stop=22)
  dev = Agilent6530Remote(pins, Guards(armed=True, allow_actuation=False), io=io)
  with pytest.raises(ActuationNotAllowed):
    dev.start_run()
  assert io.pulses == []


# -- biotage temperature ceiling ---------------------------------------------


def test_biotage_temperature_ceiling():
  dev = BiotageV10(guards=Guards(armed=False), max_temperature_c=50.0)
  dev.setup()
  with pytest.raises(ValueError):
    dev.set_temperature(80.0)
  # within ceiling is fine (dry-run, returns None)
  assert dev.set_temperature(40.0) is None


# -- decode ------------------------------------------------------------------


def test_crc16_modbus_known_vector():
  # Standard CRC-16/MODBUS check value over ASCII "123456789".
  assert crc16_modbus(b"123456789") == 0x4B37


def test_modbus_build_and_parse_roundtrip():
  frame = build_write_single_register(0x01, 0x0010, 40)
  f = parse_modbus_rtu(frame)
  assert f.crc_ok is True
  assert f.function == 0x06
  assert f.register == 0x0010
  assert f.value == 40


def test_modbus_bad_crc_detected():
  frame = bytearray(build_write_single_register(0x01, 0x0010, 40))
  frame[-1] ^= 0xFF  # corrupt the CRC
  f = parse_modbus_rtu(bytes(frame))
  assert f.crc_ok is False


def test_diff_frames():
  d = diff_frames(bytes.fromhex("aa01cc"), bytes.fromhex("aa02cc"))
  assert len(d) == 1
  assert d[0].offset == 1
  assert (d[0].a, d[0].b) == (0x01, 0x02)


def test_scan_modbus_stream_finds_frames_in_noise():
  f1 = build_write_single_register(1, 0x0010, 40)
  f2 = build_write_single_register(1, 0x0011, 1)
  stream = b"\x00\xff" + f1 + b"\xaa" + f2 + b"\x99"
  found = scan_modbus_stream(stream)
  regs = {f.register: f.value for _, f in found}
  assert regs[0x0010] == 40
  assert regs[0x0011] == 1


def test_load_serial_log(tmp_path):
  import json

  f1 = build_write_single_register(1, 0x0010, 40)
  p = tmp_path / "serial.jsonl"
  p.write_text(
    json.dumps({"t": 1.0, "hex": f1[:3].hex()}) + "\n"
    + json.dumps({"t": 1.1, "hex": f1[3:].hex()}) + "\n"
  )
  data = load_serial_log(str(p))
  assert data == f1  # chunks reassembled across lines


def test_summarize_scan_flags_changed_pin():
  samples = [{17: False, 27: False}, {17: True, 27: False}]
  s = summarize_scan(samples)
  assert s[17]["changed"] is True
  assert s[27]["changed"] is False
  assert s[17]["first"] is False and s[17]["last"] is True
