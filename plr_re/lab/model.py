"""The vocabulary the lab layer reasons in: tiers, verdicts, roles, steps, protocols.

The whole layer exists to answer one question honestly: for a given end-to-end protocol,
which steps can run headlessly today, and what exactly blocks the rest? Everything here
is stdlib-only and hardware-free, so the answer can be computed on a laptop.

The tier split is the load-bearing idea, and it is deliberately harsh:

  ZERO_DECODE  the step needs no recovered command set. It reads a file, enumerates a
               USB bus, or opens a socket. It works today.
  NEEDS_MAP    the step replays a command out of a ProtocolMap. Every seeded command in
               this repo is decoded=False, so in practice this means blocked until a
               bench capture decodes it.
  FEDERATED    the step runs somewhere else that already drives real hardware (the
               validated PyLabRobot scripts in di-omics/plr-tested), reached over a run
               card rather than a ProtocolMap.

A step is only ever called automated if a machine can start it and read its result with
nobody standing at the instrument. Anything a human must do -- seat a cartridge, carry a
plate, press Start on a vendor console -- is MANUAL, and saying so is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Tier(str, Enum):
  """How a step reaches its instrument."""

  ZERO_DECODE = "zero_decode"  # works today: file read, bus enumeration, socket probe
  NEEDS_MAP = "needs_map"  # replays a ProtocolMap command; blocked while undecoded
  FEDERATED = "federated"  # runs in plr-tested on validated hardware, via a run card


class Verdict(str, Enum):
  """What a step can actually do right now. Ordered worst to best by autonomy."""

  MANUAL = "manual"  # no code path at all; a human does it at the bench
  BLOCKED = "blocked"  # code path exists but a required command is undecoded
  SUPERVISED = "supervised"  # real hardware execution, gated on a human confirm token
  AUTOMATED = "automated"  # runs headless today, no human, no decoding

  @property
  def headless(self) -> bool:
    """True only for steps a scheduler could start unattended."""
    return self is Verdict.AUTOMATED


class Role(str, Enum):
  """Where an instrument sits in a sample flow. Drives nothing but the report; it is
  here so `lab stock` can group a lab by what each box is for rather than by vendor."""

  SAMPLE_ENTRY = "sample_entry"  # bulk population -> addressable per-well units
  LIQUID_HANDLING = "liquid_handling"  # moves fluid between labware
  THERMAL = "thermal"  # heats/cycles a plate
  CONCENTRATION = "concentration"  # removes solvent
  ANALYTICAL = "analytical"  # consumes an aliquot, returns data, hands back no material
  SEQUENCING = "sequencing"  # where wet lab becomes data
  UNKNOWN = "unknown"


class ZeroDecodeOp(str, Enum):
  """The read-only operations that need no recovered command set.

  These are the only instrument contact this repo can make today. Three of the four are
  transport-generic despite living in one instrument's module: `discover_usb` enumerates
  any USB/serial link, and both probes open a socket to any IP. The registry declares
  which are meaningful per instrument rather than which happen to be importable from it.
  """

  DISCOVER_USB = "discover_usb"  # plr_re.instruments.namocell.discover_usb
  PROBE_TCP = "probe_tcp"  # plr_re.instruments.agilent6530.probe_module
  PROBE_HTTP = "probe_http"  # plr_re.instruments.element_aviti.probe_services
  WATCH_RUN_FOLDER = "watch_run_folder"  # plr_re.instruments.element_aviti.RunFolder


@dataclass(frozen=True)
class Artifact:
  """Something a step consumes or produces. Naming these is what makes a protocol a
  graph rather than a list: a step is ready when its inputs exist.

  `physical` marks material that a human or an arm must physically carry when no
  transfer instrument covers the hop. The ledger counts those hops explicitly, because
  a lab that is fully automated at every station and still needs hands between them is
  not an automated lab.
  """

  name: str
  physical: bool = False
  note: str = ""


@dataclass(frozen=True)
class Step:
  """One operation on one instrument.

  `op` is either a ZeroDecodeOp value or a ProtocolMap command name; which one it is
  follows from the instrument's tier, and the ledger resolves it rather than trusting a
  label here. A step never carries its own verdict: verdicts are computed against a
  live workcell so they cannot drift from the code.
  """

  instrument: str  # registry key, or a federated key
  op: str
  summary: str
  consumes: Tuple[str, ...] = ()
  produces: Tuple[str, ...] = ()
  params: Dict[str, object] = field(default_factory=dict)
  # Set when the step is a bench action no code path covers (seating a cartridge,
  # carrying a plate). Forces MANUAL regardless of what the instrument can do.
  manual_reason: Optional[str] = None


@dataclass(frozen=True)
class Protocol:
  """An ordered end-to-end run across instruments.

  Ordered, not a free DAG: these are real sample flows where the order is the science,
  and a topological sort would only invent freedom the bench does not have. `consumes`
  / `produces` are still checked, so a protocol that references an artifact nothing
  produces is caught before it is costed.

  A protocol carries its own `artifacts` rather than resolving them through a shared
  table. That matters for honesty, not tidiness: whether an artifact is physical is what
  makes it a counted plate hop, so a protocol whose artifacts were looked up in a global
  would silently report zero hops the moment someone wrote a protocol of their own. An
  undeclared artifact is refused at cost time instead.
  """

  name: str
  summary: str
  steps: Tuple[Step, ...]
  artifacts: Tuple[Artifact, ...] = ()

  def artifact(self, name: str) -> Optional[Artifact]:
    for art in self.artifacts:
      if art.name == name:
        return art
    return None

  def undeclared_artifacts(self) -> List[str]:
    """Every artifact a step references that the protocol never declares."""
    declared = {a.name for a in self.artifacts}
    seen: List[str] = []
    for step in self.steps:
      for art in tuple(step.consumes) + tuple(step.produces):
        if art not in declared and art not in seen:
          seen.append(art)
    return seen

  def dangling_inputs(self) -> List[Tuple[str, str]]:
    """(step.op, artifact) for every input no earlier step produces.

    A protocol is a claim about a lab. This catches the claim being incoherent -- a step
    that consumes a library nothing prepared -- before the ledger reports on it.
    """
    seen: set = set()
    out: List[Tuple[str, str]] = []
    for step in self.steps:
      for art in step.consumes:
        if art not in seen:
          out.append((step.op, art))
      seen.update(step.produces)
    return out
