"""Guarded replay engine.

Loads a decoded ProtocolMap and replays commands by logical name over a byte
connection, behind the shared guards. Mirrors the FACSMelody backend's send() so the
producer side and the PyLabRobot consumer side behave identically:

  * armed=False        dry run: log the exact frame, transmit nothing.
  * allow_actuation     required to transmit any actuating command, even when armed.
  * coverage gate       an armed run refuses to start while any required command is
                        undecoded.
"""

from __future__ import annotations

import logging
from typing import Optional

from .guards import Guards, ProtocolMapIncompleteError
from .protocolmap import ProtocolMap
from .transports import ByteConn, MockByteConn, open_byte_conn

logger = logging.getLogger("plr_re")


def encode_param(value) -> str:
  """Placeholder parameter encoder. The real byte encoding is discovered during RE
  (vary one parameter, diff the frames) and wired in per parameter once decoded."""
  if isinstance(value, bool):
    return "01" if value else "00"
  if isinstance(value, int):
    return f"{value & 0xFF:02x}"
  if isinstance(value, bytes):
    return value.hex()
  return str(value).encode().hex()


class GuardedReplayer:
  def __init__(
    self,
    pm: ProtocolMap,
    guards: Optional[Guards] = None,
    conn: Optional[ByteConn] = None,
  ):
    self.pm = pm
    self.guards = guards or Guards()
    self._conn = conn
    self._own_conn = False

  def setup(self) -> None:
    """Enforce the coverage gate and, if armed, open the transport."""
    cov = self.pm.coverage()
    if self.guards.armed and cov["missing"]:
      raise ProtocolMapIncompleteError(cov["missing"])
    if self.guards.armed and self._conn is None:
      if self.pm.endpoint is None:
        raise RuntimeError("ProtocolMap has no endpoint; run transport discovery first.")
      self._conn = open_byte_conn(self.pm.transport.value, self.pm.endpoint)
      self._own_conn = True
      logger.info(
        "%s connected via %s at %s",
        self.pm.device,
        self.pm.transport.value,
        self.pm.endpoint,
      )
    elif not self.guards.armed:
      logger.warning("%s replayer in DRY-RUN (armed=False): no transport opened", self.pm.device)

  def stop(self) -> None:
    if self._own_conn and self._conn is not None:
      self._conn.close()
    self._conn = None
    self._own_conn = False

  def send(self, command: str, **params) -> Optional[bytes]:
    """Send a decoded command by logical name. Dry-run unless armed."""
    cmd = self.pm.commands.get(command)
    if cmd is None:
      raise KeyError(f"'{command}' is not in the ProtocolMap for {self.pm.device}")
    actuating = cmd.actuating
    self.guards.check_actuation(command, actuating)

    frame = cmd.frame_template
    if frame is not None:
      for key, value in params.items():
        frame = frame.replace("{" + key + "}", encode_param(value))

    if not self.guards.transmitting() or self._conn is None:
      logger.warning("[dry-run] would send '%s': %s %s", command, frame, params or "")
      return None
    if frame is None:
      raise ProtocolMapIncompleteError([command])
    data = bytes.fromhex(frame)
    logger.info("SEND '%s': %s", command, frame)
    self._conn.write(data)
    resp = self._conn.read()
    logger.info("RECV: %s", resp.hex() if resp else "<none>")
    return resp

  @classmethod
  def dry(cls, pm: ProtocolMap) -> "GuardedReplayer":
    """A dry-run replayer backed by a recording mock connection (for tests/preview)."""
    return cls(pm, Guards(armed=False), conn=MockByteConn())
