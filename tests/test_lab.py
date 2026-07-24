"""Device-free tests for the lab layer.

The tests that matter most here are the ones that try to make the ledger lie: claim a
step is automated when its command is undecoded, claim a decoded command is runnable
while its siblings are not, claim a federated leg runs when the seam is not wired. If
those pass, the honesty of every report the layer prints follows from them.
"""

from __future__ import annotations

import pytest

from plr_re.lab import (
  Executor,
  Step,
  Verdict,
  Workcell,
  build_ledger,
  cost_step,
  protocols,
  rank_unlocks,
  registry,
)
from plr_re.lab.model import Protocol, Tier, ZeroDecodeOp
from plr_re.protocolmap import SEEDS, Transport, seed


def _decoded_map(key, path):
  """A map on disk in the state a finished bench capture leaves it: every command decoded,
  and the transport and endpoint resolved. All three are required before a replayer will
  open anything, so a fixture that set only the first would not model a runnable map."""
  pm = seed(key)
  for cmd in pm.commands.values():
    cmd.decoded = True
    cmd.frame_template = "aa00bb"
  pm.transport = Transport.SERIAL
  pm.endpoint = "/dev/ttyUSB0@115200"
  pm.to_json(str(path))
  return str(path)


# -- registry ------------------------------------------------------------------


def test_registry_is_derived_from_seeds():
  """The registry must not drift from SEEDS: that is the whole reason it is derived."""
  assert set(registry()) == set(SEEDS)


def test_every_instrument_starts_with_nothing_decoded():
  """The honest zero state. If this ever fails, a seed shipped pre-decoded."""
  wc = Workcell.default()
  for key in registry():
    cov = wc.coverage(key)
    assert cov["decoded"] == 0, f"{key} claims decoded commands it has not earned"
    assert cov["total"] > 0


def test_declared_but_unmerged_instrument_is_not_a_crash():
  """viaflo96 lands with its own PR. Referencing it early costs out as unavailable."""
  from plr_re.lab.registry import declared

  assert declared("viaflo96")
  if "viaflo96" in SEEDS:  # already merged; nothing to assert about absence
    return
  step = Step(instrument="viaflo96", op="run_program", summary="x")
  verdict = cost_step(step, Workcell.default())
  assert verdict.verdict is Verdict.MANUAL
  assert "not on this branch" in verdict.reason


def test_unknown_instrument_still_raises():
  step = Step(instrument="not_a_real_box", op="go", summary="x")
  with pytest.raises(KeyError):
    cost_step(step, Workcell.default())


# -- verdicts ------------------------------------------------------------------


def test_zero_decode_step_is_automated():
  step = Step(instrument="namocell", op=ZeroDecodeOp.DISCOVER_USB.value, summary="x")
  v = cost_step(step, Workcell.default())
  assert v.verdict is Verdict.AUTOMATED
  assert v.tier is Tier.ZERO_DECODE


def test_zero_decode_op_the_instrument_does_not_support_raises():
  """A protocol asking the Q-TOF to enumerate USB is a bug in the protocol."""
  step = Step(instrument="agilent6530", op=ZeroDecodeOp.DISCOVER_USB.value, summary="x")
  with pytest.raises(ValueError):
    cost_step(step, Workcell.default())


def test_undecoded_command_is_blocked_and_names_its_blocker():
  step = Step(instrument="namocell", op="start_sort", summary="x")
  v = cost_step(step, Workcell.default())
  assert v.verdict is Verdict.BLOCKED
  assert v.blocking == ("start_sort",)


def test_decoded_command_still_blocked_while_siblings_are_undecoded(tmp_path):
  """The coverage gate is all-or-nothing. One decoded command buys nothing, and the
  ledger must not pretend otherwise."""
  pm = seed("namocell")
  pm.commands["get_status"].decoded = True
  pm.commands["get_status"].frame_template = "aa00"
  path = tmp_path / "partial.json"
  pm.to_json(str(path))
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(
    key="namocell", map_path=str(path)
  )
  v = cost_step(Step(instrument="namocell", op="get_status", summary="x"), wc)
  assert v.verdict is Verdict.BLOCKED
  assert "sibling" in v.reason


