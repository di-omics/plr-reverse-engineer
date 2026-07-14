"""Capture harness: record OEM-to-device traffic while you mark discrete UI actions.

This makes step 2 of every playbook one command. Two transports:

  * LAN: shells out to dumpcap/tshark to write a pcap (Agilent Tier 2).
  * Serial: reads a pyserial port and logs timestamped bytes (Biotage HMI bus).

Alongside either, a marks file records the wall-clock instant you performed each named
action, so the capture slices into action-aligned windows for correlation later.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Mark:
  t: float
  label: str


class Marks:
  """Append-only timestamped action marks, written as JSONL."""

  def __init__(self, path: str):
    self.path = path
    self.marks: List[Mark] = []

  def mark(self, label: str) -> Mark:
    m = Mark(t=time.time(), label=label)
    self.marks.append(m)
    with open(self.path, "a", encoding="utf-8") as fh:
      fh.write(json.dumps({"t": m.t, "label": m.label}) + "\n")
    return m

  def run_interactive(self) -> None:
    """Prompt loop: enter a label to timestamp an action, blank line to finish.

    Start your capture first (in another shell or via capture_lan/capture_serial),
    then perform one OEM action, come here and label it, and repeat.
    """
    print("Action marking. Type a label after each OEM action; blank line to stop.")
    while True:
      try:
        label = input("mark> ").strip()
      except EOFError:
        break
      if not label:
        break
      m = self.mark(label)
      print(f"  marked '{m.label}' at {m.t:.3f}")


def lan_capture_command(iface: str, out_pcap: str, hosts: Optional[List[str]] = None) -> List[str]:
  """Build the dumpcap/tshark command line. Prefers dumpcap (lighter, no dissectors)."""
  tool = "dumpcap" if shutil.which("dumpcap") else "tshark"
  cmd = [tool, "-i", iface, "-w", out_pcap]
  if hosts:
    expr = " or ".join(f"host {h}" for h in hosts)
    cmd += ["-f", expr]
  return cmd


def capture_lan(
  iface: str,
  out_pcap: str,
  hosts: Optional[List[str]] = None,
  seconds: Optional[float] = None,
) -> subprocess.Popen:
  """Start a LAN capture. Returns the process; caller marks actions then terminates it.

  Requires dumpcap or tshark (Wireshark) on PATH and permission to capture on `iface`.
  """
  if shutil.which("dumpcap") is None and shutil.which("tshark") is None:
    raise RuntimeError("neither dumpcap nor tshark found on PATH; install Wireshark")
  cmd = lan_capture_command(iface, out_pcap, hosts)
  if seconds is not None:
    cmd += ["-a", f"duration:{int(seconds)}"]
  return subprocess.Popen(cmd)


def http_capture_command(out_har: str, listen_port: int = 8080) -> List[str]:
  """Build a mitmdump command that intercepts HTTP(S) and writes a HAR.

  mitmproxy sits between the AvitiOS UI (or Elembio Cloud client) and the service and
  writes a HAR that `plr-re decode har` reads. Recent mitmproxy writes HAR via the
  `hardump` option. Point the client at this proxy and trust its CA to see inside TLS.
  """
  return [
    "mitmdump",
    "--listen-port",
    str(listen_port),
    "--set",
    f"hardump={out_har}",
  ]


def capture_http(out_har: str, listen_port: int = 8080) -> subprocess.Popen:
  """Start an HTTP(S) capture via mitmdump, writing a HAR for `decode har`.

  Requires mitmproxy (`pip install mitmproxy`). If the UI is a local web app you can
  reach with browser devtools, the zero-install alternative is to export a HAR from the
  Network tab and skip this entirely; `decode har` consumes either.
  """
  if shutil.which("mitmdump") is None:
    raise RuntimeError(
      "mitmdump not found on PATH; `pip install mitmproxy`, or export a HAR from the "
      "UI's browser devtools Network tab and pass it straight to `plr-re decode har`."
    )
  return subprocess.Popen(http_capture_command(out_har, listen_port))


def capture_serial(port: str, out_path: str, baud: int = 9600, seconds: Optional[float] = None):
  """Log timestamped serial bytes to `out_path` (JSONL of {t, hex}). Blocks until
  `seconds` elapse, or until interrupted. Requires pyserial."""
  import serial  # lazy

  ser = serial.Serial(port, baudrate=baud, timeout=0.2)
  deadline = None if seconds is None else time.time() + seconds
  try:
    with open(out_path, "a", encoding="utf-8") as fh:
      while deadline is None or time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
          fh.write(json.dumps({"t": time.time(), "hex": chunk.hex()}) + "\n")
          fh.flush()
  finally:
    ser.close()
