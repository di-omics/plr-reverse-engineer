# Agilent 6530 Q-TOF LC/MS: reverse-engineering the control plane

The Agilent 6530 Accurate-Mass Q-TOF and its 1260/1290 Infinity LC front end are
driven by MassHunter Acquisition and have no published headless API. Bringing the
stack under PyLabRobot means recovering the OEM control set so a Raspberry Pi can
orchestrate runs (stage plate, trigger, read state) without a person at the
MassHunter console. This page is the reproducible, safety-first playbook for doing
that. It follows the reverse-engineering methodology Rick Wierenga used to bring
other instruments into PyLabRobot, credited here and adapted for an LC/MS stack.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set
of commands), the same artifact the FACSMelody backend consumes. The reverse-engineering
that produces a ProtocolMap lives here in plr-re; the PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only loads a finished
ProtocolMap and replays it behind hard guards.

## Safety posture (read first)

The 6530 stack is high voltage plus pressurized gas plus hazardous solvents: an ESI
source at several kV, drying gas up to ~350 C, nebulizer nitrogen, a nitrogen
generator, a rough/turbo vacuum system, and a mobile phase of methanol, acetonitrile,
and formic acid. The backend is timid by default:

- `armed=False` (default) is a dry run: it logs the exact frames or line pulses it
  would send and transmits nothing.
- `allow_actuation=False` (default) refuses any command that starts a pump, opens a
  gas or HV path, or begins acquisition, even when armed. Read-only telemetry
  (status, pressure, oven temperature, error flags) is allowed.
- A live run refuses to start until every required command in the ProtocolMap is
  decoded.

Reverse-engineering an instrument you own for interoperability is legitimate. Driving
a live source carelessly is how you flood a nebulizer or strike an arc. Keep the
guards, and keep the OEM safety interlocks in place: the Pi orchestrates, it does not
replace the instrument's own limits.

## Scope: what the Pi should and should not do

The Q-TOF acquisition path (TOF tuning, reference-mass correction, ion-optics, the
high-rate data stream, mass calibration) is a metrology stack. Do not try to replace
it. The tractable, high-value target is orchestration and telemetry: tell the stack to
run, know when it is ready or errored, and get the data off. That is what makes
walkaway sample-to-result possible, and it decomposes into tiers from cheapest and
safest to hardest. The approach lands Tier 0 first, then starts Tier 1.

## The playbook

1. **Map the OEM stack and transport.** MassHunter sits on top of the Agilent
   Instrument Control Framework (ICF), which is the OEM command layer to the LC
   modules. Identify every transport the stack actually uses: the rear APG remote /
   ERI contact-closure connector on each module (digital start/stop/ready lines), the
   instrument LAN (each Infinity II module has its own IP on a private subnet behind a
   second NIC on the MassHunter PC; older 1100/1200 chain over CAN with one LAN card),
   and the MS control link over that LAN. Record IPs, ports, and connector pinouts as
   the `transport_discovery` step; it fills `ProtocolMap.transport` and `endpoint`.

2. **Capture traffic against labeled UI actions.** With capture running, perform one
   discrete MassHunter action at a time (connect, load method, set flow, set oven
   temperature, set injection volume, single-sample run, stop, standby) and mark the
   instant of each. Marking slices the capture into action-aligned windows. This is
   Rick's core move: perform one action, see exactly what bytes it produced.

3. **Correlate action to bytes and decode framing.** For each labeled window, isolate
   the frame the action produced and decode structure: header, length, payload,
   checksum. Vary a single parameter (injection volume 1 uL then 2 uL, oven 40 C then
   41 C) and diff the frames to locate and decode each parameter encoding. For the APG
   remote lines there is no framing to decode: correlate which pin transitions on
   start, stop, and ready.

4. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a
   frame template (or a contact-closure line spec) with parameter encoders and a
   success response pattern. The map seeds the required command list up front, so
   `coverage()` always reports exactly which commands are still undecoded and
   therefore still block a live run.

5. **Guarded replay.** Confirm read-only commands (get_status, read pressure and oven
   temperature) first. Replay stays a dry run until you pass both `armed=True` and, for
   actuating commands, `allow_actuation=True` with a human in the loop. The backend
   refuses to start acquisition while any required command is undecoded.

6. **Validate on the instrument.** Only after the map is complete and read-only replay
   is confirmed, run a real staged sample end to end with a human present. This moves
   the backend from not-yet-hardware-validated to hardware-validated, and it is
   follow-up to landing the code, not a prerequisite.