def test_fully_decoded_map_splits_read_from_actuation(tmp_path):
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(
    key="namocell", map_path=_decoded_map("namocell", tmp_path / "full.json")
  )
  read = cost_step(Step(instrument="namocell", op="get_status", summary="x"), wc)
  assert read.verdict is Verdict.AUTOMATED

  actuate = cost_step(Step(instrument="namocell", op="start_sort", summary="x"), wc)
  assert actuate.verdict is Verdict.SUPERVISED
  assert "allow_actuation" in actuate.reason


def test_decoded_map_without_an_endpoint_is_not_automated(tmp_path):
  """setup() refuses a map with no endpoint. Reporting the step as running headless today
  would promise a run that cannot start."""
  pm = seed("namocell")
  for cmd in pm.commands.values():
    cmd.decoded = True
    cmd.frame_template = "aa00"
  pm.transport = Transport.SERIAL
  pm.endpoint = None
  path = tmp_path / "no_endpoint.json"
  pm.to_json(str(path))
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(key="namocell", map_path=str(path))
  v = cost_step(Step(instrument="namocell", op="get_status", summary="x"), wc)
  assert v.verdict is Verdict.BLOCKED
  assert "no endpoint" in v.reason


def test_decoded_map_with_an_unopenable_transport_is_not_automated(tmp_path):
  """DEFAULT_TRANSPORT is UNKNOWN for three instruments by design, and open_byte_conn
  raises ValueError on anything but tcp/serial. A decode alone does not make it dialable."""
  pm = seed("namocell")
  for cmd in pm.commands.values():
    cmd.decoded = True
    cmd.frame_template = "aa00"
  pm.transport = Transport.UNKNOWN
  pm.endpoint = "/dev/ttyUSB0@115200"
  path = tmp_path / "unknown_transport.json"
  pm.to_json(str(path))
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(key="namocell", map_path=str(path))
  v = cost_step(Step(instrument="namocell", op="get_status", summary="x"), wc)
  assert v.verdict is Verdict.BLOCKED
  assert "transport" in v.reason


def test_missing_map_file_raises_rather_than_costing_as_zero(tmp_path):
  """A typo'd map_path silently reporting 'nothing decoded' would be the worst failure
  mode this layer has."""
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(
    key="namocell", map_path=str(tmp_path / "nope.json")
  )
  with pytest.raises(FileNotFoundError):
    wc.coverage("namocell")


def test_absent_instrument_is_manual():
  wc = Workcell.default()
  wc.instruments["namocell"] = type(wc.instruments["namocell"])(key="namocell", present=False)
  v = cost_step(Step(instrument="namocell", op="start_sort", summary="x"), wc)
  assert v.verdict is Verdict.MANUAL


# -- federated -----------------------------------------------------------------


def test_federated_step_needs_the_seam_wired():
  wc = Workcell.default()  # plr_tested_root is None
  v = cost_step(Step(instrument="star", op="wgs_preparation", summary="x"), wc)
  assert v.verdict is Verdict.MANUAL
  assert "plr_tested_root" in v.reason


def test_federated_step_is_supervised_never_automated():
  """Hardware that a human stands next to is not headless, however validated it is."""
  wc = Workcell.default()
  wc.plr_tested_root = "/somewhere/plr-tested"
  v = cost_step(Step(instrument="star", op="wgs_preparation", summary="x"), wc)
  assert v.verdict is Verdict.SUPERVISED
  assert v.verdict.headless is False


def test_federated_step_without_a_validated_run_card_is_manual():
  """An instrument's reputation must not transfer to an arbitrary step. plr-tested has no
  validated library-pooling script, so naming the STAR must not make one appear."""
  wc = Workcell.default()
  wc.plr_tested_root = "/somewhere/plr-tested"
  v = cost_step(Step(instrument="star", op="library_pool", summary="x"), wc)
  assert v.verdict is Verdict.MANUAL
  assert "no run card for 'library_pool'" in v.reason


