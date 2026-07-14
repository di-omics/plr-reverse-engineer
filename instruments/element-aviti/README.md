# Element AVITI: reverse-engineering the control plane

The Element AVITI benchtop sequencer runs AvitiOS, a sandboxed microservice operating
system. The glove-compatible touchscreen and Elembio Cloud are both clients of that
stack, and there is no published local automation API. Bringing the AVITI under
PyLabRobot means recovering the control set the UI already uses, so a run can be staged,
started, monitored, and handed off headlessly (for example from a Raspberry Pi) instead
of through the console. This page is the reproducible, safety-first playbook. It follows
the reverse-engineering methodology Rick Wierenga used to bring other instruments into
PyLabRobot, adapted here for a modern HTTP/JSON microservice instrument.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set of
commands), the same artifact the other backends consume. The reverse-engineering that
produces a ProtocolMap lives here in plr-re; the PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only loads a finished
ProtocolMap and replays it behind hard guards.

## What is different about this one

The three other instruments in this repo speak byte frames over a wire: contact-closure
lines, a raw LAN socket, a Modbus serial bus. The AVITI is a networked microservice
stack, so the transport class is HTTP/JSON, and the reverse-engineering is different in
kind: capture the UI-to-service traffic as a HAR, read the API calls out of it, and
record each one as a request template (method, path, JSON body) rather than a hex frame.
The toolkit gained an `http` transport, a `GuardedHttpReplayer`, and a HAR decoder for
exactly this.

## Safety posture (read first)

The AVITI is imaging optics (lasers), fluidics and pumps, sequencing reagents, and a
single-use flow cell that costs real money. Nothing here removes the instrument's own
interlocks; the backend is timid by default:

- `armed=False` (default) is a dry run: it logs the exact HTTP request it would send and
  transmits nothing.
- `allow_actuation=False` (default) refuses any command that commits the instrument
  (upload a manifest, set run parameters, start a run, abort a run), even when armed.
  Read-only telemetry (status, run metrics, consumables) is allowed.
- A live run refuses to start until every required command in the ProtocolMap is
  decoded.

Reverse-engineering an instrument you own for interoperability is legitimate. The point
here is orchestration and telemetry, not touching the sequencing chemistry or metrology:
the recipe, base calling, and image processing are the vendor's stack and stay untouched.

## Scope: what the Pi should and should not do

The avidity-sequencing recipe, base calling, and cytoprofiling image analysis are a
metrology and chemistry stack. Do not try to replace it. The tractable, high-value target
is orchestration and telemetry: know when a run is ready, running, or done; start and
abort a staged run; and get the data off. That decomposes into tiers from cheapest and
safest to hardest.

## Tiered attack plan

- **Tier 0 (the MVP, works today): run-folder state, read-only, zero decode.**
  AvitiOS writes each run to an output folder (local disk, USB, or an SMB share). It
  writes `RunParameters.json` at the start and `RunUploaded.json` last; the latter
  carries an `outcome` field and is what triggers downstream analysis. Reading those two
  files tells an orchestrator whether a run is running or complete, its outcome, and
  where the data is, with no protocol decoding and no risk. `plr-re aviti watch <run_dir>`
  does this and is safe to point at a live run today. It cannot *start* a run, but it
  gives honest state and a clean hand-off to Bases2Fastq.

- **Tier 1: control-plane discovery, read-only.** Find the AvitiOS service endpoint on
  the instrument's Cat6 network. `plr-re aviti probe <ip>` sweeps candidate ports and
  issues a single read-only GET, so you learn the base URL and which service answers
  before capturing anything. Purely passive.

- **Tier 2: recover the control API (the reverse engineering proper).** Capture the
  touchscreen-UI-to-service traffic (and/or the instrument-to-Elembio-Cloud traffic) as a
  HAR, perform one discrete UI action at a time and mark it, and read the request each
  action produced. The state-changing requests (POST/PUT/PATCH) are the candidates for
  start, abort, upload-manifest, and set-parameters; GETs are the read-only telemetry.
  Record each as a `Command` with `http_method`, `http_path`, and a JSON `body_template`.
  See [CAPTURE.md](CAPTURE.md) for how to get the traffic.

- **Tier 3: guarded run control.** Once the map is complete, stage a `RunManifest.csv`
  and `RunParameters.json`, then start the run behind `--armed --allow-actuation` with a
  human present, and abort the same way. Read status and live metrics freely.

## A note on Elembio Cloud

If the AVITI is in online mode it connects to Elembio Cloud, which can already monitor
runs and configure run setups remotely through the vendor's own path. That is an official
avenue and worth using where it fits. It does not replace this work: Cloud is an
account-bound web service, whereas the goal here is local, headless orchestration from a
Pi on the bench with no console and no cloud round-trip. The two compose.

## The playbook

This follows PyLabRobot's own reverse-engineering method (see Method source below). The
guiding principle from the PLR maintainer: **if you can read all the data the OEM software
sends, you can replicate it yourself.** The AVITI is a good fit for that method precisely
because it is not one of the "no-computer devices" PLR cannot yet reach: it has an
external data path (a networked microservice API), so the traffic is there to be read.

