"""The autonomy ledger: how much of a protocol runs today, and what blocks the rest.

This is the point of the lab layer. Given a workcell and a protocol, it costs every step
against the actual state of the code -- the resolved ProtocolMap, the registry, the
federated run cards -- and returns a verdict per step plus the arithmetic over them.

Two rules keep it honest:

  Nothing is automated by assertion. A step is AUTOMATED only if the map says its command
  is decoded, or if it is a zero-decode operation the instrument genuinely supports. No
  field anywhere lets a protocol author declare a step automated.

  The blockers are named, not counted. A BLOCKED step reports which undecoded command
  blocks it, so `unlocks()` can rank the remaining reverse-engineering by how many steps
  each decode would free. That turns a pile of playbooks into an ordered queue.

The physical-handoff count is the part people forget. A lab whose every station is
automated and which still needs hands to carry plates between them is not an automated
lab, so hops are counted separately and never folded into the autonomy fraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..protocolmap import Transport
from .model import Protocol, Step, Tier, Verdict, ZeroDecodeOp
from .registry import FEDERATED, declared, registry
from .workcell import Workcell

_ZERO_DECODE_VALUES = {op.value for op in ZeroDecodeOp}

# What transports.open_byte_conn can actually open (transports.py: it raises ValueError on
# anything else, and Transport.USB has no connection class on this branch). Transport.HTTP
# is handled separately by GuardedHttpReplayer via HttpConn.
_BYTE_OPENABLE = {Transport.TCP, Transport.SERIAL}


@dataclass(frozen=True)
class StepVerdict:
  """One step, costed against a real workcell."""

  step: Step
  verdict: Verdict
  tier: Optional[Tier]
  reason: str
  blocking: Tuple[str, ...] = ()  # undecoded commands standing between here and headless

  @property
  def instrument(self) -> str:
    return self.step.instrument


def cost_step(step: Step, wc: Workcell) -> StepVerdict:
  """Cost one step. Raises on an incoherent step rather than guessing.

  A protocol that names an operation an instrument does not have is a bug in the
  protocol, and it should fail loudly at cost time rather than quietly become a MANUAL
  row that someone later reads as a bench task.
  """
  # A declared bench action beats everything: no code path exists by definition.
  if step.manual_reason:
    return StepVerdict(step, Verdict.MANUAL, None, step.manual_reason)

  key = step.instrument

  # -- federated: real hardware, reached over a run card ----------------------
  if key in FEDERATED:
    fed = FEDERATED[key]
    if key not in wc.federated:
      return StepVerdict(
        step, Verdict.MANUAL, Tier.FEDERATED, f"{fed.device} is not wired into this workcell"
      )
    if wc.plr_tested_root is None:
      return StepVerdict(
        step,
        Verdict.MANUAL,
        Tier.FEDERATED,
        f"{fed.device} runs from {fed.repo}, but the workcell does not say where it is "
        "checked out (set plr_tested_root)",
      )
    # An instrument's reputation does not transfer to an arbitrary step. plr-tested has a
    # validated WGS preparation addition and a validated PCR enrichment choreography; it has no validated
    # bead cleanup and no validated library pooling. A step with no run card of its own is
    # something a human still does, and calling it supervised because it happens to name
    # the STAR would be precisely the overclaim this layer exists to prevent.
    if step.op not in fed.validated_ops:
      return StepVerdict(
        step,
        Verdict.MANUAL,
        Tier.FEDERATED,
        f"{fed.device} is validated, but no run card for '{step.op}' has been validated "
        f"on it; someone writes and proves that script first",
      )
    gate = f"confirm token {fed.confirm_token}" if fed.confirm_token else "an operator at the E-stop"
    return StepVerdict(
      step,
      Verdict.SUPERVISED,
      Tier.FEDERATED,
      f"runs via {fed.entry}, gated on {gate}. Validated: {fed.validated_ops[step.op]}",
    )

  # -- reverse-engineered instruments ----------------------------------------
  reg = registry()
  if key not in reg:
    # Declared but absent means its playbook has not landed on this branch yet. That is
    # a fact about the checkout, not an error in the protocol, so it costs out as
    # unavailable. A key nobody has ever declared is a typo and still raises.
    if declared(key):
      return StepVerdict(
        step,
        Verdict.MANUAL,
        None,
        f"'{key}' is a declared instrument whose playbook is not on this branch yet; "
        "it registers itself once its PR merges",
      )
    raise KeyError(
      f"protocol names unknown instrument '{key}'; known: {sorted(reg) + sorted(FEDERATED)}"
    )
  spec = reg[key]

  cfg = wc.instruments.get(key)
  if cfg is None or not cfg.present:
    return StepVerdict(step, Verdict.MANUAL, None, f"{spec.device} is not in this workcell")

  # Zero-decode operations: the only instrument contact available today.
  if step.op in _ZERO_DECODE_VALUES:
    if ZeroDecodeOp(step.op) not in spec.zero_decode:
      raise ValueError(
        f"'{step.op}' is not a meaningful zero-decode operation for {spec.device}; "
        f"it supports: {[o.value for o in spec.zero_decode] or 'none'}"
      )
    if step.op == ZeroDecodeOp.WATCH_RUN_FOLDER.value and not step.params.get("run_dir"):
      return StepVerdict(
        step, Verdict.MANUAL, Tier.ZERO_DECODE, "no run_dir given to watch"
      )
    return StepVerdict(step, Verdict.AUTOMATED, Tier.ZERO_DECODE, "read-only, needs no decoding")

  # Otherwise it must be a ProtocolMap command.
  pm = wc.protocol_map(key)
  cmd = pm.commands.get(step.op)
  if cmd is None:
    raise KeyError(
      f"'{step.op}' is neither a zero-decode operation nor a command in the "
      f"ProtocolMap for {spec.device}; commands: {sorted(pm.commands)}"
    )
  if not cmd.decoded:
    return StepVerdict(
      step,
      Verdict.BLOCKED,
      Tier.NEEDS_MAP,
      f"'{step.op}' is undecoded; a live run refuses to start against an incomplete map",
      blocking=(step.op,),
    )
  # Decoded. The coverage gate is all-or-nothing across the whole map, so a decoded
  # command is still unreachable while any sibling is undecoded -- report that honestly
  # rather than promising a run that setup() would refuse.
  missing = tuple(pm.coverage()["missing"])
  if missing:
    return StepVerdict(
      step,
      Verdict.BLOCKED,
      Tier.NEEDS_MAP,
      f"'{step.op}' is decoded, but the coverage gate refuses an armed run while "
      f"{len(missing)} sibling command(s) are undecoded",
      blocking=missing,
    )
  # Coverage is only the first of setup()'s three preconditions. It also needs an endpoint
  # to dial and a transport a replayer can actually open, and a fresh decode leaves both
  # open: DEFAULT_TRANSPORT is UNKNOWN for three of these instruments by design, and a
  # seeded map carries endpoint=None. Modelling only the coverage gate would report a
  # command as running headless today that setup() refuses to start at all.
  if pm.endpoint is None:
    return StepVerdict(
      step,
      Verdict.BLOCKED,
      Tier.NEEDS_MAP,
      f"'{step.op}' is decoded, but {spec.device} has no endpoint; setup() refuses until "
      "transport discovery finds one",
    )
  if pm.transport is not Transport.HTTP and pm.transport not in _BYTE_OPENABLE:
    return StepVerdict(
      step,
      Verdict.BLOCKED,
      Tier.NEEDS_MAP,
      f"'{step.op}' is decoded, but the transport is '{pm.transport.value}' and no "
      "connection class opens that; bench discovery has to settle it first",
    )
  if cmd.actuating:
    return StepVerdict(
      step,
      Verdict.SUPERVISED,
      Tier.NEEDS_MAP,
      f"'{step.op}' actuates {spec.device}; needs allow_actuation and a human present",
    )
  return StepVerdict(step, Verdict.AUTOMATED, Tier.NEEDS_MAP, f"'{step.op}' is decoded and read-only")


@dataclass(frozen=True)
class Unlock:
  """One entry in the reverse-engineering queue: finish this map, free these steps."""

  instrument: str
  steps_unblocked: int
  commands_to_decode: Tuple[str, ...]

  @property
  def cost(self) -> int:
    """Commands still to decode. A crude proxy for bench hours, but an honest one:
    every command here is one more OEM action to drive, capture, and diff."""
    return len(self.commands_to_decode)


@dataclass
class Ledger:
  """A costed protocol."""

  protocol: Protocol
  workcell: Workcell
  rows: List[StepVerdict]

  # -- arithmetic ------------------------------------------------------------

  def counts(self) -> Dict[str, int]:
    out = {v.value: 0 for v in Verdict}
    for row in self.rows:
      out[row.verdict.value] += 1
    return out

  def autonomy(self) -> float:
    """Fraction of steps that run headless today. The harshest honest number."""
    if not self.rows:
      return 0.0
    return sum(1 for r in self.rows if r.verdict.headless) / len(self.rows)

  def reachable(self) -> float:
    """Fraction that runs today at all, counting steps a human supervises.

    Reported next to autonomy() because the gap between them is the supervision burden,
    and the gap to 1.0 is the reverse-engineering debt.
    """
    if not self.rows:
      return 0.0
    ok = {Verdict.AUTOMATED, Verdict.SUPERVISED}
    return sum(1 for r in self.rows if r.verdict in ok) / len(self.rows)

  def headless_prefix(self) -> int:
    """How many steps an unattended run completes before it stops.

    This is the number that matters and it is almost always brutally smaller than
    autonomy(). Counting individually-automatable steps flatters a pipeline: a read-only
    step near the end is only reachable if everything before it also ran. An unattended
    run gets exactly as far as its first non-headless step, so that prefix -- not the
    total -- is what "how automated is this lab" actually means.
    """
    n = 0
    for row in self.rows:
      if not row.verdict.headless:
        return n
      n += 1
    return n

  def handoffs(self) -> List[Tuple[str, str, str]]:
    """(artifact, from_instrument, to_instrument) for every physical hop between boxes.

    Counted from artifacts marked physical whose producer sits on a different instrument
    than their consumer. These are the hops a human or an arm must make, and no amount of
    decoding removes them -- only a plate mover does.
    """
    producer: Dict[str, str] = {}
    physical = {a.name for a in self.protocol.artifacts if a.physical}
    out: List[Tuple[str, str, str]] = []
    for step in self.protocol.steps:
      for art in step.consumes:
        src = producer.get(art)
        if art in physical and src is not None and src != step.instrument:
          out.append((art, src, step.instrument))
      for art in step.produces:
        producer[art] = step.instrument
    return out

  def unlocks(self) -> List["Unlock"]:
    """The reverse-engineering queue, ranked by how many steps each instrument frees.

    Ranked by INSTRUMENT, not by command, and that is forced by the code rather than a
    presentation choice. The coverage gate refuses an armed run while ANY required
    command is undecoded, so decoding a single command unblocks exactly zero steps. The
    unit of progress is a finished map, and a queue that ranked individual commands
    would be advice nobody can act on.
    """
    steps: Dict[str, int] = {}
    for row in self.rows:
      if row.verdict is Verdict.BLOCKED:
        steps[row.instrument] = steps.get(row.instrument, 0) + 1
    out: List[Unlock] = []
    for key, n in steps.items():
      missing = tuple(self.workcell.coverage(key)["missing"])
      out.append(Unlock(instrument=key, steps_unblocked=n, commands_to_decode=missing))
    out.sort(key=lambda u: (-u.steps_unblocked, len(u.commands_to_decode), u.instrument))
    return out

  def first_stop(self) -> Optional[StepVerdict]:
    """The first step that cannot run headless. Where an unattended run would stop."""
    for row in self.rows:
      if not row.verdict.headless:
        return row
    return None


def rank_unlocks(protocols: List[Protocol], wc: Optional[Workcell] = None) -> List[Unlock]:
  """The reverse-engineering queue across several protocols at once.

  This is the view worth acting on. A single protocol makes every instrument look about
  equally important; across a lab's real flows, one map often carries steps in several of
  them, and that is what should go to the bench first.
  """
  wc = wc or Workcell.default()
  totals: Dict[str, int] = {}
  for protocol in protocols:
    for unlock in build_ledger(protocol, wc).unlocks():
      totals[unlock.instrument] = totals.get(unlock.instrument, 0) + unlock.steps_unblocked
  out = [
    Unlock(
      instrument=key,
      steps_unblocked=n,
      commands_to_decode=tuple(wc.coverage(key)["missing"]),
    )
    for key, n in totals.items()
  ]
  out.sort(key=lambda u: (-u.steps_unblocked, u.cost, u.instrument))
  return out


def build_ledger(protocol: Protocol, wc: Optional[Workcell] = None) -> Ledger:
  """Cost a protocol against a workcell (the honest zero state by default)."""
  wc = wc or Workcell.default()
  # An undeclared artifact would not raise anywhere; it would quietly drop out of the
  # physical-hop count and make the lab look better connected than it is. Refuse it.
  undeclared = protocol.undeclared_artifacts()
  if undeclared:
    raise ValueError(
      f"protocol '{protocol.name}' references artifacts it does not declare: "
      f"{', '.join(undeclared)}. Declare them so the ledger knows which are physical."
    )
  dangling = protocol.dangling_inputs()
  if dangling:
    pretty = ", ".join(f"{op} needs '{art}'" for op, art in dangling)
    raise ValueError(f"protocol '{protocol.name}' consumes artifacts nothing produces: {pretty}")
  return Ledger(protocol=protocol, workcell=wc, rows=[cost_step(s, wc) for s in protocol.steps])
