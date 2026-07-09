"""Safety guards shared by every instrument controller.

Two independent switches, both off by default, gate any real transmission:

  * armed            open a transport and transmit bytes / drive GPIO only if True.
  * allow_actuation  commands that move the instrument (start a run, heat, pull vacuum,
                     fire a sort, pulse a Start line) are refused unless this is True.

With armed=False everything is a dry run: it logs the exact action it would take and
transmits nothing. allow_actuation only gates a real transmission of an actuating
command; a dry run still previews it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("plr_re")


class ActuationNotAllowed(RuntimeError):
  """Raised when an actuating command is requested for live transmission while
  allow_actuation is False."""


class ProtocolMapIncompleteError(RuntimeError):
  """Raised when an armed run is attempted against a ProtocolMap that still has
  undecoded required commands."""

  def __init__(self, missing):
    self.missing = list(missing)
    super().__init__(
      "ProtocolMap is incomplete; these required commands are still undecoded: "
      + ", ".join(self.missing)
    )


@dataclass
class Guards:
  armed: bool = False
  allow_actuation: bool = False

  def transmitting(self) -> bool:
    """True if this call should actually touch hardware."""
    return self.armed

  def check_actuation(self, command: str, actuating: bool) -> None:
    """Raise if an actuating command would be transmitted without permission.

    Only enforced when armed. A dry run previews actuating commands without raising.
    """
    if self.armed and actuating and not self.allow_actuation:
      raise ActuationNotAllowed(
        f"'{command}' actuates the instrument; construct with allow_actuation=True "
        "and a human present to send it."
      )

  def dry_run_note(self, what: str) -> None:
    logger.warning("[dry-run] would %s", what)
