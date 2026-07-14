"""Device-free tests for the Namocell Hana additions: the seed shape, the guarded
byte-replay controller (dry-run, actuation gate, coverage gate), plate-format
validation, the convenience sequence, and read-only USB discovery."""

import pytest

from plr_re.guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from plr_re.instruments.namocell import (
  VALID_PLATE_FORMATS,
  NamocellDispenser,
  discover_usb,
)
from plr_re.protocolmap import Transport, seed
from plr_re.replay import GuardedReplayer
from plr_re.transports import MockByteConn


# -- seed + schema -----------------------------------------------------------


def test_seed_namocell_undecoded():
  pm = seed("namocell")
  assert pm.transport == Transport.UNKNOWN  # USB/serial resolved on the bench
  assert pm.device == "Namocell Hana"
  cov = pm.coverage()
  assert cov["decoded"] == 0
  assert cov["total"] == len(pm.commands)
  # sort / prime / set_deposition / abort / clean actuate; status/protocol/wait do not.
  assert "start_sort" in pm.actuating_commands()
  assert "prime" in pm.actuating_commands()
  assert "get_status" not in pm.actuating_commands()
  assert "load_protocol" not in pm.actuating_commands()


# -- guarded byte replay -----------------------------------------------------


def _dry_dev(conn, armed=False, allow=False):
  g = Guards(armed=armed, allow_actuation=allow)
  dev = NamocellDispenser(guards=g)
  dev.replayer = GuardedReplayer(dev.pm, g, conn=conn)
  return dev


def _decoded_map():
  """A fully decoded Namocell map with a serial endpoint, for armed-path tests."""
  pm = seed("namocell")
  pm.transport = Transport.SERIAL
  pm.endpoint = "/dev/null@9600"
  for c in pm.commands.values():
    c.decoded = True
    c.frame_template = "aa00"
  return pm


def test_namocell_dry_run_previews_no_write():
  conn = MockByteConn()
  dev = _dry_dev(conn)
  dev.setup()
  assert dev.get_status() is None
  assert dev.start_sort() is None  # actuating, but dry-run only previews
  assert conn.writes == []


def test_namocell_armed_refuses_incomplete_map():
  dev = NamocellDispenser(guards=Guards(armed=True))
  dev.replayer = GuardedReplayer(dev.pm, dev.guards, conn=MockByteConn())
  with pytest.raises(ProtocolMapIncompleteError):
    dev.setup()


def test_namocell_armed_sort_blocked_without_actuation():
  conn = MockByteConn()
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=False)
  dev = NamocellDispenser(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()  # complete map + supplied conn, so no real port is opened
  with pytest.raises(ActuationNotAllowed):
    dev.start_sort()
  assert conn.writes == []


def test_namocell_armed_allowed_sort_transmits():
  conn = MockByteConn(responses=[b"\x06"])
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=True)
  dev = NamocellDispenser(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()
  dev.start_sort()
  assert conn.writes == [bytes.fromhex("aa00")]


# -- plate-format validation -------------------------------------------------


def test_namocell_rejects_unknown_plate_format():
  dev = NamocellDispenser(guards=Guards(armed=False))
  dev.setup()
  with pytest.raises(ValueError):
    dev.set_deposition(48)
  # supported formats pass validation and dry-run to None
  for fmt in VALID_PLATE_FORMATS:
    assert dev.set_deposition(fmt) is None


def test_namocell_rejects_negative_cells_per_well():
  dev = NamocellDispenser(guards=Guards(armed=False))
  dev.setup()
  with pytest.raises(ValueError):
    dev.set_deposition(96, cells_per_well=-1)


# -- convenience sequence ----------------------------------------------------


def test_sort_to_plate_previews_no_write():
  conn = MockByteConn()
  dev = _dry_dev(conn)
  dev.setup()
  dev.sort_to_plate(protocol="single_gfp", plate_format=384, cells_per_well=1)
  assert conn.writes == []  # unarmed: the whole sequence only previews


# -- read-only USB discovery -------------------------------------------------


def test_discover_usb_returns_list_without_hardware():
  # Enumeration must not raise or require a device; it returns a (possibly empty) list.
  cands = discover_usb()
  assert isinstance(cands, list)
  for c in cands:
    assert "path" in c
    assert "likely_control" in c
