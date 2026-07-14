# Namocell capture: getting the host-to-instrument traffic

The Hana is driven over a byte link, so capture means recording the traffic between
Namocell's software and the instrument, then decoding the frames. This is the Namocell
analog of identifying Ready/Start/Stop on a contact-closure connector or exporting a HAR
from the AVITI UI: it is how you get the raw material the decoder works on.

Capture is passive. It observes traffic; it sends nothing to the instrument. Marking one
action at a time is what turns a stream of bytes into "this frame is start_sort."

## First: find the wire

Run the read-only discovery to see which USB/serial devices are attached and which is the
likely control link:

```
plr-re namocell discover
```

A port whose VID matches a common USB-serial bridge (FTDI, CP210x, Prolific, CH340) is
flagged as the candidate control link. Confirm it by unplugging the instrument and
re-running: the port that disappears is the instrument's.

## Route A: USB-serial (the strong prior)

If the control link is a USB-serial bridge, log the port with the built-in serial
capture and mark actions alongside it:

```
plr-re capture serial --port /dev/ttyUSB0 --baud 115200 --out namocell.jsonl
```

The baud rate is a bench unknown until confirmed; if frames come out as garbage, sweep
the common rates (9600, 19200, 38400, 57600, 115200) until the framing is clean. For
action marking in another shell:

```
plr-re mark --out namocell.jsonl.marks.jsonl
```

Then reassemble and decode. `plr-re decode diff` locates a field by diffing two frames
that differ in one parameter; if the bus turns out to be Modbus RTU (some HMI-to-
controller links are), `plr-re decode modbus-log namocell.jsonl` pulls the register
writes out for free.

## Route B: raw USB bulk (Wireshark + usbmon)

If discovery shows the instrument as a vendor USB device rather than a serial port, the
protocol is raw USB bulk/interrupt. Capture it with Wireshark on Linux via the usbmon
interface:

1. `sudo modprobe usbmon`
2. Find the bus the instrument is on with `lsusb` (note the VID:PID and bus number).
3. Capture that bus: `plr-re capture usb --iface usbmon0 --out namocell-usb.pcap --mark`
   (match the bus number; this shells to dumpcap/tshark, same as the LAN capture).
4. Perform and mark one action at a time as below.
5. Filter to the instrument's device in Wireshark and read the bulk-transfer payloads;
   those payloads are the frames to feed to `plr-re decode diff`.

For armed replay the map's transport becomes `usb` with endpoint
`usb:VID:PID[/out=EP,in=EP]`, served by the toolkit's pyusb byte connection (install the
`[usb]` extra: `pip install .[usb]` plus a libusb backend on the host). The bulk OUT/IN
endpoint addresses are auto-detected from the interface, or set explicitly once the
usbmon capture identifies them. Capture and decode are otherwise the same as serial.

## What to capture (one small action per mark)

Following the PyLabRobot method, keep each OEM action small and discrete so the decoder
can isolate the one frame it produced. Capture each of these once, cleanly:

- connect / open the session (learn the handshake and any keep-alive)
- load a sort protocol (single mode; then bulk mode, to see the mode field)
- set the deposition: pick 96-well, then **change it once** to 384-well, and separately
  vary cells-per-well; capturing the same action with one parameter varied is how you
  locate that field with `decode diff`
- prime the cartridge
- start a sort
- abort a sort
- run the clean/flush cycle
- poll status (the read-only telemetry)

Expect a stream of identical background frames the whole time. Per the PLR guide, a
frequently repeated frame is almost always a status/keep-alive poll, not an action; set
it aside so it does not distract from the frame you marked.

## Privacy and good citizenship

Capture only your own instrument's traffic, and treat any captured sample identifiers as
secrets. Keep raw captures out of the repo (`.gitignore` covers `*.pcap`, `*.har`, and
`*.marks.jsonl`; keep the serial `*.jsonl` logs out too), and strip anything sensitive
before sharing a capture.
