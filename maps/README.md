# maps

Decoded `ProtocolMap` JSON files land here, one per instrument, produced during the
bench session and consumed by the guarded replayer (and by the PyLabRobot backends in
di-omics/pylabrobot).

Start one from a seed and fill it in as you decode:

```
plr-re map seed biotage_v10 --out maps/biotage_v10.json
plr-re map show maps/biotage_v10.json        # what is still TODO
plr-re map coverage maps/biotage_v10.json    # exit 1 while anything is undecoded
```

A live (armed) run refuses to start while any required command is still undecoded, so a
half-mapped protocol cannot drive hardware.

Byte-frame instruments (serial/TCP) store a hex `frame_template` per command; HTTP/JSON
instruments (the Element AVITI, `Transport.HTTP`) store `http_method`, `http_path`, and a
JSON `body_template` instead. One schema covers both, so the same coverage gate and
guarded replay apply:

```
plr-re map seed element_aviti --out maps/element_aviti.json
plr-re decode har aviti.har                  # read the API calls out of a capture
plr-re map coverage maps/element_aviti.json  # exit 1 while anything is undecoded
```
