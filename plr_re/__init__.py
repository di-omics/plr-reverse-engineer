"""plr-re: reverse-engineering toolkit for bringing no-API lab instruments under
PyLabRobot control.

Core is stdlib-only. Hardware libraries (pyserial, gpiozero) are imported lazily by
the transport and instrument modules, so capture, decode, and dry-run replay run
anywhere. Everything that drives hardware is dry-run by default and refuses actuation
unless explicitly allowed. See guards.py.
"""

from .guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from .protocolmap import Command, ProtocolMap, Transport, seed

__version__ = "0.1.0"

__all__ = [
  "Guards",
  "ActuationNotAllowed",
  "ProtocolMapIncompleteError",
  "ProtocolMap",
  "Command",
  "Transport",
  "seed",
]
