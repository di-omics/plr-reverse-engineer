# Agilent APG remote wiring (Tier 0 contact closure)

How to wire a Raspberry Pi to the Agilent rear remote connector for the no-decoding
Tier 0 path: read Ready, pulse Start and Stop. Confirm every pin and polarity on the
bench before driving anything; the numbers here are the documented Agilent convention,
not a substitute for a meter.

## APG remote signals

The classic APG remote is a 9-pin connector. The signals are TTL, idle high at 5V.
Start, Stop, and Prepare are LOW-true: an input is asserted by pulling it to ground
(open-collector style). Ready is HIGH-true: 5V means the system is ready.

| Pin | Signal | Polarity | Direction (from instrument) |
| --- | --- | --- | --- |
| 1 | Digital ground (DGND) | -- | reference |
| 2 | Prepare | LOW-true | input |
| 3 | Start | LOW-true | input |
| 7 | Ready | HIGH-true | output |
| 8 | Stop | LOW-true | input |

Pins 4, 5, 6, and 9 carry generation-dependent signals (shut down, power on, start
request, or reserved) and are not needed for Tier 0. Read them off your module manual
if you want them. Source for the pinout and logic:
[Agilent 6890 remote connector pinout](https://www.agilent.com/library/support/documents/a15844.pdf).

Infinity II modules replace the APG remote with the ERI (Enhanced Remote Interface, a
15-pin sub-D with eight programmable I/O lines). An APG-to-ERI adapter cable exists
(Agilent 5188-8045); with it, the same Ready/Start/Stop mapping applies on the APG end.

## The wiring, through opto-isolators

Never wire instrument pins straight to Pi GPIO. Put an opto-isolator between them: it
protects the Pi from the instrument's 5V and any ground offset, and it gives Start/Stop
a clean contact closure to ground.

```
  Agilent APG remote                opto / relay board            Raspberry Pi (3.3V)
  ------------------                ------------------            -------------------
  pin 7  Ready (H) -----> [ opto input ch A ] -----> output ---> GPIO in  (BCM ready)
  pin 1  DGND ----------> [ common / return ]
  pin 3  Start (L) <----- [ relay/SSR ch B contact to DGND ] <-- GPIO out (BCM start)
  pin 8  Stop  (L) <----- [ relay/SSR ch C contact to DGND ] <-- GPIO out (BCM stop)
  pin 1  DGND ----------> [ relay common ]
```

- Ready: feed instrument Ready and DGND into an opto input; its isolated output drives a
  Pi input pin. Many opto-input boards invert (output goes low when the input is active),
  so do not assume `ready_active_low`; find it with `plr-re agilent scan`.
- Start and Stop: use a relay or solid-state-relay channel wired across the instrument's
  Start (or Stop) pin and DGND. Asserting the Pi output closes the contact, pulling the
  line to ground, which is the Start (or Stop) request. Release to end the pulse.

## Pin map and confirmation

`configs/agilent-pinmap.example.json` holds the Pi-side BCM assignments (arbitrary; pick
what your board wires) and the polarity flags:

```json
{ "ready": 17, "start": 27, "stop": 22,
  "ready_active_low": false, "out_active_low": true, "pulse_s": 0.25 }
```

Confirm before trusting it:

1. Find the Ready line and its Pi-side sense:
   ```
   plr-re agilent scan --pins 17 5 6 13 19 26 --armed
   ```
   It reads the pins with the instrument not-ready, then ready, and flags the pin that
   changed. Set `ready` to that BCM pin and `ready_active_low` to match what you saw
   (if the pin reads low when the instrument is ready, set it true).
2. Confirm Ready read-only, no actuation:
   ```
   plr-re agilent ready --config configs/agilent-pinmap.example.json --armed
   ```
3. Only then test Start/Stop against an armed method, human present:
   ```
   plr-re agilent start --config configs/agilent-pinmap.example.json --armed --allow-actuation
   ```
