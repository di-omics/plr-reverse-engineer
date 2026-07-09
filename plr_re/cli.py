"""plr-re command line.

Everything that can touch hardware is dry-run by default. Add --armed to open a
transport, and --allow-actuation to permit commands that move the instrument. Read-only
commands (status, ready, probe, capture, decode, map) never need those flags.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List

from .capture import Marks, capture_lan, capture_serial
from .decode import format_diff, load_serial_log, parse_modbus_rtu, scan_modbus_stream
from .guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from .instruments.agilent6530 import (
  Agilent6530Remote,
  AgilentPinMap,
  probe_module,
  summarize_scan,
)
from .instruments.biotage_v10 import BiotageV10
from .protocolmap import ProtocolMap, seed


def _log_setup():
  logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _read_hex(arg: str) -> bytes:
  """Accept a hex string ('0a1b...' with optional spaces/colons) or a path to a file
  containing hex."""
  text = arg
  if os.path.exists(arg):
    with open(arg, encoding="utf-8") as fh:
      text = fh.read()
  cleaned = "".join(ch for ch in text if ch in "0123456789abcdefABCDEF")
  return bytes.fromhex(cleaned)


# -- agilent -----------------------------------------------------------------


def _agilent(args) -> int:
  if args.action == "probe":
    result = probe_module(args.ip, port=args.port)
    print(result)
    return 0 if result.get("reachable") else 1

  if args.action == "scan":
    pins = [int(p) for p in (args.pins or [])]
    if not pins:
      print("error: --pins BCM... is required for scan", file=sys.stderr)
      return 2
    guards = Guards(armed=args.armed)
    dev = Agilent6530Remote(AgilentPinMap(ready=pins[0], start=0, stop=0), guards)
    try:
      input("Set the instrument NOT-ready (busy), then press Enter... ")
      s1 = dev.scan_lines(pins)
      input("Now set the instrument READY, then press Enter... ")
      s2 = dev.scan_lines(pins)
    finally:
      dev.close()
    for pin, info in summarize_scan([s1, s2]).items():
      tag = "  <-- changed: candidate Ready line" if info["changed"] else ""
      print(f"BCM{pin}: {info['first']} -> {info['last']}{tag}")
    if not guards.armed:
      print("(dry-run: no real GPIO read; re-run with --armed on the Pi)")
    return 0

  if not args.config:
    print(
      "error: --config PINMAP.json is required for contact-closure control.\n"
      "Create one after identifying the lines, e.g. configs/agilent-pinmap.example.json",
      file=sys.stderr,
    )
    return 2
  pinmap = AgilentPinMap.from_json(args.config)
  guards = Guards(armed=args.armed, allow_actuation=args.allow_actuation)
  dev = Agilent6530Remote(pinmap, guards)
  try:
    if args.action == "status":
      print(dev.status())
    elif args.action == "ready":
      print("ready" if dev.is_ready() else "not-ready")
    elif args.action == "start":
      dev.start_run()
      print("start pulsed" if guards.armed else "start (dry-run)")
    elif args.action == "stop":
      dev.stop_run()
      print("stop pulsed" if guards.armed else "stop (dry-run)")
  finally:
    dev.close()
  return 0


# -- biotage -----------------------------------------------------------------


def _biotage(args) -> int:
  pm = ProtocolMap.from_json(args.map) if args.map else seed("biotage_v10")
  guards = Guards(armed=args.armed, allow_actuation=args.allow_actuation)
  dev = BiotageV10(pm=pm, guards=guards, max_temperature_c=args.max_temp)
  dev.setup()
  try:
    if args.action == "status":
      dev.get_status()
    elif args.action == "set-temp":
      dev.set_temperature(args.celsius)
    elif args.action == "start":
      dev.start_method()
    elif args.action == "stop":
      dev.stop_method()
  finally:
    dev.stop()
  return 0


# -- capture -----------------------------------------------------------------


def _capture(args) -> int:
  if args.what == "lan":
    proc = capture_lan(args.iface, args.out, hosts=args.hosts, seconds=args.seconds)
    print(f"capturing on {args.iface} -> {args.out} (pid {proc.pid})")
    if args.mark:
      Marks(args.out + ".marks.jsonl").run_interactive()
      proc.terminate()
    else:
      try:
        proc.wait()
      except KeyboardInterrupt:
        proc.terminate()
    return 0
  if args.what == "serial":
    print(f"logging {args.port}@{args.baud} -> {args.out} (Ctrl-C to stop)")
    try:
      capture_serial(args.port, args.out, baud=args.baud, seconds=args.seconds)
    except KeyboardInterrupt:
      pass
    return 0
  return 2


def _mark(args) -> int:
  Marks(args.out).run_interactive()
  return 0


# -- decode ------------------------------------------------------------------


def _decode(args) -> int:
  if args.what == "diff":
    print(format_diff(_read_hex(args.a), _read_hex(args.b)))
    return 0
  if args.what == "modbus":
    f = parse_modbus_rtu(_read_hex(args.frame))
    print(
      f"addr={f.address} func=0x{f.function:02x} ({f.function_name}) "
      f"crc_ok={f.crc_ok} register={f.register} value={f.value}"
    )
    return 0
  if args.what == "modbus-log":
    frames = scan_modbus_stream(load_serial_log(args.path))
    print(f"found {len(frames)} Modbus frame(s)")
    for off, f in frames:
      print(
        f"  @{off:>5} addr={f.address} {f.function_name} "
        f"reg={f.register} val={f.value} crc_ok={f.crc_ok}"
      )
    writes = {}
    for _, f in frames:
      if f.register is not None:
        writes[(f.address, f.register)] = f.value
    if writes:
      print("register writes (addr, reg -> last value):")
      for (a, r), v in sorted(writes.items()):
        print(f"  addr {a} reg 0x{r:04x} -> {v}")
    return 0
  return 2


# -- map ---------------------------------------------------------------------


def _map(args) -> int:
  if args.what == "seed":
    pm = seed(args.instrument)
    pm.to_json(args.out)
    print(f"seeded {args.instrument} -> {args.out} ({len(pm.commands)} commands, all undecoded)")
    return 0
  pm = ProtocolMap.from_json(args.path)
  cov = pm.coverage()
  if args.what == "coverage":
    print(f"{pm.device}: {cov['decoded']}/{cov['total']} decoded")
    if cov["missing"]:
      print("  missing: " + ", ".join(cov["missing"]))
    return 0 if not cov["missing"] else 1
  if args.what == "show":
    print(f"{pm.device} via {pm.transport.value} endpoint={pm.endpoint}")
    for name, c in pm.commands.items():
      flag = "decoded" if c.decoded else "TODO"
      act = " [actuating]" if c.actuating else ""
      print(f"  {name:<16} {flag}{act}  {c.notes}")
    return 0
  return 2


def build_parser() -> argparse.ArgumentParser:
  p = argparse.ArgumentParser(prog="plr-re", description=__doc__)
  sub = p.add_subparsers(dest="cmd", required=True)

  def arm_flags(sp):
    sp.add_argument("--armed", action="store_true", help="open transport / drive hardware")
    sp.add_argument(
      "--allow-actuation",
      action="store_true",
      help="permit commands that move the instrument",
    )

  ag = sub.add_parser("agilent", help="Agilent 6530 Tier 0 contact closure + LAN probe")
  ag.add_argument("action", choices=["status", "ready", "start", "stop", "probe", "scan"])
  ag.add_argument("ip", nargs="?", help="module IP (probe only)")
  ag.add_argument("--port", type=int, default=23, help="probe port (default 23)")
  ag.add_argument("--config", help="pin map JSON")
  ag.add_argument("--pins", nargs="*", help="candidate BCM input pins (scan only)")
  arm_flags(ag)
  ag.set_defaults(func=_agilent)

  bt = sub.add_parser("biotage", help="Biotage V-10 setpoint control via guarded replay")
  bt.add_argument("action", choices=["status", "set-temp", "start", "stop"])
  bt.add_argument("celsius", nargs="?", type=float, help="temperature (set-temp only)")
  bt.add_argument("--map", help="decoded ProtocolMap JSON (default: undecoded seed)")
  bt.add_argument("--max-temp", type=float, default=60.0, help="temperature ceiling C")
  arm_flags(bt)
  bt.set_defaults(func=_biotage)

  cap = sub.add_parser("capture", help="capture OEM traffic while marking actions")
  capsub = cap.add_subparsers(dest="what", required=True)
  lan = capsub.add_parser("lan")
  lan.add_argument("--iface", required=True)
  lan.add_argument("--out", required=True, help="output pcap")
  lan.add_argument("--hosts", nargs="*", help="capture filter to these hosts")
  lan.add_argument("--seconds", type=float)
  lan.add_argument("--mark", action="store_true", help="interactive action marking")
  ser = capsub.add_parser("serial")
  ser.add_argument("--port", required=True)
  ser.add_argument("--baud", type=int, default=9600)
  ser.add_argument("--out", required=True, help="output JSONL")
  ser.add_argument("--seconds", type=float)
  cap.set_defaults(func=_capture)

  mk = sub.add_parser("mark", help="standalone interactive action marking")
  mk.add_argument("--out", required=True, help="marks JSONL")
  mk.set_defaults(func=_mark)

  dec = sub.add_parser("decode", help="correlate captures into decoded commands")
  decsub = dec.add_subparsers(dest="what", required=True)
  df = decsub.add_parser("diff")
  df.add_argument("a", help="hex string or file")
  df.add_argument("b", help="hex string or file")
  mb = decsub.add_parser("modbus")
  mb.add_argument("frame", help="hex string or file (one RTU frame)")
  mbl = decsub.add_parser("modbus-log")
  mbl.add_argument("path", help="serial capture JSONL from `capture serial`")
  dec.set_defaults(func=_decode)

  mp = sub.add_parser("map", help="seed, inspect, and check ProtocolMap coverage")
  mpsub = mp.add_subparsers(dest="what", required=True)
  sd = mpsub.add_parser("seed")
  sd.add_argument("instrument", choices=["facsmelody", "agilent6530", "biotage_v10"])
  sd.add_argument("--out", required=True)
  cv = mpsub.add_parser("coverage")
  cv.add_argument("path")
  sh = mpsub.add_parser("show")
  sh.add_argument("path")
  mp.set_defaults(func=_map)

  return p


def main(argv: List[str] = None) -> int:
  _log_setup()
  args = build_parser().parse_args(argv)
  try:
    return args.func(args)
  except (
    ActuationNotAllowed,
    ProtocolMapIncompleteError,
    ValueError,
    KeyError,
    FileNotFoundError,
    RuntimeError,
  ) as e:
    # Expected, actionable failures (safety refusals, missing map/config, unreachable
    # hardware) print cleanly instead of dumping a traceback.
    print(f"error: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
