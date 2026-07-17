"""Walk a protocol, run what is real, and stop honestly at the first thing that is not.

The executor does exactly two things, and refusing to do a third is the whole design:

  It performs the zero-decode steps. Enumerating a USB bus, probing a port, reading a run
  folder. These are read-only, need no recovered command set, and work today, so an
  armed run does them for real and returns the data.

  It stops at the first step that cannot run headless, and says why. It does not skip
  ahead to the next automatable step, and it does not simulate the blocked one. A run
  that pretended to sort a plate and then truthfully read a run folder would be worse
  than useless: it would look like a working pipeline.

Actuation is deliberately out of scope here. Anything that moves an instrument goes
through that instrument's own controller and its own arming switches; this layer schedules
and reports, it does not gain a second path to hardware that bypasses those guards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ledger import Ledger, build_ledger
from .model import Protocol, Step, Verdict, ZeroDecodeOp
from .registry import FEDERATED, registry
from .workcell import Workcell

logger = logging.getLogger("plr_re")


@dataclass
class StepResult:
  """What actually happened for one step."""

  step: Step
  verdict: Verdict
  executed: bool
  data: Optional[Any] = None
  note: str = ""


@dataclass
class Handoff:
  """The card handed to a human when the run stops.

  It names the step, why the machine cannot do it, and -- when the blocker is a missing
  command set rather than a physical act -- the bench work that would remove the stop.
  """

  step: Step
  reason: str
  blocking: List[str] = field(default_factory=list)
  gap_closer: str = ""

  def render(self) -> str:
    reg = registry()
    device = reg[self.step.instrument].device if self.step.instrument in reg else (
      FEDERATED[self.step.instrument].device if self.step.instrument in FEDERATED else self.step.instrument
    )
    lines = [
      "-- run stopped: a human is needed --",
      f"  instrument : {device}",
      f"  step       : {self.step.summary}",
      f"  reason     : {self.reason}",
    ]
    if self.blocking:
      lines.append(f"  undecoded  : {', '.join(self.blocking)}")
    if self.gap_closer:
      lines.append(f"  to unblock : {self.gap_closer}")
    return "\n".join(lines)


@dataclass
class RunReport:
  protocol: Protocol
  ledger: Ledger
  results: List[StepResult]
  handoff: Optional[Handoff]

  @property
  def completed(self) -> int:
    return sum(1 for r in self.results if r.executed)

  def render(self) -> str:
    lines = [f"protocol: {self.protocol.name}  ({len(self.protocol.steps)} steps)", ""]
    for i, r in enumerate(self.results, 1):
      mark = "ran " if r.executed else "----"
      lines.append(f"  {i:2d}. [{mark}] {r.step.summary}")
      if r.note:
        lines.append(f"          {r.note}")
    lines.append("")
    if self.handoff is not None:
      lines.append(self.handoff.render())
      lines.append("")
      lines.append(
        f"completed {self.completed} of {len(self.protocol.steps)} steps unattended "
        f"before stopping."
      )
    else:
      lines.append(f"completed all {self.completed} steps unattended.")
    return "\n".join(lines)


# What would remove each blocker. Keyed by instrument; deliberately specific, because a
# handoff card that says "decode the protocol" helps nobody standing at a bench.
_GAP_CLOSERS: Dict[str, str] = {
  "facsmelody": (
    "resolve the transport first (it is the only instrument here with no prior), then "
    "capture FACSChorus driving one sort action at a time and decode the frames"
  ),
  "agilent6530": (
    "for contact closure: meter the APG rear connector, fill in a pin map, and confirm "
    "Ready with `agilent scan`. For LAN control: capture the MassHunter/ICF traffic"
  ),
  "biotage_v10": (
    "settle the transport (the code seeds serial, the playbook argues for an Ethernet "
    "sniff first), then capture the Control Centre driving one setpoint at a time"
  ),
  "element_aviti": (
    "capture the AvitiOS UI traffic to a HAR and run `decode har` to separate the "
    "control calls from the status polling"
  ),
  "namocell": (
    "confirm USB-serial vs raw bulk with `namocell discover`, then capture the bundled "
    "PC driving one sort at a time"
  ),
  "viaflo96": (
    "capture VIALINK uploading one program, then diff two programs that differ in a "
    "single step to decode the serialization"
  ),
}


class Executor:
  """Runs a protocol as far as it honestly goes.

  armed=False previews every step and touches nothing. armed=True performs the
  zero-decode reads for real; it never actuates anything, on any setting.
  """

  def __init__(self, workcell: Optional[Workcell] = None, armed: bool = False):
    self.workcell = workcell or Workcell.default()
    self.armed = armed

  def run(self, protocol: Protocol) -> RunReport:
    ledger = build_ledger(protocol, self.workcell)
    results: List[StepResult] = []
    handoff: Optional[Handoff] = None

    for row in ledger.rows:
      if not row.verdict.headless:
        handoff = Handoff(
          step=row.step,
          reason=row.reason,
          blocking=list(row.blocking),
          gap_closer=_GAP_CLOSERS.get(row.step.instrument, "") if row.verdict is Verdict.BLOCKED else "",
        )
        break

      if not self.armed:
        results.append(
          StepResult(row.step, row.verdict, executed=False, note="[dry-run] would run; nothing sent")
        )
        continue

      try:
        data = self._perform(row.step)
      except (OSError, RuntimeError, ValueError, KeyError) as e:
        # A read that fails is a real answer about the bench, not a crash. Record it and
        # stop: continuing past a failed preflight would be exactly the dishonesty this
        # layer exists to prevent.
        results.append(StepResult(row.step, row.verdict, executed=False, note=f"failed: {e}"))
        handoff = Handoff(step=row.step, reason=f"a read-only preflight failed: {e}")
        break
      results.append(StepResult(row.step, row.verdict, executed=True, data=data, note=_summarize(data)))

    return RunReport(protocol=protocol, ledger=ledger, results=results, handoff=handoff)

  # -- the zero-decode operations, for real ----------------------------------

  def _perform(self, step: Step) -> Any:
    op = step.op
    cfg = self.workcell.instruments.get(step.instrument)
    endpoint = cfg.endpoint if cfg else None

    if op == ZeroDecodeOp.DISCOVER_USB.value:
      from ..instruments.namocell import discover_usb

      return discover_usb()

    if op == ZeroDecodeOp.PROBE_TCP.value:
      from ..instruments.agilent6530 import probe_module

      if not endpoint:
        raise RuntimeError(f"no endpoint for '{step.instrument}'; set one in the workcell")
      host, port = _split_endpoint(endpoint, default_port=23)
      return probe_module(host, port=port)

    if op == ZeroDecodeOp.PROBE_HTTP.value:
      from ..instruments.element_aviti import probe_services

      if not endpoint:
        raise RuntimeError(f"no endpoint for '{step.instrument}'; set one in the workcell")
      # Honor a configured port, and sweep the candidate list only when none was given.
      host, port = _split_endpoint(endpoint, default_port=0)
      return probe_services(host, ports=[port] if port else None)

    if op == ZeroDecodeOp.WATCH_RUN_FOLDER.value:
      from ..instruments.element_aviti import RunFolder

      run_dir = step.params.get("run_dir")
      if not run_dir:
        raise RuntimeError("no run_dir given to watch")
      return RunFolder(str(run_dir)).state()

    raise KeyError(f"executor cannot perform '{op}'; it only performs zero-decode operations")


def _split_endpoint(endpoint: str, default_port: int) -> tuple:
  """Split 'host:port' into (host, port). A bare host keeps the default.

  A port that is present but unparseable raises rather than falling through. Returning
  the whole string as the host would turn a typo into a connection attempt against a
  hostname that cannot resolve, and the resulting "unreachable" would read as a fact
  about the instrument instead of a fact about the config. IPv6 must be bracketed.
  """
  ep = endpoint.split("//")[-1].rstrip("/")
  if ep.startswith("["):  # [::1] or [::1]:8080
    host, _, rest = ep.partition("]")
    host = host[1:]
    if rest.startswith(":"):
      port = rest[1:]
      if not port.isdigit():
        raise ValueError(f"endpoint '{endpoint}' has a non-numeric port '{port}'")
      return host, int(port)
    return host, default_port
  if ep.count(":") > 1:
    raise ValueError(
      f"endpoint '{endpoint}' is ambiguous; bracket an IPv6 address as '[::1]:8080'"
    )
  if ":" in ep:
    host, port = ep.rsplit(":", 1)
    if not port.isdigit():
      raise ValueError(f"endpoint '{endpoint}' has a non-numeric port '{port}'")
    return host, int(port)
  return ep, default_port


def _summarize(data: Any) -> str:
  """One line of what a read returned, for the run report."""
  if isinstance(data, list):
    return f"{len(data)} result(s)"
  if isinstance(data, dict):
    if "state" in data:
      return f"state={data['state']} outcome={data.get('outcome')}"
    if "reachable" in data:
      return f"reachable={data['reachable']} banner={data.get('banner', '')[:40]!r}"
  return ""
