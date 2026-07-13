"""Decode workbench: correlate captures into decoded commands.

Two tools that cover most of the framing work:

  * diff_frames: vary one parameter, capture two frames, and see exactly which bytes
    changed. This is how each parameter encoding is located.
  * Modbus RTU: if a serial bus decodes as Modbus (common for HMI-to-controller links),
    the register map falls out almost for free.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class ByteDiff:
  offset: int
  a: Optional[int]
  b: Optional[int]


def diff_frames(a: bytes, b: bytes) -> List[ByteDiff]:
  """Return the positions where two frames differ (including length differences)."""
  out: List[ByteDiff] = []
  n = max(len(a), len(b))
  for i in range(n):
    av = a[i] if i < len(a) else None
    bv = b[i] if i < len(b) else None
    if av != bv:
      out.append(ByteDiff(offset=i, a=av, b=bv))
  return out


def format_diff(a: bytes, b: bytes) -> str:
  diffs = diff_frames(a, b)
  if not diffs:
    return "frames identical"
  lines = [f"{len(diffs)} differing byte(s):"]
  for d in diffs:
    av = "--" if d.a is None else f"{d.a:02x}"
    bv = "--" if d.b is None else f"{d.b:02x}"
    lines.append(f"  offset {d.offset:>4}: {av} -> {bv}")
  return "\n".join(lines)


# -- Modbus RTU --------------------------------------------------------------

MODBUS_FUNCTIONS = {
  0x01: "read_coils",
  0x02: "read_discrete_inputs",
  0x03: "read_holding_registers",
  0x04: "read_input_registers",
  0x05: "write_single_coil",
  0x06: "write_single_register",
  0x0F: "write_multiple_coils",
  0x10: "write_multiple_registers",
}


def crc16_modbus(data: bytes) -> int:
  """Standard Modbus RTU CRC16 (poly 0xA001), returned as the on-wire little-endian int
  value (low byte first when appended to a frame)."""
  crc = 0xFFFF
  for byte in data:
    crc ^= byte
    for _ in range(8):
      if crc & 1:
        crc = (crc >> 1) ^ 0xA001
      else:
        crc >>= 1
  return crc


@dataclass
class ModbusFrame:
  address: int
  function: int
  function_name: str
  data: bytes
  crc_ok: bool
  register: Optional[int] = None
  value: Optional[int] = None


def parse_modbus_rtu(frame: bytes) -> ModbusFrame:
  """Parse a Modbus RTU frame and validate its CRC. For the two write functions that
  matter most for setpoints (0x06, 0x10) it also pulls out the register and value."""
  if len(frame) < 4:
    raise ValueError("frame too short for Modbus RTU (need addr, func, and CRC)")
  address = frame[0]
  function = frame[1]
  body = frame[:-2]
  crc_on_wire = frame[-2] | (frame[-1] << 8)
  crc_ok = crc16_modbus(body) == crc_on_wire
  data = frame[2:-2]

  register = value = None
  if function == 0x06 and len(data) >= 4:  # write single register
    register = (data[0] << 8) | data[1]
    value = (data[2] << 8) | data[3]
  elif function == 0x10 and len(data) >= 5:  # write multiple registers
    register = (data[0] << 8) | data[1]
    # count at data[2..3], byte count at data[4], first value follows
    if len(data) >= 7:
      value = (data[5] << 8) | data[6]

  return ModbusFrame(
    address=address,
    function=function,
    function_name=MODBUS_FUNCTIONS.get(function, f"0x{function:02x}"),
    data=data,
    crc_ok=crc_ok,
    register=register,
    value=value,
  )


def build_write_single_register(address: int, register: int, value: int) -> bytes:
  """Build a Modbus RTU write-single-register (0x06) frame with a valid CRC. Useful as a
  replay frame_template once the register map is known."""
  body = bytes(
    [address, 0x06, (register >> 8) & 0xFF, register & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
  )
  crc = crc16_modbus(body)
  return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def scan_modbus_stream(data: bytes) -> List[Tuple[int, ModbusFrame]]:
  """Find every CRC-valid Modbus RTU frame in a raw byte stream.

  A serial capture is a stream of bytes with no explicit frame boundaries, so this walks
  the stream and, at each offset, accepts the shortest CRC-valid frame (a false positive
  at the wrong length is a 1-in-65536 CRC collision, so this is reliable in practice).
  Returns (offset, frame) pairs. Point it at a V-10 HMI-bus capture and the register
  writes fall out.
  """
  out: List[Tuple[int, ModbusFrame]] = []
  i = 0
  n = len(data)
  while i <= n - 4:
    matched = False
    upper = min(256, n - i)
    for length in range(4, upper + 1):
      frame = data[i : i + length]
      crc_on_wire = frame[-2] | (frame[-1] << 8)
      if crc16_modbus(frame[:-2]) == crc_on_wire:
        out.append((i, parse_modbus_rtu(frame)))
        i += length
        matched = True
        break
    if not matched:
      i += 1
  return out


def load_serial_log(path: str) -> bytes:
  """Concatenate a serial capture written by `plr-re capture serial` (JSONL of
  {t, hex}) back into one byte stream for framing."""
  chunks: List[bytes] = []
  with open(path, encoding="utf-8") as fh:
    for line in fh:
      line = line.strip()
      if not line:
        continue
      chunks.append(bytes.fromhex(json.loads(line)["hex"]))
  return b"".join(chunks)


# -- HTTP / HAR --------------------------------------------------------------
# The HTTP analog of diff/Modbus. An HTTP/JSON microservice stack (AvitiOS) is
# reverse-engineered by capturing the UI-to-service traffic as a HAR and reading the
# API calls out of it: which request an action produced, its method and path, and its
# JSON body. State-changing verbs (POST/PUT/PATCH/DELETE) are the candidate actuation
# commands (start_run, abort_run, upload_manifest); GET/HEAD are read-only.

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class HttpCall:
  method: str
  url: str
  host: str
  path: str
  status: Optional[int]
  request_body: Optional[str]

  @property
  def is_write(self) -> bool:
    return self.method.upper() in WRITE_METHODS


def _split_url(url: str) -> Tuple[str, str]:
  """Return (host, path) from a URL without pulling in urllib for a simple split."""
  rest = url.split("://", 1)[-1]
  slash = rest.find("/")
  if slash < 0:
    return rest, "/"
  return rest[:slash], rest[slash:]


def parse_har(source) -> List[HttpCall]:
  """Parse a HAR (dict, JSON text, or file path) into a list of HttpCall.

  A HAR is the standard capture format that browsers export from devtools and that
  mitmproxy can write, so this is transport-agnostic: it works whether the UI is a
  local web app or the traffic was intercepted on the wire.
  """
  if isinstance(source, str):
    text = open(source, encoding="utf-8").read() if os.path.exists(source) else source
    har = json.loads(text)
  else:
    har = source
  calls: List[HttpCall] = []
  for entry in har.get("log", {}).get("entries", []):
    req = entry.get("request", {})
    resp = entry.get("response", {})
    url = req.get("url", "")
    host, path = _split_url(url)
    post = req.get("postData", {}) or {}
    body = post.get("text")
    calls.append(
      HttpCall(
        method=req.get("method", "GET"),
        url=url,
        host=host,
        path=path,
        status=resp.get("status"),
        request_body=body,
      )
    )
  return calls


def summarize_har(calls: List[HttpCall]) -> List[dict]:
  """Fold HTTP calls into unique (method, path) endpoints, marking the writes and the
  likely status/keep-alive polling.

  This applies the PyLabRobot reverse-engineering heuristics directly. The writes
  (POST/PUT/PATCH/DELETE) are the reverse-engineering targets: perform one UI action at
  a time and the state-changing request it produced is the command to record. And, per
  the PLR guide, "frequently repeated commands are often status/keep-alive messages", so
  a read endpoint that repeats is flagged as likely polling and sorted to the bottom,
  out of the way of the action you are looking for.
  """
  seen: Dict[Tuple[str, str], dict] = {}
  for c in calls:
    key = (c.method.upper(), c.path)
    row = seen.get(key)
    if row is None:
      seen[key] = {
        "method": c.method.upper(),
        "path": c.path,
        "host": c.host,
        "is_write": c.is_write,
        "count": 1,
        "has_body": bool(c.request_body),
      }
    else:
      row["count"] += 1
      row["has_body"] = row["has_body"] or bool(c.request_body)
  for row in seen.values():
    # A repeated read is probably the UI's status/keep-alive poll, not a discrete action.
    row["likely_status"] = (not row["is_write"]) and row["count"] >= 2
  # writes first (candidate actuation), meaningful reads next, keep-alive polling last
  return sorted(
    seen.values(), key=lambda r: (not r["is_write"], r["likely_status"], r["path"])
  )
