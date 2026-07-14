"""Device-free tests for the raw-USB byte transport: endpoint-spec parsing, the
open_byte_conn routing (including a clean error when pyusb is absent), and that a
USB-transport ProtocolMap replays through the same guards/coverage as any byte map."""

import pytest

from plr_re.guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from plr_re.protocolmap import Transport, seed
from plr_re.replay import GuardedReplayer
from plr_re.transports import MockByteConn, open_byte_conn, parse_usb_endpoint


# -- endpoint spec parsing ---------------------------------------------------


def test_parse_usb_endpoint_basic_with_scheme():
  spec = parse_usb_endpoint("usb:0x1234:0x5678")
  assert (spec.vid, spec.pid) == (0x1234, 0x5678)
  assert spec.ep_out is None and spec.ep_in is None
  assert spec.interface == 0


def test_parse_usb_endpoint_bare_hex_no_scheme():
  # VID/PID are hex even without a 0x prefix (USB identifiers are always hexadecimal).
  spec = parse_usb_endpoint("1a86:7523")
  assert (spec.vid, spec.pid) == (0x1A86, 0x7523)


def test_parse_usb_endpoint_with_endpoints_and_iface():
  spec = parse_usb_endpoint("usb:0x0403:0x6001/out=0x02,in=0x81,iface=1")
  assert spec.ep_out == 0x02
  assert spec.ep_in == 0x81
  assert spec.interface == 0x01


def test_parse_usb_endpoint_rejects_malformed():
  with pytest.raises(ValueError):
    parse_usb_endpoint("usb:not-a-pair")


# -- open_byte_conn routing --------------------------------------------------


def test_open_byte_conn_unknown_transport_still_raises():
  # Regression: adding usb must not change the unknown-transport behavior.
  with pytest.raises(ValueError):
    open_byte_conn("carrier-pigeon", "x")


def test_open_byte_conn_usb_clean_error_without_pyusb():
  try:
    import usb.core  # noqa: F401
  except ImportError:
    pass
  else:
    pytest.skip("pyusb is installed; the missing-dependency path is not exercised here")
  with pytest.raises(RuntimeError) as ei:
    open_byte_conn("usb", "usb:0x1234:0x5678")
  assert "pyusb" in str(ei.value).lower()


def test_open_byte_conn_usb_clean_error_without_libusb_backend(monkeypatch):
  # pyusb installed but no native libusb backend: usb.core.find raises NoBackendError
  # (a ValueError subclass, not ImportError/USBError). It must become the friendly,
  # actionable RuntimeError, not an unhelpful bare error. Fake pyusb so the test is
  # deterministic whether or not real pyusb is installed in the environment.
  import sys
  import types

  class _NoBackendError(ValueError):
    pass

  class _USBError(OSError):
    pass

  usb_mod = types.ModuleType("usb")
  core = types.ModuleType("usb.core")
  util = types.ModuleType("usb.util")
  core.NoBackendError = _NoBackendError
  core.USBError = _USBError

  def _find(**kw):
    raise _NoBackendError("No backend available")

  core.find = _find
  usb_mod.core = core
  usb_mod.util = util
  monkeypatch.setitem(sys.modules, "usb", usb_mod)
  monkeypatch.setitem(sys.modules, "usb.core", core)
  monkeypatch.setitem(sys.modules, "usb.util", util)

  with pytest.raises(RuntimeError) as ei:
    open_byte_conn("usb", "usb:0x1234:0x5678")
  assert "libusb" in str(ei.value).lower()


# -- a USB-transport map replays like any other byte map ---------------------


def _decoded_usb_map():
  pm = seed("namocell")
  pm.transport = Transport.USB
  pm.endpoint = "usb:0x1234:0x5678"
  for c in pm.commands.values():
    c.decoded = True
    c.frame_template = "aa00cc"
  return pm


def test_usb_map_armed_actuation_gate_and_transmit():
  # The replayer is transport-agnostic once handed a connection: a USB-typed map obeys
  # the same coverage gate and actuation opt-in as a serial/tcp map.
  conn = MockByteConn(responses=[b"\x06"])
  pm = _decoded_usb_map()

  blocked = GuardedReplayer(pm, Guards(armed=True, allow_actuation=False), conn=conn)
  blocked.setup()
  with pytest.raises(ActuationNotAllowed):
    blocked.send("start_sort")
  assert conn.writes == []

  allowed = GuardedReplayer(pm, Guards(armed=True, allow_actuation=True), conn=conn)
  allowed.setup()
  allowed.send("start_sort")
  assert conn.writes == [bytes.fromhex("aa00cc")]


def test_usb_map_armed_refuses_incomplete():
  pm = seed("namocell")
  pm.transport = Transport.USB
  pm.endpoint = "usb:0x1234:0x5678"
  r = GuardedReplayer(pm, Guards(armed=True), conn=MockByteConn())
  with pytest.raises(ProtocolMapIncompleteError):
    r.setup()
