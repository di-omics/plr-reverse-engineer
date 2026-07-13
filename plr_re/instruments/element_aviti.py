"""Element AVITI: run-state telemetry and guarded control of the AvitiOS API.

AvitiOS is a sandboxed microservice OS; the touchscreen UI and Elembio Cloud are both
clients of an HTTP/JSON control plane that has no published local API. This module has
two paths, from safest to hardest:

  * RunFolder (Tier 0, zero decode, works today): the instrument writes a run folder
    with RunParameters.json at the start and RunUploaded.json last (it carries an
    `outcome` field and triggers downstream analysis). Reading those two files gives a
    run orchestrator honest state (running / complete / outcome) and the data location
    with no protocol decoding and no risk. It only reads files off a shared folder.

  * ElementAviti (guarded API): start_run / abort_run / status over the recovered
    AvitiOS HTTP API, behind the same arming switches as every other backend. Dry-run
    until the ProtocolMap is decoded and the caller arms it with a human present.

probe_services() is a read-only network sweep to find the control plane's ports.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import List, Optional

from ..guards import Guards
from ..protocolmap import ProtocolMap, seed
from ..replay import GuardedHttpReplayer

logger = logging.getLogger("plr_re")


# -- Tier 0: run-folder state (no decoding, read-only file access) ------------


class RunFolder:
  """Read AVITI run state off the output folder. Pure file reads, no network.

  State machine, grounded in what AvitiOS writes:
    * RunParameters.json present, RunUploaded.json absent  -> running
    * RunUploaded.json present                             -> complete (+ outcome)
    * neither                                              -> unknown
  """

  def __init__(self, path: str):
    self.path = path

  def _read_json(self, name: str) -> Optional[dict]:
    p = os.path.join(self.path, name)
    if not os.path.exists(p):
      return None
    try:
      with open(p, encoding="utf-8") as fh:
        return json.load(fh)
    except (OSError, ValueError) as e:
      logger.warning("could not read %s: %s", p, e)
      return None

  def parameters(self) -> Optional[dict]:
    """RunParameters.json: the recipe and parameters the run was started with."""
    return self._read_json("RunParameters.json")

  def uploaded(self) -> Optional[dict]:
    """RunUploaded.json: written last on completion; carries the run `outcome`."""
    return self._read_json("RunUploaded.json")

  def state(self) -> dict:
    params = self.parameters()
    uploaded = self.uploaded()
    if uploaded is not None:
      state = "complete"
    elif params is not None:
      state = "running"
    else:
      state = "unknown"
    outcome = None
    if isinstance(uploaded, dict):
      # `outcome` is the documented completion field; fall back gracefully if renamed.
      outcome = uploaded.get("outcome") or uploaded.get("Outcome")
    return {
      "run_dir": self.path,
      "state": state,
      "outcome": outcome,
      "has_parameters": params is not None,
      "has_uploaded": uploaded is not None,
    }


# -- read-only network probe --------------------------------------------------

# Candidate ports the AvitiOS control plane / UI may listen on. The bench sweep
# confirms which are real; nothing here is assumed to be correct.
DEFAULT_PROBE_PORTS = [443, 80, 8080, 8443, 3000, 5000, 8000]


def probe_services(
  ip: str, ports: Optional[List[int]] = None, timeout: float = 1.5
) -> List[dict]:
  """Read-only sweep: which candidate ports accept a connection, and does an HTTP GET
  on '/' return anything. Purely passive; opens a socket and issues a single GET, never
  a state-changing request. Use it to find the control-plane endpoint before capture."""
  results: List[dict] = []
  for port in ports or DEFAULT_PROBE_PORTS:
    row = {"ip": ip, "port": port, "open": False}
    try:
      with socket.create_connection((ip, port), timeout=timeout):
        row["open"] = True
    except OSError as e:
      row["error"] = str(e)
      results.append(row)
      continue
    scheme = "https" if port in (443, 8443) else "http"
    try:
      info = _http_get_head(f"{scheme}://{ip}:{port}/", timeout=timeout)
      row.update(info)
    except OSError as e:
      row["http_error"] = str(e)
    results.append(row)
  return results


def _http_get_head(url: str, timeout: float) -> dict:
  """Issue one read-only GET and return status plus a short body snippet."""
  import ssl
  import urllib.error
  import urllib.request

  ctx = None
  if url.startswith("https"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # instrument certs are commonly self-signed
  req = urllib.request.Request(url, method="GET")
  try:
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
      snippet = resp.read(200)
      return {"http_status": resp.status, "snippet": snippet.decode("latin-1", "replace")}
  except urllib.error.HTTPError as e:
    return {"http_status": e.code}


# -- guarded API control ------------------------------------------------------


class ElementAviti:
  def __init__(
    self,
    pm: Optional[ProtocolMap] = None,
    guards: Optional[Guards] = None,
    replayer: Optional[GuardedHttpReplayer] = None,
    verify_tls: bool = True,
    token: Optional[str] = None,
  ):
    self.pm = pm or seed("element_aviti")
    self.guards = guards or Guards()
    self.replayer = replayer or GuardedHttpReplayer(
      self.pm, self.guards, verify_tls=verify_tls, token=token
    )

  def setup(self) -> None:
    self.replayer.setup()

  def stop(self) -> None:
    self.replayer.stop()

  # -- read-only -------------------------------------------------------------

  def connect(self):
    return self.replayer.send("connect")

  def get_status(self):
    return self.replayer.send("get_status")

  def get_run_metrics(self):
    return self.replayer.send("get_run_metrics")

  def list_consumables(self):
    return self.replayer.send("list_consumables")

  # -- actuation (gated by the replayer) -------------------------------------

  def upload_manifest(self, manifest_path: str):
    """Stage a RunManifest.csv for the next run. Gated: staging commits the instrument
    to a run definition, so it needs the actuation opt-in like the other writes."""
    return self.replayer.send("upload_manifest", manifest=manifest_path)

  def set_run_parameters(self, cycles: int, **kw):
    return self.replayer.send("set_run_parameters", cycles=int(cycles), **kw)

  def start_run(self):
    return self.replayer.send("start_run")

  def abort_run(self):
    return self.replayer.send("abort_run")
