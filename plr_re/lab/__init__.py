"""The lab layer: wire the reverse-engineered instruments into one declared lab, and be
honest about how much of it runs.

Every other module in this package brings ONE instrument under control. This one asks the
question that only makes sense across all of them at once: given the instruments on the
bench and the command sets decoded so far, how much of an end-to-end protocol runs
without a human, and what exactly is in the way?

    from plr_re.lab import Workcell, build_ledger, protocols

    ledger = build_ledger(protocols.get("single_cell_genomics"))
    ledger.autonomy()         # fraction of steps that run headless today
    ledger.headless_prefix()  # how far an unattended run actually gets
    ledger.unlocks()          # which decode would free the most steps, ranked

The answers are currently bleak, and that is the feature. Nothing here can flatter the
lab: verdicts are computed from the resolved ProtocolMap, so a step is automated only if
its command is genuinely decoded, and the reference protocols include the cartridge
seating and flow-cell loading that a demo would quietly omit.
"""

from .executor import Executor, Handoff, RunReport, StepResult
from .ledger import Ledger, StepVerdict, Unlock, build_ledger, cost_step, rank_unlocks
from .model import Artifact, Protocol, Role, Step, Tier, Verdict, ZeroDecodeOp
from .registry import FEDERATED, FederatedSpec, InstrumentSpec, declared, registry, spec
from .workcell import InstrumentConfig, Workcell

__all__ = [
  "Artifact",
  "Executor",
  "FEDERATED",
  "FederatedSpec",
  "Handoff",
  "InstrumentConfig",
  "InstrumentSpec",
  "Ledger",
  "Protocol",
  "Role",
  "RunReport",
  "Step",
  "StepResult",
  "StepVerdict",
  "Tier",
  "Unlock",
  "Verdict",
  "Workcell",
  "ZeroDecodeOp",
  "build_ledger",
  "cost_step",
  "declared",
  "rank_unlocks",
  "registry",
  "spec",
]
