"""CNC 2130 - MES/ERP namoyish dasturi (kompyuterda ishlaydi).

Pico ni USB dan o'zi topadi, brauzerda oyna ochadi va haqiqiy MES tizimi
kabi ishlaydi: vaqt yuboradi, holatni ko'rsatadi, hodisalarni saqlaydi va
tasdiqlaydi, sabab so'raydi, sozlamalarni yuboradi, kunlik hisobot chiqaradi.

Ishga tushirish:
    python tools/mes_demo.py            # Pico ni o'zi topadi
    python tools/mes_demo.py COM5 8140  # port va veb-port qo'lda

.exe yig'ish:
    pip install pyinstaller
    pyinstaller --onefile --name CNC2130-MES tools/mes_demo.py

Ma'lumotlar dastur yonidagi mes_malumot.json fayliga saqlanadi.
"""

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial
from serial.tools import list_ports

PICO_VID = 0x2E8A
TIME_PERIOD_S = 10
LINK_TIMEOUT_S = 30

REASONS = {
    "TOOL_BREAK": "Asbob singan",
    "NO_MATERIAL": "Material tugagan",
    "POWER_OUT": "Elektr uzilgan",
    "PROGRAM_ERR": "Dastur xatosi",
    "BREAKDOWN": "Stanok buzilgan",
    "REPAIR": "Ta'mirlash",
    "LUNCH": "Tushlik",
    "HANDOVER": "Smena topshirish",
    "SETUP": "Dastur tayyorlash",
    "OTHER": "Boshqa",
}

