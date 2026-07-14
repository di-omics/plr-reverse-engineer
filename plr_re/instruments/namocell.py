"""Namocell Hana single-cell dispenser: read-only transport discovery and guarded
control of the sort/dispense workflow.

The Hana is a benchtop instrument driven by a bundled Windows PC running Namocell's
software over a byte link (USB-serial is the strong prior; the exact wire is confirmed
on the bench). It isolates cells from a disposable microfluidic cartridge on
fluorescence and light scatter and dispenses them into 96/384-well plates at low
sort pressure. There is no published automation API, so driving it headlessly means
recovering the OEM command set as a ProtocolMap and replaying it behind the guards.

Two paths, from safest to hardest:

  * discover_usb (read-only, works today, zero decode): enumerate the USB/serial
    devices attached to the host so you can identify the instrument's control link
    before capturing anything. It only lists what is present; it opens no session and
    sends nothing. This is the Namocell analog of `aviti probe` / `agilent scan`.

  * NamocellDispenser (guarded byte replay): connect / get_status / load_protocol /
    set_deposition / prime / start_sort / abort / clean over the recovered command
    set, behind the same arming switches as every other backend. Dry-run until the
    ProtocolMap is decoded and the caller arms it with a human present.
"""

from __future__ import annotations

import glob
import logging
from typing import List, Optional

from ..guards import Guards
from ..protocolmap import ProtocolMap, seed
from ..replay import GuardedReplayer

logger = logging.getLogger("plr_re")

# The Hana dispenses into standard SBS plates; other formats are refused before framing.
VALID_PLATE_FORMATS = (96, 384)

# USB vendor IDs of the common USB-serial bridges an instrument control link usually
# rides on. A match is a strong hint that a port is the dispenser's wire rather than an
# unrelated device; discovery flags it, the bench confirms it.
USB_SERIAL_BRIDGE_VIDS = {
  0x0403: "FTDI",
  0x10C4: "Silicon Labs (CP210x)",
  0x067B: "Prolific",
  0x1A86: "QinHeng (CH340)",
}


# -- read-only transport discovery (no decoding, touches nothing) -------------


def discover_usb() -> List[dict]:
  """Enumerate attached USB/serial devices to find the dispenser's control link.

  Read-only: it lists what is plugged in and opens no session. Uses pyserial's
  list_ports when available (gives VID/PID and a description); falls back to globbing
  the usual tty device paths so the core stays stdlib-only. Each result is a dict
  (JSON-friendly) with a `likely_control` flag set when the VID matches a common
  USB-serial bridge. Confirm the real port and its framing on the bench before trusting
  it; this only narrows the search.
  """
  candidates: List[dict] = []
  try:
    from serial.tools import list_ports  # lazy; part of pyserial ([serial] extra)
  except ImportError:
    # stdlib fallback: list common USB-serial device nodes without VID/PID detail.
    for pattern in (
      "/dev/tty.usbserial*",
      "/dev/tty.usbmodem*",
      "/dev/ttyUSB*",
      "/dev/ttyACM*",
    ):
      for path in sorted(glob.glob(pattern)):
        candidates.append(
          {
            "path": path,
            "description": "",
            "vid": None,
            "pid": None,
            "likely_control": True,  # a USB-serial node is a plausible control link
          }
        )
    if not candidates:
      logger.info(
        "no USB-serial device nodes found; install the [serial] extra for VID/PID detail"
      )
    return candidates

  for p in list_ports.comports():
    vid = getattr(p, "vid", None)
    candidates.append(
      {
        "path": p.device,
        "description": (p.description or "").strip(),
        "vid": vid,
        "pid": getattr(p, "pid", None),
        "manufacturer": getattr(p, "manufacturer", None),
        "likely_control": vid in USB_SERIAL_BRIDGE_VIDS,
      }
    )
  return candidates


# -- guarded byte-replay control ----------------------------------------------


class NamocellDispenser:
  """Guarded controller for the Namocell Hana over a byte transport.

  Until the ProtocolMap is decoded on the bench these calls dry-run: they log the frame
  they would send and transmit nothing. Once the map is filled in (frame templates for
  each command), the same calls drive the instrument behind the guards. Read-only
  commands (get_status, wait_complete) are always allowed; anything that moves fluid,
  pressurizes the cartridge, or fires a sort is refused unless actuation is allowed.
  """

  def __init__(
    self,
    pm: Optional[ProtocolMap] = None,
    guards: Optional[Guards] = None,
    replayer: Optional[GuardedReplayer] = None,
  ):
    self.pm = pm or seed("namocell")
    self.guards = guards or Guards()
    self.replayer = replayer or GuardedReplayer(self.pm, self.guards)

  def setup(self) -> None:
    self.replayer.setup()

  def stop(self) -> None:
    self.replayer.stop()

  # -- read-only -------------------------------------------------------------

  def connect(self):
    return self.replayer.send("connect")

  def get_status(self):
    return self.replayer.send("get_status")

  def load_protocol(self, name: str):
    """Select the sort mode (single/bulk) and gating by a named OEM protocol. This
    configures the next run; it does not move the instrument, so it is not actuation."""
    return self.replayer.send("load_protocol", name=name)

  def wait_complete(self):
    return self.replayer.send("wait_complete")

  # -- actuation (gated by the replayer) -------------------------------------

  def set_deposition(self, plate_format: int, cells_per_well: int = 1):
    """Set the destination plate format and target cells-per-well, and stage the plate.

    Refuses a plate format the Hana does not dispense into, before anything is framed,
    so a bad format cannot reach the stage.
    """
    if plate_format not in VALID_PLATE_FORMATS:
      raise ValueError(
        f"plate_format {plate_format} is not supported; the Hana dispenses into "
        f"{' or '.join(str(f) for f in VALID_PLATE_FORMATS)}-well plates."
      )
    if cells_per_well < 0:
      raise ValueError("cells_per_well must be >= 0")
    return self.replayer.send(
      "set_deposition", plate=int(plate_format), cells=int(cells_per_well)
    )

  def prime(self):
    return self.replayer.send("prime")

  def start_sort(self):
    return self.replayer.send("start_sort")

  def abort(self):
    return self.replayer.send("abort")

  def clean(self):
    return self.replayer.send("clean")

  def sort_to_plate(
    self, *, protocol: str, plate_format: int = 96, cells_per_well: int = 1
  ):
    """Convenience sequence: select the protocol, stage the plate, prime the cartridge,
    and start dispensing. Each step is gated and dry-run until armed with a complete
    map, so calling this on an undecoded seed only previews the run."""
    self.load_protocol(protocol)
    self.set_deposition(plate_format, cells_per_well)
    self.prime()
    self.start_sort()
