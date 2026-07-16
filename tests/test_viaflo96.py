"""Device-free tests for the Integra VIAFLO 96 additions: the seed shape, the guarded
byte-replay controller (dry-run, actuation gate, coverage gate), head-volume validation,
and the convenience program sequence."""

import pytest

from plr_re.guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from plr_re.instruments.viaflo96 import IntegraViaflo96
from plr_re.protocolmap import Transport, seed
from plr_re.replay import GuardedReplayer
from plr_re.transports import MockByteConn


# -- seed + schema -----------------------------------------------------------


def test_seed_viaflo96_undecoded():
  pm = seed("viaflo96")
  assert pm.transport == Transport.UNKNOWN  # USB-serial vs raw USB resolved on the bench
  assert pm.device == "Integra VIAFLO 96"
  cov = pm.coverage()
  assert cov["decoded"] == 0
  assert cov["total"] == len(pm.commands)
  # upload / home / run / abort actuate; status/identity/programs/select do not.
  act = pm.actuating_commands()
  assert "upload_program" in act
  assert "run_program" in act
  assert "home" in act
  assert "abort" in act
  assert "get_status" not in act
  assert "get_identity" not in act
  assert "list_programs" not in act
  assert "select_program" not in act  # selection only, no motion


# -- guarded byte replay -----------------------------------------------------


def _dry_dev(conn, armed=False, allow=False, max_volume_ul=300.0):
  g = Guards(armed=armed, allow_actuation=allow)
  dev = IntegraViaflo96(guards=g, max_volume_ul=max_volume_ul)
  dev.replayer = GuardedReplayer(dev.pm, g, conn=conn)
  return dev


def _decoded_map():
  """A fully decoded VIAFLO 96 map with a serial endpoint, for armed-path tests."""
  pm = seed("viaflo96")
  pm.transport = Transport.SERIAL
  pm.endpoint = "/dev/null@9600"
  for c in pm.commands.values():
    c.decoded = True
    c.frame_template = "aa00"
  return pm


def test_viaflo_dry_run_previews_no_write():
  conn = MockByteConn()
  dev = _dry_dev(conn)
  dev.setup()
  assert dev.get_status() is None
  assert dev.run_program() is None  # actuating, but dry-run only previews
  assert conn.writes == []


def test_viaflo_armed_refuses_incomplete_map():
  dev = IntegraViaflo96(guards=Guards(armed=True))
  dev.replayer = GuardedReplayer(dev.pm, dev.guards, conn=MockByteConn())
  with pytest.raises(ProtocolMapIncompleteError):
    dev.setup()


def test_viaflo_armed_run_blocked_without_actuation():
  conn = MockByteConn()
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=False)
  dev = IntegraViaflo96(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()  # complete map + supplied conn, so no real port is opened
  with pytest.raises(ActuationNotAllowed):
    dev.run_program()
  assert conn.writes == []


def test_viaflo_armed_allowed_run_transmits():
  conn = MockByteConn(responses=[b"\x06"])
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=True)
  dev = IntegraViaflo96(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()
  dev.run_program()
  assert conn.writes == [bytes.fromhex("aa00")]


def test_viaflo_armed_upload_blocked_without_actuation():
  # upload_program writes device memory: the signature actuating command for this
  # program-transfer instrument. It must be refused when armed without actuation.
  conn = MockByteConn()
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=False)
  dev = IntegraViaflo96(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()
  with pytest.raises(ActuationNotAllowed):
    dev.upload_program("gfp_transfer")
  assert conn.writes == []


def test_viaflo_armed_allowed_upload_transmits():
  conn = MockByteConn(responses=[b"\x06"])
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=True)
  dev = IntegraViaflo96(pm=pm, guards=g)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()
  dev.upload_program("gfp_transfer")
  assert conn.writes == [bytes.fromhex("aa00")]


def test_viaflo_over_head_volume_rejected_before_actuation_gate():
  # _check_volume runs before the replayer's actuation gate, so an over-head-volume upload
  # is refused with ValueError even under armed-but-not-allowed guards, transmitting nothing.
  conn = MockByteConn()
  pm = _decoded_map()
  g = Guards(armed=True, allow_actuation=False)
  dev = IntegraViaflo96(pm=pm, guards=g, max_volume_ul=125.0)
  dev.replayer = GuardedReplayer(pm, g, conn=conn)
  dev.setup()
  with pytest.raises(ValueError):
    dev.upload_program("too_big", volumes=[200.0])
  assert conn.writes == []


# -- head-volume validation --------------------------------------------------


def test_viaflo_rejects_bad_max_volume():
  with pytest.raises(ValueError):
    IntegraViaflo96(max_volume_ul=0)


def test_viaflo_upload_rejects_over_head_volume():
  dev = _dry_dev(MockByteConn(), max_volume_ul=125.0)
  dev.setup()
  # a step above the installed head's ceiling is refused before framing
  with pytest.raises(ValueError):
    dev.upload_program("gfp_transfer", volumes=[50.0, 200.0])
  # a zero/negative volume is also refused
  with pytest.raises(ValueError):
    dev.upload_program("gfp_transfer", volumes=[0.0])
  # volumes within the head range pass validation and dry-run to None
  assert dev.upload_program("gfp_transfer", volumes=[10.0, 125.0]) is None


# -- convenience sequence ----------------------------------------------------


def test_run_named_program_previews_no_write():
  conn = MockByteConn()
  dev = _dry_dev(conn, max_volume_ul=300.0)
  dev.setup()
  dev.run_named_program("serial_dilution", volumes=[100.0, 50.0])
  assert conn.writes == []  # unarmed: the whole sequence only previews