TYPE_UZ = {
    "POWER_ON": "Stanok yoqiq turdi",
    "POWER_CYCLE": "Tok o'chib-yondi",
    "UNPLANNED_STOP": "Rejasiz to'xtash",
    "SPINDLE_IDLE": "Bo'sh turish",
    "AUTO_SHUTDOWN": "Avtomatik o'chirildi",
    "SHUTDOWN_FAILED": "O'chmadi",
    "PLANNED_STOP": "Rejali to'xtash",
    "LOG_FULL": "Jurnal to'ldi",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def data_path():
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    return os.path.join(base, "mes_malumot.json")


class Mes:
    """MES serveri o'rnida: hodisalarni saqlaydi va tasdiqlaydi."""

    def __init__(self):
        self.lock = threading.RLock()
        self.events = {}          # id -> hodisa
        self.state = None
        self.hello = None
        self.acked = None
        self.received = set()
        self.traffic = []         # (vaqt, yo'nalish, matn)
        self.link_ok = False
        self.port_name = None
        self.last_rx = 0
        self.outbox = []          # Pico ga yuboriladigan xabarlar
        self.errors = []
        self.load()

    # ---------- saqlash ----------

    def load(self):
        # Panel sozlamalari (Pico ga yuborilmaydi, shu kompyuterda saqlanadi)
        self.ui = {"t_warn": 120, "ovoz": True, "ovoz_kuch": 100}
        try:
            with open(data_path(), encoding="utf-8") as f:
                saved = json.load(f)
            self.events = saved.get("events", {})
            self.ui.update(saved.get("ui", {}))
        except (OSError, ValueError):
            self.events = {}

    def save(self):
        try:
            with open(data_path(), "w", encoding="utf-8") as f:
                json.dump({"events": self.events, "ui": self.ui}, f, ensure_ascii=False)
        except OSError as e:
            self.log_error("saqlanmadi: %s" % e)

    def set_ui(self, body):
        with self.lock:
            warn = body.get("t_warn")
            if isinstance(warn, (int, float)) and 0 <= warn <= 3600:
                self.ui["t_warn"] = int(warn)
            if "ovoz" in body:
                self.ui["ovoz"] = bool(body["ovoz"])
            kuch = body.get("ovoz_kuch")
            if isinstance(kuch, (int, float)):
                # 50% dan past tushirib bo'lmaydi - operator eshitmay qolmasin
                self.ui["ovoz_kuch"] = max(50, min(100, int(kuch)))
            self.save()
            return dict(self.ui)

    def log_error(self, msg):
        self.errors.append("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        del self.errors[:-20]

    # ---------- trafik ----------

    def trace(self, direction, text):
        with self.lock:
            self.traffic.append((time.strftime("%H:%M:%S"), direction, text[:400]))
            del self.traffic[:-200]

    def send(self, obj):
        with self.lock:
            self.outbox.append(obj)

    # ---------- Pico xabarlari ----------

    def on_message(self, msg):
        with self.lock:
            self.last_rx = time.time()
            self.link_ok = True
            kind = msg.get("t")
            if kind == "state":
                self.state = msg
                if self.acked is None or msg.get("last_ack", 0) > (self.acked or 0):
                    self.acked = msg.get("last_ack", 0)
            elif kind == "hello":
                self.hello = msg
                self.acked = msg.get("last_ack", 0)
                if msg.get("need_time"):
                    self.send(time_msg())
            elif kind == "event":
                self.on_event(msg)
            elif kind == "log":
                self.log_error("Pico: %s" % msg.get("msg"))

    def on_event(self, ev):
        ev = dict(ev)
        ev.pop("t", None)
        eid = ev.get("id")
        old = self.events.get(eid)
        if old is None:
            ev["_qabul"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            ev["_sabab"] = None
            ev["_sabab_matn"] = None
            self.events[eid] = ev
        elif not old.get("start") and ev.get("start"):
            # Avval vaqtsiz kelgan edi, endi Pico aniq vaqtini tikladi
            for k in ("start", "end", "time_uncertain", "time_restored"):
                old[k] = ev.get(k)
        self.save()
        seq = ev.get("seq")
        if isinstance(seq, int):
            self.received.add(seq)
        n = self.acked if self.acked is not None else min(self.received) - 1
        while n + 1 in self.received:
            n += 1
        self.acked = n
        self.send({"t": "ack", "seq": n})

    # ---------- hisobotlar ----------

    def event_list(self):
        return sorted(self.events.values(), key=lambda e: e.get("seq") or 0)

    def queue(self):
        return [e for e in self.event_list()
                if e.get("reason_required") and not e.get("_sabab")]

    def close_reason(self, eid, code, text):
        with self.lock:
            q = self.queue()
            if not q:
                return False, "Navbat bo'sh"
            if q[0]["id"] != eid:
                return False, "Avval eng eski hodisaga sabab kiriting"
            if code not in REASONS:
                return False, "Noma'lum sabab"
            if code == "OTHER" and not (text or "").strip():
                return False, "Boshqa tanlanganda matn majburiy"
            q[0]["_sabab"] = code
            q[0]["_sabab_matn"] = text
            self.save()
            return True, "Sabab saqlandi"

    def warn_left(self):
        """Ogohlantirish kerak bo'lsa - o'chishgacha qolgan soniya, aks holda None."""
        with self.lock:
            st = self.state
            warn = self.ui.get("t_warn", 0)
            if (not st or not self.ui.get("ovoz") or not warn or not self.link_ok
                    or st.get("blocked_by") or not st.get("auto_shutdown")
                    or st.get("state") not in ("IDLE_HOME", "IDLE_AWAY")):
                return None
            lim = st.get("t1") if st.get("home") else st.get("t2")
            idle = (st.get("idle_sec") or 0) + (time.time() - self.last_rx)
            left = int(max(0, lim - idle))
            return left if left <= warn else None

    def daily(self):
        """ERP uchun: kun bo'yicha yoqiq va ish vaqti."""
        days = {}
        for e in self.event_list():
            day = (e.get("end") or e.get("_qabul") or "")[:10]
            if not day:
                continue
            d = days.setdefault(day, {"on": 0, "run": 0, "idle": 0, "stops": 0,
                                      "shutdowns": 0})
            t = e.get("type")
            dur = e.get("duration_sec") or 0
            if t == "POWER_ON":
                d["on"] += dur
                d["run"] += e.get("run_sec") or 0
            elif t in ("SPINDLE_IDLE", "UNPLANNED_STOP"):
                d["idle"] += dur
                d["stops"] += 1
            elif t == "AUTO_SHUTDOWN":
                d["shutdowns"] += 1
        return days

    def snapshot(self):
        with self.lock:
            st = self.state
            if st and time.time() - self.last_rx > LINK_TIMEOUT_S:
                self.link_ok = False
            return {
                "link": self.link_ok,
                # Oxirgi xabardan beri o'tgan vaqt: hisoblagichlar ekranda
                # 1 soniyalab silliq yurishi uchun
                "age": max(0, round(time.time() - self.last_rx, 1)) if st else 0,
                "port": self.port_name,
                "hello": self.hello,
                "state": st,
                "acked": self.acked,
                "events": self.event_list()[-100:],
                "queue": self.queue()[:8],
                "queue_len": len(self.queue()),
                "reasons": REASONS,
                "types": TYPE_UZ,
                "ui": dict(self.ui),
                "daily": self.daily(),
                "traffic": self.traffic[-30:],
                "errors": self.errors[-8:],
                "count": len(self.events),
            }


_SIREN_FILES = {}


def siren_file(vol=100, seconds=0.8, lo=950, hi=1900, period=0.42):
    """Sirena ovozi WAV fayl qilib yoziladi (balandligi sozlanadi).

    vol - 50..100 foiz. Pastroq qilib bo'lmaydi: operator ogohlantirishni
    eshitmay qolmasligi kerak.
    """
    import math
    import struct
    import tempfile
    import wave

    vol = max(50, min(100, int(vol)))
    path = _SIREN_FILES.get(vol)
    if path and os.path.exists(path):
        return path
    rate = 22050
    n = int(rate * seconds)
    amp = 32000 * vol / 100.0
    out = bytearray()
    phase = 0.0
    for i in range(n):
        t = i / rate
        f = lo + (hi - lo) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))
        phase += 2 * math.pi * f / rate
        # Sinus to'lqin - ingichka, tiniq sirena (kvadrat to'lqin dag'al edi)
        s = math.sin(phase)
        env = min(1.0, t / 0.02, (seconds - t) / 0.05)
        out += struct.pack("<h", int(s * env * amp))
    path = os.path.join(tempfile.gettempdir(), "cnc2130_sirena_v2_%d.wav" % vol)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(out))
    _SIREN_FILES[vol] = path
    return path


