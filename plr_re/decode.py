"""Decode workbench: correlate captures into decoded commands.

Two tools that cover most of the framing work:

  * diff_frames: vary one parameter, capture two frames, and see exactly which bytes
    changed. This is how each parameter encoding is located.
  * Modbus RTU: if a serial bus decodes as Modbus (common for HMI-to-controller links),
    the register map falls out almost for free.
"""

from __future__ import annotations

import json
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