## Tiered attack plan

- **Tier 0 (the MVP): APG remote / ERI contact closure over Pi GPIO.**
  Every Agilent module exposes a rear remote connector carrying digital lines: Ready,
  Start, Stop, Prepare, Start Request, Power On, Shut Down. They are simple active-low
  contact-closure / TTL signals. A Pi drives them through opto-isolators (never wire
  GPIO straight to the instrument): read the Ready line, pulse Start to begin an
  armed method, pulse Stop to abort, read the error/not-ready line. Read the exact
  pinout off the module's manual for your generation (APG remote on 1100/1200, ERI on
  Infinity II); do not assume pin numbers. This needs zero protocol decoding, is fully
  reversible, and is enough to let the Pi orchestrate: stage the plate, arm the LC and
  MS method in MassHunter, then start and monitor the run by wire. This is the honest
  first deliverable.

- **Tier 1: LAN telemetry, read-only.** Point the Pi at each module IP on the
  instrument subnet. The modules run an onboard web server and historically answer a
  text status interface over Telnet; pull module state, pressure, oven temperature,
  lamp hours, and error logs. Read-only, safe to enable, and it gives the orchestrator
  real feedback instead of just a ready contact.

- **Tier 2: sniff MassHunter/ICF to module LAN control.** This is the reverse
  engineering proper and where the LC ProtocolMap is recovered. Capture the control
  traffic (see bench kit), correlate single-parameter MassHunter actions to frames,
  decode injection volume, flow, gradient table, and oven setpoint, and decode the run
  start/stop frames. Discover the control port from the capture; do not assume it.

- **Tier 3 (stretch, later): MS control and data.** Full MS acquisition control
  is out of scope. For data, do not fight the wire: watch the acquisition output folder
  and read the produced `.d` dataset off disk. That gets results to the Pi without
  touching the high-rate stream.

## Approach: bench kit and run of show

Bring:

- Raspberry Pi with GPIO broken out, plus an opto-isolator / relay HAT for the remote
  lines. Never connect GPIO directly to instrument contacts.
- The mating APG remote / ERI connector and hookup wire, plus a multimeter and a
  cheap logic analyzer to identify which line is Ready/Start/Stop before you drive
  anything.
- A managed switch with port mirroring (SPAN) for the instrument LAN, or plan to run
  Wireshark directly on the MassHunter PC bound to its instrument NIC (simplest, no
  tap). An Ethernet tap is the fallback.
- The module and MS manuals for the remote pinout and the module default IPs.

Run of show:

1. Photograph the rear panel and cabling before touching anything. Note module IPs
   and the remote connector.
2. Tier 0: identify Ready/Start/Stop by meter and logic analyzer, wire the Pi through
   opto-isolators, and confirm you can read Ready and pulse Start/Stop against an armed
   method. Keep `allow_actuation=False` until you have watched one full manual run.
3. Tier 1: from the Pi, reach each module IP, open the web page, try the Telnet status
   interface, and log pressure and oven temperature read-only.
4. Tier 2: start a Wireshark capture on the instrument NIC, then in MassHunter change
   one parameter at a time (injection volume, oven, flow) and single-sample run.
   Mark each action. Save the pcap for offline correlation in the plr-re toolkit.

## PyLabRobot integration shape

Mirror the FACSMelody layout under `pylabrobot/agilent/`. The stack is two small,
event-oriented capabilities so orchestration and metrology stay separable:

- A `LiquidChromatograph` capability: `get_status`, `inject(volume, vial)`,
  `set_gradient(...)`, `set_oven(temp)`, `start_run`, `stop_run`.
- A `MassSpectrometer` capability: `get_status`, `start_acquisition(method)`,
  `stop_acquisition`, `abort`.

An `Agilent6530` device owns a driver that holds three transports behind the ProtocolMap
consumer: contact closure (Pi GPIO, for the APG/ERI lines), TCP (module LAN control),
and read-only telemetry. The same two arming switches gate transmission: `armed` opens
the transports, `allow_actuation` releases any command that starts a pump, gas, HV, or
acquisition. Contact-closure lines are represented in the ProtocolMap as a line spec
(which pin, pulse or level, active-low) alongside the TCP frame templates.

## Current status

Transport discovery and the LC ProtocolMap are being recovered with the plr-re toolkit.
Tier 0 (contact closure) is reversible and needs no decoding; the LAN control map is
in progress. The PLR backend is dry-run by default and refuses a live run against an
incomplete map. Hardware validation (step 6) will be reported honestly when it lands.