1. **Map the OEM stack and transport.** AvitiOS is a microservice stack behind the
   touchscreen UI and the Cloud connector. Identify the control-plane endpoint (base URL,
   port, TLS) with `aviti probe`. Record it as `ProtocolMap.endpoint`; the transport is
   `http`.

2. **Drive the OEM interface with small test programs.** Per the PLR method, keep each
   action minimal so the capture is easy to read: open one run-setup screen, upload one
   small manifest, change one field, start, abort. Do one discrete action at a time.

3. **Intercept the traffic while marking each action.** With capture running (see
   [CAPTURE.md](CAPTURE.md)), mark the instant of each action so the capture slices into
   action-aligned windows. This is the HTTP analog of the Wireshark capture PLR uses on
   USB/serial instruments; here the wire is HTTP/JSON, so a HAR is the capture.

4. **Correlate action to request; ignore the keep-alive.** `plr-re decode har cap.har`
   lists the calls with writes first and flags the repeated reads. Per the PLR heuristic,
   a **frequently repeated command is usually a status/keep-alive poll**, not the action
   you want, so the tool marks those `~status/keep-alive?` and sorts them last. The
   state-changing request left in your marked window is the command.

5. **Decode each field by varying one parameter.** Also straight from the PLR method:
   change a single parameter in the OEM UI (cycles 150 then 300), capture both, and diff
   the two JSON bodies to locate that field. Template it with a `{param}` placeholder.

6. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a
   request template (method, path, body) with a success status. The map seeds the
   required command list up front, so `coverage()` always reports exactly which commands
   are still undecoded and therefore still block a live run.

7. **Guarded replay, then validate on the instrument.** Confirm read-only commands
   (status, metrics, consumables) first. Replay stays a dry run until you pass both
   `armed=True` and, for actuating commands, `allow_actuation=True` with a human in the
   loop; the backend refuses to start a run while any required command is undecoded. Only
   after the map is complete and read-only replay is confirmed, stage a real run and start
   it end to end with a human present.

## Commands

```
# Tier 0: run-folder state (safe on a live run today)
plr-re aviti watch /mnt/aviti-output/20260713_AV1_run42

# Tier 1: find the control-plane endpoint (read-only)
plr-re aviti probe 192.168.1.50
plr-re aviti probe 192.168.1.50 --ports 443 8443 8080 3000

# Tier 2: capture and decode the UI-to-service API
plr-re capture http --out cap.har              # or export a HAR from browser devtools
plr-re decode har cap.har                      # writes first = candidate actuation

# Build and track the map
plr-re map seed element_aviti --out maps/element_aviti.json
plr-re map coverage maps/element_aviti.json    # exit 1 while anything is undecoded

# Tier 3: guarded control. Dry-run until armed; a live run needs a complete map.
plr-re aviti status  --config configs/aviti.example.json
plr-re aviti start   --map maps/element_aviti.json --config configs/aviti.example.json \
                     --manifest RunManifest.csv --armed --allow-actuation
plr-re aviti abort   --map maps/element_aviti.json --config configs/aviti.example.json \
                     --armed --allow-actuation
```

## PyLabRobot integration shape

Follow PLR's three-layer machine structure (frontend, abstract backend, concrete
backend), the same shape as every other machine type in the library:

- **Frontend** `Sequencer(MachineFrontend)` in `pylabrobot/sequencing/sequencer.py`: the
  user-facing API with convenience methods (`run(manifest, cycles=...)`), holding
  application state.
- **Abstract backend** `SequencerBackend(MachineBackend)` in `backend.py`: the minimal
  set of atomic, immediately-executed commands every sequencer implements: `setup`,
  `stop`, `get_status`, `get_run_metrics`, `list_consumables`, `upload_manifest`,
  `set_run_parameters`, `start_run`, `abort_run`. Per PLR, commands are interactive and
  minimal.
- **Concrete backend** `ElementAvitiBackend(SequencerBackend)`: loads a finished
  `ProtocolMap` and replays each command as an HTTP request, plus the read-only run-folder
  watcher for Tier 0 state. The two arming switches gate transmission: `armed` opens the
  transport, `allow_actuation` releases any command that commits the instrument to a run.

Writing it is the documented PLR workflow: copy `backend.py`, rename the class to
`ElementAvitiBackend`, remove the `abc` decorators, and implement the methods against the
recovered ProtocolMap. Capture and decoding stay here in plr-re; only the finished
ProtocolMap and the replay side ship in the library.

## Method source

This playbook applies PyLabRobot's existing reverse-engineering method rather than
inventing one, adapted from a USB/serial wire to an HTTP/JSON one:

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

Tier 0 (run-folder state) is implemented and safe to use today; it needs no decoding and
only reads the output folder. Tier 1 discovery is a read-only probe. The control API
(Tier 2/3) is recovered with the plr-re toolkit on the bench: the seed map lists the
required commands, the HAR decoder reads them out of a capture, and the guarded HTTP
replayer refuses a live run against an incomplete map. Hardware validation (step 6) will
be reported honestly when it lands.
