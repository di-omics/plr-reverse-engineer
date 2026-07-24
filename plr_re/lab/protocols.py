"""Reference protocols: two real end-to-end flows across the instruments in this repo.

These are not demos. They are the actual shape of the two loops these six instruments
were reverse-engineered for -- a single-cell genomics run and a small-molecule chemistry
run -- written out step by step so the ledger can cost them against real code and report
what a lab built out of them could do today.

They are deliberately unflattering. Every step a human must do is marked manual and every
command that is undecoded costs out as blocked, so the resulting autonomy numbers are
low. That is the correct answer, and a reference protocol that produced a flattering one
by skipping the cartridge seating or the flow-cell load would be worthless as a plan.

Each begins with the preflight checks that genuinely run today. That is not padding: a
zero-decode link check before you commit a plate is exactly what the Tier 0 tooling is
good for, and it is the part of these protocols that works.
"""

from __future__ import annotations

from typing import Dict

from .model import Artifact, Protocol, Step, ZeroDecodeOp

# Artifacts. `physical` marks material that must be physically carried when its producer
# and consumer sit on different instruments; the ledger counts those hops separately.
ARTIFACTS: Dict[str, Artifact] = {
  "cell_suspension": Artifact("cell_suspension", physical=True, note="bulk population, loaded by hand"),
  "sorted_plate": Artifact("sorted_plate", physical=True, note="96-well, one cell per well"),
  "lysate_plate": Artifact("lysate_plate", physical=True, note="lysed, ready for amplification"),
  "amplified_plate": Artifact("amplified_plate", physical=True, note="post WGS preparation"),
  "enriched_plate": Artifact("enriched_plate", physical=True, note="post PCR enrichment"),
  "library_plate": Artifact("library_plate", physical=True, note="indexed, poolable"),
  "flow_cell": Artifact("flow_cell", physical=True, note="loaded by hand with reagents"),
  "run_folder": Artifact("run_folder", note="AvitiOS output folder; data, not material"),
  "run_outcome": Artifact("run_outcome", note="running/complete + outcome, read off the folder"),
  "source_plate": Artifact("source_plate", physical=True, note="compound plate"),
  "diluted_plate": Artifact("diluted_plate", physical=True),
  "dried_sample": Artifact("dried_sample", physical=True, note="solvent removed"),
  "chromatogram": Artifact("chromatogram", note=".d dataset written by MassHunter"),
}


# -- single-cell genomics ------------------------------------------------------
# Sort single cells, amplify, prep a library, sequence it, and read the outcome. This is
# the loop plr-tested has already validated the middle of: the STAR and ODTC legs are the
# only ones in this protocol that have ever moved real hardware.

SINGLE_CELL_GENOMICS = Protocol(
  name="single_cell_genomics",
  summary=(
    "Single cells to sequencing outcome: Namocell sort, STAR WGS preparation, ODTC PCR enrichment, "
    "STAR library prep, AVITI sequencing, run-folder readout."
  ),
  artifacts=tuple(ARTIFACTS.values()),
  steps=(
    Step(
      instrument="namocell",
      op=ZeroDecodeOp.DISCOVER_USB.value,
      summary="preflight: confirm the dispenser's control link enumerates",
    ),
    Step(
      instrument="namocell",
      op="manual_load",
      summary="seat the disposable cartridge and load the cell suspension",
      produces=("cell_suspension",),
      manual_reason="seating a cartridge and loading a suspension is a bench action; no code path covers it",
    ),
    Step(
      instrument="namocell",
      op="load_protocol",
      summary="select the sort mode and the gate on scatter/fluorescence",
      consumes=("cell_suspension",),
    ),
    Step(instrument="namocell", op="prime", summary="bring fluidics to sort pressure, verify stable"),
    Step(
      instrument="namocell",
      op="set_deposition",
      summary="96-well plate, one cell per well",
      params={"plate": 96, "cells_per_well": 1},
    ),
    Step(
      instrument="namocell",
      op="start_sort",
      summary="dispense single cells into the staged plate",
      produces=("sorted_plate",),
    ),
    Step(instrument="namocell", op="wait_complete", summary="poll until the plate is fully sorted"),
    Step(
      instrument="star",
      op="wgs_preparation",
      summary="lyse and add WGS preparation reaction mix (validated dry on hardware)",
      consumes=("sorted_plate",),
      produces=("lysate_plate",),
    ),
    Step(
      instrument="odtc",
      op="pcr_enrichment_round1",
      summary="30-cycle amplification (validated: 36.6 min, mean 0.27 C setpoint error)",
      consumes=("lysate_plate",),
      produces=("amplified_plate",),
    ),
    Step(
      instrument="star",
      op="pcr_enrichment_cleanup",
      summary="magnetic bead cleanup and index addition",
      consumes=("amplified_plate",),
      produces=("enriched_plate",),
    ),
    Step(
      instrument="star",
      op="library_pool",
      summary="normalize and pool the indexed library",
      consumes=("enriched_plate",),
      produces=("library_plate",),
    ),
    Step(
      instrument="element_aviti",
      op=ZeroDecodeOp.PROBE_HTTP.value,
      summary="preflight: confirm the AvitiOS control plane answers before committing reagents",
    ),
    Step(
      instrument="element_aviti",
      op="manual_load",
      summary="load the flow cell, reagents, and buffer",
      produces=("flow_cell",),
      manual_reason="loading a flow cell and reagents is a bench action; no code path covers it",
    ),
    Step(
      instrument="element_aviti",
      op="upload_manifest",
      summary="stage the RunManifest.csv for the pooled library",
      consumes=("library_plate", "flow_cell"),
    ),
    Step(
      instrument="element_aviti",
      op="set_run_parameters",
      summary="set cycles and recipe",
      params={"cycles": 150},
    ),
    Step(
      instrument="element_aviti",
      op="start_run",
      summary="commit the flow cell and begin sequencing",
      produces=("run_folder",),
    ),
    Step(
      instrument="element_aviti",
      op=ZeroDecodeOp.WATCH_RUN_FOLDER.value,
      summary="read run state and outcome off the output folder until complete",
      consumes=("run_folder",),
      produces=("run_outcome",),
      params={"run_dir": "/mnt/aviti-output/<run>"},
    ),
  ),
)


