# Telco AI Fault and Alarm Management: Netcool to Automated Assurance demo

A runnable prototype that shows alarm deduplication, topology correlation, root cause analysis, anomaly detection,
fault prediction and closed-loop remediation on a synthetic multi-domain network (IP/MPLS core, DWDM, 5G RAN, FTTH).

## Run it
- Local: `python backend/server.py` then open http://localhost:8080 (Python 3.9+, no packages needed).
- Docker: `docker compose up --build`, then open http://localhost:8080.
- Frontend only: open `frontend/index.html` in a browser. It falls back to an in-browser copy of the same engine.
- Tests: `python -m unittest discover -s tests`

Click a scenario button (fiber cut, cell site power loss, FTTH feeder cut, CPU spike, optical degradation, noise storm).
Medium-risk fixes wait for approval; low-risk fixes run automatically when "Auto-remediate" is on.

## Architecture
```
SNMP traps / syslog / event streams
  --> Collection: Adapters            POST /api/events, UDP syslog :5140          (server.py)
  --> Collection: IP Mediation        validate device and alarm type, normalize   (engine.ingest)
  --> Collection: Filtering and Enrichment  dedup, noise suppression, service impact (engine.py)
  --> Collection: Persistence         in-memory store in this demo
  --> Management: Fault and Problem Lifecycle  correlate, root cause, playbook, approval, resolve
  --> GET /api/state, polled every second by the web console (frontend/index.html)
```
| Endpoint | Purpose |
|---|---|
| POST /api/events | Ingest one event or a list: `{"src":"P1","type":"LINK_DOWN","proto":"SNMP"}` |
| POST /api/scenario/{fiber-cut,cell-site,pon,cpu,optical,storm} | Inject a synthetic fault |
| POST /api/approve/{incident id} | Approve a proposed remediation |
| POST /api/auto, /api/reset | Toggle auto-remediation, reset state |
| GET /api/state, /api/catalog, /api/health | State, valid devices and alarm types, liveness |

Send a syslog line: `python tools/send_syslog.py P1 LINK_DOWN`.

## AI logic
- **Deduplication:** device plus alarm type is the key; repeats raise the Tally counter (same idea as a Netcool Identifier).
- **Correlation:** an alarm joins an open incident if it is within two hops of a member alarm and arrives within 45 s.
  Noise alarms only join within one hop and never open an incident.
- **Root cause:** highest causal rank among the incident's alarms. Confidence rises with evidence count and the rank gap to the runner-up.
- **Impact:** services whose path crosses the root device, with customer counts.
- **Anomaly detection:** EWMA baseline and z-score on events per second, flagged above 3.
- **Prediction:** logistic risk score on repeated optical alarms; above 0.7 triggers a proactive reroute.
- **Remediation:** playbook per root cause. Low risk and confidence of 0.8 or more runs automatically; medium risk needs approval.

## Metrics tracked
Raw events, unique alarms after dedup, incidents, noise reduction, anomalies flagged, MTTD, MTTR, and affected customers.
MTTD and MTTR baselines in the UI (4 min and 45 min) are assumed placeholders. Replace them with figures from your Netcool history.

## Netcool to HPE Telco Automated Assurance
Netcool/OMNIbus, per IBM's 8.1.0 overview, is built from probes, ObjectServers (in-memory event store), gateways,
desktop and web event lists, administration tools, and a MIB manager. The HPE datasheet describes two principal components,
Fault Management and Performance Management, sized and configured independently. Fault Management has a Collection layer
(Adapters, IP Mediation, Filtering and Enrichment, Persistence) and a Management layer (Fault and Problem Lifecycle Management).
Component names are confirmed; what each does is inferred from its name, so verify behaviour and interfaces with HPE.
This demo covers Fault Management only. Performance Management is not modeled.

| Netcool | HPE AA component | In this demo |
|---|---|---|
| Probes | Collection: Adapters | HTTP ingest, UDP syslog, protocol tags |
| Probe rules files, MIB Manager | Collection: IP Mediation | `RULES` catalog and validation |
| Suppression and lookup automations, Impact | Collection: Filtering and Enrichment | Noise suppression, service impact, device context |
| ObjectServer event store | Collection: Persistence | In-memory store |
| ObjectServer dedup, event lists, automations | Management: Fault and Problem Lifecycle Management | Dedup, incidents, root cause, approval-gated remediation |
| Gateways | Not in the confirmed list | ITSM ticket id per incident |

Suggested path: size Fault Management first, run in parallel with Netcool, port mediation and filtering rules,
compare incidents with operator workflow, enable approval-based remediation, then cut over by region.

## Limits of this prototype
State is in memory and resets on restart. Topology, devices and services are hard-coded demo data. The "AI" is
transparent heuristics and statistics, not trained models. Remediation steps are simulated; nothing touches real devices.
SNMP traps are not decoded natively: forward them through snmptrapd or a collector to `/api/events`. There is no authentication.
To productionize: persistent store, discovered topology, trained models validated on replayed Netcool history, authenticated
device adapters, and an audit trail for every automated action.
