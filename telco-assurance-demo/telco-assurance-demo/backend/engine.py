"""Fault and alarm engine. Stages follow the HPE AA Fault Management layers:
Collection (adapters in server.py, IP mediation = validation in ingest(), filtering and enrichment = dedup, noise and impact,
persistence = in-memory store) then Management (fault and problem lifecycle = correlation, root cause, remediation).
Functions: dedup, topology correlation, root cause ranking,
anomaly detection, fault prediction and closed-loop remediation. Standard library only."""
import math, random, threading
from collections import deque

EDGES = [("PE1","P1"),("P1","DWDM1"),("DWDM1","P2"),("P2","PE2"),("P2","PE3"),("P1","RR1"),("P2","RR1"),
         ("AGG1","PE1"),("OLT1","AGG1"),("CSR1","PE2"),("GNB1","CSR1"),("UPF1","P2")]
SERVICES = {
    "VPN-ACME": {"path": ["PE1","P1","DWDM1","P2","PE2"], "c": 1200},
    "VPN-BANK": {"path": ["PE1","P1","DWDM1","P2","PE3"], "c": 3400},
    "VPN-MEDIA": {"path": ["PE3","P2","PE2"], "c": 800},
    "5G-SLICE-URLLC": {"path": ["GNB1","CSR1","PE2","P2","UPF1"], "c": 18500},
    "FTTH-AUSTIN": {"path": ["OLT1","AGG1","PE1","P1","DWDM1","P2"], "c": 9200}}
NODES = sorted({n for e in EDGES for n in e})
ADJ = {}
for a, b in EDGES:
    ADJ.setdefault(a, []).append(b); ADJ.setdefault(b, []).append(a)

# alarm type: (causal rank, severity, text, is_noise). Higher rank = closer to root cause.
RULES = {
 "LOS": (10,"Critical","Loss of signal on optical port",0), "POWER_LOSS": (9,"Critical","Site power loss, both rectifiers down",0),
 "PON_LOS": (9,"Critical","PON port loss of signal",0), "LINK_DOWN": (8,"Critical","Interface link down",0),
 "CPU_HIGH": (7,"Major","Control-plane CPU above 90%",0), "OPTICAL_RX_LOW": (6,"Minor","Optical Rx power below threshold",0),
 "LDP_DOWN": (5,"Major","LDP session lost",0), "OSPF_FLAP": (4,"Minor","OSPF neighbor flapping",0),
 "BGP_DOWN": (4,"Major","BGP neighbor down",0), "BFD_DOWN": (3,"Major","BFD session down",0),
 "SERVICE_DEGRADED": (1,"Critical","Customer service degraded",0), "CELL_OUTAGE": (1,"Critical","gNodeB cell outage",0),
 "ONT_OFFLINE": (1,"Major","ONTs offline on PON port",0), "NTP_DRIFT": (0,"Info","NTP offset warning",1),
 "AUTH_FAIL": (0,"Info","SSH login failure",1), "INTF_FLAP_MINOR": (0,"Minor","Access port flap",1)}
NOISE = [t for t, r in RULES.items() if r[3]]
PLAYBOOKS = {
 "LOS": ("low", ["Verify adjacency loss over NETCONF","Trigger MPLS-TE fast reroute to backup LSP","Validate VPN reachability probes","Open fiber dispatch ticket in ITSM"]),
 "CPU_HIGH": ("medium", ["Snapshot process table","Apply control-plane policing profile","Confirm CPU back under 60%"]),
 "OPTICAL_RX_LOW": ("low", ["Shift LSPs off the degrading link","Schedule connector cleaning","Verify Rx power after shift"]),
 "POWER_LOSS": ("medium", ["Confirm power alarm and battery runtime","Reroute backhaul through microwave backup","Dispatch field crew with spare rectifier","Verify cell back on air"]),
 "PON_LOS": ("low", ["Run OTDR test on the PON port","Locate the feeder fault from the trace","Open fiber dispatch ticket in ITSM","Verify ONTs recover"]),
 "_": ("low", ["Collect device diagnostics","Create ticket with enrichment attached"])}
