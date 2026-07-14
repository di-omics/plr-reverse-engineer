# Namocell Hana: reverse-engineering the control plane

The Namocell Hana (Bio-Techne) is a benchtop single-cell dispenser. It isolates cells
from a disposable microfluidic cartridge on fluorescence and light scatter and dispenses
them into 96/384-well plates at low sort pressure (gentle enough to keep cells viable),
in a single step with no aerosol. It is driven by a bundled Windows PC running Namocell's
software; there is no published automation API. Bringing the Hana under PyLabRobot means
recovering the OEM command set the software already uses, so a run can be staged,
primed, dispensed, and cleaned headlessly (for example from a Raspberry Pi) instead of
through the console. This page is the reproducible, safety-first playbook. It follows the
reverse-engineering methodology Rick Wierenga used to bring other instruments into
PyLabRobot, adapted here for a single-cell dispenser.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set of
commands), the same artifact the other backends consume. The reverse-engineering that
produces a ProtocolMap lives here in plr-re; the PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only loads a finished
ProtocolMap and replays it behind hard guards.

## What kind of instrument this is (and what that means for RE)

Unlike the Element AVITI (a networked HTTP/JSON microservice stack), the Hana is a local
instrument driven by an application over a byte link. The strong prior is a USB-serial
control link (an FTDI/CP210x-class bridge), with the detection camera as a separate USB
device; a raw USB bulk protocol is the alternative the bench resolves. Either way the
reverse-engineering is the byte-frame kind the toolkit already covers: capture the
host-to-instrument traffic, mark one discrete action at a time, and diff single-parameter
frames to decode each command. No new transport is needed; it reuses the same guarded
byte replayer as the Biotage V-10.

## Safety posture (read first)

This drives a cell dispenser with pressurized microfluidics and a single-use cartridge
that costs real money, handling live (often precious, low-input) samples. The backend is
timid by default:

- `armed=False` (default) is a dry run: it logs the exact frame it would send and
  transmits nothing.
- `allow_actuation=False` (default) refuses any command that moves fluid, pressurizes the
  cartridge, or fires a sort (set_deposition, prime, start_sort, clean), even when armed.
  Read-only commands (get_status, wait_complete) are allowed.
- A live run refuses to start until every required command in the ProtocolMap is decoded.
- The destination plate format is validated against what the Hana dispenses into (96 or
  384) before anything is framed.

Reverse-engineering an instrument you own for interoperability is a legitimate,
well-established practice. The point here is orchestration, not touching the sort
chemistry: the microfluidic sort itself, the cartridge, and the low-pressure limit are
the instrument's own and stay untouched.

## Tiered attack plan

From cheapest and safest to hardest:

- **Tier 0 (works today): read-only transport discovery, zero decode.** `plr-re namocell
  discover` enumerates the USB/serial devices attached to the host so you can identify
  the instrument's control link (VID/PID, port path) before capturing anything. It only
  lists what is present; it opens no session and sends nothing. This is the Namocell
  analog of `aviti probe` / `agilent scan`: find the wire first.

- **Tier 1: resolve the transport branch.** Confirm whether the control link is USB-serial
  (record `path@baud`, transport `serial`) or a raw USB bulk device (transport `usb`,
  which needs a pyusb byte connection added to the toolkit). Record the answer as
  `ProtocolMap.transport` and `endpoint`.

- **Tier 2: recover the command set (the reverse engineering proper).** With capture
  running (see [CAPTURE.md](CAPTURE.md)), drive the OEM software through one discrete
  action at a time (connect, load a sort protocol, set the plate/deposition, prime,
  start, abort, clean) and mark each. Isolate the frame each action produced, decode its
  framing (header, length, payload, checksum), and diff single-parameter variants (96 vs
  384 plate, 1 vs N cells-per-well) to decode each field. Record each as a `Command` with
  a `frame_template`.

- **Tier 3: guarded dispense control.** Once the map is complete, load a protocol, stage
  the plate, prime the cartridge, and start the sort behind `--armed --allow-actuation`
  with a human present; abort and clean the same way. Read status freely.

## A note on a read-only results path (bench-confirmed)

The Namocell software writes a per-run sort/dispense report (which wells received a cell,
counts, and gating statistics). If that report lands in a known folder, reading it is a
second zero-decode Tier 0 telemetry path, exactly analogous to the AVITI run-folder
watcher: an orchestrator learns which wells are filled and the run outcome with no
protocol decoding and no risk. The exact filename and schema are an on-the-bench
confirmation, so this repo does not ship a parser against a guessed format; it is called
out here because it is the highest-value read-only add once the folder is confirmed.

## The playbook

This follows PyLabRobot's own reverse-engineering method (see Method source below). The
guiding principle from the PLR maintainer: **if you can read all the data the OEM software
sends, you can replicate it yourself.** The Hana fits that method because the host-to-
instrument control link is an external data path whose traffic can be read.

1. **Map the OEM stack and transport.** Identify how Namocell's software reaches the
   instrument. Start with `namocell discover` to list the USB/serial candidates, then
   confirm the branch (USB-serial vs raw USB). Record it as `ProtocolMap.transport` and
   `endpoint`.

