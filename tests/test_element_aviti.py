"""Device-free tests for the Element AVITI additions: HTTP transport class, guarded
HTTP replay, the Tier-0 run-folder reader, and HAR decoding."""

import json

import pytest

from plr_re.decode import parse_har, summarize_har
from plr_re.guards import ActuationNotAllowed, Guards, ProtocolMapIncompleteError
from plr_re.instruments.element_aviti import ElementAviti, RunFolder
from plr_re.protocolmap import Command, ProtocolMap, Transport, seed
from plr_re.replay import GuardedHttpReplayer, fill_template
from plr_re.transports import HttpResponse, MockHttpConn


# -- seed + schema -----------------------------------------------------------


def test_seed_element_aviti_undecoded_http():
  pm = seed("element_aviti")
  assert pm.transport == Transport.HTTP
  assert pm.device == "Element AVITI"
  cov = pm.coverage()
  assert cov["decoded"] == 0
  assert cov["total"] == len(pm.commands)
  # start/abort/upload/set-params are actuation; status/metrics/consumables are not.
  assert "start_run" in pm.actuating_commands()
  assert "get_status" not in pm.actuating_commands()


def test_protocolmap_http_fields_roundtrip(tmp_path):
  pm = seed("element_aviti")
  pm.endpoint = "https://10.0.0.5"
  c = pm.commands["start_run"]
  c.decoded = True
  c.http_method = "POST"
  c.http_path = "/v1/runs/{run_id}/start"
  c.body_template = '{"confirm": true}'
  path = tmp_path / "aviti.json"
  pm.to_json(str(path))
  back = ProtocolMap.from_json(str(path))
  assert back.transport == Transport.HTTP
  assert back.endpoint == "https://10.0.0.5"
  rc = back.commands["start_run"]
  assert rc.http_method == "POST"
  assert rc.http_path == "/v1/runs/{run_id}/start"
  assert rc.body_template == '{"confirm": true}'


# -- fill_template -----------------------------------------------------------


def test_fill_template_types():
  # strings stay quoted, numbers/bools stay bare, so the filled body is valid JSON.
  body = fill_template('{"n": {n}, "name": {name}, "ok": {ok}}', {"n": 5, "name": "L1", "ok": True})
  assert json.loads(body) == {"n": 5, "name": "L1", "ok": True}
  assert fill_template(None, {"n": 1}) is None


# -- guarded HTTP replay -----------------------------------------------------


def _http_map(actuating: bool) -> ProtocolMap:
  pm = ProtocolMap(device="test", transport=Transport.HTTP, endpoint="https://127.0.0.1")
  pm.commands["go"] = Command(
    name="go",
    decoded=True,
    actuating=actuating,
    http_method="POST",
    http_path="/v1/go",
    body_template='{"n": {n}}',
  )
  return pm


def test_http_dry_run_sends_nothing():
  conn = MockHttpConn()
  r = GuardedHttpReplayer(_http_map(actuating=True), Guards(armed=False), conn=conn)
  r.setup()
  assert r.send("go", n=5) is None
  assert conn.requests == []


def test_http_armed_actuation_requires_permission():
  conn = MockHttpConn()
  r = GuardedHttpReplayer(
    _http_map(actuating=True), Guards(armed=True, allow_actuation=False), conn=conn
  )
  r.setup()
  with pytest.raises(ActuationNotAllowed):
    r.send("go", n=5)
  assert conn.requests == []


def test_http_armed_allowed_sends_filled_request():
  conn = MockHttpConn(responses=[HttpResponse(201, b'{"ok": true}')])
  r = GuardedHttpReplayer(
    _http_map(actuating=True), Guards(armed=True, allow_actuation=True), conn=conn
  )
  r.setup()
  resp = r.send("go", n=5)
  assert conn.requests == [("POST", "/v1/go", b'{"n": 5}')]
  assert resp.status == 201


