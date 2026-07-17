"""A workcell: the instruments actually in front of you, and the maps decoded so far.

The registry says what an instrument IS. A workcell says what YOU have: which boxes are
on the bench, where each one's decoded ProtocolMap lives, and what endpoint it answers
on. Everything the ledger reports is computed against a workcell, so the report tracks
your bench rather than an idealized one.

The important behaviour is the fallback. Ask for an instrument's map and you get the
decoded one off disk if it exists, and the undecoded seed if it does not. There is no
third option and no way to assert coverage you have not earned: a missing map file is
not an error, it is simply a map with nothing decoded, and it costs out that way.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..protocolmap import ProtocolMap, seed
from .registry import FEDERATED, registry


@dataclass(frozen=True)
class InstrumentConfig:
  """One instrument's local reality: is it here, and how far is its map."""

  key: str
  present: bool = True
  map_path: Optional[str] = None
  endpoint: Optional[str] = None
  note: str = ""


@dataclass
class Workcell:
  """A named lab. `instruments` is keyed by registry key; `federated` names the
  plr-tested instruments this bench can reach."""

  name: str = "default"
  instruments: Dict[str, InstrumentConfig] = field(default_factory=dict)
  federated: Tuple[str, ...] = ()
  # Where plr-tested is checked out, for federated run cards. None means the seam is
  # declared but not wired, and the ledger says so rather than guessing a path.
  plr_tested_root: Optional[str] = None

  # -- construction ----------------------------------------------------------

  @classmethod
  def default(cls) -> "Workcell":
    """Every registered instrument, present, with no decoded maps and no endpoints.

    This is the honest zero state and it is what `lab stock` reports on a fresh clone:
    every box declared, nothing decoded, nothing reachable.
    """
    return cls(
      name="default",
      instruments={k: InstrumentConfig(key=k) for k in registry()},
      federated=tuple(FEDERATED),
    )

  @classmethod
  def from_json(cls, path: str) -> "Workcell":
    with open(path, encoding="utf-8") as fh:
      d = json.load(fh)
    known = registry()
    instruments: Dict[str, InstrumentConfig] = {}
    for key, cfg in (d.get("instruments") or {}).items():
      if key not in known:
        raise KeyError(f"workcell names unknown instrument '{key}'; known: {sorted(known)}")
      cfg = cfg or {}
      instruments[key] = InstrumentConfig(
        key=key,
        present=bool(cfg.get("present", True)),
        map_path=cfg.get("map_path"),
        endpoint=cfg.get("endpoint"),
        note=cfg.get("note", ""),
      )
    federated = tuple(d.get("federated") or ())
    for key in federated:
      if key not in FEDERATED:
        raise KeyError(f"workcell names unknown federated instrument '{key}'; known: {sorted(FEDERATED)}")
    return cls(
      name=d.get("name", "workcell"),
      instruments=instruments,
      federated=federated,
      plr_tested_root=d.get("plr_tested_root"),
    )

  def to_json(self, path: str) -> None:
    payload = {
      "name": self.name,
      "plr_tested_root": self.plr_tested_root,
      "federated": list(self.federated),
      "instruments": {
        k: {"present": c.present, "map_path": c.map_path, "endpoint": c.endpoint, "note": c.note}
        for k, c in self.instruments.items()
      },
    }
    with open(path, "w", encoding="utf-8") as fh:
      json.dump(payload, fh, indent=2)

  # -- map resolution --------------------------------------------------------

  def protocol_map(self, key: str) -> ProtocolMap:
    """The instrument's map: decoded from disk when present, the undecoded seed when not.

    A declared map_path that does not exist is a real mistake (a typo silently costing
    out as "nothing decoded" would be worse than a crash), so that raises. No map_path
    at all is not a mistake: it is the normal state of an instrument nobody has captured
    yet, and it seeds.
    """
    cfg = self.instruments.get(key)
    if cfg is not None and cfg.map_path:
      if not os.path.exists(cfg.map_path):
        raise FileNotFoundError(
          f"workcell declares map_path '{cfg.map_path}' for '{key}' but it does not exist"
        )
      pm = ProtocolMap.from_json(cfg.map_path)
    else:
      pm = seed(key)
    if cfg is not None and cfg.endpoint and pm.endpoint is None:
      pm.endpoint = cfg.endpoint
    return pm

  def coverage(self, key: str) -> dict:
    """decoded/total/missing for one instrument, against its resolved map."""
    return self.protocol_map(key).coverage()

  def present_keys(self) -> List[str]:
    return [k for k, c in self.instruments.items() if c.present]

  def is_federated(self, key: str) -> bool:
    return key in self.federated
