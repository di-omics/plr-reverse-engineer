"""The instrument registry: what is in the lab, and how honest each entry is.

The registry is DERIVED from plr_re.protocolmap.SEEDS rather than restated alongside it.
That is deliberate. SEEDS is already the repo's single source of truth for what commands
an instrument needs, and a hand-maintained parallel list would drift the first time an
instrument landed. Adding an instrument to SEEDS puts it in the lab automatically.

_META therefore only carries what SEEDS cannot know: what the box is FOR, and which
zero-decode operations are meaningful against it. Metadata for an instrument that is not
yet in SEEDS is harmless and sits inert until its key appears, so an instrument on an
unmerged branch registers itself the moment it lands on main with no edit here.

FEDERATED is the other half of the lab: instruments this repo does not reverse-engineer
because they already have a driver that has been run on real hardware, in
di-omics/plr-tested. They are reached over a run card, not a ProtocolMap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ..protocolmap import DEFAULT_TRANSPORT, DEVICE_NAMES, SEEDS, Transport
from .model import Role, ZeroDecodeOp


@dataclass(frozen=True)
class InstrumentSpec:
  """A reverse-engineered instrument: what it is, and what it can honestly do today."""

  key: str
  device: str
  role: Role
  transport: Transport
  # Not one transport in this repo has been confirmed against the instrument; every one
  # is a prior that bench discovery is supposed to settle (protocolmap.py: "Default
  # transport guess per instrument. Discovery on the bench confirms it."). The field is
  # here so a bench run can flip it and the ledger notices, not for decoration.
  transport_confirmed: bool
  transport_note: str
  zero_decode: Tuple[ZeroDecodeOp, ...]
  controller: Optional[str]  # import path; None when no controller module exists yet
  note: str = ""

  @property
  def seed_commands(self) -> Tuple[str, ...]:
    return tuple(name for name, _actuating, _note in SEEDS[self.key])


@dataclass(frozen=True)
class FederatedSpec:
  """An instrument driven from another repo that has already run it on hardware.

  `entry` is a run card, not an API: plr-tested's own README is explicit that the seam is
  `run_on_pi.sh <script> [args]`, which rsyncs the working tree to the Pi and runs it in
  a venv. `plan_flag` is the introspection seam that touches nothing; `confirm_token` is
  the motion gate.

  `validated_ops` is the load-bearing field and it is deliberately narrow: it maps an
  operation to what an operator has ACTUALLY watched that operation do on the instrument.
  An operation absent from it costs out as manual, however capable the instrument is. The
  distinction matters because "the STAR is validated" is not a claim about any particular
  step: plr-tested has a validated whole-genome preparation addition and a validated pcr_enrichment choreography,
  and no validated bead cleanup or library pooling at all. Letting a step inherit the
  instrument's reputation is exactly the overclaim this layer exists to prevent.
  """

  key: str
  device: str
  role: Role
  repo: str
  entry: str
  plan_flag: Optional[str]
  confirm_token: Optional[str]
  validated: str
  validated_ops: Dict[str, str]
  note: str = ""


# What SEEDS cannot know. Keys that are not in SEEDS are ignored (see registry()).
_META: Dict[str, dict] = {
  "facsmelody": dict(
    role=Role.SAMPLE_ENTRY,
    # Transport is UNKNOWN with no committed prior -- alone among the six. So both
    # discovery paths are candidates: it is not yet known whether the link is a byte
    # link or a network service, and finding out is step one of the playbook.
    zero_decode=(ZeroDecodeOp.DISCOVER_USB, ZeroDecodeOp.PROBE_TCP),
    controller=None,
    transport_note="unknown, and the only instrument here with no committed prior",
    note=(
      "No controller module in this repo. A guarded backend scaffold lives on the "
      "di-omics/pylabrobot fork (branch facsmelody-sorter) and is proposed upstream; it "
      "is not merged, not hardware-validated, and its commands still raise "
      "NotImplementedError."
    ),
  ),
  "agilent6530": dict(
    role=Role.ANALYTICAL,
    zero_decode=(ZeroDecodeOp.PROBE_TCP,),
    controller="plr_re.instruments.agilent6530",
    transport_note="TCP prior for the LC LAN stack; contact closure is a separate path",
    note=(
      "Two transports with different status. Contact closure over Pi GPIO needs no "
      "decoding, but it does need a pin map discovered with a meter and the gpiozero "
      "extra, so it is bench work rather than a headless path today. The LAN control "
      "map is undecoded."
    ),
  ),
  "biotage_v10": dict(
    role=Role.CONCENTRATION,
    zero_decode=(ZeroDecodeOp.DISCOVER_USB,),
    controller="plr_re.instruments.biotage_v10",
    transport_note="serial prior in code; the playbook argues for a TCP sniff first",
    note=(
      "Nothing reaches this instrument today, not even a temperature read. The code "
      "seeds Transport.SERIAL while the playbook proposes an Ethernet sniff as the "
      "primary recovery path; the bench settles it."
    ),
  ),
  "element_aviti": dict(
    role=Role.SEQUENCING,
    zero_decode=(ZeroDecodeOp.PROBE_HTTP, ZeroDecodeOp.WATCH_RUN_FOLDER),
    controller="plr_re.instruments.element_aviti",
    transport_note="HTTP prior; AvitiOS is a microservice stack with no published local API",
    note=(
      "The one instrument with real state available today: RunFolder reads "
      "RunParameters.json and RunUploaded.json off the output folder and reports "
      "running/complete plus the run outcome, with no decoding and no network."
    ),
  ),
  "namocell": dict(
    role=Role.SAMPLE_ENTRY,
    zero_decode=(ZeroDecodeOp.DISCOVER_USB,),
    controller="plr_re.instruments.namocell",
    transport_note="unknown; strong USB prior, USB-serial vs raw bulk unresolved",
    note=(
      "discover_usb enumerates the link read-only and falls back to a stdlib glob of "
      "/dev when pyserial is absent, so it runs on a bare install."
    ),
  ),
  # Not on main yet: this instrument arrives with PR #4 (branch integra-viaflo-96).
  # The entry is inert until "viaflo96" appears in SEEDS, then registers itself.
  "viaflo96": dict(
    role=Role.LIQUID_HANDLING,
    zero_decode=(ZeroDecodeOp.DISCOVER_USB,),
    controller="plr_re.instruments.viaflo96",
    transport_note="unknown; strong USB prior, virtual-COM vs raw USB unresolved",
    note=(
      "Program transfer, not a live command stream: VIALINK serializes a whole program, "
      "uploads it, and the pipette runs it standalone. Per-step aspirate/dispense is "
      "not addressable over the link."
    ),
  ),
}


def registry() -> Dict[str, InstrumentSpec]:
  """Every reverse-engineered instrument in the lab, derived from SEEDS.

  An instrument in SEEDS without _META still registers, with an UNKNOWN role and no
  zero-decode operations. That is the safe direction to fail: a new instrument shows up
  in `lab stock` as present-but-uncharacterized instead of vanishing from the report.
  """
  out: Dict[str, InstrumentSpec] = {}
  for key in SEEDS:
    meta = _META.get(key, {})
    out[key] = InstrumentSpec(
      key=key,
      device=DEVICE_NAMES.get(key, key),
      role=meta.get("role", Role.UNKNOWN),
      transport=DEFAULT_TRANSPORT.get(key, Transport.UNKNOWN),
      transport_confirmed=False,
      transport_note=meta.get("transport_note", "no prior recorded"),
      zero_decode=meta.get("zero_decode", ()),
      controller=meta.get("controller"),
      note=meta.get("note", ""),
    )
  return out


# Instruments already driven on real hardware from di-omics/plr-tested. The claims in
# `validated` are what an operator has attested to in that repo's status tables; nothing
# here is inferred from code that merely exists.
FEDERATED: Dict[str, FederatedSpec] = {
  "star": FederatedSpec(
    key="star",
    device="liquid handler",
    role=Role.LIQUID_HANDLING,
    repo="di-omics/plr-tested",
    entry="liquid-handler/run_on_pi.sh",
    plan_flag="--plan",
    confirm_token="RUN_PCR_ENRICHMENT_ODTC_LIDDED_FULL",
    validated=(
      "Safe init, whole-genome preparation single-column and full-plate dry, iSWAP lid moves, and the "
      "lidded pcr_enrichment choreography: 13 motion legs, 22 SUCCESS, 0 failures, deck "
      "self-returned to start."
    ),
    validated_ops={
      "pta_wga_lysis": (
        "single-column and full-plate DRY, lysis 3.0 uL and reaction 6.0 uL; the wet "
        "single addition is written but has never run"
      ),
      "pcr_enrichment_choreography": (
        "13 motion legs, 22 SUCCESS, 0 failures, deck self-returned; dry"
      ),
      "iswap_lid_move": "rail35 pos0 to HHS rail27 pos2 and return, 6 of 6 clean",
    },
    note="Dry runs only. A wet run of the full choreography is still owed.",
  ),
  "odtc": FederatedSpec(
    key="odtc",
    device="Inheco ODTC thermocycler",
    role=Role.THERMAL,
    repo="di-omics/plr-tested",
    entry="instrument-integrations/run_on_pi.sh",
    plan_flag=None,
    confirm_token=None,
    validated=(
      "Bring-up, hold to 45.00 C, cycling to 50.00 C, and pcr-enrichment-round1: 30 real cycles, "
      "36.6 min, mean 0.27 C setpoint error."
    ),
    validated_ops={
      "pcr_enrichment_round1": (
        "30 real cycles on the instrument, 36.6 min, mean 0.27 C setpoint error; note "
        "98 C denaturation grazes the 99 C block ceiling, and the choreography does not "
        "close the door around the thermal leg"
      ),
    },
    note=(
      "The choreography never closes the ODTC door around the thermal leg, which is not "
      "a thermally sound way to run a real PCR. pcr-enrichment-round2 has never run."
    ),
  ),
  "hhs": FederatedSpec(
    key="hhs",
    device="heater-shaker",
    role=Role.THERMAL,
    repo="di-omics/plr-tested",
    entry="liquid-handler/run_on_pi.sh",
    plan_flag=None,
    confirm_token=None,
    validated="iSWAP handoff to the HHS and return, 6/6 repeatability.",
    validated_ops={
      "iswap_to_hhs": "6 of 6 transfers clean, pickup landed at z 0.950 every time",
    },
    note="Driven over the STAR TCC bus, so it shares the STAR's one-process constraint.",
  ),
}


# Hard constraint from plr-tested: exactly one driver process per instrument. Two STAR
# clients raise USBError [Errno 16] Resource busy; on the ODTC the collision is quieter,
# because a second process re-registers the event receiver and silently steals the
# first one's callbacks. Any scheduler built on this layer must serialize per instrument.
ONE_PROCESS_PER_INSTRUMENT = True


def spec(key: str) -> InstrumentSpec:
  reg = registry()
  if key not in reg:
    raise KeyError(f"unknown instrument '{key}'; known: {sorted(reg)}")
  return reg[key]


def declared(key: str) -> bool:
  """True for an instrument this repo knows about, whether or not it is on this branch.

  The gap between declared() and registry() is real and worth naming: an instrument
  whose playbook is still in review has metadata here but no entry in SEEDS. A protocol
  that references it is ahead of the branch, not wrong, and the ledger costs it as
  unavailable rather than crashing on it. A genuine typo is in neither and still raises.
  """
  return key in _META or key in SEEDS or key in FEDERATED