def test_supervised_reason_carries_the_actual_validation_record():
  """A supervised verdict must say what was actually watched, including the caveats: the
  WGS preparation leg is dry-validated and its wet form has never run."""
  wc = Workcell.default()
  wc.plr_tested_root = "/somewhere/plr-tested"
  v = cost_step(Step(instrument="star", op="wgs_preparation", summary="x"), wc)
  assert "DRY" in v.reason and "never run" in v.reason


# -- protocols and arithmetic --------------------------------------------------


def test_reference_protocols_are_coherent():
  for p in protocols.REFERENCE_PROTOCOLS.values():
    assert p.dangling_inputs() == [], f"{p.name} consumes an artifact nothing produces"


def test_protocol_with_dangling_input_is_rejected():
  bad = Protocol(
    name="bad",
    summary="x",
    steps=(Step(instrument="namocell", op="start_sort", summary="x", consumes=("ghost",)),),
  )
  with pytest.raises(ValueError):
    build_ledger(bad)


def test_headless_prefix_stops_at_the_first_non_headless_step():
  ledger = build_ledger(protocols.get("single_cell_genomics"))
  # step 1 is the zero-decode preflight; step 2 is seating a cartridge by hand.
  assert ledger.headless_prefix() == 1
  assert ledger.first_stop().step.manual_reason is not None


def test_autonomy_never_exceeds_reachable():
  for name in protocols.REFERENCE_PROTOCOLS:
    ledger = build_ledger(protocols.get(name))
    assert ledger.autonomy() <= ledger.reachable() <= 1.0


def test_single_cell_genomics_is_not_autonomous_today():
  """The headline claim, pinned. If this ever passes trivially, something decoded a map
  it should not have, or a step quietly stopped being counted."""
  ledger = build_ledger(protocols.get("single_cell_genomics"))
  assert ledger.autonomy() < 0.25
  assert ledger.counts()["blocked"] > 0


def test_a_user_protocol_must_declare_its_artifacts():
  """The silent-zero-hops trap: if artifacts were resolved through a shared table, a
  protocol written outside protocols.py would report no plate hops at all."""
  bad = Protocol(
    name="undeclared",
    summary="x",
    steps=(
      Step(instrument="namocell", op="start_sort", summary="x", produces=("my_plate",)),
      Step(instrument="agilent6530", op="start_run", summary="x", consumes=("my_plate",)),
    ),
  )
  with pytest.raises(ValueError, match="does not declare"):
    build_ledger(bad)


def test_a_user_protocol_that_declares_its_artifacts_gets_its_hops_counted():
  from plr_re.lab.model import Artifact

  good = Protocol(
    name="declared",
    summary="x",
    artifacts=(Artifact("my_plate", physical=True),),
    steps=(
      Step(instrument="namocell", op="start_sort", summary="x", produces=("my_plate",)),
      Step(instrument="agilent6530", op="start_run", summary="x", consumes=("my_plate",)),
    ),
  )
  assert build_ledger(good).handoffs() == [("my_plate", "namocell", "agilent6530")]


def test_handoffs_counts_physical_hops_between_instruments():
  wc = Workcell.default()
  wc.plr_tested_root = "/somewhere/plr-tested"
  hops = build_ledger(protocols.get("single_cell_genomics"), wc).handoffs()
  arts = [h[0] for h in hops]
  assert "sorted_plate" in arts  # namocell -> star: a plate someone carries
  for _art, src, dst in hops:
    assert src != dst


def test_unlocks_are_ranked_by_instrument_not_command():
  """Because the coverage gate is all-or-nothing, the unit of progress is a whole map."""
  ledger = build_ledger(protocols.get("single_cell_genomics"))
  unlocks = ledger.unlocks()
  assert unlocks, "expected blocked steps to produce a queue"
  assert unlocks == sorted(unlocks, key=lambda u: (-u.steps_unblocked, u.cost, u.instrument))
  top = unlocks[0]
  assert top.instrument == "namocell"
  assert top.cost == len(SEEDS["namocell"])  # the whole map, not one command


