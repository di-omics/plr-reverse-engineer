"""Transports: byte connections (serial, TCP) and a contact-closure GPIO interface.

Hardware libraries are imported lazily inside the real implementations, so importing
this module never requires pyserial or gpiozero. Mock implementations back dry-run and
tests and record what they were asked to do.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

logger = logging.getLogger("plr_re")


# -- byte connections --------------------------------------------------------


class ByteConn(Protocol):
  def write(self, data: bytes) -> None: ...
  def read(self, size: int = 512) -> bytes: ...
  def close(self) -> None: ...


class MockByteConn:
  """Records writes; returns queued or empty reads. Backs dry-run and tests."""

  def __init__(self, responses: Optional[List[bytes]] = None):
    self.writes: List[bytes] = []
    self._responses = list(responses or [])

  def write(self, data: bytes) -> None:
    self.writes.append(bytes(data))

  def read(self, size: int = 512) -> bytes:
    return self._responses.pop(0) if self._responses else b""

  def close(self) -> None:
    pass


class SerialConn:
  def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0, **kw):
    import serial  # lazy

    self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout, **kw)

  def write(self, data: bytes) -> None:
    self.ser.write(data)

  def read(self, size: int = 512) -> bytes:
    return self.ser.read(size)

  def close(self) -> None:
    self.ser.close()


class TcpConn:
  def __init__(self, host: str, port: int, timeout: float = 1.0):
    self.sock = socket.create_connection((host, port), timeout=timeout)

  def write(self, data: bytes) -> None:
    self.sock.sendall(data)

  def read(self, size: int = 512) -> bytes:
    try:
      return self.sock.recv(size)
    except OSError:
      return b""

  def close(self) -> None:
    self.sock.close()


def open_byte_conn(transport: str, endpoint: str, timeout: float = 1.0) -> ByteConn:
  """Open a real byte connection. `endpoint` is 'host:port' for TCP, a device path
  (optionally 'path@baud') for serial."""
  if transport == "tcp":
    host, port = endpoint.rsplit(":", 1)
    return TcpConn(host, int(port), timeout=timeout)
  if transport == "serial":
    if "@" in endpoint:
      path, baud = endpoint.rsplit("@", 1)
      return SerialConn(path, baudrate=int(baud), timeout=timeout)
    return SerialConn(endpoint, timeout=timeout)
  raise ValueError(f"open_byte_conn does not handle transport '{transport}'")


# -- HTTP(S)/JSON control plane ----------------------------------------------
# Modern microservice instruments (AvitiOS on the Element AVITI) speak an HTTP/JSON
# API rather than byte frames. These are stdlib-only (urllib), so the core stays
# dependency-free. A recording mock backs dry-run and tests.


@dataclass
class HttpResponse:
  status: int
  body: bytes

  def json(self):
    import json  # lazy; body may not be JSON

    return json.loads(self.body.decode("utf-8")) if self.body else None


class HttpConnProto(Protocol):
  def request(
    self, method: str, path: str, body: Optional[bytes] = None, headers: Optional[dict] = None
  ) -> "HttpResponse": ...
  def close(self) -> None: ...


class MockHttpConn:
  """Records requests; returns queued or default responses. Backs dry-run and tests."""

  def __init__(self, responses: Optional[List["HttpResponse"]] = None):
    self.requests: List[Tuple[str, str, Optional[bytes]]] = []
    self._responses = list(responses or [])

  def request(
    self, method: str, path: str, body: Optional[bytes] = None, headers: Optional[dict] = None
  ) -> "HttpResponse":
    self.requests.append((method, path, bytes(body) if body is not None else None))
    return self._responses.pop(0) if self._responses else HttpResponse(200, b"")

  def close(self) -> None:
    pass


class HttpConn:
  """Real HTTP(S) connection over urllib. `base_url` is the instrument origin, e.g.
  'https://192.168.1.50'. Self-signed instrument certificates are common, so TLS
  verification can be turned off deliberately; it stays on by default.

  A bearer token (from the recovered auth handshake) is sent as Authorization if set.
  This transport only sends what the guarded replayer hands it; it adds no commands of
  its own.
  """

  def __init__(
    self,
    base_url: str,
    timeout: float = 5.0,
    verify_tls: bool = True,
    token: Optional[str] = None,
  ):
    self.base_url = base_url.rstrip("/")
    self.timeout = timeout
    self.verify_tls = verify_tls
    self.token = token

  def request(
    self, method: str, path: str, body: Optional[bytes] = None, headers: Optional[dict] = None
  ) -> HttpResponse:
    import ssl
    import urllib.error
    import urllib.request

    url = self.base_url + (path if path.startswith("/") else "/" + path)
    hdrs = dict(headers or {})
    if body is not None:
      hdrs.setdefault("Content-Type", "application/json")
    if self.token:
      hdrs.setdefault("Authorization", f"Bearer {self.token}")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
    ctx = None
    if url.startswith("https") and not self.verify_tls:
      ctx = ssl.create_default_context()
      ctx.check_hostname = False
      ctx.verify_mode = ssl.CERT_NONE
    try:
      with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
        return HttpResponse(resp.status, resp.read())
    except urllib.error.HTTPError as e:
      # An HTTP error status is still a real response; hand it back rather than raising.
      return HttpResponse(e.code, e.read())

  def close(self) -> None:
    pass


# -- contact closure ---------------------------------------------------------


class ContactClosureIO(Protocol):
  def read_line(self, bcm_pin: int, active_low: bool) -> bool: ...
  def pulse(self, bcm_pin: int, seconds: float, active_low: bool) -> None: ...
  def close(self) -> None: ...


class MockContactClosureIO:
  """Records pulses and returns programmable line levels. Backs dry-run and tests."""

  def __init__(self, levels: Optional[dict] = None):
    # levels maps bcm_pin -> logical bool (already active-adjusted True/False)
    self.levels = dict(levels or {})
    self.pulses: List[Tuple[int, float]] = []

  def read_line(self, bcm_pin: int, active_low: bool) -> bool:
    return bool(self.levels.get(bcm_pin, False))

  def pulse(self, bcm_pin: int, seconds: float, active_low: bool) -> None:
    self.pulses.append((bcm_pin, seconds))

  def close(self) -> None:
    pass


class GpioContactClosureIO:
  """Real Pi GPIO via gpiozero. Inputs read a line; outputs pulse a contact closure.

  active_low reflects the APG/ERI convention where asserting a line pulls it low. For
  an input, a low electrical level then means logical-asserted (returns True). For an
  output pulse, we drive to the asserted level for `seconds` then release.
  """

  def __init__(self):
    import gpiozero  # lazy

    self._gz = gpiozero
    self._inputs = {}
    self._outputs = {}

  def _input(self, bcm_pin: int, active_low: bool):
    dev = self._inputs.get(bcm_pin)
    if dev is None:
      # pull_up so an open (unasserted) active-low line reads high/not-asserted.
      dev = self._gz.DigitalInputDevice(bcm_pin, pull_up=active_low)
      self._inputs[bcm_pin] = dev
    return dev

  def _output(self, bcm_pin: int, active_low: bool):
    dev = self._outputs.get(bcm_pin)
    if dev is None:
      # active_high False means .on() drives the pin low (asserted for active-low).
      dev = self._gz.DigitalOutputDevice(
        bcm_pin, active_high=not active_low, initial_value=False
      )
      self._outputs[bcm_pin] = dev
    return dev

  def read_line(self, bcm_pin: int, active_low: bool) -> bool:
    dev = self._input(bcm_pin, active_low)
    # DigitalInputDevice.is_active is True when the pin is at its active level, which
    # with pull_up=active_low corresponds to the line being asserted.
    return bool(dev.is_active)

  def pulse(self, bcm_pin: int, seconds: float, active_low: bool) -> None:
    dev = self._output(bcm_pin, active_low)
    dev.on()
    time.sleep(seconds)
    dev.off()

  def close(self) -> None:
    for dev in list(self._inputs.values()) + list(self._outputs.values()):
      try:
        dev.close()
      except Exception:  # noqa: BLE001 - best-effort cleanup
        pass
    self._inputs.clear()
    self._outputs.clear()