def test_http_armed_run_refuses_incomplete_map():
  pm = seed("element_aviti")  # all undecoded
  r = GuardedHttpReplayer(pm, Guards(armed=True), conn=MockHttpConn())
  with pytest.raises(ProtocolMapIncompleteError):
    r.setup()


def test_element_aviti_start_gated_dry_run():
  # A full dry-run device: undecoded seed, unarmed. start_run previews, sends nothing.
  dev = ElementAviti(guards=Guards(armed=False), replayer=None)
  dev.replayer = GuardedHttpReplayer(dev.pm, dev.guards, conn=MockHttpConn())
  dev.setup()
  assert dev.start_run() is None
  assert dev.replayer._conn.requests == []


# -- Tier 0 run-folder reader ------------------------------------------------


def test_runfolder_unknown_when_empty(tmp_path):
  st = RunFolder(str(tmp_path)).state()
  assert st["state"] == "unknown"
  assert st["has_parameters"] is False


def test_runfolder_running_then_complete(tmp_path):
  (tmp_path / "RunParameters.json").write_text(json.dumps({"Cycles": 150}))
  st = RunFolder(str(tmp_path)).state()
  assert st["state"] == "running"
  assert st["has_parameters"] is True
  assert st["outcome"] is None

  # RunUploaded.json is written last and carries the outcome -> run is complete.
  (tmp_path / "RunUploaded.json").write_text(json.dumps({"outcome": "success"}))
  st = RunFolder(str(tmp_path)).state()
  assert st["state"] == "complete"
  assert st["outcome"] == "success"


def test_runfolder_tolerates_bad_json(tmp_path):
  (tmp_path / "RunParameters.json").write_text("{not valid json")
  st = RunFolder(str(tmp_path)).state()
  # unreadable parameters degrade to unknown rather than raising
  assert st["state"] == "unknown"


# -- HAR decode --------------------------------------------------------------


def _sample_har() -> dict:
  return {
    "log": {
      "entries": [
        {
          "request": {"method": "GET", "url": "https://inst.local/v1/status"},
          "response": {"status": 200},
        },
        {
          "request": {
            "method": "POST",
            "url": "https://inst.local/v1/runs",
            "postData": {"text": '{"cycles": 150}'},
          },
          "response": {"status": 201},
        },
        {
          "request": {"method": "GET", "url": "https://inst.local/v1/status"},
          "response": {"status": 200},
        },
      ]
    }
  }


def test_parse_har_counts_and_bodies():
  calls = parse_har(_sample_har())
  assert len(calls) == 3
  post = [c for c in calls if c.method == "POST"][0]
  assert post.path == "/v1/runs"
  assert post.host == "inst.local"
  assert post.is_write is True
  assert post.request_body == '{"cycles": 150}'


def test_parse_har_accepts_json_text():
  calls = parse_har(json.dumps(_sample_har()))
  assert len(calls) == 3


def test_summarize_har_writes_first_and_dedupes():
  rows = summarize_har(parse_har(_sample_har()))
  # POST /v1/runs (a write) sorts before the read; GET /v1/status collapses to count 2.
  assert rows[0]["is_write"] is True
  assert rows[0]["path"] == "/v1/runs"
  assert rows[0]["has_body"] is True
  assert rows[0]["likely_status"] is False
  status_row = [r for r in rows if r["path"] == "/v1/status"][0]
  assert status_row["is_write"] is False
  assert status_row["count"] == 2
  # PLR heuristic: a repeated read is flagged as likely status/keep-alive polling.
  assert status_row["likely_status"] is True


def test_summarize_har_single_read_not_flagged_keepalive():
  # A read seen once is a candidate meaningful read, not keep-alive noise.
  har = {
    "log": {
      "entries": [
        {"request": {"method": "GET", "url": "https://x/v1/consumables"}, "response": {}}
      ]
    }
  }
  rows = summarize_har(parse_har(har))
  assert rows[0]["likely_status"] is False
