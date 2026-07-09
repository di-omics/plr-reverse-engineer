# plr-re

Reverse-engineering playbooks and tooling for bringing lab instruments that have no
published automation API under PyLabRobot control, so a run can drive them headlessly
(for example from a Raspberry Pi) instead of through the vendor console.

This repo is the reverse-engineering side. The PyLabRobot backends that actually talk to
the hardware live in [di-omics/pylabrobot](https://github.com/di-omics/pylabrobot). The
split is deliberate: the work here recovers a vendor command set and captures it as a
`ProtocolMap` (a decoded, replayable set of commands); the backend there loads a finished
ProtocolMap and replays it behind hard safety guards. Capture, correlation, and decoding
stay out of the PyLabRobot tree; only the read side ships in the library.

The method follows the reverse-engineering approach Rick Wierenga used to bring other
instruments into PyLabRobot, credited and adapted per instrument.

## Instruments

| Instrument | What it is | Playbook | Status |
| --- | --- | --- | --- |
| BD FACSMelody | Cell sorter (FACS) | [instruments/bd-facsmelody](instruments/bd-facsmelody/README.md) | Backend in PyLabRobot (dry-run tested); command set being recovered |
| Agilent 6530 Q-TOF | Accurate-mass LC/MS | [instruments/agilent-6530-qtof](instruments/agilent-6530-qtof/README.md) | Priority; Tier 0 contact closure needs no decoding, LAN control map in progress |
| Biotage V-10 Touch | Solvent evaporator | [instruments/biotage-v10-touch](instruments/biotage-v10-touch/README.md) | Transport branch resolved on the bench, then ProtocolMap recovery |

[PREFLIGHT.md](PREFLIGHT.md) is the checkbox buy-and-pack checklist,
[bench-kit.md](bench-kit.md) is the bill of materials with rationale, [APPROACH.md](APPROACH.md)
is the hour-by-hour bench runbook, and
[instruments/agilent-6530-qtof/WIRING.md](instruments/agilent-6530-qtof/WIRING.md) is the
contact-closure wiring with the APG pinout.

## Can I plug in and go?

Partly, and the honest split matters:

- **Mass spec Tier 0 (contact closure): yes, plug in and go.** It needs no decoding.
  Wire the Pi to the rear remote lines, identify which pin is Ready/Start/Stop with a
  meter and logic analyzer, fill in a pin map, and run armed. `plr_re.instruments.agilent6530`
  reads Ready and pulses Start/Stop behind the guards.
- **Decoded protocol control (LAN for the Q-TOF, the HMI bus for the V-10): not before
  the bench.** By definition: the command bytes are unknown until you capture them from
  the instrument, so no one can pre-bake a working `start_run` or `set_temperature`
  frame. What is baked is the tooling that makes each step one command: capture with
  action marking, a byte-diff correlator, a Modbus decoder, and a guarded replayer that
  runs a map the moment it is complete.

In short: the contact-closure MVP is go; the rest is a fast, guarded capture-and-decode
loop rather than a manual one.

## Quickstart (the toolkit)

```
pip install -e .            # core is stdlib-only; add [serial] or [pi] extras on the Pi

# Tier 0 mass spec, contact closure. Dry-run by default (logs, touches nothing):
plr-re agilent status --config configs/agilent-pinmap.example.json
plr-re agilent start  --config configs/agilent-pinmap.example.json           # dry-run
plr-re agilent start  --config configs/agilent-pinmap.example.json --armed --allow-actuation
plr-re agilent scan   --pins 17 5 6 13 19 26 --armed        # find which pin is Ready
plr-re agilent probe 169.254.1.10                           # Tier 1 LAN, read-only

# Capture OEM traffic while you mark each discrete action:
plr-re capture lan --iface eth1 --hosts 169.254.1.10 --out cap.pcap --mark
plr-re capture serial --port /dev/ttyUSB0 --baud 19200 --out v10.jsonl

# Decode: diff two single-parameter frames, decode one Modbus frame, or scan a whole
# serial capture into a register map (the V-10 setpoint-to-register mapping):
plr-re decode diff aa0128cc aa0129cc
plr-re decode modbus 0106001000288811
plr-re decode modbus-log v10.jsonl

# Build and track a ProtocolMap:
plr-re map seed biotage_v10 --out maps/biotage_v10.json
plr-re map coverage maps/biotage_v10.json     # exits non-zero while anything is undecoded

# Biotage setpoint, guarded, with a hard temperature ceiling. Dry-run until armed:
plr-re biotage set-temp 40 --map maps/biotage_v10.json
```

Everything that can move hardware is dry-run until `--armed`, and actuating commands
additionally need `--allow-actuation`. A live run refuses to start against an incomplete
map. Device-free tests cover the guards, the coverage gate, contact-closure dry-run, and
the decoders (`pytest`).

## The method

Every instrument follows the same spine; the per-instrument playbook fills in the
specifics.

1. Map the OEM stack and transport. Find how the vendor software reaches the device
   (USB, serial, TCP, contact closure) and record the endpoint. This fills
   `ProtocolMap.transport` and `endpoint`.
2. Capture traffic against labeled UI actions. With capture running, perform one
   discrete vendor action at a time and mark the instant of each, so the capture slices
   into action-aligned windows. Perform one action, see exactly what bytes it produced.
3. Correlate action to bytes and decode framing. Isolate the frame an action produced
   and decode header, length, payload, and checksum. Vary a single parameter and diff
   the frames to decode each parameter encoding.
4. Build the ProtocolMap with coverage tracking. Record each decoded command as a frame
   template with parameter encoders and a success response. The required command list is
   seeded up front, so a coverage check always reports exactly what still blocks a live
   run.
5. Guarded replay. Confirm read-only commands first. Replay stays a dry run until the
   backend is armed and, for actuating commands, actuation is explicitly allowed with a
   human in the loop.
6. Validate on the instrument. Only after the map is complete and read-only replay is
   confirmed, run the real operation end to end with a human present.

## Safety posture

These are real instruments with lasers, high voltage, pressurized gas, heat, vacuum, and
hazardous solvents. Every backend is timid by default:

- Dry run by default: it logs the exact frames it would send and transmits nothing.
- Actuating commands (anything that moves fluid, fires a sort, starts a pump or gas or
  high voltage, heats, or pulls vacuum) require an explicit, separate opt-in even once
  armed.
- A live run refuses to start while any required command in the ProtocolMap is
  undecoded, so a half-mapped protocol cannot drive hardware.

Reverse-engineering an instrument you own for interoperability is a legitimate,
well-established practice. The guards and the vendor interlocks stay in place: this
tooling orchestrates, it does not remove an instrument's own limits.
