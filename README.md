# plr-reverse-engineer

Reverse-engineering playbooks and tooling for bringing lab instruments that have no
published automation API under PyLabRobot control, so a run can drive them headlessly
(for example from a Raspberry Pi) instead of through the vendor console.

> The installed CLI and Python package are `plr-re` / `plr_re` (short, for typing at the
> bench). The repository is named `plr-reverse-engineer` so its purpose is obvious at a
> glance; they are the same project.

This repo is the reverse-engineering side. The PyLabRobot backends that actually talk to
the hardware live in [di-omics/pylabrobot](https://github.com/di-omics/pylabrobot). The
split is deliberate: the work here recovers a vendor command set and captures it as a
`ProtocolMap` (a decoded, replayable set of commands); the backend there loads a finished
ProtocolMap and replays it behind hard safety guards. Capture, correlation, and decoding
stay out of the PyLabRobot tree; only the read side ships in the library.

The method follows PyLabRobot's own reverse-engineering approach (Rick Wierenga and the
PLR maintainers), credited and adapted per instrument. The core principle, from the PLR
guide, is that if you can read all the data the OEM software sends, you can replicate it:
drive small OEM test programs, capture the traffic, vary one parameter and diff, and treat
frequently repeated frames as status/keep-alive. See the
[PLR RE guide](https://discuss.pylabrobot.org/t/is-there-guide-to-reverse-engineer-a-machine-to-be-supported-to-plr/285),
the [contributor guide](https://docs.pylabrobot.org/stable/contributor_guide/new-machine-type.html),
and the talk ["How To Reverse Engineer Lab Equipment"](https://www.youtube.com/watch?v=waHR1ErHN-Y).

## Instruments

| Instrument | What it is | Playbook | Status |
| --- | --- | --- | --- |
| BD FACSMelody | Cell sorter (FACS) | [instruments/bd-facsmelody](instruments/bd-facsmelody/README.md) | Backend in PyLabRobot (dry-run tested); command set being recovered |
| Agilent 6530 Q-TOF | Accurate-mass LC/MS | [instruments/agilent-6530-qtof](instruments/agilent-6530-qtof/README.md) | Priority; Tier 0 contact closure needs no decoding, LAN control map in progress |
| Biotage V-10 Touch | Solvent evaporator | [instruments/biotage-v10-touch](instruments/biotage-v10-touch/README.md) | Transport branch resolved on the bench, then ProtocolMap recovery |
| Element AVITI | DNA sequencer (NGS) | [instruments/element-aviti](instruments/element-aviti/README.md) | Tier 0 run-folder telemetry works today; HTTP/JSON control API being recovered |
| Namocell Hana | Single-cell dispenser | [instruments/namocell-hana](instruments/namocell-hana/README.md) | Tier 0 USB discovery works today; byte command set being recovered |

## The whole lab

Each playbook above brings one instrument under control. `plr-re lab` asks the question
that only makes sense across all of them at once: given the instruments on the bench and
the command sets decoded so far, how much of an end-to-end run happens without a human,
and what exactly is in the way?

```
plr-re lab stock                          # every instrument, its role, how far its map is
plr-re lab ledger single_cell_genomics    # cost a protocol step by step
plr-re lab gaps                           # the RE queue, ranked by steps freed
plr-re lab run single_cell_genomics       # run it as far as it honestly goes
```

The answer today is unflattering, which is the point. Costing the single-cell genomics
reference protocol (Namocell sort -> STAR WGS preparation -> ODTC PCR enrichment -> STAR library -> AVITI
sequencing -> run-folder readout) against this repo as it stands, with a plr-tested
checkout wired in via `--plr-tested` (without it those legs cost out as manual too, and
`reachable` drops from 29% to 18%):

| | steps | |
| --- | --- | --- |
| automated | 3 of 17 | run headless today: two link preflights and the AVITI run-folder read |
| supervised | 2 of 17 | a validated run card exists in [plr-tested](https://github.com/di-omics/plr-tested), gated on a confirm token and an operator |
| blocked | 8 of 17 | the command is undecoded; the coverage gate refuses the run |
| manual | 4 of 17 | seating a cartridge, loading a flow cell, and two STAR steps nobody has written a validated script for |

**An unattended run reaches step 1 of 17 before it stops**, and there are 4 physical plate
hops no amount of decoding removes. That number, not the 18% autonomy figure, is what
"how automated is this lab" actually means: a read-only step near the end is only
reachable if everything before it also ran.

Note what the supervised row does *not* include. plr-tested has a validated WGS preparation
addition and a validated PCR enrichment choreography; it has no validated bead cleanup and no
validated library pooling. So those two steps cost out as manual even though they name a
validated instrument, because an instrument's record is not a claim about an arbitrary
step on it. The WGS preparation leg that does count is dry-validated, and the ledger says so in
the same breath: its wet form has never run.

Nothing in the layer can flatter the lab. Verdicts are computed from the resolved
ProtocolMap, so a step counts as automated only if its command is genuinely decoded --
there is no field a protocol author can set to declare one. The reference protocols
deliberately include the cartridge seating and flow-cell loading a demo would omit. And
because the coverage gate is all-or-nothing across a map, `lab gaps` ranks by instrument
rather than by command: decoding one command of an instrument frees no steps at all, so a
per-command queue would be advice nobody could act on.

The registry is derived from `plr_re.protocolmap.SEEDS` rather than restated beside it, so
an instrument cannot drift out of the lab and a new playbook joins it automatically.

[PREFLIGHT.md](PREFLIGHT.md) is the checkbox buy-and-pack checklist,
[PI-SETUP.md](PI-SETUP.md) prepares the Raspberry Pi capture host,
[bench-kit.md](bench-kit.md) is the bill of materials with rationale, [APPROACH.md](APPROACH.md)
is the hour-by-hour bench runbook,
[instruments/agilent-6530-qtof/WIRING.md](instruments/agilent-6530-qtof/WIRING.md) is the
contact-closure wiring with the APG pinout,
[instruments/element-aviti/CAPTURE.md](instruments/element-aviti/CAPTURE.md) is how to
capture the AVITI control-plane traffic for decoding, and
[instruments/namocell-hana/CAPTURE.md](instruments/namocell-hana/CAPTURE.md) is how to
capture the Namocell host-to-instrument byte traffic.

## Can I plug in and go?

Partly, and the honest split matters:

- **Mass spec Tier 0 (contact closure): yes, plug in and go.** It needs no decoding.
  Wire the Pi to the rear remote lines, identify which pin is Ready/Start/Stop with a
  meter and logic analyzer, fill in a pin map, and run armed. `plr_re.instruments.agilent6530`
  reads Ready and pulses Start/Stop behind the guards.
- **AVITI Tier 0 (run-folder state): yes, read-only, today.** The AVITI writes each run
  to an output folder, ending with `RunUploaded.json` (which carries an `outcome`).
  `plr-re aviti watch <run_dir>` reports running/complete/outcome with no decoding and no
  risk, so an orchestrator gets honest run state and a clean hand-off to Bases2Fastq.
- **Namocell Tier 0 (transport discovery): yes, read-only, today.** The Hana is a byte-
  protocol instrument with no plug-in-and-go control path, but `plr-re namocell discover`
  enumerates the USB/serial link read-only, with no decoding, to find the wire before
  capture. Driving the dispenser is a bench capture-and-decode job like the FACSMelody.
- **Decoded protocol control (LAN for the Q-TOF, the HMI bus for the V-10, the AvitiOS
  HTTP API for the AVITI, the byte link for the Hana): not before the bench.** By
  definition: the commands are unknown
  until you capture them from the instrument, so no one can pre-bake a working `start_run`
  or `set_temperature`. What is baked is the tooling that makes each step one command:
  capture with action marking, a byte-diff correlator, a Modbus decoder, a HAR decoder,
  and a guarded replayer that runs a map the moment it is complete.

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

# Element AVITI. Tier 0 run-folder state is read-only and needs no decoding:
plr-re aviti watch /mnt/aviti-output/20260713_AV1_run42     # running/complete/outcome
plr-re aviti probe 192.168.1.50                             # find the control endpoint
plr-re aviti status --config configs/aviti.example.json     # dry-run until armed

# Namocell Hana. Tier 0 USB/serial discovery is read-only and needs no decoding:
plr-re namocell discover                                    # find the control link
plr-re namocell status --config configs/namocell.example.json          # dry-run until armed
plr-re namocell sort --protocol single_gfp --plate 384      # dry-run: previews the sequence

# Capture OEM traffic while you mark each discrete action:
plr-re capture lan --iface eth1 --hosts 169.254.1.10 --out cap.pcap --mark
plr-re capture serial --port /dev/ttyUSB0 --baud 19200 --out v10.jsonl
plr-re capture http --out aviti.har                         # AvitiOS UI/service traffic

# Decode: diff two single-parameter frames, decode one Modbus frame, scan a whole
# serial capture into a register map, or read the API calls out of an AVITI HAR:
plr-re decode diff aa0128cc aa0129cc
plr-re decode modbus 0106001000288811
plr-re decode modbus-log v10.jsonl
plr-re decode har aviti.har                                 # writes first = actuation

# Build and track a ProtocolMap:
plr-re map seed biotage_v10 --out maps/biotage_v10.json
plr-re map coverage maps/biotage_v10.json     # exits non-zero while anything is undecoded

# Biotage setpoint, guarded, with a hard temperature ceiling. Dry-run until armed:
plr-re biotage set-temp 40 --map maps/biotage_v10.json

# The whole lab: what runs today across every instrument, and what blocks the rest.
plr-re lab stock                             # inventory + per-instrument coverage
plr-re lab protocols                         # the reference end-to-end flows
plr-re lab ledger single_cell_genomics       # cost it step by step (non-zero while blocked)
plr-re lab gaps                              # which map to decode next, ranked by steps freed
plr-re lab run single_cell_genomics --armed  # perform the read-only steps, stop at the first human
plr-re lab ledger single_cell_genomics --plr-tested ../plr-tested   # wire the validated STAR/ODTC legs
```

Everything that can move hardware is dry-run until `--armed`, and actuating commands
additionally need `--allow-actuation`. A live run refuses to start against an incomplete
map. Device-free tests cover the guards, the coverage gate, contact-closure and HTTP
dry-run, the run-folder reader, and the decoders (`pytest`).

## The method

Every instrument follows the same spine; the per-instrument playbook fills in the
specifics.

1. Map the OEM stack and transport. Find how the vendor software reaches the device
   (USB, serial, TCP, contact closure, or an HTTP/JSON microservice API) and record the
   endpoint. This fills `ProtocolMap.transport` and `endpoint`.
2. Capture traffic against labeled UI actions. With capture running, perform one
   discrete vendor action at a time and mark the instant of each, so the capture slices
   into action-aligned windows. Perform one action, see exactly what bytes it produced.
3. Correlate action to bytes and decode framing. Isolate the frame an action produced
   and decode header, length, payload, and checksum. Vary a single parameter and diff
   the frames to decode each parameter encoding. Per the PLR method, a frequently
   repeated frame is usually a status/keep-alive message, not the action you want; set it
   aside (the HAR decoder flags these automatically for HTTP instruments).
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

These are real instruments with lasers, high voltage, pressurized gas, heat, vacuum,
hazardous solvents, and single-use consumables that cost real money. Every backend is
timid by default:

- Dry run by default: it logs the exact frames or requests it would send and transmits
  nothing.
- Actuating commands (anything that moves fluid, fires a sort, starts a pump or gas or
  high voltage, heats, pulls vacuum, or commits a sequencing run) require an explicit,
  separate opt-in even once armed.
- A live run refuses to start while any required command in the ProtocolMap is
  undecoded, so a half-mapped protocol cannot drive hardware.

Reverse-engineering an instrument you own for interoperability is a legitimate,
well-established practice. The guards and the vendor interlocks stay in place: this
tooling orchestrates, it does not remove an instrument's own limits.