def test_rank_unlocks_aggregates_across_protocols():
  wc = Workcell.default()
  every = [protocols.get(n) for n in protocols.REFERENCE_PROTOCOLS]
  ranked = rank_unlocks(every, wc)
  assert {u.instrument for u in ranked} >= {"namocell", "element_aviti"}
  assert ranked == sorted(ranked, key=lambda u: (-u.steps_unblocked, u.cost, u.instrument))


# -- executor ------------------------------------------------------------------


def test_executor_dry_run_touches_nothing():
  report = Executor(Workcell.default(), armed=False).run(protocols.get("single_cell_genomics"))
  assert report.completed == 0
  assert all(not r.executed for r in report.results)


def test_executor_stops_at_the_first_human_step_and_hands_off():
  report = Executor(Workcell.default(), armed=False).run(protocols.get("single_cell_genomics"))
  assert report.handoff is not None
  assert "cartridge" in report.handoff.step.summary
  assert "run stopped" in report.handoff.render()


def test_executor_never_runs_past_a_blocked_step():
  """The critical safety property: no skipping ahead to a later automatable step. The
  AVITI run-folder read is automatable, but it must never execute in a run whose sort
  never happened."""
  report = Executor(Workcell.default(), armed=False).run(protocols.get("single_cell_genomics"))
  ran_ops = [r.step.op for r in report.results]
  assert ZeroDecodeOp.WATCH_RUN_FOLDER.value not in ran_ops


def test_executor_refuses_to_perform_a_non_zero_decode_op():
  ex = Executor(Workcell.default(), armed=True)
  with pytest.raises(KeyError):
    ex._perform(Step(instrument="namocell", op="start_sort", summary="x"))


def test_split_endpoint_parses_and_refuses_ambiguity():
  from plr_re.lab.executor import _split_endpoint

  assert _split_endpoint("192.168.1.50:8080", 23) == ("192.168.1.50", 8080)
  assert _split_endpoint("192.168.1.50", 23) == ("192.168.1.50", 23)
  assert _split_endpoint("https://192.168.1.50/", 0) == ("192.168.1.50", 0)
  assert _split_endpoint("[::1]:8080", 23) == ("::1", 8080)
  # A typo must not silently become a hostname; "unreachable" would then read as a fact
  # about the instrument rather than about the config.
  with pytest.raises(ValueError):
    _split_endpoint("host:notanumber", 23)
  with pytest.raises(ValueError):
    _split_endpoint("::1", 23)


def test_handoff_card_names_the_gap_closer_for_a_blocked_step():
  """A card that says 'decode the protocol' helps nobody at a bench."""
  p = Protocol(
    name="just_blocked",
    summary="x",
    steps=(Step(instrument="namocell", op="start_sort", summary="fire a sort"),),
  )
  report = Executor(Workcell.default(), armed=False).run(p)
  assert report.handoff is not None
  assert "discover" in report.handoff.gap_closer
  assert "start_sort" in report.handoff.blocking


# -- workcell round trip -------------------------------------------------------


def test_workcell_json_round_trip(tmp_path):
  wc = Workcell.default()
  wc.plr_tested_root = "/somewhere/plr-tested"
  path = tmp_path / "wc.json"
  wc.to_json(str(path))
  back = Workcell.from_json(str(path))
  assert set(back.instruments) == set(wc.instruments)
  assert back.plr_tested_root == "/somewhere/plr-tested"
  assert back.federated == wc.federated


def test_workcell_rejects_an_unknown_instrument(tmp_path):
  path = tmp_path / "bad.json"
  path.write_text('{"name": "x", "instruments": {"not_a_box": {}}}', encoding="utf-8")
  with pytest.raises(KeyError):
    Workcell.from_json(str(path))
