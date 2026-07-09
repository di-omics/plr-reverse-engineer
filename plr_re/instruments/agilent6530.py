"""Agilent 6530 Q-TOF: Tier 0 contact-closure control over Pi GPIO.

This is the plug-in-and-go path. It needs no reverse-engineering: the rear APG remote
(1100/1200) or ERI (Infinity II) connector carries digital Ready / Start / Stop lines,
and a Pi reads Ready and pulses Start/Stop through opto-isolators. Fill the pin map in
after you identify each line with a meter and logic analyzer, then run armed.

Reading Ready is safe and allowed. Pulsing Start or Stop is actuation and is refused
unless allow_actuation=True.

Also here: probe_module(), a read-only LAN reachability/banner check for Tier 1.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Optional

from ..guards import Guards
from ..transports import ContactClosureIO, GpioContactClosureIO, MockContactClosureIO

logger = logging.getLogger("plr_re")


@dataclass
class AgilentPinMap:
  """BCM pin numbers for the remote lines, and the line convention.

  BCM pin numbers on the Pi side of the opto-isolators, and the line polarity.

  Per the Agilent APG remote spec the signals are TTL, idle high (5V):
    * Ready is HIGH-true: a high level means the system is ready. -> ready_active_low=False
    * Start and Stop are LOW-true: pull the line to ground to assert. -> out_active_low=True

  The opto-isolator stage between the instrument and the Pi can invert either sense, so
  identify and confirm each pin and its polarity on the bench (see `plr-re agilent scan`)
  before trusting these defaults.
  """

  ready: int
  start: int
  stop: int
  ready_active_low: bool = False  # APG Ready is HIGH-true
  out_active_low: bool = True  # APG Start/Stop are LOW-true (pull to assert)
  pulse_s: float = 0.25

  def to_json(self, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
      json.dump(self.__dict__, fh, indent=2)

  @classmethod
  def from_json(cls, path: str) -> "AgilentPinMap":
    with open(path, encoding="utf-8") as fh:
      return cls(**json.load(fh))


class Agilent6530Remote:
  def __init__(
    self,
    pinmap: AgilentPinMap,
    guards: Optional[Guards] = None,
    io: Optional[ContactClosureIO] = None,
  ):
    self.pinmap = pinmap
    self.guards = guards or Guards()
    if io is not None:
      self.io = io
    elif self.guards.armed:
      self.io = GpioContactClosureIO()
    else:
      # Dry run: a mock that reports not-ready and records would-be pulses.
      self.io = MockContactClosureIO()

  # -- read-only (safe) ------------------------------------------------------

  def is_ready(self) -> bool:
    return self.io.read_line(self.pinmap.ready, self.pinmap.ready_active_low)

  def status(self) -> dict:
    return {"ready": self.is_ready(), "armed": self.guards.armed}

  def wait_ready(self, timeout_s: float = 600.0, poll_s: float = 1.0) -> bool:
    waited = 0.0
    while waited < timeout_s:
      if self.is_ready():
        return True
      time.sleep(poll_s)
      waited += poll_s
    return False

  # -- actuation (gated) -----------------------------------------------------

  def start_run(self) -> None:
    self.guards.check_actuation("start_run", actuating=True)
    if not self.guards.transmitting():
      self.guards.dry_run_note(
        f"pulse START on BCM{self.pinmap.start} for {self.pinmap.pulse_s}s"
      )
      return
    logger.info("pulse START on BCM%s", self.pinmap.start)
    self.io.pulse(self.pinmap.start, self.pinmap.pulse_s, self.pinmap.out_active_low)

  def stop_run(self) -> None:
    self.guards.check_actuation("stop_run", actuating=True)
    if not self.guards.transmitting():
      self.guards.dry_run_note(
        f"pulse STOP on BCM{self.pinmap.stop} for {self.pinmap.pulse_s}s"
      )
      return
    logger.info("pulse STOP on BCM%s", self.pinmap.stop)
    self.io.pulse(self.pinmap.stop, self.pinmap.pulse_s, self.pinmap.out_active_low)

  def scan_lines(self, candidate_pins, active_low: bool = False) -> dict:
    """Read the logical level of each candidate BCM input pin once.

    Use this to find which pin carries Ready: read with the instrument not-ready, put
    the instrument ready, read again, and see which pin flipped. `summarize_scan` folds
    a sequence of these reads into a per-pin changed/steady report.
    """
    return {pin: self.io.read_line(pin, active_low) for pin in candidate_pins}

  def close(self) -> None:
    self.io.close()


def summarize_scan(samples) -> dict:
  """Fold a list of scan_lines() readings (each a {pin: bool} dict) into a per-pin
  summary: whether the pin changed across samples and its first and last level. The pin
  that changed when you toggled the instrument's Ready state is your Ready line."""
  if not samples:
    return {}
  pins = sorted(samples[0])
  out = {}
  for pin in pins:
    seq = [bool(s.get(pin, False)) for s in samples]
    out[pin] = {"changed": len(set(seq)) > 1, "first": seq[0], "last": seq[-1]}
  return out


def probe_module(ip: str, port: int = 23, timeout: float = 2.0) -> dict:
  """Tier 1 read-only LAN check: is the module reachable, and does it return a banner?

  Tries a TCP connect (default port 23, the historical Telnet status interface) and reads
  whatever banner it offers. Purely passive; sends nothing. Returns reachability and any
  banner text so you can log module state without touching the control stream.
  """
  result = {"ip": ip, "port": port, "reachable": False, "banner": ""}
  try:
    with socket.create_connection((ip, port), timeout=timeout) as s:
      result["reachable"] = True
      s.settimeout(timeout)
      try:
        data = s.recv(256)
        result["banner"] = data.decode("latin-1", "replace").strip()
      except OSError:
        pass
  except OSError as e:
    result["error"] = str(e)
  return result
