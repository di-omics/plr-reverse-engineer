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
