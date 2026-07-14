"""Transports: byte connections (serial, TCP, USB) and a contact-closure GPIO interface.

Hardware libraries are imported lazily inside the real implementations, so importing
this module never requires pyserial, pyusb, or gpiozero. Mock implementations back
dry-run and tests and record what they were asked to do.
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


# -- raw USB bulk ------------------------------------------------------------
# For an instrument whose control link is a raw USB device rather than a USB-serial
# bridge (the Namocell raw-USB branch). pyusb is imported lazily, so the core stays
# dependency-free; install the [usb] extra (pyusb) plus a libusb backend on the host.


@dataclass
class UsbEndpointSpec:
  """A parsed USB endpoint spec. VID/PID and endpoint addresses are hex (USB
  identifiers are always hexadecimal); bulk OUT/IN are auto-detected when omitted."""

  vid: int
  pid: int
  ep_out: Optional[int] = None
  ep_in: Optional[int] = None
  interface: int = 0


def parse_usb_endpoint(endpoint: str) -> UsbEndpointSpec:
  """Parse 'usb:VID:PID[/out=EP,in=EP,iface=N]' into a UsbEndpointSpec.

  VID, PID, and endpoint addresses are hexadecimal, with an optional leading 0x. The
  'usb:' scheme prefix is optional. Omitted OUT/IN endpoints are resolved from the
  interface's first bulk endpoints at open time.
  """
  spec = endpoint.strip()
  if spec.lower().startswith("usb:"):
    spec = spec[4:]
  opts: dict = {}
  if "/" in spec:
    spec, opt_str = spec.split("/", 1)
    for kv in opt_str.split(","):
      if not kv.strip():
        continue
      key, _, val = kv.partition("=")
      opts[key.strip().lower()] = val.strip()
  if ":" not in spec:
    raise ValueError(
      f"USB endpoint '{endpoint}' must be 'usb:VID:PID' (hex), e.g. usb:0x1234:0x5678"
    )
  vid_s, pid_s = spec.split(":", 1)
  out = UsbEndpointSpec(vid=int(vid_s, 16), pid=int(pid_s, 16))
  if "out" in opts:
    out.ep_out = int(opts["out"], 16)
  if "in" in opts:
    out.ep_in = int(opts["in"], 16)
  if "iface" in opts:
    # Interface numbers are small decimal indices; accept a 0x prefix for an explicit hex.
    out.interface = int(opts["iface"], 0)
  return out


class UsbConn:
  """Real raw-USB bulk connection over pyusb.

  `endpoint` is 'usb:VID:PID[/out=EP,in=EP,iface=N]'. Finds the device, claims the
  interface (detaching a kernel driver on Linux where possible), and writes/reads over
  its bulk OUT/IN endpoints. A read timeout returns empty rather than raising, matching
  the other byte connections. This transports only what the guarded replayer hands it;
  it adds no commands of its own.
  """

  def __init__(self, endpoint, timeout: float = 1.0):
    try:
      import usb.core  # lazy; part of the [usb] extra
      import usb.util
    except ImportError as e:
      raise RuntimeError(
        "USB transport needs pyusb; `pip install .[usb]` (and a libusb backend on the "
        "host). If the link is a USB-serial bridge, use transport 'serial' instead."
      ) from e

    self._util = usb.util
    self._USBError = usb.core.USBError
    self.timeout_ms = int(timeout * 1000)
    spec = parse_usb_endpoint(endpoint) if isinstance(endpoint, str) else endpoint

    try:
      dev = usb.core.find(idVendor=spec.vid, idProduct=spec.pid)
    except usb.core.NoBackendError as e:
      # pyusb is pure Python and imports fine, but the native libusb library it drives
      # was not found. This is the missing-backend case the message above refers to; it
      # only surfaces here, on the first call that actually touches the backend.
      raise RuntimeError(
        "USB transport needs a native libusb backend on the host (pyusb is installed "
        "but no libusb library was found); install libusb (e.g. `brew install libusb` "
        "or `apt install libusb-1.0-0`), or use transport 'serial' if the link is a "
        "USB-serial bridge."
      ) from e
    if dev is None:
      raise RuntimeError(
        f"no USB device {spec.vid:04x}:{spec.pid:04x} found; check it is connected and "
        "that you have permission (udev rule / run as a user that can claim it)."
      )
    # A kernel driver may hold the interface on Linux; release it if we can.
    try:
      if dev.is_kernel_driver_active(spec.interface):
        dev.detach_kernel_driver(spec.interface)
    except (NotImplementedError, self._USBError):
      pass
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(spec.interface, 0)]
    self.dev = dev
    self.intf = intf
    self._ep_out = self._resolve_ep(spec.ep_out, usb.util.ENDPOINT_OUT)
    self._ep_in = self._resolve_ep(spec.ep_in, usb.util.ENDPOINT_IN)

  def _resolve_ep(self, explicit: Optional[int], direction):
    if explicit is not None:
      ep = self._util.find_descriptor(self.intf, bEndpointAddress=explicit)
      if ep is None:
        raise RuntimeError(f"USB endpoint 0x{explicit:02x} not present on the interface")
      return ep
    ep = self._util.find_descriptor(
      self.intf,
      custom_match=lambda e: (
        self._util.endpoint_direction(e.bEndpointAddress) == direction
        and self._util.endpoint_type(e.bmAttributes) == self._util.ENDPOINT_TYPE_BULK
      ),
    )
    if ep is None:
      side = "OUT" if direction == self._util.ENDPOINT_OUT else "IN"
      raise RuntimeError(
        f"no bulk {side} endpoint on the interface; give it explicitly with "
        "/out=0xNN,in=0xNN once identified from a usbmon capture."
      )
    return ep

  def write(self, data: bytes) -> None:
    self._ep_out.write(bytes(data), timeout=self.timeout_ms)

  def read(self, size: int = 512) -> bytes:
    try:
      return bytes(self._ep_in.read(size, timeout=self.timeout_ms))
    except self._USBError:
      # A timeout with no data pending is normal for a read poll; report empty.
      return b""

  def close(self) -> None:
    try:
      self._util.dispose_resources(self.dev)
    except Exception:  # noqa: BLE001 - best-effort cleanup
      pass


def open_byte_conn(transport: str, endpoint: str, timeout: float = 1.0) -> ByteConn:
  """Open a real byte connection. `endpoint` is 'host:port' for TCP, a device path
  (optionally 'path@baud') for serial, or 'usb:VID:PID[/out=EP,in=EP]' for raw USB."""
  if transport == "tcp":
    host, port = endpoint.rsplit(":", 1)
    return TcpConn(host, int(port), timeout=timeout)
  if transport == "serial":
    if "@" in endpoint:
      path, baud = endpoint.rsplit("@", 1)
      return SerialConn(path, baudrate=int(baud), timeout=timeout)
    return SerialConn(endpoint, timeout=timeout)
  if transport == "usb":
    return UsbConn(endpoint, timeout=timeout)
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
