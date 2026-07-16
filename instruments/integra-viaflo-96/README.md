# Integra VIAFLO 96: reverse-engineering the control plane

The Integra VIAFLO 96 (INTEGRA Biosciences) is a benchtop 96-channel electronic pipette
(the same platform ships in 24- and 384-channel variants). An interchangeable pipetting
head moves in Z over a small three-position stage; the operator places a plate or
reservoir under the head by hand, and the head aspirates and dispenses all 96 channels at
once. It is programmed on its own touchscreen and by INTEGRA's VIALINK software over a USB
link (Type A-to-B, directly or via the programming stand / communication module). There is
no published automation API. Bringing the VIAFLO 96 under PyLabRobot means recovering the
command set VIALINK already uses, so a run can be built, uploaded, and started headlessly
(for example from a Raspberry Pi) instead of through the touchscreen. This page is the
reproducible, safety-first playbook. It follows the reverse-engineering methodology Rick
Wierenga used to bring other instruments into PyLabRobot, adapted here for an electronic
pipette.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set of
commands), the same artifact the other backends consume. The reverse-engineering that
produces a ProtocolMap lives here in plr-re; the PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only loads a finished
ProtocolMap and replays it behind hard guards.

## What kind of instrument this is (and what that means for RE)

The VIAFLO 96's control model is the distinctive thing, and it changes the shape of the
RE. Unlike the Namocell Hana (a live sort/dispense command stream) or the Element AVITI (a
networked HTTP/JSON control plane), the VIAFLO 96 is a **program-transfer** device:
VIALINK serializes a whole pipetting program (a named sequence of steps), transfers it into
the pipette's memory over USB, and the pipette then runs it standalone. After the transfer,
no host connection is required to run.

So the headless-control commands to recover are telemetry plus **upload a program, select
it, and run it** (and stop it), not a live per-step aspirate/dispense stream. The per-step
liquid-handling semantics (volumes, speeds, mix, tip actions) live *inside* the uploaded
program, so the highest-value decoding target is the program serialization format: how a
program's steps and their volumes are laid out in the bytes VIALINK uploads. Whether the
link also exposes atomic per-step control (drive one aspirate/dispense live) is a genuine
bench question; it is not assumed here.

The USB link is the strong prior for the transport. It most likely presents as a
USB-serial / virtual-COM device (an FTDI/CP210x-class bridge or a CDC ACM port), which the
toolkit's existing serial byte connection drives directly; a raw USB bulk device is the
alternative the bench resolves (served by the toolkit's USB byte connection once the
raw-USB transport lands). Either way the reverse-engineering is the byte-frame kind the
toolkit already covers: capture the host-to-instrument traffic, mark one discrete action at
a time, and diff single-parameter frames to decode each command. No new transport is
needed for the USB-serial branch; it reuses the same guarded byte replayer as the Biotage
V-10 and the Namocell.

## Safety posture (read first)

This drives a 96-channel pipette that moves a head in Z onto plates and tip racks and moves
real liquid across 96 channels, over precious samples and consumable tips that cost real
money. The backend is timid by default:

- `armed=False` (default) is a dry run: it logs the exact frame it would send and transmits
  nothing.
- `allow_actuation=False` (default) refuses any command that writes device memory or moves
  the head (upload_program, home, run_program, abort), even when armed. Read-only commands
  (get_status, get_identity, list_programs) and the pure selection (select_program) are
  allowed.
