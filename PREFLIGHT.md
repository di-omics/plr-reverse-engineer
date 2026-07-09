# Preflight: hardware checklist

Everything to buy, bring, and install for the bench session. Mass spec first; it is
enough on its own for day one. Full rationale is in [bench-kit.md](bench-kit.md); the run
of show is in [APPROACH.md](APPROACH.md). Prices are approximate USD for planning.

The mass-spec side is non-invasive: contact closure on the rear remote connector and a
passive LAN mirror. Nothing wires into the source, gas, fluidics, or high voltage.

## 0. Two facts, already resolved (neither blocks ordering)

- V-10 Control Centre link: CONFIRMED Ethernet / TCP-IP. Biotage's "V-10 Touch
  Installation and Safety" doc sets it up as a direct Ethernet link with a static IP (the
  "Control Centre communication port" is an Ethernet port). The V-10's RS-232 port is for
  a Gilson liquid handler and the carousel, not the PC. So you already have what you need
  (the managed switch); nothing extra to buy for the V-10 primary path.
- Agilent remote connector: a 6530-era stack is almost certainly APG (9-pin); only
  Infinity II modules use ERI (15-pin). You do not need to resolve this before ordering:
  buy the APG cable plus the ERI-to-APG adapter (Agilent 5188-8045) and you are covered
  either way. Optional: check the LC module model numbers (Infinity II = ERI, older =
  APG) to decide whether you can skip the ~30 adapter.

## 1. Mass spec (Agilent 6530) -- PRIORITY

Control and capture host
- [ ] Raspberry Pi 5, 8 GB (~80)
- [ ] Official Pi 5 27W USB-C power supply (~12)
- [ ] Pi 5 active cooler (~5)
- [ ] 256 GB NVMe + M.2 HAT (~40), or a 64 GB A2 microSD (~10) for contact-closure only
- [ ] Pi 5 case (~10)

Tier 0 contact closure (the MVP)
- [ ] Opto-isolated I/O HAT with inputs and relay/SSR outputs, e.g. a Sequent Microsystems
      automation HAT (~50). Cheaper split: PC817 opto-input module (~8) + relay/SSR HAT (~12)
- [ ] 8-channel USB logic analyzer, sigrok/PulseView compatible (~20)
- [ ] Digital multimeter (~25) [skip if you own one]
- [ ] Jumper wires (M-F, F-F), 22-24 AWG hookup wire, small breadboard (~15)
- [ ] APG remote cable to splice, or the bare 9-pin APG mating connector (~8 to 40)
- [ ] Agilent 5188-8045 ERI-to-APG adapter (~30): covers the Infinity II (ERI) case so you
      are connector-safe either way. Skip only if you confirmed plain APG modules

Tier 1/2 LAN telemetry and sniff
- [ ] Managed switch with port mirroring, TP-Link TL-SG108E Easy Smart (~35)
- [ ] USB 3.0 to gigabit Ethernet adapter (~15)
- [ ] Cat6 patch cables, four (~15)

Mass-spec order-now subtotal: ~300 to 350.

## 2. Biotage V-10 Touch (Control Centre link first)

The V-10 ships an OEM Control Centre PC app that drives it over a direct Ethernet link
(TCP/IP), so the primary path just sniffs that link with the switch you already have. Buy
the teardown items only if you cannot get on it.

Primary (Control Centre link is Ethernet/TCP-IP, confirmed)
- [ ] Nothing to buy. Sniff it with the managed switch above: put the switch inline
      between the Control Centre PC and the V-10 and mirror the port. Add one Cat6 cable
      if you are short one.

Fallback only (internal HMI-bus teardown, or tapping the Gilson serial port)
- [ ] FTDI USB-to-RS232 adapter (~15)
- [ ] FTDI USB-to-RS485 adapter (~12)
- [ ] FTDI FT232RL USB-to-TTL UART, 3.3V/5V (~8)
- [ ] Bi-directional logic level shifter, 3.3V/5V (~5)
- [ ] Micro-hook grabber test clips (~10)
- [ ] Precision screwdriver set including Torx (~15)
- [ ] ESD wrist strap (~8)
- [ ] Soldering iron, fine solder, flux (~30) [skip if you own one]

Plan C (touchscreen CV/UI, only if the bus is not viable)
- [ ] Raspberry Pi Camera Module 3 + adjustable arm (~35)

Biotage order-now subtotal: primary ~0 (reuses the switch), fallback ~100.

## 3. Shared bench

- [ ] Powered USB hub (~20)
- [ ] Label tape and a marker (~5)
- [ ] Heat-shrink and ferrule assortment (~10)

## 4. Bring (things you already have)

- [ ] Laptop with Wireshark installed
- [ ] The V-10 Touch Control Centre PC, or a laptop with Control Centre installed, and the
      cable it uses to reach the V-10
- [ ] Access to the MassHunter acquisition PC
- [ ] Manuals: the Agilent module manual (remote pinout) and the Biotage "Installation and
      Safety" document
- [ ] Phone or camera for the rear-panel photos in step 0

## 5. Software to install (free)

- [ ] Wireshark (gives you dumpcap/tshark) on the capture host
- [ ] Python 3.9+ on the Pi, then from this repo: `pip install -e '.[serial,pi]'`
      (installs pyserial, and gpiozero + lgpio for Pi 5 GPIO)

## Rough total

Mass spec kit ~300 to 350 (plus ~30 for the 5188-8045 adapter if you want ERI insurance),
V-10 primary ~0 (reuses the switch), shared ~35. Call it ~350 to 400 to walk in ready; the
V-10 teardown fallback is another ~100 only if you ever need it.
