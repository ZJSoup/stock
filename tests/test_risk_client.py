import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from common.risk_client import RiskBudgetClient


def serve(payload, port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_can_open_true():
    srv = serve({"account": {"halt": False},
                 "strategies": {"momo": {"can_open": True}}}, 8911)
    c = RiskBudgetClient("http://127.0.0.1:8911")
    ok, reason = c.can_open("momo")
    assert ok, reason
    srv.shutdown()


def test_halt_blocks():
    srv = serve({"account": {"halt": True},
                 "strategies": {"momo": {"can_open": True}}}, 8912)
    c = RiskBudgetClient("http://127.0.0.1:8912")
    ok, _ = c.can_open("momo")
    assert ok is False
    assert c.is_halted() is True
    srv.shutdown()


def test_strategy_budget_blocks():
    srv = serve({"account": {"halt": False},
                 "strategies": {"momo": {"can_open": False}}}, 8913)
    ok, reason = RiskBudgetClient("http://127.0.0.1:8913").can_open("momo")
    assert ok is False and reason
    srv.shutdown()


def test_unreachable_fail_closed():
    c = RiskBudgetClient("http://127.0.0.1:8999", timeout=1.0)
    ok, reason = c.can_open("momo")
    assert ok is False and reason
    assert c.is_halted() is True