def play_alarm(vol=100):
    """Ogohlantirish sirenasi - kompyuter karnayidan (brauzerga bog'liq emas)."""
    try:
        import winsound
    except ImportError:
        return
    try:
        winsound.PlaySound(siren_file(vol), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


class Alarm(threading.Thread):
    """O'chishga oz qolganda ogohlantiradi. Vaqt yaqinlashgan sari tezlashadi."""

    daemon = True

    def __init__(self, mes):
        super().__init__()
        self.mes = mes
        self.stop = False

    def run(self):
        last = 0
        while not self.stop:
            time.sleep(0.2)
            left = self.mes.warn_left()
            if left is None:
                last = 0
                continue
            # Har soniyada bir marta: sirena ~0.8 s, keyin qisqa tanaffus
            if time.time() - last >= 1.0:
                last = time.time()
                play_alarm(self.mes.ui.get("ovoz_kuch", 100))


def time_msg():
    lt = time.localtime()
    return {"t": "time", "ts": [lt.tm_year, lt.tm_mon, lt.tm_mday,
                                lt.tm_hour, lt.tm_min, lt.tm_sec]}


def find_port():
    for p in list_ports.comports():
        if p.vid == PICO_VID:
            return p.device
    return None


class Link(threading.Thread):
    """USB aloqa: ulanish, o'qish, yuborish. Uzilsa o'zi qayta ulanadi."""

    daemon = True

    def __init__(self, mes, port=None):
        super().__init__()
        self.mes = mes
        self.fixed_port = port
        self.stop = False

    def run(self):
        buf = b""
        ser = None
        last_time = 0
        while not self.stop:
            if ser is None:
                port = self.fixed_port or find_port()
                if port is None:
                    self.mes.port_name = None
                    self.mes.link_ok = False
                    time.sleep(2)
                    continue
                try:
                    ser = serial.Serial(port, 115200, timeout=0.2)
                    self.mes.port_name = port
                    self.mes.trace("TIZIM", "ulandi: %s" % port)
                    buf = b""
                    last_time = 0
                except serial.SerialException as e:
                    self.mes.port_name = None
                    self.mes.link_ok = False
                    self.mes.log_error("port ochilmadi (%s): %s" % (port, e))
                    time.sleep(3)
                    continue
            try:
                now = time.time()
                if now - last_time >= TIME_PERIOD_S:
                    last_time = now
                    self.mes.send(time_msg())
                with self.mes.lock:
                    out, self.mes.outbox = self.mes.outbox, []
                for obj in out:
                    line = json.dumps(obj, ensure_ascii=False)
                    ser.write((line + "\n").encode())
                    self.mes.trace("MES>", line)
                buf += ser.read(4096)
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    text = raw.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        self.mes.trace("pico", text)     # print() qatorlari
                        continue
                    if isinstance(msg, dict):
                        if msg.get("t") != "state":
                            self.mes.trace("PICO>", text)
                        self.mes.on_message(msg)
            except (serial.SerialException, OSError) as e:
                self.mes.log_error("aloqa uzildi: %s" % e)
                self.mes.link_ok = False
                self.mes.port_name = None
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                time.sleep(2)
        if ser is not None:
            ser.close()


class Server(ThreadingHTTPServer):
    # Band portni "bosib olish" mumkin emas: aks holda Windows da eski
    # jarayonning soketi osilib qolib, ulanishlar javobsiz qoladi
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    mes = None

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
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, self.mes.snapshot())
        elif self.path == "/api/csv":
            rows = ["seq;turi;boshlanish;tugash;davom_sek;ish;sabab;vaqt_aniqmi"]
            for e in self.mes.event_list():
                rows.append(";".join(str(x) for x in [
                    e.get("seq"), e.get("type"), e.get("start") or "",
                    e.get("end") or "", e.get("duration_sec"), e.get("job") or "",
                    e.get("_sabab") or "", "yo'q" if e.get("time_uncertain") else "ha"]))
            self._send(200, "﻿".encode() + "\r\n".join(rows).encode("utf-8"),
                       "text/csv; charset=utf-8")
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False, "msg": "JSON xato"})
        if self.path == "/api/send":
            self.mes.send(body)
            return self._send(200, {"ok": True})
        if self.path == "/api/reason":
            ok, msg = self.mes.close_reason(body.get("id"), body.get("code"),
                                            body.get("text"))
            return self._send(200, {"ok": ok, "msg": msg})
        if self.path == "/api/sozlama":
            return self._send(200, {"ok": True, "ui": self.mes.set_ui(body)})
        if self.path == "/api/ovoz_sinov":
            vol = body.get("ovoz_kuch", self.mes.ui.get("ovoz_kuch", 100))
            threading.Thread(target=play_alarm, args=(vol,), daemon=True).start()
            return self._send(200, {"ok": True})
        if self.path == "/api/tozala":
            with self.mes.lock:
                self.mes.events = {}
                self.mes.save()
            return self._send(200, {"ok": True})
        self._send(404, {"ok": False})


