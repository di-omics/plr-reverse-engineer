"""Dress rehearsal for the Namocell Hana capture -> decode -> guarded-replay pipeline.

Run it with no hardware:  python examples/namocell_rehearsal.py

IMPORTANT: every byte frame below is SYNTHETIC. It is a plausible-shaped fake used only
to exercise the toolkit end to end WITHOUT the instrument; it is NOT a recovered Namocell
command set, and nothing here is a real Namocell protocol value. On bench day the only
change is to replace these synthetic frames with the real ones captured off the box (via
`plr-re capture serial` or `plr-re capture usb`); every other line stays the same.

This mirrors what the device-free tests assert, but as a readable walk-through so you can
see each stage and both safety gates before a real unit is ever connected.
"""

import logging
import sys

from plr_re.decode import format_diff
from plr_re.guards import ActuationNotAllowed, Guards
from plr_re.protocolmap import Transport, seed
from plr_re.replay import GuardedReplayer, encode_param
from plr_re.transports import MockByteConn

# Route logging to stdout so the "[dry-run] would send" lines stay in order with prints.
logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(message)s")


def rule(title):
  print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


def main():
  # 1. -- SYNTHETIC capture: two "set deposition" frames, one parameter varied --
  # Framing (invented for the demo): AA | opcode | plate-format code | cells | sum | CC.
  # We captured the OEM app setting a 96-well plate, then a 384-well plate. Only the
  # plate-format byte should move; that is how `decode diff` locates the field.
  rule("1. SYNTHETIC capture: set-deposition frame, 96-well vs 384-well")
  frame_96 = bytes.fromhex("aa03011157cc")  # plate code 01 = 96-well
  frame_384 = bytes.fromhex("aa03021158cc")  # plate code 02 = 384-well (checksum moves too)
  print("  96-well :", frame_96.hex())
  print("  384-well:", frame_384.hex())

  rule("2. decode diff: which byte(s) carry the plate format")
  print(format_diff(frame_96, frame_384))
  print("\n  -> offset 2 is the plate-format code (01/02); offset 4 is the checksum that")
  print("     follows from it. That is the whole 'vary one parameter and diff' move.")

  # 3. -- Build the ProtocolMap from what we just decoded ----------------------
  rule("3. Build the ProtocolMap (seed -> fill decoded commands)")
  pm = seed("namocell")
  print(f"  seeded: {pm.device}, {len(pm.commands)} commands, coverage {pm.coverage()['decoded']}/9")
  sd = pm.commands["set_deposition"]
  sd.transport = Transport.SERIAL
  sd.frame_template = "aa03{plate}{cells}00cc"  # checksum recomputed by the real backend
  sd.decoded = True
  print("  decoded set_deposition ->", sd.frame_template)
  cov = pm.coverage()
  print(f"  coverage now {cov['decoded']}/9  (still incomplete: {len(cov['missing'])} to go)")

  # 4. -- The coverage gate refuses an armed run on a half-mapped protocol -----
  rule("4. Safety gate A: an armed run refuses a half-decoded map")
  armed = GuardedReplayer(pm, Guards(armed=True), conn=MockByteConn())
  try:
    armed.setup()
    print("  ERROR: should not reach here")
  except Exception as e:  # ProtocolMapIncompleteError
    print(f"  refused as expected: {type(e).__name__}")
    print(f"    {e}")

  # 5. -- Complete the (SYNTHETIC) map, then exercise guarded replay -----------
  rule("5. Complete the SYNTHETIC map, then exercise guarded replay")
  for c in pm.commands.values():
    if not c.decoded:
      c.transport = Transport.SERIAL
      c.frame_template = "aa00cc"  # synthetic placeholder frame per command
      c.decoded = True
  pm.endpoint = "/dev/ttyUSB0@115200"
  print(f"  coverage {pm.coverage()['decoded']}/9  -> complete")

  # 5a. dry-run (unarmed): previews, transmits nothing
  conn = MockByteConn()
  dry = GuardedReplayer(pm, Guards(armed=False), conn=conn)
  dry.setup()
  dry.send("set_deposition", plate=2, cells=1)  # 384-well, 1 cell/well
  print(f"  [dry-run]  set_deposition previewed; bytes actually written: {conn.writes}")

  # 5b. armed + actuation allowed: the filled frame goes to the (mock) wire
  conn = MockByteConn(responses=[b"\x06"])
  live = GuardedReplayer(pm, Guards(armed=True, allow_actuation=True), conn=conn)
  live.setup()
  live.send("set_deposition", plate=2, cells=1)
  filled = "aa03" + encode_param(2) + encode_param(1) + "00cc"
  print(f"  [armed]    set_deposition sent frame: {conn.writes[0].hex()}  (expected {filled})")

  # 6. -- Actuation is refused without the explicit opt-in ---------------------
  rule("6. Safety gate B: actuation is refused without the explicit opt-in")
  conn = MockByteConn()
  gated = GuardedReplayer(pm, Guards(armed=True, allow_actuation=False), conn=conn)
  gated.setup()
  try:
    gated.send("start_sort")  # start_sort is an actuating command
    print("  ERROR: should not reach here")
  except ActuationNotAllowed as e:
    print(f"  refused as expected: {type(e).__name__}")
    print(f"    {e}")
  print(f"  bytes written while refusing: {conn.writes}")

  rule("TAKEAWAY")
  print(
    "  The full pipeline runs end to end today on synthetic frames:\n"
    "    capture -> decode diff -> ProtocolMap -> coverage gate -> guarded replay,\n"
    "  with both safety gates (incomplete-map, actuation opt-in) enforced.\n"
    "  Bench day is a swap, not a rewrite: replace the SYNTHETIC frames in step 1 with\n"
    "  ones captured off the instrument, and the rest holds."
  )


if __name__ == "__main__":
  main()