SCENARIOS = {
 "fiber-cut": [("DWDM1","LOS","SNMP",0),("P1","LINK_DOWN","SNMP",1),("P2","LINK_DOWN","SNMP",1),("RR1","BGP_DOWN","STREAM",2),
   ("P1","LDP_DOWN","SYSLOG",2),("P2","LDP_DOWN","SYSLOG",2),("PE1","BGP_DOWN","STREAM",3),("PE2","BGP_DOWN","STREAM",3),
   ("PE3","BGP_DOWN","STREAM",4),("PE1","SERVICE_DEGRADED","STREAM",4),("DWDM1","LOS","SNMP",5),("P1","LINK_DOWN","SNMP",6)],
 "cell-site": [("CSR1","POWER_LOSS","SNMP",0),("CSR1","BFD_DOWN","SYSLOG",1),("PE2","BFD_DOWN","SYSLOG",1),
   ("GNB1","CELL_OUTAGE","STREAM",2),("GNB1","SERVICE_DEGRADED","STREAM",3),("GNB1","CELL_OUTAGE","STREAM",4)],
 "pon": [("OLT1","PON_LOS","SNMP",0),("OLT1","ONT_OFFLINE","STREAM",1),("AGG1","BFD_DOWN","SYSLOG",2),
   ("OLT1","SERVICE_DEGRADED","STREAM",3),("OLT1","ONT_OFFLINE","STREAM",4)],
 "cpu": [("P2","CPU_HIGH","SNMP",0),("P2","OSPF_FLAP","SYSLOG",1),("PE2","OSPF_FLAP","SYSLOG",2),("PE3","OSPF_FLAP","SYSLOG",2),("P2","OSPF_FLAP","SYSLOG",3)],
 "optical": [("DWDM1","OPTICAL_RX_LOW","SNMP",k * 1.5) for k in range(10)],
 "storm": [(NODES[k % len(NODES)], NOISE[k % 3], "SYSLOG", k / 6) for k in range(60)]}