PAGE = r"""<!doctype html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CNC 2130 — MES / ERP</title>
<style>
 :root{--bg:#eef1f4;--card:#fff;--ink:#1b2430;--muted:#66717e;--line:#dde2e8;
       --green:#1f9d55;--yellow:#e0a800;--blue:#2d6cdf;--red:#d64545;--gray:#7b8794;--accent:#0f6fff}
 *{box-sizing:border-box}[hidden]{display:none!important}
 body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",Roboto,sans-serif}
 header{background:#17202b;color:#fff;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;padding:10px 16px;position:sticky;top:0;z-index:5}
 header h1{font-size:16px;margin:0}.sp{flex:1}
 .pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:650}
 .ok{background:#e3f6ea;color:var(--green)}.bad{background:#fde8e8;color:var(--red)}.mid{background:#fff4d6;color:#8a6400}
 main{max-width:1400px;margin:0 auto;padding:14px 16px 40px;display:grid;gap:14px;grid-template-columns:repeat(12,minmax(0,1fr))}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
 .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 10px}
 .c4{grid-column:span 4}.c5{grid-column:span 5}.c7{grid-column:span 7}.c8{grid-column:span 8}.c12{grid-column:span 12}
 @media(max-width:1000px){main>*{grid-column:1/-1!important}}
 .banner{border-radius:12px;color:#fff;padding:22px 14px;text-align:center;background:var(--gray)}
 .banner .big{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums}.banner .sub{opacity:.92;margin-top:4px}
 .sig{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:10px}
 .sig div{border-radius:10px;padding:8px 4px;text-align:center;background:#f6f8fa;border:1px solid var(--line)}
 .sig b{display:block;font-size:12px;color:var(--muted);font-weight:650}
 .sig span{font-size:13px;font-weight:700;color:var(--muted)}
 .sig div.on{background:#e3f6ea;border-color:var(--green)}.sig div.on span{color:var(--green)}
 .sig div.rel{background:#fde8e8;border-color:var(--red)}.sig div.rel span{color:var(--red)}
 .st-WORKING{background:var(--green)}.st-IDLE{background:var(--yellow);color:#231a00}
 .st-PLANNED_STOP{background:var(--blue)}.st-BLOCKED,.st-SHUTDOWN{background:var(--red)}
 .banner.ogoh{background:var(--red)!important;color:#fff;animation:blink 1s steps(2,start) infinite}
 @keyframes blink{50%{background:#8c1f1f!important}}
 .bar{height:8px;background:#e6eaef;border-radius:8px;overflow:hidden;margin-top:10px}
 .bar i{display:block;height:100%;background:#fff;width:0;transition:width .3s}
 .kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin-top:12px}
 .kv dt{color:var(--muted)}.kv dd{margin:0;text-align:right;font-weight:600}
 button{border:1px solid var(--line);background:#f6f8fa;color:var(--ink);border-radius:10px;padding:10px 12px;font:inherit;font-weight:600;cursor:pointer}
 button:hover{background:#eaf1ff}button.pri{background:var(--accent);border-color:var(--accent);color:#fff}
 button.red{color:var(--red)}
 .btns{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}
 input{width:100%;padding:8px;border:1px solid var(--line);border-radius:8px;font:inherit}
 label.f{font-size:12px;color:var(--muted)}
 .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
 .tbl{overflow:auto;max-height:340px}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
 th{position:sticky;top:0;background:#f6f8fa;color:var(--muted)}
 td.num{text-align:right;font-variant-numeric:tabular-nums}
 pre{background:#101820;color:#cfe3d2;border-radius:10px;padding:10px;margin:0;font:12px/1.45 Consolas,monospace;max-height:280px;overflow:auto;white-space:pre-wrap}
 .reasons{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
 .qhead{background:#fff4d6;border-radius:10px;padding:10px 12px}
 .dim{color:var(--muted)}.warn{background:#fde8e8;color:var(--red);border-radius:10px;padding:10px;font-weight:600}
 .relay{margin-top:10px;border-radius:10px;padding:12px;text-align:center;font-weight:700;border:2px solid var(--line);background:#f6f8fa;color:var(--muted)}
 .relay.on{background:#fde8e8;border-color:var(--red);color:var(--red)}
</style></head><body>
<header><h1>CNC 2130 · MES / ERP</h1>
 <span id="conn" class="pill bad">ulanmagan</span><span id="pinfo" style="opacity:.85"></span>
 <span class="sp"></span>
 <button id="ovoz">Ovoz: —</button><button id="sinov">Ovoz sinovi</button>
 <button onclick="location.href='/api/csv'">CSV yuklab olish</button>
 <button class="red" id="clear">Bazani tozalash</button>
</header>
<main>
 <section class="card c5"><h2>Stanok holati</h2>
  <div class="banner" id="banner"><div class="big" id="big">MA'LUMOT YO'Q</div>
   <div class="sub" id="sub">Pico ulanmagan — stanok o'chiq deb hisoblanadi</div>
   <div class="bar" id="idlebar" hidden><i></i></div></div>
  <div class="sig" id="sig"></div>
  <div class="relay" id="relay">RELE (GP0): bo'sh</div>
  <dl class="kv" id="kv"></dl>
 </section>

 <section class="card c7"><h2>Boshqaruv (MES → Pico)</h2>
  <div class="grid2">
   <div><label class="f">Ish kodi</label><input id="job" value="ORD-2026-0417"></div>
   <div><label class="f">Operator</label><input id="oper" value="OP-1142"></div>
  </div>
  <div class="btns">
   <button class="pri" data-cmd="start_job">Ishni boshlash</button>
   <button data-cmd="end_job">Ishni tugatish</button>
   <button data-stop="LUNCH" data-sec="3600">Tushlik (60 daq)</button>
   <button data-stop="SETUP" data-sec="900">Sozlash (15 daq)</button>
   <button data-cmd="planned_stop_end">To'xtashni tugatish</button>
   <button class="red" id="block">Bloklash / ochish</button>
  </div>
  <h2 style="margin-top:16px">Sozlamalar</h2>
  <div class="grid2">
   <div><label class="f">t1, daqiqa (0 nuqtada)</label><input type="number" step="0.5" id="t1"></div>
   <div><label class="f">t2, daqiqa (tashqarida)</label><input type="number" step="0.5" id="t2"></div>
   <div><label class="f">t_relay, soniya (rele yoniq turadi)</label><input type="number" id="t_relay"></div>
   <div><label class="f">Ogohlantirish, daqiqa (o'chishdan oldin)</label>
     <input type="number" step="0.5" id="t_warn"></div>
   <div><label class="f">Ovoz balandligi, % (50 dan past bo'lmaydi)</label>
     <input type="number" min="50" max="100" step="5" id="ovoz_kuch"></div>
   <div><label class="f">Avtomatik o'chirish</label>
     <select id="auto" style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--line)">
       <option value="1">yoqiq</option><option value="0">o'chiq</option></select></div>
  </div>
  <details style="margin-top:10px"><summary class="dim" style="cursor:pointer">Qo'shimcha sozlamalar</summary>
   <div class="grid2" style="margin-top:8px">
    <div><label class="f">t_reason, daqiqa</label><input type="number" step="0.5" id="t_reason">
      <div class="dim" style="font-size:12px">Shundan uzun to'xtashga operatordan sabab so'raladi.
        0 qilinsa har bir qisqa to'xtash ham sabab so'raydi va navbat to'lib ketadi.</div></div>
    <div><label class="f">t_merge, soniya</label><input type="number" id="t_merge">
      <div class="dim" style="font-size:12px">Chastotnik signalidagi shundan qisqa uzilishlar
        e'tiborga olinmaydi (asbob almashtirish, yo'nalish o'zgarishi).</div></div>
   </div></details>
  <button class="pri" id="sendcfg" style="width:100%;margin-top:10px">Sozlamani yuborish</button>
  <div class="dim" style="font-size:12px;margin-top:6px">Bo'sh turish vaqtlari daqiqada, texnik qiymatlar soniyada.</div>
 </section>

 <section class="card c5"><h2>Sabablar navbati · <span id="qlen">0</span> ta</h2>
  <div id="queue"><div class="dim">Navbat bo'sh</div></div></section>

 <section class="card c7"><h2>Kunlik hisobot (ERP)</h2>
  <div class="tbl"><table><thead><tr><th>Kun</th><th class="num">Yoqiq</th><th class="num">Ish (shpindel)</th>
   <th class="num">Bo'sh</th><th class="num">To'xtash</th><th class="num">Avto o'chirish</th></tr></thead>
   <tbody id="daily"></tbody></table></div></section>

 <section class="card c8"><h2>Hodisalar · <span id="evc">0</span> ta · tasdiqlangan seq: <span id="ack">—</span></h2>
  <div class="tbl"><table><thead><tr><th class="num">seq</th><th>Turi</th><th>Boshlanish</th>
   <th class="num">Davom</th><th>Ish</th><th>Sabab</th><th>Vaqt</th></tr></thead>
   <tbody id="rows"></tbody></table></div></section>

 <section class="card c4"><h2>USB trafik</h2><pre id="traffic"></pre>
  <div id="err" class="warn" style="margin-top:8px" hidden></div></section>
</main>
<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let S=null, cfgLoaded=false, blocked=false;
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// 0-59 -> "45 soniya", 1 daqiqadan boshlab -> "2:05 daqiqa", 1 soatdan -> "1 soat 05:30"
const pad=n=>String(n).padStart(2,"0");
const human=s=>{s=Math.floor(s||0);
  if(s<60)return s+" soniya";
  const h=Math.floor(s/3600),m=Math.floor(s%3600/60),q=s%60;
  return h?h+" soat "+pad(m)+":"+pad(q):m+":"+pad(q)+" daqiqa"};
async function post(p,b){const r=await fetch(p,{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify(b||{})});return r.json()}
const send=o=>post("/api/send",o);

// Ogohlantirish signalini dasturning o'zi (kompyuter karnayi) chiqaradi -
// brauzer ovozi bloklanishi yoki bet fonda qolishi ta'sir qilmaydi.
const STATES={WORKING:"ISHLAYAPTI",OFF:"O'CHIQ",IDLE_HOME:"BO'SH TURIBDI",IDLE_AWAY:"BO'SH TURIBDI",
  PLANNED_STOP:"REJALI TO'XTASH",BLOCKED:"BLOKLANGAN",SHUTDOWN:"O'CHIRILMOQDA"};

function render(s){
 S=s;
 $("#conn").textContent=s.link?"ulangan":"ma'lumot yo'q";
 $("#conn").className="pill "+(s.link?"ok":"bad");
 $("#pinfo").textContent=(s.port?s.port+" · ":"")+(s.hello?s.hello.machine+" · yuklanish #"+s.hello.boot:"");
 const st=s.state, idle=st&&(st.state==="IDLE_HOME"||st.state==="IDLE_AWAY");
 const bar=$("#idlebar"); bar.hidden=true;
 if(!st||!s.link){$("#banner").className="banner";$("#big").textContent="MA'LUMOT YO'Q";
   $("#sub").textContent="Pico dan xabar kelmayapti — stanok o'chiq deb hisoblanadi";}
 else{
  // Xabarlar orasida hisoblagichlar shu yerda sanaladi (1 soniyalab silliq)
  const age=s.age||0;
  const idleSec=Math.floor((st.idle_sec||0)+(idle?age:0));
  const onSec=Math.floor((st.on_sec||0)+(st.pwr?age:0));
  const runSec=Math.floor((st.run_sec||0)+(st.run?age:0));
  $("#banner").className="banner st-"+(idle?"IDLE":st.state);
  $("#big").textContent=STATES[st.state]+(idle?" — "+human(idleSec):
    st.state==="PLANNED_STOP"?" — "+(st.planned||""):"");
  // 0 nuqta holati har doim va darhol ko'rinadi
  const uy=st.pwr?(st.home?"0 nuqtada":"0 nuqtadan tashqarida"):"";
  let sub=idle?uy:(st.job?"Ish: "+st.job+(uy?" · "+uy:""):uy);
  let ogoh=false;
  if(idle){const lim=st.home?st.t1:st.t2, qoldi=Math.max(0,lim-idleSec);
    if(st.blocked_by==="maint")sub+=" · ta'mirlash kaliti: o'chirish bloklangan";
    else if(st.blocked_by==="config")sub+=" · avtomatik o'chirish o'chiq";
    else if(st.blocked_by==="failed")sub+=" · o'chirishga urinildi, stanok o'chmadi — shpindel ishlagandan keyin qayta urinadi";
    else{
      // Katta raqam - teskari hisob: 1:05, 1:04, ... 35, 34, 33
      $("#big").textContent="O'CHISHGA "+human(qoldi);
      sub="Bo'sh turibdi "+human(idleSec)+(uy?" · "+uy:"");
      bar.hidden=false;bar.firstElementChild.style.width=Math.min(100,100*idleSec/lim)+"%";
      const warn=(s.ui&&s.ui.t_warn)||0;
      if(warn>0&&qoldi<=warn){
        ogoh=true;
        $("#big").textContent="STANOK O'CHADI — "+human(qoldi);
        sub="Harakat qilmasangiz stanok o'chadi"+(uy?" · "+uy:"");
      }
    }}
  $("#banner").classList.toggle("ogoh",ogoh);
  // Shpindel to'xtadi, lekin qisqa uzilish filtri (t_merge) hali tugamadi
  if(st.state==="WORKING"&&!st.run)sub="Shpindel TO'XTADI · qisqa uzilish filtri "+st.t_merge+" s kutilmoqda"+(uy?" · "+uy:"");
  if(st.state!=="WORKING"&&st.run&&st.state!=="OFF")sub="Shpindel YONDI"+(uy?" · "+uy:"");
  if(st.state==="SHUTDOWN")sub="Rele yoniq ("+st.t_relay+" s) · stanok o'chishi kutilmoqda";
  $("#sub").textContent=sub||" ";
  const sg=[["TARMOQ",st.pwr],["SHPINDEL",st.run],["0 NUQTA",st.home],["KALIT",st.maint],["RELE",st.relay]];
  $("#sig").innerHTML=sg.map(([n,v],i)=>`<div class="${v?(i===4?"rel":"on"):""}"><b>${n}</b>
    <span>${v?"BOR":"yo'q"}</span></div>`).join("");
  blocked=st.blocked;
  const r=$("#relay");r.classList.toggle("on",!!st.relay);
  r.textContent=st.relay?"RELE (GP0): YONIQ — stanok o'chirilmoqda":"RELE (GP0): bo'sh";
  $("#kv").innerHTML=[
   ["Stanok yoqiq","<b>"+human(onSec)+"</b> (shpindel "+human(runSec)+")"],
   ["Signallar","PWR="+st.pwr+" RUN="+st.run+" HOME="+st.home+" KALIT="+st.maint],
   ["Ish / operator",(st.job||"—")+" / "+(st.operator||"—")],
   ["t1 / t2",human(st.t1)+" / "+human(st.t2)],
   ["t_relay (rele yoniq turadi)",human(st.t_relay)],
   ["Avtomatik o'chirish",st.auto_shutdown?"yoqiq":"o'chiq"],
   ["Hodisalar: yozilgan / tasdiqlangan",st.seq+" / "+st.last_ack],
   ["Pico soati",st.ts?st.ts.replace("T"," ")+" ("+st.time_source+")":"noma'lum"],
   ["Pico yoqilgandan beri",human(st.uptime_sec)],
  ].map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join("");
  if(!cfgLoaded){const m=x=>Math.round(x/6)/10;
   $("#t1").value=m(st.t1);$("#t2").value=m(st.t2);$("#t_reason").value=m(st.t_reason);
   $("#t_merge").value=st.t_merge;$("#t_relay").value=st.t_relay;
   $("#t_warn").value=Math.round(((s.ui&&s.ui.t_warn)||0)/6)/10;
   $("#ovoz_kuch").value=(s.ui&&s.ui.ovoz_kuch)||100;
   $("#auto").value=st.auto_shutdown?"1":"0";cfgLoaded=true;}
 }
 $("#ovoz").textContent="Ovoz: "+(s.ui&&s.ui.ovoz?"yoqiq":"o'chiq");
 $("#evc").textContent=s.count;$("#ack").textContent=s.acked??"—";
 $("#rows").innerHTML=s.events.slice().reverse().map(e=>`<tr>
  <td class="num">${e.seq}</td><td>${esc(s.types[e.type]||e.type)}${e.type==="POWER_ON"?
    '<br><span class="dim">shpindel '+esc(human(e.run_sec))+"</span>":""}</td>
  <td>${e.start?esc(e.start.replace("T"," ")):'<span class="pill mid">kun ichida</span>'}</td>
  <td class="num">${e.duration_sec==null?"—":esc(human(e.duration_sec))}</td>
  <td>${esc(e.job||"—")}</td>
  <td>${!e.reason_required?'<span class="dim">kerak emas</span>':e._sabab?
    '<span class="pill ok">'+esc(s.reasons[e._sabab]||e._sabab)+"</span>":'<span class="pill bad">kutilmoqda</span>'}</td>
  <td>${e.time_uncertain?'<span class="pill mid">noma\'lum</span>':
    e.time_restored?'<span class="pill ok">tiklandi</span>':'<span class="pill ok">aniq</span>'}</td></tr>`).join("")
  ||'<tr><td colspan="7" class="dim">Hali hodisa yo\'q</td></tr>';
 const days=Object.entries(s.daily).sort().reverse();
 $("#daily").innerHTML=days.map(([d,v])=>`<tr><td>${d}</td><td class="num">${human(v.on)}</td>
  <td class="num">${human(v.run)}</td><td class="num">${human(v.idle)}</td>
  <td class="num">${v.stops}</td><td class="num">${v.shutdowns}</td></tr>`).join("")
  ||'<tr><td colspan="6" class="dim">Hali ma\'lumot yo\'q</td></tr>';
 renderQueue(s);
 $("#traffic").textContent=s.traffic.slice().reverse().map(([t,d,m])=>t+"  "+d.padEnd(6)+" "+m).join("\n");
 $("#err").hidden=!s.errors.length;$("#err").textContent=s.errors.slice().reverse().join("\n");
}
function renderQueue(s){
 const q=s.queue,box=$("#queue");$("#qlen").textContent=s.queue_len;
 if(!q.length){box.innerHTML='<div class="dim">Navbat bo\'sh. Sabab so\'raladigan hodisa kelganda shu yerda chiqadi.</div>';box._id=null;return}
 const e=q[0];if(box._id===e.id&&box._n===s.queue_len)return;
 box._id=e.id;box._n=s.queue_len;
 box.innerHTML=`<div class="qhead"><b>${esc(s.types[e.type]||e.type)}</b> · ${esc(human(e.duration_sec))}
   <div class="dim">${esc((e.start||"vaqti noma'lum").replace("T"," "))} · ${esc(e.job||"ish yo'q")}</div></div>
  <div class="reasons">${Object.entries(s.reasons).filter(([k])=>k!=="OTHER")
   .map(([k,v])=>`<button data-r="${k}">${esc(v)}</button>`).join("")}</div>
  <div class="grid2" style="margin-top:8px;grid-template-columns:1fr auto">
   <input id="othertext" placeholder="Boshqa sabab (matn majburiy)"><button data-r="OTHER">Boshqa</button></div>`;
 box.querySelectorAll("[data-r]").forEach(b=>b.onclick=async()=>{
   const j=await post("/api/reason",{id:e.id,code:b.dataset.r,text:$("#othertext")?.value||""});
   box._id=null;if(!j.ok)alert(j.msg);refresh();});
}
$$("[data-cmd]").forEach(b=>b.onclick=()=>{const c={t:"cmd",cmd:b.dataset.cmd};
 if(c.cmd==="start_job"){c.job=$("#job").value;c.operator=$("#oper").value}send(c)});
$$("[data-stop]").forEach(b=>b.onclick=()=>send({t:"cmd",cmd:"planned_stop",type:b.dataset.stop,planned_sec:+b.dataset.sec}));
$("#block").onclick=()=>send({t:"cmd",cmd:"block",value:!blocked});
$("#sendcfg").onclick=()=>{const sec=id=>Math.round(parseFloat(String($(id).value).replace(",","."))*60);
 const cfg={t:"config",t1:sec("#t1"),t2:sec("#t2"),t_reason:sec("#t_reason"),
  t_merge:+$("#t_merge").value,t_relay:+$("#t_relay").value,auto_shutdown:$("#auto").value==="1"};
 const bad=["t1","t2","t_reason","t_merge","t_relay"].filter(k=>!Number.isFinite(cfg[k]));
 if(bad.length)return alert("Noto'g'ri qiymat: "+bad.join(", "));
 send(cfg);
 // Ogohlantirish vaqti Pico ga emas, shu dasturga saqlanadi
 post("/api/sozlama",{t_warn:Math.round(parseFloat(String($("#t_warn").value).replace(",","."))*60)||0,
                      ovoz_kuch:+$("#ovoz_kuch").value||100});
 cfgLoaded=false};
$("#ovoz").onclick=async()=>{const on=!(S&&S.ui&&S.ui.ovoz);
 await post("/api/sozlama",{ovoz:on});if(on)post("/api/ovoz_sinov");refresh()};
$("#sinov").onclick=()=>post("/api/ovoz_sinov",{ovoz_kuch:+$("#ovoz_kuch").value||100});
$("#clear").onclick=()=>{if(confirm("Saqlangan hodisalar o'chiriladi. Davom etamizmi?"))post("/api/tozala").then(refresh)};
async function refresh(){try{render(await(await fetch("/api/state")).json())}catch(e){}}
refresh();setInterval(refresh,500);
</script></body></html>
"""


def main():
    port = None
    web_port = 8140
    for a in sys.argv[1:]:
        if a.upper().startswith("COM") or a.startswith("/dev/"):
            port = a
        elif a.isdigit():
            web_port = int(a)

    mes = Mes()
    Handler.mes = mes
    link = Link(mes, port)
    link.start()
    alarm = Alarm(mes)
    alarm.start()

    srv = None
    for p in range(web_port, web_port + 10):
        try:
            srv = Server(("127.0.0.1", p), Handler)
            web_port = p
            break
        except OSError:
            print("port %d band, keyingisi tekshirilmoqda..." % p)
    if srv is None:
        link.stop = True
        raise SystemExit("Bo'sh port topilmadi (%d..%d)" % (web_port, web_port + 9))
    url = "http://127.0.0.1:%d" % web_port
    print("=" * 64)
    print("CNC 2130 - MES / ERP namoyish dasturi")
    print("=" * 64)
    print("Oyna:", url)
    print("Pico:", port or "avtomatik topiladi (USB)")
    print("Ma'lumot fayli:", data_path())
    print("To'xtatish: shu oynada Ctrl+C yoki oynani yopish")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.stop = True
        mes.save()


if __name__ == "__main__":
    main()
