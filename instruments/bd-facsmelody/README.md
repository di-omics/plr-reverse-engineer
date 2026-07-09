# BD FACSMelody: reverse-engineering the control plane

The BD FACSMelody has no published automation API, so driving it headlessly means
recovering its OEM command set. This page is the reproducible, safety-first playbook
for doing that. It follows the reverse-engineering methodology Rick Wierenga used to
bring other instruments into PyLabRobot, credited here and adapted for a cell sorter.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set of
commands). The reverse-engineering that produces a ProtocolMap lives here in plr-re; the
PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only consumes a finished
ProtocolMap and replays it behind hard guards.

## Safety posture (read first)

This drives a Class 1/3B-laser cell sorter with pressurized fluidics. The backend is
timid by default:

- `armed=False` (default) is a dry run: it logs the exact frames it would send and
  transmits nothing.
- `allow_actuation=False` (default) refuses to transmit any command that moves fluid or
  fires a sort (prime, start_sort, clean, set_deposition), even when armed.
- A live run refuses to start until every required command in the ProtocolMap is
  decoded.

Reverse-engineering an instrument you own for interoperability is a legitimate,
well-established practice. Driving a live sorter carelessly is how you aerosolize a
sample. Keep the guards.

## The playbook

1. **Map the OEM stack and transport.** Identify how the OEM software (BD FACSChorus)
   talks to the Melody: USB, serial, or TCP. Record vendor and product identifiers and
   the endpoint. This is the `transport_discovery` step and it fills in the
   `ProtocolMap.transport` and `endpoint`.

2. **Capture traffic against labeled UI actions.** With capture running, perform one
   discrete OEM action at a time (connect, load a sort template, set deposition, prime,
   start sort, abort, clean) and mark the instant of each action. Marking lets you slice
   the capture into action-aligned windows. This is Rick's core move: perform one
   action, see exactly what bytes it produced.

3. **Correlate action to bytes and decode framing.** For each labeled window, isolate
   the frame the action produced, then decode structure: header, length field, payload,
   and checksum. Vary a single parameter (for example cells-per-well) and diff the
   frames to locate and decode each parameter encoding.

4. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a
   frame template with parameter encoders and a success response pattern. The map seeds
   the required command list up front, so `coverage()` always reports exactly which
   commands are still undecoded and therefore still block a live run.

5. **Guarded replay.** Confirm read-only commands (get_status) first. Replay stays a dry
   run until you pass both `armed=True` and, for actuating commands, `allow_actuation=True`
   with a human in the loop. The backend refuses to start a live sort while any required
   command is undecoded.

6. **Validate on the instrument.** Only after the map is complete and replay is
   confirmed on read-only commands, run a real sort-to-plate with a human present. This
   is the step that moves the backend from not-yet-hardware-validated to
   hardware-validated, and it is follow-up to opening the PR, not a prerequisite.

## Current status

The command set for the FACSMelody is being recovered with the plr-re toolkit. The
backend in PyLabRobot is complete and tested in dry-run and chatterbox modes; it has not
yet been run against an instrument. Hardware validation (step 6) is in progress and will
be reported honestly when it lands.