# -- small-molecule chemistry --------------------------------------------------
# Dilute, dry down, analyze. None of these three instruments has a decoded command set,
# so this protocol exists mainly to show what a chemistry loop would cost -- and to give
# `lab gaps` a second flow to rank reverse-engineering work against.

SMALL_MOLECULE_QC = Protocol(
  name="small_molecule_qc",
  summary=(
    "Compound plate to accurate mass: VIAFLO 96 serial dilution, V-10 solvent removal, "
    "6530 Q-TOF LC/MS."
  ),
  artifacts=tuple(ARTIFACTS.values()),
  steps=(
    Step(
      instrument="viaflo96",
      op=ZeroDecodeOp.DISCOVER_USB.value,
      summary="preflight: confirm the pipette's programming link enumerates",
    ),
    Step(
      instrument="viaflo96",
      op="manual_load",
      summary="load the tip box and the source plate on the deck",
      produces=("source_plate",),
      manual_reason="the VIAFLO 96 has no deck automation; labware is placed by hand",
    ),
    Step(
      instrument="viaflo96",
      op="upload_program",
      summary="transfer the serialized serial-dilution program into device memory",
      consumes=("source_plate",),
      params={"program": "serial_dilution"},
    ),
    Step(instrument="viaflo96", op="select_program", summary="set the active program"),
    Step(
      instrument="viaflo96",
      op="run_program",
      summary="execute the dilution series standalone",
      produces=("diluted_plate",),
    ),
    Step(
      instrument="biotage_v10",
      op="manual_load",
      summary="transfer aliquots into V-10 vials and load the rack",
      consumes=("diluted_plate",),
      manual_reason="no plate-to-vial transfer instrument is in this workcell",
    ),
    Step(
      instrument="biotage_v10",
      op="set_temperature",
      summary="set the evaporation setpoint (refused above the ceiling)",
      params={"celsius": 40.0},
    ),
    Step(
      instrument="biotage_v10",
      op="start_method",
      summary="run the evaporation method",
      produces=("dried_sample",),
    ),
    Step(instrument="biotage_v10", op="get_status", summary="poll until the method completes"),
    Step(
      instrument="agilent6530",
      op=ZeroDecodeOp.PROBE_TCP.value,
      summary="preflight: confirm the LC stack answers on the LAN",
    ),
    Step(
      instrument="agilent6530",
      op="manual_load",
      summary="reconstitute and place vials in the autosampler",
      consumes=("dried_sample",),
      manual_reason="no vial-handling instrument is in this workcell",
    ),
    Step(
      instrument="agilent6530",
      op="set_injection",
      summary="set injection volume and vial position",
      params={"volume_ul": 5},
    ),
    Step(
      instrument="agilent6530",
      op="start_run",
      summary="begin the LC/MS acquisition",
      produces=("chromatogram",),
    ),
  ),
)


REFERENCE_PROTOCOLS: Dict[str, Protocol] = {
  p.name: p for p in (SINGLE_CELL_GENOMICS, SMALL_MOLECULE_QC)
}


def get(name: str) -> Protocol:
  if name not in REFERENCE_PROTOCOLS:
    raise KeyError(f"unknown protocol '{name}'; known: {sorted(REFERENCE_PROTOCOLS)}")
  return REFERENCE_PROTOCOLS[name]