class Engine:
    def __init__(self, auto=True, seed=None):
        self.lock = threading.RLock(); self.rng = random.Random(seed); self.auto = auto; self.reset()

    def reset(self):
        with self.lock:
            self.now = 0; self.alarms = []; self.incs = []; self.raw = 0; self.q = []; self.hist = deque(maxlen=40)
            self.cur = 0; self.m = 2.0; self.v = 1.0; self.anom = []; self.risk = {}; self.ids = 0; self.iid = 100

    def dist(self, a, b):
        if a == b: return 0
        seen, frontier, d = {a}, [a], 0
        while frontier:
            d += 1; nxt = []
            for n in frontier:
                for m in ADJ.get(n, []):
                    if m == b: return d
                    if m not in seen: seen.add(m); nxt.append(m)
            frontier = nxt
        return 99

    # ---- ingest, dedup, correlation ----
    def ingest(self, src, type_, proto="STREAM"):
        if src not in ADJ: raise ValueError("unknown device " + str(src))
        if type_ not in RULES: raise ValueError("unknown alarm type " + str(type_))
        with self.lock: self._ingest(src, type_, proto)

    def _ingest(self, src, type_, proto):
        self.raw += 1; self.cur += 1; k = src + type_
        ex = next((a for a in self.alarms if a["k"] == k and a["st"] != "Cleared"), None)
        if ex:  # deduplication: same device and alarm type increments the tally
            ex["n"] += 1; ex["last"] = self.now
            if type_ == "OPTICAL_RX_LOW": self._predict(ex)
            return
        r = RULES[type_]; self.ids += 1
        a = {"id": self.ids, "k": k, "src": src, "type": type_, "sev": r[1], "text": r[2], "proto": proto,
             "t": self.now, "last": self.now, "n": 1, "noise": bool(r[3]), "inc": None, "st": "Open"}
        self.alarms.insert(0, a); del self.alarms[1500:]; self._correlate(a)

    def _open(self, i): return i["st"] != "Resolved" and self.now - i["t0"] < 45

    def _correlate(self, a):
        if a["noise"]:
            i = next((i for i in self.incs if self._open(i) and any(self.dist(x["src"], a["src"]) <= 1 for x in i["al"])), None)
            if i: self._attach(i, a)
            return
        i = next((i for i in self.incs if self._open(i) and any(not x["noise"] and self.dist(x["src"], a["src"]) <= 2 for x in i["al"])), None)
        if not i:
            self.iid += 1
            i = {"id": self.iid, "tkt": "TKT-%d" % (5000 + self.iid), "t0": self.now, "tFault": a["t"], "al": [], "st": "Open", "rem": None, "mttd": 0}
            self.incs.insert(0, i)
        self._attach(i, a)
        if a["type"] == "OPTICAL_RX_LOW": self._predict(a)

    def _attach(self, i, a):
        a["inc"] = i["id"]; i["al"].append(a)
        nn = sorted([x for x in i["al"] if not x["noise"]], key=lambda x: -RULES[x["type"]][0])
        i["root"] = nn[0] if nn else i["al"][0]
        i["sev"] = next(s for s in ("Critical", "Major", "Minor", "Info") if any(x["sev"] == s for x in i["al"]))
        i["imp"] = [s for s, d in SERVICES.items() if i["root"]["src"] in d["path"]]
        i["cust"] = sum(SERVICES[s]["c"] for s in i["imp"])
        gap = RULES[nn[0]["type"]][0] - RULES[nn[1]["type"]][0] if len(nn) > 1 else 0
        i["conf"] = min(.98, .55 + .06 * len(nn) + .04 * gap)   # more evidence and a clearer causal gap raise confidence
        if i["root"]["type"] != "SERVICE_DEGRADED":
            if i["rem"] and i["rem"]["st"] == "Proposed" and i["rem"]["rt"] != i["root"]["type"]: i["rem"] = None
            if not i["rem"]:
                risk, steps = PLAYBOOKS.get(i["root"]["type"], PLAYBOOKS["_"])
                i["rem"] = {"risk": risk, "steps": list(steps), "k": 0, "st": "Proposed", "rt": i["root"]["type"]}
            self._try_auto(i)

    def _try_auto(self, i):
        r = i["rem"]
        if self.auto and r and r["st"] == "Proposed" and r["risk"] == "low" and i["root"]["type"] != "OPTICAL_RX_LOW" and i["conf"] >= .8:
            self._start(i, True)

    def _predict(self, a):
        p = 1 / (1 + math.exp(-.5 * (a["n"] - 6)))   # logistic risk on repeated optical alarms
        self.risk[a["src"]] = {"p": p, "n": a["n"]}
        i = next((i for i in self.incs if i["id"] == a["inc"]), None)
        if i:
            i["pred"] = p
            if p > .7 and i["rem"] and i["rem"]["st"] == "Proposed" and self.auto: self._start(i, True)

    def _start(self, i, auto):
        if i["rem"]["st"] != "Proposed": return False
        i["rem"].update(st="Running", auto=auto, next=self.now + 3); i["st"] = "Remediating"; return True

    # ---- public controls ----
    def approve(self, inc_id):
        with self.lock:
            i = next((i for i in self.incs if i["id"] == inc_id), None)
            return bool(i and i["rem"] and self._start(i, False))

    def set_auto(self, v):
        with self.lock: self.auto = bool(v)

    def run_scenario(self, name):
        if name not in SCENARIOS: raise ValueError("unknown scenario " + name)
        with self.lock: self.q += [(self.now + d, (s, t, p)) for s, t, p, d in SCENARIOS[name]]

    # ---- clock ----
    def tick(self):
        with self.lock:
            self.now += 1
            if self.rng.random() < .35: self._ingest(self.rng.choice(NODES), self.rng.choice(NOISE), "SYSLOG")
            due = [x for x in self.q if x[0] <= self.now]; self.q = [x for x in self.q if x[0] > self.now]
            for _, args in due: self._ingest(*args)
            for i in self.incs:
                r = i["rem"]
                if r and r["st"] == "Running" and self.now >= r["next"]:
                    r["k"] += 1; r["next"] = self.now + 3
                    if r["k"] >= len(r["steps"]):
                        r["st"] = "Done"; i["st"] = "Resolved"; i["t1"] = self.now
                        for a in i["al"]: a["st"] = "Cleared"
            z = (self.cur - self.m) / math.sqrt(self.v + 1)   # EWMA baseline z-score on events per second
            self.hist.append({"c": self.cur, "z": z})
            if z > 3 and self.cur > 5: self.anom.insert(0, {"t": self.now, "c": self.cur, "z": z})
            self.v = .9 * self.v + .1 * (self.cur - self.m) ** 2; self.m = .9 * self.m + .1 * self.cur; self.cur = 0

    def snapshot(self):
        with self.lock:
            incs = []
            for i in self.incs:
                c = {k: v for k, v in i.items() if k not in ("al", "root")}
                c["al_ids"] = [x["id"] for x in i["al"]]; c["root_id"] = i["root"]["id"]
                c["rem"] = dict(i["rem"]) if i["rem"] else None; incs.append(c)
            return {"now": self.now, "raw": self.raw, "alarms": [dict(a) for a in self.alarms], "incs": incs,
                    "hist": list(self.hist), "anom": self.anom[:20], "m": self.m, "risk": self.risk, "auto": self.auto}
