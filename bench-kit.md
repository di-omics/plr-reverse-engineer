# Instrument reverse-engineering bench kit (bill of materials)

What to buy to reverse-engineer and Pi-control the Agilent 6530 Q-TOF LC/MS and the
Biotage V-10 Touch, per the
[Agilent 6530 playbook](instruments/agilent-6530-qtof/README.md) and the
[Biotage V-10 Touch playbook](instruments/biotage-v10-touch/README.md). The mass spec is
the priority, so its kit is listed first and is enough on its own for the first bench
session.

Everything here is non-invasive on the mass spec: contact closure on the rear remote
connector and a passive LAN mirror. Nothing wires into the source, fluidics, gas, or
high voltage. Prices are approximate USD for planning, not quotes.

Two items depend on the exact connector on the hardware and should be confirmed from a
photo before ordering; they are called out in "Confirm before ordering" at the end.
Everything else in the "order now" lists is safe to buy today.

## Priority: Agilent 6530 Q-TOF LC/MS

### Control and capture host (order now)

- Raspberry Pi 5, 8 GB (~80). Capture and control host.
- Official Pi 5 27W USB-C power supply (~12).
- Pi 5 active cooler (~5).
- NVMe M.2 HAT plus 256 GB NVMe, or a 64 GB A2 microSD (~40 / ~10). NVMe is worth it for
  packet capture; microSD is fine for contact-closure-only work.
- Pi 5 case (~10).

### Tier 0, contact closure over the rear remote (order now, this is the MVP)

- Opto-isolated digital I/O for the Pi (~50). One board that has both opto-isolated
  inputs (to read the Ready line the instrument drives) and relay or open-drain outputs
  (to pulse Start and Stop as a contact closure into the instrument). A Sequent
  Microsystems industrial/building automation HAT with screw terminals is the clean
  option. Cheaper discrete alternative: a 4-channel PC817 opto-input module (~8) plus a
  2 to 4 channel relay or solid-state-relay HAT (~12).
- 8-channel USB logic analyzer, sigrok/PulseView compatible (~20). Confirm which rear
  pin is Ready, Start, and Stop before driving anything. A genuine Saleae Logic 8 is the
  premium option (~400) if you want it for the Biotage bus work too.
- Digital multimeter (~25), if not already on the bench. Identify the lines by continuity
  first.
- Jumper wires (M-F and F-F), 22 to 24 AWG hookup wire, small breadboard (~15).

### Tier 1 and 2, LAN telemetry and sniff (order now)

- Managed switch with port mirroring / SPAN, e.g. TP-Link TL-SG108E Easy Smart (~35).
  Mirrors the instrument-LAN traffic to the Pi without a tap and stays gigabit-clean.
- USB 3.0 to gigabit Ethernet adapter for the Pi (~15). Second NIC, so the Pi keeps its
  normal network while capturing on the mirror port.
- Cat6 patch cables, four (~15).
- Optional passive tap instead of the switch: Great Scott Gadgets Throwing Star LAN Tap
  (~15) but 10/100 only; for a gigabit non-disruptive tap you need a powered aggregation
  tap (~150 to 300). The managed switch is the recommended primary.
- Software is free: Wireshark and tcpdump.

Mass spec kit rough subtotal, recommended choices, order-now items: ~300 to 350 USD.

## Biotage V-10 Touch (secondary)

The V-10 has a documented external interface: Biotage's Control Centre PC app drives it
over a direct Ethernet link (TCP/IP, static IP; confirmed in the Biotage "Installation and
Safety" doc). So the primary path sniffs that link with the managed switch above (nothing
extra to buy), and the serial/teardown items below (USB-serial, logic analyzer,
screwdrivers, soldering) are the fallback, not the first move.

Reuses the Pi 5, logic analyzer, and multimeter above. For a permanent second install,
add a Raspberry Pi 4, 4 GB with PSU and SD card (~75).

### Order now

- FTDI USB-to-RS232 adapter (~15). For a documented external DB9 or the HMI bus if it is
  RS-232 level.
- FTDI USB-to-RS485 adapter (~12). The internal HMI-to-controller bus is often RS-485
  Modbus RTU.
- FTDI FT232RL USB-to-TTL UART adapter, 3.3V/5V selectable (~8). For a bare TTL header.
- Bi-directional logic level shifter, 3.3V/5V (~5).
- Micro-hook grabber test clips / IC test hooks (~10). Tap the inter-board header without
  soldering.
- Precision screwdriver set including Torx (~15). To open the case.
- ESD wrist strap (~8).
- Soldering iron, fine solder, flux (~30), if not on the bench, in case a header must be
  tacked on.

### Plan B, touchscreen CV/UI fallback (order now if you want it staged)

- Raspberry Pi Camera Module 3 plus an adjustable arm mount (~35). For the lab-cv
  fallback if the internal bus is not worth tapping. The touch-actuator rig is a
  fabrication item and is deferred, not a purchase.

Biotage kit rough subtotal, order-now items: ~90 to 130 USD.

## Shared bench (order now)

- Powered USB hub (~20). Several USB adapters and the analyzer at once.
- Label tape and a marker (~5). Mark captures and lines as you go.
- Heat-shrink and ferrule assortment (~10).

## Confirm before ordering (send a photo first)

These two depend on the exact hardware and should be pinned from a photo before the
purchase request goes in. Everything above can be ordered without them.

1. Agilent rear remote connector. The mating connector differs by module generation:
   APG remote on 1100/1200, ERI on Infinity II. Photograph the rear panel of the LC
   modules and the MS. Then order either the Agilent remote cable to splice or the bare
   mating connector for that generation.
2. Biotage interface. Photograph the rear panel for any external serial, USB, or
   Ethernet port and, once the case is open, the inter-board header, so the connector
   pitch and level (RS-232, RS-485, or TTL) are known. The three FTDI adapters above
   cover all three cases, so this is only to avoid buying a connector that does not fit.
