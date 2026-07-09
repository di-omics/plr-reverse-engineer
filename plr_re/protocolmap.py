"""ProtocolMap schema, per-instrument seed lists, and coverage.

A ProtocolMap is the decoded command set that lets the guarded replayer drive an
instrument headlessly. This module is the producer side of the same JSON artifact the
PyLabRobot backends consume (github.com/di-omics/pylabrobot). It is stdlib-only and
always importable.

The seed lists give every instrument an explicit target command list up front, so
coverage() always reports exactly which commands are still undecoded and therefore
still block a live run.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Transport(str, Enum):
  SERIAL = "serial"  # pyserial COM/tty (also RS-485 / Modbus RTU)
  TCP = "tcp"  # raw TCP socket
  USB = "usb"  # PyUSB bulk/interrupt endpoints
  CONTACT_CLOSURE = "contact_closure"  # digital lines over Pi GPIO (APG remote / ERI)
  UNKNOWN = "unknown"


@dataclass
class Command:
  """A decoded, replayable command.

  `frame_template` is the hex payload to send, with `{param}` placeholders filled at
  send time. Everything stays Optional until decoding fills it in, so an undecoded
  command still documents intent and keeps the replayer honest about what is missing.
  """

  name: str
  transport: Transport = Transport.UNKNOWN
  frame_template: Optional[str] = None
  response_regex: Optional[str] = None
  params: Dict[str, str] = field(default_factory=dict)
  terminator: Optional[str] = None
  checksum: Optional[str] = None
  evidence: List[str] = field(default_factory=list)
  decoded: bool = False
  actuating: bool = False
  notes: str = ""


@dataclass
class ProtocolMap:
  """Everything needed to drive an instrument headlessly."""

  device: str = "unknown"
  transport: Transport = Transport.UNKNOWN
  endpoint: Optional[str] = None
  commands: Dict[str, Command] = field(default_factory=dict)
  created: float = field(default_factory=time.time)
  notes: str = ""

  def to_json(self, path: str) -> None:
    payload = {
      "device": self.device,
      "transport": self.transport.value,
      "endpoint": self.endpoint,
      "created": self.created,
      "notes": self.notes,
      "commands": {
        name: {**asdict(c), "transport": c.transport.value} for name, c in self.commands.items()
      },
    }
    with open(path, "w", encoding="utf-8") as fh:
      json.dump(payload, fh, indent=2)

  @classmethod
  def from_json(cls, path: str) -> "ProtocolMap":
    with open(path, encoding="utf-8") as fh:
      d = json.load(fh)
    pm = cls(
      device=d.get("device", "unknown"),
      transport=Transport(d.get("transport", "unknown")),
      endpoint=d.get("endpoint"),
      created=d.get("created", time.time()),
      notes=d.get("notes", ""),
    )
    for name, c in d.get("commands", {}).items():
      c = dict(c)
      c["transport"] = Transport(c.get("transport", "unknown"))
      pm.commands[name] = Command(**c)
    return pm

  def coverage(self) -> dict:
    total = len(self.commands)
    done = sum(1 for c in self.commands.values() if c.decoded)
    return {
      "decoded": done,
      "total": total,
      "missing": [n for n, c in self.commands.items() if not c.decoded],
    }

  def actuating_commands(self) -> set:
    return {n for n, c in self.commands.items() if c.actuating}


# Per-instrument required command lists. Each entry is (name, actuating, note).
# Seeded undecoded so the RE work has an explicit target and the replayer can report
# exactly what still blocks a live run. Contact-closure control (Agilent Tier 0) is
# handled directly by the controller and does not need a map, so it is not seeded here.
SEEDS: Dict[str, List[Tuple[str, bool, str]]] = {
  "facsmelody": [
    ("connect", False, "open the control link / handshake"),
    ("get_status", False, "poll instrument state (idle/running/clog/error)"),
    ("load_template", False, "select a pre-built sort experiment/gate by name"),
    ("set_deposition", True, "set plate format and target cells-per-well"),
    ("prime", True, "prime fluidics / start stream, verify break-off stable"),
    ("start_sort", True, "begin depositing into the staged plate"),
    ("wait_complete", False, "block/poll until the plate is fully sorted"),
    ("abort", True, "emergency stop the sort"),
    ("clean", True, "run the clean/flush cycle between samples"),
  ],
  "agilent6530": [
    ("connect", False, "open the module LAN control link / handshake"),
    ("get_status", False, "poll module state and error flags"),
    ("read_pressure", False, "read pump pressure (read-only telemetry)"),
    ("read_oven", False, "read column oven temperature (read-only telemetry)"),
    ("set_flow", True, "set pump flow rate"),
    ("set_oven", True, "set column oven temperature"),
    ("set_injection", True, "set autosampler injection volume and vial"),
    ("load_method", True, "push an LC method / gradient table"),
    ("start_run", True, "begin the run (LAN path; see also contact closure)"),
    ("stop_run", True, "stop the run"),
  ],
  "biotage_v10": [
    ("get_status", False, "poll state, temperature, and pressure"),
    ("read_temperature", False, "read block/lamp temperature (read-only)"),
    ("read_pressure", False, "read vacuum pressure (read-only)"),
    ("set_temperature", True, "set heat setpoint (clamped to ceiling)"),
    ("set_vacuum", True, "turn vacuum on/off"),
    ("set_spin", True, "start/stop the sample spin"),
    ("set_gas", True, "turn the nitrogen assist on/off"),
    ("start_method", True, "start the evaporation method"),
    ("stop_method", True, "stop the method"),
  ],
}

# Default transport guess per instrument. Discovery on the bench confirms it.
DEFAULT_TRANSPORT: Dict[str, Transport] = {
  "facsmelody": Transport.UNKNOWN,
  "agilent6530": Transport.TCP,
  "biotage_v10": Transport.SERIAL,
}

DEVICE_NAMES: Dict[str, str] = {
  "facsmelody": "BD FACSMelody",
  "agilent6530": "Agilent 6530 Q-TOF",
  "biotage_v10": "Biotage V-10 Touch",
}


def seed(instrument: str) -> ProtocolMap:
  """Return a ProtocolMap seeded with an instrument's required, undecoded commands."""
  if instrument not in SEEDS:
    raise KeyError(f"unknown instrument '{instrument}'; known: {sorted(SEEDS)}")
  pm = ProtocolMap(
    device=DEVICE_NAMES[instrument],
    transport=DEFAULT_TRANSPORT[instrument],
  )
  for name, actuating, note in SEEDS[instrument]:
    pm.commands[name] = Command(name=name, notes=note, actuating=actuating, decoded=False)
  return pm
