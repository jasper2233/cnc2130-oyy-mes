"""Virtual Pico + MES panel (brauzerda).

Pico dagi firmware kodi (firmware/*.py) o'zgartirilmasdan kompyuterda ishlaydi.
Faqat pinlar, vaqt va micro USB aloqa soxta. Brauzerdan stanok signallarini
berib, releni, jurnalni, MES tomonini va sabablar navbatini kuzatish mumkin.

Ishga tushirish:
    python tools/webpanel.py            # http://127.0.0.1:8130
    python tools/webpanel.py 8200       # boshqa port
"""

import builtins
import calendar
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time as real_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "stubs"))
sys.path.insert(0, os.path.join(ROOT, "firmware"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import simenv  # noqa: E402

_rt = simenv.install()
import config  # noqa: E402
import controller as ctl  # noqa: E402
import main as fw_main  # noqa: E402
import mesmock  # noqa: E402
import simtime  # noqa: E402
import fakemachine  # noqa: E402
import fakeusb  # noqa: E402
from eventlog import to_message, line_seq  # noqa: E402
simenv.restore_time(_rt)

DATA = tempfile.mkdtemp(prefix="cnc-panel-")
config.LOG_PATH = os.path.join(DATA, "events.jsonl")
config.STATE_PATH = os.path.join(DATA, "state.json")
config.NO_WDT_FLAG = os.path.join(DATA, "nowdt")

CLOCK = simtime.CLOCK
LOCK = threading.RLock()
CONSOLE = collections.deque(maxlen=80)

_orig_print = builtins.print


def _capture_print(*a, **kw):
    """Firmware print() larini panel konsoliga ham yig'amiz."""
    if kw.get("file") is None:
        ts = simtime.localtime()
        CONSOLE.append("%02d:%02d:%02d  %s" % (ts[3], ts[4], ts[5],
                                              " ".join(str(x) for x in a)))
    _orig_print(*a, **kw)


builtins.print = _capture_print


class MachineModel:
    """Stanokning soddalashtirilgan modeli: rele yonsa, stanokdagi vaqt relesi
    OFF_DELAY_S dan keyin tokni uzadi."""

    def __init__(self):
        self.auto = True
        self.broken = False        # rele ishlasa ham stanok o'chmaydi
        self.off_delay_s = 5
        self._relay_since = None

    def update(self, ctrl):
        if not self.auto or self.broken:
            self._relay_since = None
            return
        if ctrl.relay_active:
            if self._relay_since is None:
                self._relay_since = CLOCK.now_ms
            elif CLOCK.now_ms - self._relay_since >= self.off_delay_s * 1000:
                set_input("pwr", False)
                set_input("run", False)
                self._relay_since = None
        else:
            self._relay_since = None


PIN_OF = {"pwr": config.PIN_PWR, "run": config.PIN_RUN,
          "home": config.PIN_HOME, "maint": config.PIN_MAINT}
INPUTS = {"pwr": False, "run": False, "home": False, "maint": False}


def set_input(name, on):
    INPUTS[name] = bool(on)
    active_low = config.MAINT_ACTIVE_LOW if name == "maint" else config.INPUT_ACTIVE_LOW
    fakemachine.PINS[PIN_OF[name]] = (0 if on else 1) if active_low else (1 if on else 0)


class Sim:
    def __init__(self):
        self.speed = 10
        self.running = True
        self.model = MachineModel()
        self.mes_base = calendar.timegm(real_time.localtime())
        self.boots = 0
        self.app = None
        self.mes = None
        self.fresh(wipe=True)

    def mes_now(self):
        return self.mes_base + CLOCK.now_ms // 1000

    def fresh(self, wipe=False):
        with LOCK:
            if wipe:
                for f in os.listdir(DATA):
                    os.remove(os.path.join(DATA, f))
                CLOCK.reset()
                self.mes_base = calendar.timegm(real_time.localtime())
                fakeusb.USB.reset()
                self.mes = mesmock.MES(fakeusb.USB, config.MACHINE_ID,
                                       now_fn=self.mes_now)
                for k in INPUTS:
                    set_input(k, False)
                CONSOLE.clear()
                self.boots = 0
            else:
                # Pico toki uzilib qaytdi: soat noma'lum, kompyuter vaqti kutiladi
                CLOCK.base = simtime.PICO_BOOT_EPOCH - CLOCK.now_ms // 1000
            fakemachine.PINS.pop(config.PIN_RELAY, None)
            self.boots += 1
            print("--- Pico yoqildi (%d) ---" % self.boots)
            self.app = fw_main.App(use_wdt=True, port=fakeusb.Port())
            fakemachine.WDT_STATE["max_gap"] = 0

    def step(self):
        CLOCK.advance(config.TICK_MS)
        self.app.step()
        self.mes.poll()
        self.model.update(self.app.ctrl)

    def loop(self):
        last = real_time.monotonic()
        while self.running:
            real_time.sleep(0.02)
            now = real_time.monotonic()
            sim_ms = int((now - last) * 1000 * self.speed)
            last = now
            if self.speed == 0:
                continue            # pauza: vaqt faqat "+1 daqiqa" bilan suriladi
            with LOCK:
                steps = max(1, sim_ms // config.TICK_MS)
                for _ in range(min(steps, 20000)):
                    self.step()


SIM = Sim()


def human_sec(s):
    """TZ 12.8: "12 daqiqa" ko'rinishida."""
    s = int(s or 0)
    if s < 60:
        return "%d soniya" % s
    if s < 3600:
        return "%d daqiqa" % (s // 60)
    return "%d soat %d daqiqa" % (s // 3600, (s % 3600) // 60)


def pico_log_tail(n=40):
    rows = []
    try:
        with open(config.LOG_PATH, "rb") as f:
            lines = f.readlines()[-n:]
        for line in lines:
            if line_seq(line) is None:
                continue
            try:
                rows.append(to_message(json.loads(line)))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def state_json():
    with LOCK:
        app = SIM.app
        c = app.ctrl
        snap = c.snapshot()
        t = simtime.localtime()
        limit = None
        if c.state in (ctl.IDLE_HOME, ctl.IDLE_AWAY):
            limit = app.store.cfg("t1") if c.home.value else app.store.cfg("t2")
        mes_events = SIM.mes.event_list()[-60:]
        queue = SIM.mes.pending_reasons()
        size = os.path.getsize(config.LOG_PATH) if os.path.exists(config.LOG_PATH) else 0
        return {
            "snap": snap,
            "inputs": INPUTS,
            "relay": c.relay_active,
            "idle_human": human_sec(c.idle_sec),
            "limit": limit,
            "shutdown_blocked_by": ("maint" if c.maint_on else
                                    "config" if not app.store.cfg("auto_shutdown") else
                                    "failed" if c._shutdown_failed else None),
            "sim_time": "%04d-%02d-%02d %02d:%02d:%02d" % tuple(t[:6]),
            "speed": SIM.speed,
            "boots": SIM.boots,
            "model": {"auto": SIM.model.auto, "broken": SIM.model.broken,
                      "off_delay_s": SIM.model.off_delay_s},
            "net": {"pc": fakeusb.USB.pc_on, "stalled": fakeusb.USB.stalled,
                    "connected": app.link.connected,
                    "drop_next": SIM.mes.drop_next_event},
            "wdt": {"max_gap": fakemachine.WDT_STATE["max_gap"],
                    "timeout": config.WDT_TIMEOUT_MS},
            "log": {"seq": app.store.data["seq"], "last_ack": app.store.data["last_ack"],
                    "pending": app.log.pending_count(), "bytes": size,
                    "max_bytes": config.LOG_MAX_BYTES, "full": app.log.full,
                    "tail": pico_log_tail()},
            "mes": {"acked": SIM.mes.acked, "duplicates": SIM.mes.duplicates,
                    "time_syncs": SIM.mes.time_syncs, "states": SIM.mes.state_count,
                    "events": mes_events, "queue": queue[:8],
                    "queue_len": len(queue), "on_by_day": SIM.mes.on_seconds_by_day()},
            "reasons": mesmock.REASONS,
            "console": list(CONSOLE),
            "traffic": [(ms, d, _msg_type(line), line.decode(errors="replace")[:160])
                        for ms, d, line in fakeusb.USB.log[-25:]],
        }


def _msg_type(line):
    try:
        return str(json.loads(line).get("t", "?"))
    except (ValueError, AttributeError):
        return "matn"


def handle_post(path, body):
    with LOCK:
        app = SIM.app
        if path == "/api/input":
            for k, v in body.items():
                if k in INPUTS:
                    set_input(k, v)
        elif path == "/api/net":
            if "pc" in body:
                fakeusb.USB.set_pc(bool(body["pc"]))
            if "stalled" in body:
                fakeusb.USB.stalled = bool(body["stalled"])
            if body.get("drop_next"):
                SIM.mes.drop_next_event += int(body["drop_next"])
        elif path == "/api/speed":
            SIM.speed = max(0, min(600, int(body.get("speed", 1))))
        elif path == "/api/model":
            for k in ("auto", "broken"):
                if k in body:
                    setattr(SIM.model, k, bool(body[k]))
        elif path == "/api/cmd":
            cmd = body.pop("cmd")
            SIM.mes.command(cmd, **body)
        elif path == "/api/config":
            SIM.mes.push_config(body)
        elif path == "/api/raw":
            SIM.mes.push_raw(body["text"])
        elif path == "/api/reason":
            ok, msg = SIM.mes.close_reason(body.get("id"), body.get("code"),
                                           body.get("text"), bool(body.get("group")))
            return {"ok": ok, "msg": msg}
        elif path == "/api/reboot":
            SIM.fresh(wipe=False)
        elif path == "/api/reset":
            SIM.fresh(wipe=True)
        elif path == "/api/advance":
            # Tezkor: N soniyani darhol o'tkazish
            secs = max(0, min(7200, int(body.get("seconds", 0))))
            for _ in range(secs * 1000 // config.TICK_MS):
                SIM.step()
        else:
            return {"ok": False, "msg": "noma'lum yo'l"}
    return {"ok": True}


def run_selftest():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "simulate.py")],
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    return {"code": r.returncode, "out": r.stdout[-60000:] + r.stderr[-5000:]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "tools", "webpanel.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, state_json())
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False, "msg": "JSON xato"})
        try:
            if self.path == "/api/selftest":
                return self._send(200, run_selftest())
            self._send(200, handle_post(self.path, body))
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8130
    threading.Thread(target=SIM.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _orig_print("Virtual panel: http://127.0.0.1:%d   (to'xtatish: Ctrl+C)" % port)
    _orig_print("Ma'lumotlar papkasi:", DATA)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SIM.running = False
        shutil.rmtree(DATA, ignore_errors=True)


if __name__ == "__main__":
    main()
