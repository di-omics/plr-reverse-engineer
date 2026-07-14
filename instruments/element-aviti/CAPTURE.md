# AVITI capture: getting the control-plane traffic

The AVITI control plane is HTTP/JSON, so capture means recording the requests the
touchscreen UI (or the Elembio Cloud connector) makes to the AvitiOS microservices, then
reading them out of a HAR. This is the AVITI analog of identifying Ready/Start/Stop on a
contact-closure connector: it is how you get the raw material the decoder works on.

Capture is passive. It observes traffic; it sends nothing to the instrument. Marking one
action at a time is what turns a wall of requests into "this request is start_run."

## Route A: browser devtools HAR export (preferred when the UI is a web app)

If the AvitiOS UI is reachable as a web app in a browser (from the instrument or a
laptop on its network), this needs no extra tooling:

1. Open the UI in Chrome/Edge, open DevTools, and select the Network tab.
2. Enable "Preserve log" so navigations do not clear it.
3. Perform one discrete action (open run setup, upload a manifest, set cycles, start,
   abort, refresh status). Note what you did and when.
4. Right-click the Network list and "Save all as HAR with content."
5. `plr-re decode har cap.har` lists the calls, writes first.

For action marking alongside it, run `plr-re mark --out cap.har.marks.jsonl` in a shell
and type a label right after each UI action, so you can line up timestamps later.

## Route B: mitmproxy on the network (intercept TLS)

When the UI is not a browser you can open DevTools in, or you want the instrument-to-Cloud
traffic, put mitmproxy between the client and the service. This requires trusting
mitmproxy's CA on the client, which you can only do on a device you administer:

```
plr-re capture http --out cap.har --port 8080     # launches mitmdump, writes a HAR
```

Point the client at the proxy (`http(s)_proxy` or the OS proxy settings) and install the
mitmproxy CA so it can see inside TLS. Then perform and mark one action at a time as
above. `plr-re capture http` requires `pip install mitmproxy`; if it is not present the
command tells you to fall back to Route A.

## Route C: on-wire sniff with a TLS keylog (advanced)

If neither UI-devtools nor a trusted proxy is possible, mirror the instrument's switch
port and capture with Wireshark, and only decrypt if the client can be told to write an
`SSLKEYLOGFILE`. Without the keys, a TLS capture shows only endpoints and sizes, not
bodies. This is the last resort; Route A or B is almost always easier.

## What to capture (one small action per mark)

Following the PyLabRobot method: keep each OEM action small and discrete so the decoder
can isolate the one request it produced. Capture each of these once, cleanly:

- open the run-setup screen (learn the read models)
- upload a `RunManifest.csv`
- set cycles / recipe, then **change it once** (e.g. 150 then 300): capturing the same
  action with one parameter varied is how you locate that field with `decode har` and a
  body diff
- start a run
- abort a run
- poll status and live run metrics (the read-only telemetry)

Expect a stream of identical background requests the whole time. Per the PLR guide, a
frequently repeated command is almost always a status/keep-alive poll, not an action;
`plr-re decode har` flags those `~status/keep-alive?` and sorts them last so they do not
distract from the write you marked.

## Auth

AvitiOS and Elembio Cloud use OAuth2/OIDC. The recovered API will need whatever the UI
uses: a session cookie or a bearer token. Capture the auth handshake too, and record the
token mechanism as the `connect` command so the replayer can authenticate. Do not commit
real tokens to the map; pass them at run time via `--token` or the config file.

## Privacy and good citizenship

Capture only your own instrument's traffic, and treat any captured tokens or sample
identifiers as secrets: keep HARs out of the repo (`.gitignore` covers `*.har`), and
strip anything sensitive before sharing a capture.
