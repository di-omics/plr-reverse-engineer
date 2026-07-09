# Biotage V-10 Touch: reverse-engineering the control plane

The Biotage V-10 Touch is a rapid solvent evaporation system: a spinning sample vial
under controlled heat, vacuum, and a nitrogen gas assist, driven from a touchscreen
HMI with no published automation API. Bringing it under PyLabRobot lets a Raspberry Pi
set the method and start, monitor, and stop an evaporation as a step inside a larger
run, so a dry-down stops being a manual hand-off. This page is the reproducible,
safety-first playbook for recovering its control set. It follows the
reverse-engineering methodology Rick Wierenga used to bring other instruments into
PyLabRobot, credited here and adapted for an evaporator.

The recovered command set is captured as a `ProtocolMap` (a decoded, replayable set of
commands), the same artifact the FACSMelody backend consumes. The reverse-engineering
that produces a ProtocolMap lives here in plr-re; the PyLabRobot backend (in
[di-omics/pylabrobot](https://github.com/di-omics/pylabrobot)) only loads a finished
ProtocolMap and replays it behind hard guards.

## The V-10 has a documented external interface (use it first)

Good news that reshapes this playbook: the V-10 Touch is not only a touchscreen. Biotage
ships V-10 Touch Control Centre, a Windows app (a Controller plus an Editor) that runs on
an external computer and drives the system, including integration with a liquid handler.
When the Control Centre is driving, the touchscreen shows "Remote Control", and only one
master talks at a time (the Control Centre or the touchscreen).

The link is a direct Ethernet connection (TCP/IP with a static IP), confirmed in the
Biotage "V-10 Touch Installation and Safety" document: the "Control Centre communication
port" is an Ethernet port, and setup configures a fixed IP on the PC's Ethernet adapter.
The V-10's RS-232 port is for a Gilson liquid handler and the carousel, not the PC link.

So the primary path is not a teardown. Run the Control Centre against the V-10 and mirror
that Ethernet link with a managed switch, exactly like the mass-spec Tier 2 capture:
perform one Controller action, see the bytes. Opening the case to tap the internal HMI bus
is the fallback, only if you cannot get on the Control Centre link. Config, users,
workspaces, and logs live under `C:\Program Files (x86)\Biotage\Biotage V-10 Touch Control
Centre\`.

## Safety posture (read first)

The V-10 heats volatile, flammable organic solvents (dichloromethane, methanol, and
similar) under vacuum while spinning, with a heated block or lamp that can reach
solvent boiling points. The hazard is a runaway heat plus vacuum plus solvent
combination. The backend is timid by default:

- `armed=False` (default) is a dry run: it logs the exact frames it would send and
  transmits nothing.
- `allow_actuation=False` (default) refuses any command that turns on the heater,
  starts the vacuum, spins the vial, opens the gas, or starts a method, even when
  armed. Read-only telemetry (status, temperature, pressure) is allowed.
- A live run refuses to start until every required command in the ProtocolMap is
  decoded, and a configured temperature ceiling is enforced so a decoded setpoint
  cannot command the heater past a safe limit.

Reverse-engineering an instrument you own for interoperability is legitimate. Keep the
guards and keep the OEM interlocks in place: the Pi sets and starts a method, it does
not remove the instrument's own temperature and vacuum limits. Have a physical E-stop
reachable.

## Scope

The V-10 is a small setpoint machine: temperature, vacuum, spin, gas, and time, plus a
few readbacks. That makes the whole thing tractable to recover in a day, unlike a
metrology instrument. The question is only which interface the touchscreen uses to
reach the controller, and the plan branches on that.

## The playbook

1. **Map the OEM stack and transport.** Primary: get on the Control Centre link. It is a
   direct Ethernet connection (TCP/IP, static IP); connect the Control Centre PC to the
   V-10's Ethernet port per the "Installation and Safety" document and note the IPs.
   Fallback: if you cannot use the Control Centre link, the internal HMI-to-controller bus
   is the teardown target (a two-board design, a touchscreen HMI sending setpoints to a
   motion/IO controller, likely Modbus RTU). Record what you find as `transport_discovery`;
   it fills `ProtocolMap.transport` and `endpoint`.

2. **Capture traffic against labeled UI actions.** With capture running on the Control
   Centre link, perform one discrete Controller action at a time (set temperature, vacuum
   on, spin start, gas on, start method, stop) and mark the instant of each. This is
   Rick's core move: perform one action, see exactly what bytes it produced. The Control
   Centre is the OEM here and the V-10 is the device. In the teardown fallback the same
   applies to HMI-to-controller traffic.

3. **Correlate action to bytes and decode framing.** If the bus is Modbus RTU (common
   for HMI-to-PLC links), decoding is nearly free: each frame is slave address,
   function code (03 read holding, 06 write single, 10 write multiple), register,
   value, and a CRC16. Change one setpoint at a time (40 C then 41 C) and read which
   register moved to map registers to setpoints. If it is a custom frame, decode
   header, length, payload, and checksum and diff single-parameter changes, exactly as
   the FACSMelody playbook does.

4. **Build the ProtocolMap with coverage tracking.** Record each decoded command as a
   frame template (or a Modbus register write) with parameter encoders and a success
   response. The map seeds the required command list up front, so `coverage()` reports
   exactly which commands still block a live run.

5. **Guarded replay.** Confirm read-only commands (get_status, read temperature and
   pressure) first. Replay stays a dry run until you pass both `armed=True` and, for
   actuating commands, `allow_actuation=True` with a human in the loop, and the
   temperature ceiling is enforced on every setpoint. Decide how the Pi joins the bus:
   cleanest is to replace the HMI so the Pi is the sole master (Modbus master, or the
   custom-protocol master); sitting as a second master on a shared RS-485 multidrop
   risks contention and is not recommended.

6. **Validate on the instrument.** Only after the map is complete and read-only replay
   is confirmed, run a real evaporation end to end with a human present. This moves the
   backend from not-yet-hardware-validated to hardware-validated.

## Plan B: CV/UI automation of the touchscreen

If the internal bus is inaccessible, encrypted, or not worth the teardown, fall back to
driving the touchscreen the way di-omics drives FACSChorus: computer vision on a camera
feed plus a touch actuator, in di-omics/lab-cv. It reads the screen state and taps the
setpoints and Start/Stop. It is slower and less precise than a decoded bus but it needs
no teardown and does not void anything. Treat it as the fallback, not the first move.

## Approach: bench kit and run of show

Bring:

- The Control Centre PC (or a laptop with Control Centre installed), its Ethernet cable to
  the V-10, and the managed switch plus a spare Cat6 to mirror the link to the capture host.
- For the fallback only: a USB-to-serial and USB-to-RS485 adapter, a logic analyzer
  (Saleae-class), screwdrivers to open the case, and a camera mount in case Plan B is needed.
- The "Installation and Safety" document for the Control Centre IP setup.

Run of show:

1. Get on the Control Centre link. Connect Control Centre to the V-10 over Ethernet and
   note both IPs. Put the managed switch inline between the PC and the V-10 and mirror the
   port to the capture host.
2. Drive the V-10 from the Control Centre one action at a time, marking each:
   `plr-re capture lan --iface eth1 --hosts <v10-ip> --out v10.pcap --mark`.
3. Decode: extract the TCP payloads per marked action and `plr-re decode diff` two
   single-parameter frames; if the payloads are Modbus/TCP or Modbus-like,
   `plr-re decode modbus-log` reads a byte stream straight into a register map.
4. Only once decoded: have the Pi drive as the sole master (Control Centre closed) and
   confirm read-only reads before enabling actuation, with the temperature ceiling set.
5. Fallback if you cannot get on the Control Centre link: open the case, find the
   inter-board serial header, recover baud/framing with the logic analyzer, and tap the
   internal bus instead.

## PyLabRobot integration shape

Mirror the FACSMelody layout under `pylabrobot/biotage/v10/`. Expose one small,
event-oriented `Evaporator` capability: `get_status`, `set_temperature(temp)`,
`set_vacuum(on)`, `set_spin(rpm)`, `set_gas(on)`, `evaporate(*, temperature, vacuum,
seconds)`, and `abort`. An `EvaporatorBackend` ABC keeps the surface hardware-agnostic
so another dry-down instrument can implement it later.

A `BiotageV10` device owns a driver over the serial (or RS-485/Modbus) transport behind
the ProtocolMap consumer. The same two arming switches gate transmission: `armed` opens
the transport, `allow_actuation` releases any command that heats, pulls vacuum, spins,
or starts a method. The driver additionally clamps every temperature setpoint to a
configured ceiling before it is ever framed.

## Current status

The primary transport is the documented Control Centre link (Ethernet/TCP-IP, confirmed);
sniffing it, rather than a teardown, is the expected path, with the internal HMI bus as
fallback.
The ProtocolMap is then recovered with the plr-re toolkit; a Modbus RTU link would make
the register map quick. The PLR backend is dry-run by default, enforces a temperature
ceiling, and refuses a live run against an incomplete map. Hardware validation (step 6)
will be reported honestly when it lands.
