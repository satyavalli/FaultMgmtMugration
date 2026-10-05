"""HTTP API, UDP syslog receiver and static file server. Standard library only.
Run: python backend/server.py   (PORT=8080, SYSLOG_PORT=5140, 0 disables syslog)"""
import json, os, re, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from engine import Engine, NODES, RULES, SCENARIOS

ENGINE = Engine()
FRONT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json"}

def ticker():
    while True:
        time.sleep(1); ENGINE.tick()

def syslog_listener(port):
    """Accepts RFC 3164-style lines containing a device name and an alarm keyword, e.g. 'P1 LINK_DOWN ge-0/0/1'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", port))
    while True:
        text = s.recvfrom(4096)[0].decode(errors="ignore")
        src = next((n for n in NODES if re.search(r"\b%s\b" % n, text, re.I)), None)
        typ = next((t for t in RULES if t in text.upper()), None)
        if src and typ: ENGINE.ingest(src, typ, "SYSLOG")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/health": return self._json(200, {"ok": True})
        if p == "/api/state": return self._json(200, ENGINE.snapshot())
        if p == "/api/catalog": return self._json(200, {"devices": NODES, "alarm_types": list(RULES), "scenarios": list(SCENARIOS)})
        f = os.path.normpath(os.path.join(FRONT, "index.html" if p == "/" else p.lstrip("/")))
        if not f.startswith(FRONT) or not os.path.isfile(f): return self._json(404, {"error": "not found"})
        b = open(f, "rb").read()
        self.send_response(200); self.send_header("Content-Type", TYPES.get(os.path.splitext(f)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if p == "/api/events":   # one event or a list: {"src":"P1","type":"LINK_DOWN","proto":"SNMP"}
                for e in (body if isinstance(body, list) else [body]): ENGINE.ingest(e["src"], e["type"], e.get("proto", "STREAM"))
            elif p.startswith("/api/scenario/"): ENGINE.run_scenario(p.rsplit("/", 1)[1])
            elif p.startswith("/api/approve/"):
                if not ENGINE.approve(int(p.rsplit("/", 1)[1])): return self._json(409, {"error": "nothing to approve"})
            elif p == "/api/auto": ENGINE.set_auto(body.get("auto"))
            elif p == "/api/reset": ENGINE.reset()
            else: return self._json(404, {"error": "not found"})
            self._json(200, {"ok": True})
        except (ValueError, KeyError) as e: self._json(400, {"error": str(e)})

def main():
    port, sp = int(os.environ.get("PORT", 8080)), int(os.environ.get("SYSLOG_PORT", 5140))
    threading.Thread(target=ticker, daemon=True).start()
    if sp: threading.Thread(target=syslog_listener, args=(sp,), daemon=True).start()
    print("Assurance console on http://localhost:%d (syslog udp/%d)" % (port, sp))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

if __name__ == "__main__": main()