- A live run refuses to start until every required command in the ProtocolMap is decoded.
- When a program's step volumes are supplied (the programmatic `upload_program(...,
  volumes=[...])` / `run_named_program` path), each is validated against the installed
  pipetting head's ceiling (`max_volume_ul`, set per head) before the program is framed, so a
  program that would exceed the installed head is refused. This mirrors the Biotage
  temperature ceiling. Until the program format is decoded, the CLI upload-by-name path
  cannot extract step volumes from a stored program, so it sets the ceiling but does not yet
  enforce it; once the format is decoded, extraction closes that gap.

Reverse-engineering an instrument you own for interoperability is a legitimate,
well-established practice. The point here is orchestration, not touching the pipetting
mechanics: the head's motion limits, the tip-fit interlock, and the pipetting head's own
volume range are the instrument's own and stay untouched.

## Tiered attack plan

From cheapest and safest to hardest:

- **Tier 0 (works today): read-only transport discovery, zero decode.** `plr-re viaflo
  discover` enumerates the USB/serial devices attached to the host so you can identify the
  pipette's control link (VID/PID, port path) before capturing anything. It only lists what
  is present; it opens no session and sends nothing. It is the transport-generic enumerator
  shared with the Namocell path: find the wire first.

- **Tier 1: resolve the transport branch.** Confirm whether the control link is USB-serial /
  virtual-COM (record `path@baud`, transport `serial`) or a raw USB bulk device (transport
  `usb`, endpoint `usb:VID:PID[/out=EP,in=EP]`, served by the toolkit's USB byte connection
  once the raw-USB transport lands; the `serial` branch is armed-ready today). Unplug and
  re-run discovery to see which port disappears. Record the answer as `ProtocolMap.transport`
  and `endpoint`.

- **Tier 2: recover the command set (the reverse engineering proper).** With capture running
  (see [CAPTURE.md](CAPTURE.md)), drive VIALINK through one discrete action at a time
  (connect, read identity, list programs, upload a small program, select it, run it, stop
  it) and mark each. Isolate the frame each action produced, decode its framing (header,
  length, payload, checksum), and diff single-parameter variants to decode each field. The
  program upload is the big one: capture two uploads of the same program that differ in one
  step volume, and `plr-re decode diff` the two transfers to locate how a volume is encoded.

- **Tier 3: guarded program control.** Once the map is complete, upload a program, select
  it, and run it behind `--armed --allow-actuation` with a human present; home and abort the
  same way. Read status, identity, and the program list freely.

## The program serialization is the payload (and a read-only results path)

Two notes specific to a program-transfer instrument:

- **Decode the program format, not just the verbs.** Recovering `upload_program` as an
  opaque blob is not enough to be useful; the value is in decoding how a program's steps and
  their volumes are serialized, so a run can synthesize a new program (an aspirate/dispense
  pattern with the right volumes) rather than only replaying one captured blob. Treat the
  program body as its own small format to reverse: vary one step volume, diff the two
  uploads, and template the volume field with a `{param}` placeholder.

- **A read-only log path may exist.** If VIALINK (or the pipette) writes a run log or a
  transferred-program record to a known folder, reading it is a zero-decode telemetry path,
  analogous to the AVITI run-folder watcher: an orchestrator learns which program ran and
  its outcome with no protocol decoding and no risk. The exact filename and schema are an
  on-the-bench confirmation, so this repo does not ship a parser against a guessed format;
  it is called out because it is a cheap read-only add once the folder is confirmed.

## The playbook

This follows PyLabRobot's own reverse-engineering method (see Method source below). The
guiding principle from the PLR maintainer: **if you can read all the data the OEM software
sends, you can replicate it yourself.** The VIAFLO 96 fits that method because the
host-to-instrument USB link is an external data path whose traffic can be read.

1. **Map the OEM stack and transport.** Identify how VIALINK reaches the pipette. Start with
   `viaflo discover` to list the USB/serial candidates, then confirm the branch (USB-serial
   vs raw USB). Record it as `ProtocolMap.transport` and `endpoint`.

2. **Drive the OEM interface with small test programs.** Per the PLR method, keep each action
   minimal so the capture is easy to read: connect, read identity, list programs, upload one
   short program, select it, run it, stop it. One discrete action at a time.

3. **Intercept the traffic while marking each action.** With capture running (see
   [CAPTURE.md](CAPTURE.md)), mark the instant of each action so the capture slices into
   action-aligned windows. For USB-serial this is `plr-re capture serial`; for raw USB it is
   a Wireshark usbmon capture.

4. **Correlate action to bytes and decode framing.** Isolate the frame an action produced and
   decode its header, length, payload, and checksum. Per the PLR heuristic, a frequently
   repeated frame is usually a status/keep-alive poll, not the action you want; set it aside.

5. **Decode each field by varying one parameter.** Straight from the PLR method: change a
   single parameter in VIALINK (one step volume, then another; one program name, then
   another), capture both, and `plr-re decode diff` the two transfers to locate that field.
   Template it with a `{param}` placeholder.

6. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a frame
   template. The map seeds the required command list up front, so `coverage()` always reports
   exactly which commands are still undecoded and therefore still block a live run.

7. **Guarded replay, then validate on the instrument.** Confirm read-only commands
   (get_status, get_identity, list_programs) first. Replay stays a dry run until you pass both
   `armed=True` and, for actuating commands, `allow_actuation=True` with a human in the loop;
   the backend refuses to start while any required command is undecoded, and refuses a program
   whose supplied step volumes exceed the installed head. Only after the map is complete and
   read-only replay is confirmed, run a real program with a human present.

## Commands

```
# Tier 0: read-only USB/serial discovery (find the control link; touches nothing)
plr-re viaflo discover

# Tier 2: capture and decode the host-to-instrument byte traffic
plr-re capture serial --port /dev/ttyUSB0 --baud 115200 --out viaflo.jsonl
plr-re decode diff <upload_10uL_hex> <upload_20uL_hex>   # vary one step volume, see the field