2. **Drive the OEM interface with small test programs.** Per the PLR method, keep each
   action minimal so the capture is easy to read: connect, load one protocol, set the
   plate once, prime, start, abort, clean. One discrete action at a time.

3. **Intercept the traffic while marking each action.** With capture running (see
   [CAPTURE.md](CAPTURE.md)), mark the instant of each action so the capture slices into
   action-aligned windows. For USB-serial this is `plr-re capture serial`; for raw USB it
   is a Wireshark usbmon capture.

4. **Correlate action to bytes and decode framing.** Isolate the frame an action
   produced and decode its header, length, payload, and checksum. Per the PLR heuristic,
   a frequently repeated frame is usually a status/keep-alive poll, not the action you
   want; set it aside.

5. **Decode each field by varying one parameter.** Straight from the PLR method: change a
   single parameter in the OEM UI (96-well then 384-well; one cell-per-well then several),
   capture both, and `plr-re decode diff` the two frames to locate that field. Template it
   with a `{param}` placeholder.

6. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a
   frame template. The map seeds the required command list up front, so `coverage()`
   always reports exactly which commands are still undecoded and therefore still block a
   live run.

7. **Guarded replay, then validate on the instrument.** Confirm read-only commands
   (get_status) first. Replay stays a dry run until you pass both `armed=True` and, for
   actuating commands, `allow_actuation=True` with a human in the loop; the backend
   refuses to start while any required command is undecoded. Only after the map is
   complete and read-only replay is confirmed, run a real sort-to-plate with a human
   present.

## Commands

```
# Tier 0: read-only USB/serial discovery (find the control link; touches nothing)
plr-re namocell discover

# Tier 2: capture and decode the host-to-instrument byte traffic
plr-re capture serial --port /dev/ttyUSB0 --baud 115200 --out namocell.jsonl
plr-re decode diff aa019600cc aa029600cc         # vary one field, see which byte moved

# Build and track the map
plr-re map seed namocell --out maps/namocell.json
plr-re map coverage maps/namocell.json           # exit 1 while anything is undecoded

# Tier 3: guarded control. Dry-run until armed; a live run needs a complete map.
plr-re namocell status --config configs/namocell.example.json
plr-re namocell sort   --map maps/namocell.json --config configs/namocell.example.json \
                       --protocol single_gfp --plate 384 --armed --allow-actuation
plr-re namocell abort  --map maps/namocell.json --config configs/namocell.example.json \
                       --armed --allow-actuation
```

## PyLabRobot integration shape

Follow PLR's three-layer machine structure (frontend, abstract backend, concrete
backend), the same shape as every other machine type in the library:

- **Frontend** `CellDispenser(MachineFrontend)`: the user-facing API with convenience
  methods (`sort_to_plate(protocol, plate=96, cells_per_well=1)`), holding application
  state.
- **Abstract backend** `CellDispenserBackend(MachineBackend)`: the minimal set of atomic
  commands every single-cell dispenser implements: `setup`, `stop`, `get_status`,
  `load_protocol`, `set_deposition`, `prime`, `start_sort`, `wait_complete`, `abort`,
  `clean`. Per PLR, commands are interactive and minimal.
- **Concrete backend** `NamocellHanaBackend(CellDispenserBackend)`: loads a finished
  `ProtocolMap` and replays each command as a byte frame. The two arming switches gate
  transmission: `armed` opens the transport, `allow_actuation` releases any command that
  moves fluid or fires a sort.

Writing it is the documented PLR workflow: copy `backend.py`, rename the class to
`NamocellHanaBackend`, remove the `abc` decorators, and implement the methods against the
recovered ProtocolMap. Capture and decoding stay here in plr-re; only the finished
ProtocolMap and the replay side ship in the library.

## Method source

This playbook applies PyLabRobot's existing reverse-engineering method rather than
inventing one:

- PLR reverse-engineering guidance (read all the data, drive small OEM test programs, vary
  one parameter and diff, repeated frames are usually status/keep-alive):
  <https://discuss.pylabrobot.org/t/is-there-guide-to-reverse-engineer-a-machine-to-be-supported-to-plr/285>
- Contributing a new machine type (frontend / abstract `MachineBackend` / concrete
  backend, atomic `setup`/`stop`):
  <https://docs.pylabrobot.org/stable/contributor_guide/new-machine-type.html>
- Contributing / writing a backend (copy `backend.py`, implement the methods):
  <https://docs.pylabrobot.org/stable/contributor_guide/contributing.html>
- Rick Wierenga, "How To Reverse Engineer Lab Equipment":
  <https://www.youtube.com/watch?v=waHR1ErHN-Y>

## Current status

Tier 0 (read-only USB/serial discovery) is implemented and safe to use today; it needs no
decoding and only enumerates the attached devices. The command set (Tier 2/3) is recovered
with the plr-re toolkit on the bench: the seed map lists the required commands, the
byte-diff decoder reads each field out of a capture, and the guarded replayer refuses a
live run against an incomplete map and rejects an unsupported plate format. Hardware
validation (step 7) will be reported honestly when it lands.
