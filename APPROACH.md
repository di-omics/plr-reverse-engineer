# Approach

The order to work in on the bench, mass spec first. Everything is dry-run until you add
`--armed`, and actuating commands additionally need `--allow-actuation`. Rehearse each
phase in dry-run before you arm it. Keep the vendor interlocks in place; the Pi
orchestrates, it does not replace the instrument's own limits.

Install on the Pi first:

```
pip install -e '.[serial,pi]'
```

## Phase 0: recon (both instruments, ~20 min)

- Photograph both rear panels before touching anything: the Agilent remote connector
  (APG 9-pin or ERI 15-pin), each LC module's IP label, and the V-10 back panel.
- Note the MassHunter PC's instrument NIC and subnet.
- For the V-10: is there any external serial, USB, or Ethernet port? If yes, you may
  skip the teardown.

## Phase 1: Agilent Tier 0, contact closure (the MVP, ~60 min)

Follow [instruments/agilent-6530-qtof/WIRING.md](instruments/agilent-6530-qtof/WIRING.md).

1. Wire Ready, Start, Stop, and DGND through the opto/relay board to the Pi. Never
   direct-connect.
2. Find the Ready line and its sense:
   ```
   plr-re agilent scan --pins 17 5 6 13 19 26 --armed
   ```
   Set `ready` and `ready_active_low` in a pin map from what changed.
3. Confirm Ready, read-only:
   ```
   plr-re agilent status --config configs/agilent-pinmap.example.json --armed
   ```
4. Arm a method in MassHunter, then test Start/Stop with a human present:
   ```
   plr-re agilent start --config configs/agilent-pinmap.example.json --armed --allow-actuation
   plr-re agilent stop  --config configs/agilent-pinmap.example.json --armed --allow-actuation
   ```

Done for the day if: the Pi reads Ready correctly and Start/Stop drive an armed run.

## Phase 2: Agilent Tier 1, LAN telemetry (read-only, ~20 min)

```
plr-re agilent probe <module-ip>
```

Reachable modules and any banner get logged. Purely passive; sends nothing.

## Phase 3: Agilent Tier 2, capture LAN control (optional, time-permitting)

```
plr-re capture lan --iface eth1 --hosts <module-ip> --out cap.pcap --mark
```

In MassHunter, change one parameter at a time (injection volume, oven, flow) and
single-sample run; label each in the mark prompt. Save the pcap for offline decode. Use
`plr-re decode diff <frameA> <frameB>` on the two frames from a single-parameter change.
Track what you decode:

```
plr-re map seed agilent6530 --out maps/agilent6530.json
plr-re map coverage maps/agilent6530.json
```

## Phase 4: Biotage V-10 Touch (~60 min)

The V-10 has a documented external interface: Biotage's Control Centre PC app drives it
over a direct Ethernet link (TCP/IP, static IP). Sniff that link; a teardown is only the
fallback.

1. Connect Control Centre to the V-10 over Ethernet (see the "Installation and Safety" doc
   for the static-IP setup) and note both IPs. Put the managed switch inline between the PC
   and the V-10 and mirror the port to the capture host.
2. Drive the V-10 from Control Centre one setpoint at a time, marking each:
   ```
   plr-re capture lan --iface eth1 --hosts <v10-ip> --out v10.pcap --mark
   ```
3. Extract the TCP payloads per marked action and decode. Diff two single-parameter
   frames, or if the payload is a Modbus-like byte stream read it into a register map:
   ```
   plr-re decode diff <frameA> <frameB>
   plr-re decode modbus-log <bytes.jsonl>
   ```
   A register-writes summary, if any, is your setpoint-to-register map. Seed a map to track it:
   ```
   plr-re map seed biotage_v10 --out maps/biotage_v10.json
   ```
4. Rehearse control dry-run, then arm behind the temperature ceiling, human present
   (close Control Centre first so the Pi is the sole master):
   ```
   plr-re biotage set-temp 40 --map maps/biotage_v10.json
   plr-re biotage set-temp 40 --map maps/biotage_v10.json --armed --allow-actuation
   ```
5. Fallback only: if you cannot get on the Control Centre link, open the case, find the
   inter-board header, recover baud/framing with the logic analyzer, and tap that bus.

## If something is off

- `scan` shows no change: wrong candidate pins, or the opto input is not passing Ready.
  Meter the instrument Ready pin against DGND first.
- `modbus-log` finds zero frames: wrong baud/parity, or the bus is not Modbus. Re-check
  framing on the logic analyzer and try the byte-diff path instead.
- A live command refuses with "ProtocolMap is incomplete": expected. The map still has
  undecoded required commands; finish decoding or drive only what is decoded.
