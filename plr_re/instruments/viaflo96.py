"""Integra VIAFLO 96 electronic pipette: read-only transport discovery and guarded
control of the program-transfer workflow.

The VIAFLO 96 is a benchtop 96-channel (also 24- and 384-channel) electronic pipette
with an interchangeable pipetting head that moves in Z over a small stage; the operator
places a plate or reservoir under the head by hand. It is programmed on its own
touchscreen and by INTEGRA's VIALINK software over a USB link (Type A-to-B, directly or
via the programming stand / communication module). There is no published automation API.

Its control model is different from a live command stream: VIALINK serializes a whole
pipetting program, transfers it into the pipette's memory, and the pipette then runs it
standalone (after the transfer, no host connection is required). So driving it headlessly
means recovering how a program is serialized and uploaded, plus the telemetry and run/stop
commands. The per-step aspirate/dispense semantics live inside the uploaded program, not
as separate USB commands; whether the link also exposes atomic per-step control is a bench
question, called out in the playbook.

Two paths, from safest to hardest, mirroring the Namocell Hana:

  * discovery (read-only, works today, zero decode): enumerate the USB/serial devices on
    the host to identify the pipette's control link before capturing anything. This is
    the transport-generic enumerator shared with the Namocell path; the CLI wires it in
    as `plr-re viaflo discover`. It only lists what is present; it opens no session and
    sends nothing.

  * IntegraViaflo96 (guarded byte replay): connect / get_status / get_identity /
    list_programs / select_program / upload_program / home / run_program / abort over the
    recovered command set, behind the same arming switches as every other backend. Dry-run
    until the ProtocolMap is decoded and the caller arms it with a human present.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from ..guards import Guards
from ..protocolmap import ProtocolMap, seed
from ..replay import GuardedReplayer

logger = logging.getLogger("plr_re")

# The VIAFLO 96 takes interchangeable 96-channel pipetting heads whose volume range
# depends on the installed head (e.g. 12.5, 125, 300, or 1250 uL heads). The controller
# clamps program step volumes to a ceiling the operator sets to match the installed head,
# the same shape as the Biotage temperature ceiling. Confirm the exact range for your head
# on the bench; the default here is a common mid head and must be overridden per head.
DEFAULT_MAX_VOLUME_UL = 300.0


class IntegraViaflo96:
  """Guarded controller for the Integra VIAFLO 96 over a byte transport.

  Until the ProtocolMap is decoded on the bench these calls dry-run: they log the frame
  they would send and transmit nothing. Once the map is filled in (a frame template per
  command, including the serialized-program upload), the same calls drive the pipette
  behind the guards. Read-only commands (get_status, get_identity, list_programs,
  select_program) are always allowed; anything that writes device memory or moves the head
  (upload_program, home, run_program, abort) is refused unless actuation is allowed.

  `max_volume_ul` is the installed pipetting head's ceiling. When a program's step volumes
  are known they are validated against it before anything is framed, so a program that
  would exceed the installed head cannot be uploaded.
  """

  def __init__(
    self,
    pm: Optional[ProtocolMap] = None,
    guards: Optional[Guards] = None,
    replayer: Optional[GuardedReplayer] = None,
    max_volume_ul: float = DEFAULT_MAX_VOLUME_UL,
  ):
    if max_volume_ul <= 0:
      raise ValueError("max_volume_ul must be > 0 (set it to the installed head's max)")
    self.pm = pm or seed("viaflo96")
    self.guards = guards or Guards()
    self.replayer = replayer or GuardedReplayer(self.pm, self.guards)
    self.max_volume_ul = float(max_volume_ul)

  def setup(self) -> None:
    self.replayer.setup()

  def stop(self) -> None:
    self.replayer.stop()

  # -- read-only -------------------------------------------------------------

  def connect(self):
    return self.replayer.send("connect")

  def get_status(self):
    return self.replayer.send("get_status")

  def get_identity(self):
    """Read the pipette's model / serial / firmware (the VIALINK library read). Read-only;
    it does not move the head or change stored programs, so it is not actuation."""
    return self.replayer.send("get_identity")

  def list_programs(self):
    return self.replayer.send("list_programs")

  def select_program(self, name: str):
    """Set the active program to run next by name. Selection only: it points the pipette
    at a stored program and moves nothing, so it is not actuation."""
    return self.replayer.send("select_program", name=name)

  # -- actuation (gated by the replayer) -------------------------------------

  def _check_volume(self, volume_ul: float) -> None:
    if not 0 < volume_ul <= self.max_volume_ul:
      raise ValueError(
        f"volume {volume_ul} uL is outside the installed head's range "
        f"(0, {self.max_volume_ul}] uL; set max_volume_ul to your head or fix the program."
      )

  def upload_program(self, program: str, *, volumes: Optional[Iterable[float]] = None):
    """Transfer a serialized pipetting program into device memory.

    Writing a program mutates the pipette's stored program set, so it is gated as
    actuation. If the program's per-step volumes are known, every one is validated against
    the installed head's ceiling before the program is framed, so a program that would
    exceed the head cannot be uploaded.
    """
    if volumes is not None:
      for v in volumes:
        self._check_volume(v)
    return self.replayer.send("upload_program", name=program)

  def home(self):
    return self.replayer.send("home")

  def run_program(self, name: Optional[str] = None):
    """Execute the active program. If `name` is given, select it first (a non-actuating
    selection) and then run it (actuation)."""
    if name is not None:
      self.select_program(name)
    return self.replayer.send("run_program")

  def abort(self):
    return self.replayer.send("abort")

  def run_named_program(self, program: str, *, volumes: Optional[Iterable[float]] = None):
    """Convenience sequence: upload a program, then select and run it. Each step is gated
    and dry-run until armed with a complete map, so calling this on an undecoded seed only
    previews the run and validates the program's volumes against the installed head."""
    self.upload_program(program, volumes=volumes)
    self.run_program(program)
