# VIAFLO 96 capture: getting the host-to-instrument traffic

The VIAFLO 96 is driven over a USB link, so capture means recording the traffic between
INTEGRA's VIALINK software and the pipette, then decoding the frames. This is the VIAFLO
analog of identifying Ready/Start/Stop on a contact-closure connector or exporting a HAR
from the AVITI UI: it is how you get the raw material the decoder works on.

Capture is passive. It observes traffic; it sends nothing to the instrument. Marking one
action at a time is what turns a stream of bytes into "this frame is run_program" and, more
importantly for this instrument, "this block is the serialized program."

## First: find the wire

Run the read-only discovery to see which USB/serial devices are attached and which is the
likely control link:

```
plr-re viaflo discover
```

The pipette connects over USB Type A-to-B, directly or through the programming stand or the
communication module. A port whose VID matches a common USB-serial bridge (FTDI, CP210x,
Prolific, CH340) is flagged as the candidate control link; a CDC ACM virtual-COM port is
just as likely for an INTEGRA communication module. Confirm it by unplugging the pipette (or
the stand) and re-running: the port that disappears is the pipette's.

## Route A: USB-serial / virtual-COM (the strong prior)

If the control link is a USB-serial bridge or a CDC ACM virtual-COM port, log it with the
built-in serial capture and mark actions alongside it:

```
plr-re capture serial --port /dev/ttyUSB0 --baud 115200 --out viaflo.jsonl
```

The baud rate is a bench unknown until confirmed; if frames come out as garbage, sweep the
common rates (9600, 19200, 38400, 57600, 115200) until the framing is clean. For action
marking in another shell:

```
plr-re mark --out viaflo.jsonl.marks.jsonl
```

Then reassemble and decode. `plr-re decode diff` locates a field by diffing two frames that
differ in one parameter; if the link turns out to be Modbus-framed, `plr-re decode
modbus-log viaflo.jsonl` pulls the register writes out for free.

## Route B: raw USB bulk (Wireshark + usbmon)

If discovery shows the pipette as a vendor USB device rather than a serial port, the
protocol is raw USB bulk/interrupt. Capture it with Wireshark on Linux via the usbmon
interface:

1. `sudo modprobe usbmon`
2. Find the bus the pipette is on with `lsusb` (note the VID:PID and bus number).
3. Capture that bus with Wireshark/dumpcap on the matching `usbmonN` interface.
4. Perform and mark one action at a time as below.
5. Filter to the pipette's device in Wireshark and read the bulk-transfer payloads; those
   payloads are the frames to feed to `plr-re decode diff`.

For armed replay the map's transport becomes `usb` with endpoint
`usb:VID:PID[/out=EP,in=EP]`, served by the toolkit's USB byte connection once the raw-USB
transport lands (the `serial` branch is armed-ready today). The bulk OUT/IN endpoint addresses
are read from the usbmon capture. Capture and decode are otherwise the same as serial.

## What to capture (one small action per mark)

Following the PyLabRobot method, keep each VIALINK action small and discrete so the decoder
can isolate the one frame it produced. Capture each of these once, cleanly:

- connect / open the session (learn the handshake and any keep-alive)
- read the pipette identity (VIALINK reads model / serial / firmware for its library)
- list the programs stored on the pipette
- **upload a short program** (this is the important one; see below)
- select a program to run next
- run the active program
- stop / abort a run
- home the head
- poll status (the read-only telemetry)

### The program upload is its own small format

The single highest-value capture is the program upload, because the pipetting semantics live
inside it. Decode it as its own format:

- Build a minimal one-step program in VIALINK (for example, aspirate 10 uL), upload it, and
  mark the transfer.
- Change **one** step volume (aspirate 20 uL), upload again, and mark it. `plr-re decode
  diff` on the two transfers locates how a volume is encoded; template that field with a
  `{param}` placeholder.
- Repeat for the other fields you need (dispense volume, mix, speed, tip pickup/eject) one
  parameter at a time. The goal is to synthesize a new program, not just replay one blob.

Expect a stream of identical background frames the whole time. Per the PLR guide, a
frequently repeated frame is almost always a status/keep-alive poll, not an action; set it
aside so it does not distract from the frame you marked.

## Privacy and good citizenship

Capture only your own instrument's traffic, and treat any captured sample or method
identifiers as secrets. Keep raw captures out of the repo (`.gitignore` covers `*.pcap`,
`*.har`, and `*.marks.jsonl`; keep the serial `*.jsonl` logs out too), and strip anything
sensitive before sharing a capture.
