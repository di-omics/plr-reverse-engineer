# examples

Runnable, hardware-free walk-throughs of the plr-re pipeline.

- [namocell_rehearsal.py](namocell_rehearsal.py): a dress rehearsal of the Namocell Hana
  flow (capture -> `decode diff` -> ProtocolMap -> coverage gate -> guarded replay), on
  **synthetic** frames so it runs with no instrument. It shows both safety gates firing:
  an armed run refuses a half-decoded map, and an actuating command refuses to transmit
  without the explicit opt-in.

  ```
  pip install -e .
  python examples/namocell_rehearsal.py
  ```

  Every frame in it is a clearly-labeled fake, not a recovered command set. On the bench
  the only change is to swap the synthetic frames for ones captured off the instrument;
  the rest of the pipeline is unchanged.
