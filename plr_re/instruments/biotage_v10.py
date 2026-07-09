"""Biotage V-10 Touch: setpoint control over the serial / Modbus bus via guarded replay.

Until the ProtocolMap is decoded on the bench these calls dry-run: they log the frame
they would send and transmit nothing. Once the map is filled in (frame templates, or
Modbus register writes), the same calls drive the instrument behind the guards.

Extra safety: every temperature setpoint is clamped to a configured ceiling before it is
ever framed, so a decoded setpoint cannot command the heater past a safe limit.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..guards import Guards
from ..protocolmap import ProtocolMap, seed
from ..replay import GuardedReplayer

logger = logging.getLogger("plr_re")


class BiotageV10:
  def __init__(
    self,
    pm: Optional[ProtocolMap] = None,
    guards: Optional[Guards] = None,
    max_temperature_c: float = 60.0,
    replayer: Optional[GuardedReplayer] = None,
  ):
    self.max_temperature_c = max_temperature_c
    self.pm = pm or seed("biotage_v10")
    self.guards = guards or Guards()
    self.replayer = replayer or GuardedReplayer(self.pm, self.guards)

  def setup(self) -> None:
    self.replayer.setup()

  def stop(self) -> None:
    self.replayer.stop()

  # -- read-only -------------------------------------------------------------

  def get_status(self):
    return self.replayer.send("get_status")

  def read_temperature(self):
    return self.replayer.send("read_temperature")

  def read_pressure(self):
    return self.replayer.send("read_pressure")

  # -- actuation (gated by the replayer) -------------------------------------

  def set_temperature(self, celsius: float):
    if celsius > self.max_temperature_c:
      raise ValueError(
        f"temperature {celsius}C exceeds the configured ceiling "
        f"{self.max_temperature_c}C; raise max_temperature_c deliberately to allow it."
      )
    return self.replayer.send("set_temperature", celsius=int(round(celsius)))

  def set_vacuum(self, on: bool):
    return self.replayer.send("set_vacuum", on=on)

  def set_spin(self, on: bool):
    return self.replayer.send("set_spin", on=on)

  def set_gas(self, on: bool):
    return self.replayer.send("set_gas", on=on)

  def start_method(self):
    return self.replayer.send("start_method")

  def stop_method(self):
    return self.replayer.send("stop_method")

  def evaporate(self, *, temperature: float, vacuum: bool = True, spin: bool = True):
    """Convenience sequence: set heat and services, then start the method. Each step is
    gated and dry-run until armed with a complete map."""
    self.set_temperature(temperature)
    self.set_vacuum(vacuum)
    self.set_spin(spin)
    self.start_method()
