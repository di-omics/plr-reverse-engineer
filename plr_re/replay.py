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
from .transports import (
  ByteConn,
  HttpConn,
  HttpConnProto,
  MockByteConn,
  MockHttpConn,
  open_byte_conn,
)

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


def fill_template(template: Optional[str], params: dict) -> Optional[str]:
  """Substitute {param} placeholders in an HTTP path or JSON body template.

  Values are inserted as JSON so a string stays quoted and a number/bool stays bare,
  which keeps a decoded body_template valid JSON after filling.
  """
  if template is None:
    return None
  import json

  out = template
  for key, value in params.items():
    out = out.replace("{" + key + "}", json.dumps(value))
  return out


class GuardedHttpReplayer:
  """Guarded replay for an HTTP/JSON control plane (Transport.HTTP).

  Same two arming switches and coverage gate as the byte replayer, so the AvitiOS
  producer side and any PyLabRobot consumer behave identically:

    * armed=False        dry run: log the exact request, send nothing.
    * allow_actuation    required to send any actuating command, even when armed.
    * coverage gate      an armed run refuses to start while any required command is
                         still undecoded.

  A command carries http_method + http_path and an optional JSON body_template with
  {param} placeholders. Nothing is sent until the map says how.
  """

  def __init__(
    self,
    pm: ProtocolMap,
    guards: Optional[Guards] = None,
    conn: Optional[HttpConnProto] = None,
    verify_tls: bool = True,
    token: Optional[str] = None,
  ):
    self.pm = pm
    self.guards = guards or Guards()
    self._conn = conn
    self._own_conn = False
    self._verify_tls = verify_tls
    self._token = token

  def setup(self) -> None:
    cov = self.pm.coverage()
    if self.guards.armed and cov["missing"]:
      raise ProtocolMapIncompleteError(cov["missing"])
    if self.guards.armed and self._conn is None:
      if self.pm.endpoint is None:
        raise RuntimeError("ProtocolMap has no endpoint; run transport discovery first.")
      self._conn = HttpConn(self.pm.endpoint, verify_tls=self._verify_tls, token=self._token)
      self._own_conn = True
      logger.info("%s connected via http at %s", self.pm.device, self.pm.endpoint)
    elif not self.guards.armed:
      logger.warning(
        "%s HTTP replayer in DRY-RUN (armed=False): no request sent", self.pm.device
      )

  def stop(self) -> None:
    if self._own_conn and self._conn is not None:
      self._conn.close()
    self._conn = None
    self._own_conn = False

  def send(self, command: str, **params):
    """Send a decoded HTTP command by logical name. Dry-run unless armed."""
    cmd = self.pm.commands.get(command)
    if cmd is None:
      raise KeyError(f"'{command}' is not in the ProtocolMap for {self.pm.device}")
    self.guards.check_actuation(command, cmd.actuating)

    method = (cmd.http_method or "GET").upper()
    path = fill_template(cmd.http_path, params)
    body_str = fill_template(cmd.body_template, params)

    if not self.guards.transmitting() or self._conn is None:
      logger.warning(
        "[dry-run] would %s %s%s", method, path, f" body={body_str}" if body_str else ""
      )
      return None
    if path is None:
      raise ProtocolMapIncompleteError([command])
    body = body_str.encode("utf-8") if body_str is not None else None
    logger.info("HTTP %s %s%s", method, path, f" body={body_str}" if body_str else "")
    resp = self._conn.request(method, path, body=body)
    logger.info("RECV: %s %s", resp.status, resp.body[:200] if resp.body else b"")
    return resp

  @classmethod
  def dry(cls, pm: ProtocolMap) -> "GuardedHttpReplayer":
    """A dry-run HTTP replayer backed by a recording mock (for tests/preview)."""
    return cls(pm, Guards(armed=False), conn=MockHttpConn())