# Build and track the map
plr-re map seed viaflo96 --out maps/viaflo96.json
plr-re map coverage maps/viaflo96.json                   # exit 1 while anything is undecoded

# Tier 3: guarded control. Dry-run until armed; a live run needs a complete map.
plr-re viaflo status   --config configs/viaflo96.example.json
plr-re viaflo identity --config configs/viaflo96.example.json
plr-re viaflo upload   --map maps/viaflo96.json --config configs/viaflo96.example.json \
                       --program serial_dilution --max-volume 300 --armed --allow-actuation
plr-re viaflo run      --map maps/viaflo96.json --config configs/viaflo96.example.json \
                       --program serial_dilution --armed --allow-actuation
plr-re viaflo abort    --map maps/viaflo96.json --config configs/viaflo96.example.json \
                       --armed --allow-actuation
```

## PyLabRobot integration shape

The VIAFLO 96 is itself a liquid handler, so it maps onto PLR's `LiquidHandler` frontend
with a dedicated backend rather than the machine-frontend shape used for a reader or a
sorter. Two things make the backend distinctive, and both follow from the program-transfer
control model:

- **Fixed geometry, not an X/Y arm.** The head services one plate position at a time and
  moves only in Z, aspirating and dispensing all 96 (or 24/384) channels together. In PLR
  terms it is a fixed 96-channel head over a small deck, not an X/Y-addressable channel arm;
  a resource model with the three stage positions and a single 96-tip head fits it.

- **Batch program transfer, with an atomic path as a bench question.** The confirmed
  capability is: compile a PLR pipetting protocol (or the subset the head supports) into a
  VIAFLO program, upload it, and run it. So the concrete backend's `aspirate`/`dispense` may
  be *accumulated* into a program and committed on run, rather than each being a live USB
  command. If the bench shows the link exposes atomic per-step control, the backend can
  implement `aspirate`/`dispense` live instead; the ProtocolMap records whichever is true.

Follow PLR's three-layer structure (frontend, abstract backend, concrete backend):

- **Frontend** `LiquidHandler` with a VIAFLO deck: the user-facing API, with convenience
  methods for a whole-plate transfer.
- **Abstract backend** `LiquidHandlerBackend`: the atomic commands (`setup`, `stop`,
  `pick_up_tips`, `aspirate`, `dispense`, `drop_tips`), plus the program lifecycle the
  concrete backend needs (`upload_program`, `run_program`, `abort`).
- **Concrete backend** `IntegraViaflo96Backend`: loads a finished `ProtocolMap` and either
  compiles accumulated steps into a program to upload and run, or replays atomic commands if
  the link supports them. The two arming switches gate transmission: `armed` opens the
  transport, `allow_actuation` releases any command that writes device memory or moves the
  head.

Writing it is the documented PLR workflow: copy `backend.py`, rename the class to
`IntegraViaflo96Backend`, remove the `abc` decorators, and implement the methods against the
recovered ProtocolMap. Capture and decoding stay here in plr-re; only the finished
ProtocolMap and the replay side ship in the library.

## Method source

This playbook applies PyLabRobot's existing reverse-engineering method rather than inventing
one:

- PLR reverse-engineering guidance (read all the data, drive small OEM test programs, vary
  one parameter and diff, repeated frames are usually status/keep-alive):
  <https://discuss.pylabrobot.org/t/is-there-guide-to-reverse-engineer-a-machine-to-be-supported-to-plr/285>
- Contributing a new machine type (frontend / abstract `MachineBackend` / concrete backend,
  atomic `setup`/`stop`):
  <https://docs.pylabrobot.org/stable/contributor_guide/new-machine-type.html>
- Contributing / writing a backend (copy `backend.py`, implement the methods):
  <https://docs.pylabrobot.org/stable/contributor_guide/contributing.html>
- Rick Wierenga, "How To Reverse Engineer Lab Equipment":
  <https://www.youtube.com/watch?v=waHR1ErHN-Y>
- INTEGRA VIALINK pipette management software (the OEM software whose USB traffic is
  captured): <https://www.integra-biosciences.com/global/en/pipette-software/vialink>

## Current status

Tier 0 (read-only USB/serial discovery) is implemented and safe to use today; it needs no
decoding and only enumerates the attached devices. The command set (Tier 2/3) is recovered
with the plr-re toolkit on the bench: the seed map lists the required commands, the byte-diff
decoder reads each field (including the program's step volumes) out of a capture, and the
guarded replayer refuses a live run against an incomplete map and refuses a program whose
supplied step volumes exceed the installed head. Hardware validation (step 7) will be reported honestly
when it lands.
