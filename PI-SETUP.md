# Raspberry Pi setup (capture host and controller)

Prep the Pi at home the night before, not at the instrument: the installs need internet
and you do not want to be running apt under time pressure on the bench. By the time you
leave, the Pi should be a ready-to-go box you just plug in and SSH into.

Assumes a Pi 5, headless (no monitor; you drive it from your laptop over SSH). Pi 4 and
monitor differences are noted inline.

## What you need

- The Pi and its power supply (Pi 5 wants the 27W USB-C; a Pi 4 uses a 15W USB-C)
- A microSD card (16 GB+) and an SD reader on your laptop
- Your laptop, to flash and to SSH in
- An Ethernet cable, and ideally a USB-to-Ethernet adapter (a second network port: one to
  reach the Pi, one to capture on)

Note: the Pi is only the capture host. To actually tap the V-10 link you also need the
managed switch and Ethernet cables on the bench (see APPROACH.md).

## 1. Flash Raspberry Pi OS (on your laptop)

1. Install Raspberry Pi Imager (raspberrypi.com/software) and insert the SD card.
2. Choose Device -> your Pi model. Choose OS -> Raspberry Pi OS (64-bit). Choose Storage
   -> the SD card.
3. Next -> Edit Settings (this is what makes it headless):
   - Hostname: plr-pi
   - Enable SSH, password authentication, set a username and password
   - Configure wireless LAN: your home WiFi SSID and password (so it connects on first boot)
   - Set locale and timezone
4. Write, wait, eject.

## 2. First boot and SSH in

1. Put the SD in the Pi, plug in power, wait about 60 to 90 seconds.
2. From your laptop: `ssh <username>@plr-pi.local`. If .local does not resolve, get the
   IP from your router and `ssh <username>@<ip>`.

Monitor path instead: plug in HDMI and a keyboard, skip the SSH steps, use a terminal on
the Pi.

## 3. Update the system

```
sudo apt update && sudo apt full-upgrade -y
sudo reboot        # then SSH back in
```

## 4. Install the tools

```
sudo apt install -y git python3-venv python3-pip tshark tcpdump
```

When the tshark installer asks "Should non-superusers be able to capture packets?" answer
Yes, then add yourself to the capture group (log out and back in afterward):

```
sudo usermod -aG wireshark $USER
```

Then the toolkit, in a virtual environment (Pi OS Bookworm blocks system-wide pip, so a
venv is the clean way):

```
git clone https://github.com/di-omics/plr-reverse-engineer.git
cd plr-reverse-engineer
python3 -m venv ~/plr-venv
source ~/plr-venv/bin/activate
pip install -e .
```

## 5. Verify it works (still at home)

```
plr-re --help                                        # CLI is alive
python3 -m pytest -q                                  # should say 18 passed
plr-re capture lan --iface eth0 --out /tmp/test.pcap --seconds 5
ls -l /tmp/test.pcap                                  # non-zero size = capture works
```

If those three pass, the Pi is genuinely ready.

## 6. Two network roles (for on-site capture)

You want the Pi to capture on the switch mirror port while you can still reach it to type
commands. Two easy options:

- WiFi to reach it, built-in eth0 to capture on the mirror port, or
- USB-Ethernet adapter (eth1) to capture, eth0 or WiFi to reach it.

Check interfaces with `ip link` (you will see eth0, wlan0, and eth1 if the USB adapter is
plugged in). The capture NIC does not need an IP; it just listens. Point the tool at it:
`plr-re capture lan --iface eth1 ...`.

## 7. On-site

1. Bring the prepped Pi, its power supply (or a USB-C power bank), Ethernet cables, the
   USB-Ethernet adapter, and the managed switch.
2. Power the Pi, SSH in from your laptop.
3. `source ~/plr-venv/bin/activate && cd plr-reverse-engineer`
4. Follow APPROACH.md: switch inline on the Control Centre to V-10 link, mirror to the Pi
   capture NIC, run the capture workflow.

## Notes

- Every new SSH session you must re-activate the venv (`source ~/plr-venv/bin/activate`)
  or `plr-re` is not found. To make it automatic, add that line to the end of `~/.bashrc`.
- The mass-spec GPIO path (contact closure) also needs lgpio and gpiozero:
  `sudo apt install -y python3-lgpio` and recreate the venv with `--system-site-packages`,
  or `pip install gpiozero lgpio` in the venv. The V-10 does not need any of that, so skip
  it for the first bench day.
