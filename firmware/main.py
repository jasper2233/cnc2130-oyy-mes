"""CNC 2130 monitoring moduli - asosiy dastur.

Pico stanok yonidagi kompyuterga micro USB bilan ulanadi. Kompyuterdagi
MES sahifasi (brauzer, Web Serial) USB portni ochib, JSON qatorlar orqali
gaplashadi. Kompyuterga qo'shimcha dastur o'rnatilmaydi, Wi-Fi yo'q.

Kompyuter o'chiq bo'lsa dastur to'xtamaydi: Controller mustaqil ishlaydi,
hodisalar lokal jurnalga yoziladi va kompyuter yoqilganda yuboriladi.

Hech bir USB amali WDT muddatidan (8 s) uzoq to'xtab qolmasligi shart.

Protokol (har qator bitta JSON obyekt, "t" - xabar turi):
  Kompyuter -> Pico:
    {"t": "time", "ts": [2026, 9, 15, 14, 32, 11]}   har 10 s, aloqa belgisi
    {"t": "ack", "seq": 17}                           17 gacha saqlandi
    {"t": "config", "t1": 900, ...}
    {"t": "jobs", "jobs": [...]}
    {"t": "cmd", "cmd": "start_job", "job": "...", "operator": "..."}
  Pico -> kompyuter:
    {"t": "hello", ...}   aloqa boshlanganda
    {"t": "state", ...}   har 10 s (TZ 15.2)
    {"t": "event", ...}   tasdiqlanmagan hodisalar (TZ 15.3)
    {"t": "log", "msg": "..."}
  JSON bo'lmagan qatorlar (print) e'tiborsiz qoldiriladi.
"""

import json
import sys
import time

import machine

import config
from clock import time_is_sane
from controller import Controller
from ds3231 import DS3231
from eventlog import EventLog, Store, resolve_time


class UsbPort:
    """micro USB (sys.stdin/stdout) ustidan bloklamaydigan o'qish-yozish."""

    def __init__(self):
        import select
        self._rx = select.poll()
        self._rx.register(sys.stdin, select.POLLIN)
        self._tx = select.poll()
        self._tx.register(sys.stdout, select.POLLOUT)

    def read(self, limit):
        out = bytearray()
        while len(out) < limit and self._rx.poll(0):
            c = sys.stdin.buffer.read(1)
            if not c:
                break
            out.extend(c)
        return out

    def writable(self):
        return bool(self._tx.poll(0))

    def write(self, text):
        sys.stdout.write(text)


class NoPort:
    """Aloqasiz ishlash (vositalar va sinovlar uchun)."""

    def read(self, limit):
        return b""

    def writable(self):
        return False

    def write(self, text):
        pass


class Link:
    """Kompyuter bilan aloqa. Uzilishlarga chidamli.

    Kompyuterdan oxirgi LINK_TIMEOUT_S ichida xabar kelgan bo'lsa - aloqa bor.
    Faqat shunda yoziladi: port yopiq bo'lsa ma'lumot baribir yo'qoladi.
    """

    def __init__(self, ctrl, store, log, port, feed=None):
        self.ctrl = ctrl
        self.store = store
        self.log = log
        self.port = port
        self.feed = feed or (lambda: None)
        self.connected = False
        self._buf = bytearray()
        self._out = []             # [(tur, qator)]
        self._state_due = False
        self._last_rx = None
        self._stall_until = None
        self._more = False         # tasdiq keldi - keyingi hodisalarni yuborish
        self._queued_upto = 0      # navbatga qo'yilgan eng katta seq

    # ---------- o'qish ----------

    def pump(self):
        now = time.ticks_ms()
        data = self.port.read(config.RX_BYTES_PER_STEP)
        if data:
            self._last_rx = now
            new = not self.connected
            self.connected = True
            for b in data:
                if b == 10:
                    line = bytes(self._buf)
                    self._buf = bytearray()
                    self._on_line(line)
                elif len(self._buf) < config.RX_MAX_LINE:
                    self._buf.append(b)
            if new:
                # Qatorlardan keyin: birinchi xabar odatda vaqt - hodisalar
                # aniq vaqt bilan ketsin
                self._on_connect()
        elif (self.connected and time.ticks_diff(now, self._last_rx)
              > config.LINK_TIMEOUT_S * 1000):
            self._on_disconnect()

        if self.connected and not self._events_queued() and (
                self._more or self.store.data["seq"] > self._queued_upto):
            # Tasdiq keldi yoki yangi hodisa yozildi - kutmasdan yuboramiz
            self._more = False
            self.queue_events()
        self._flush()

    def _on_line(self, line):
        line = line.strip()
        if not line.startswith(b"{"):
            return
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError("obyekt emas")
            self._dispatch(data)
        except Exception as e:
            # Bitta buzuq xabar aloqani ham, Pico ni ham to'xtatmasin
            self.send({"t": "log", "msg": "xabar rad etildi: %s" % e})

    def _dispatch(self, d):
        t = d.get("t")
        if t == "time":
            self._set_time(d.get("ts"))
        elif t == "ack":
            seq = d.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                if self.log.ack(seq):
                    self._more = True
        elif t == "config":
            changed, rejected = self.store.update_config(d)
            if rejected:
                self.send({"t": "log", "msg": "noto'g'ri sozlama rad etildi: %s" % rejected})
            self._state_due = True
        elif t == "jobs":
            # Rejalashtirilgan ishlar ro'yxati (TZ 9.1)
            jobs = d.get("jobs")
            if isinstance(jobs, list):
                self.store.data["jobs"] = jobs
                self.store.save()
        elif t == "cmd":
            self._handle_cmd(d)
            self._state_due = True

    def _set_time(self, t):
        # [yil, oy, kun, soat, daqiqa, soniya] - kompyuterning mahalliy vaqti
        if not (isinstance(t, list) and len(t) == 6
                and all(isinstance(x, int) for x in t) and t[0] >= 2024):
            return
        machine.RTC().datetime((t[0], t[1], t[2], 0, t[3], t[4], t[5], 0))
        if self.ctrl.rtc is not None and self.ctrl.rtc.ok:
            self.ctrl.rtc.sync_from_system()
        self.ctrl.time_source = "pc"
        self.ctrl.on_time_synced()

    def _handle_cmd(self, d):
        cmd = d.get("cmd")
        if cmd == "start_job":
            self.store.data["job"] = d.get("job")
            self.store.data["operator"] = d.get("operator")
            self.store.save()
        elif cmd == "end_job":
            self.store.data["job"] = None
            self.store.save()
        elif cmd == "planned_stop":
            self.ctrl.set_planned_stop(d.get("type"), d.get("planned_sec", 0))
        elif cmd == "planned_stop_end":
            self.ctrl.clear_planned_stop()
        elif cmd == "block":
            self.ctrl.set_blocked(d.get("value", False))

    # ---------- ulanish ----------

    def _on_connect(self):
        self.send({"t": "hello", "machine": config.MACHINE_ID,
                   "boot": self.store.data.get("boot", 0),
                   "need_time": not time_is_sane(),
                   "seq": self.store.data["seq"],
                   "last_ack": self.store.data["last_ack"]})
        self._state_due = True
        self.queue_events()

    def _on_disconnect(self):
        self.connected = False
        self._out = []
        self._buf = bytearray()
        self._state_due = False
        self._stall_until = None

    # ---------- yozish ----------

    def send(self, obj, kind="msg"):
        if not self.connected or len(self._out) >= config.TX_QUEUE_MAX:
            return
        self._out.append((kind, json.dumps(obj)))

    def _events_queued(self):
        for kind, _ in self._out:
            if kind == "event":
                return True
        return False

    def queue_events(self):
        """Tasdiqlanmagan hodisalar, xronologik tartibda (TZ 5.2)."""
        if not self.connected:
            return
        boot = self.store.data.get("boot", 0)
        now = self.ctrl.now_ms
        # Yangi hodisa yo'q bo'lsa ham pump() qayta-qayta faylni o'qimasin
        self._queued_upto = self.store.data["seq"]
        for ev in self.log.pending(limit=config.EVENTS_PER_BATCH):
            ev = resolve_time(ev, boot, now)
            ev["t"] = "event"
            self.send(ev, "event")

    def notify_change(self):
        """Holat o'zgardi - kompyuterga darhol xabar berish."""
        self._state_due = True

    def heartbeat(self):
        if not self.connected:
            return
        self._state_due = True
        if not self._events_queued():
            self.queue_events()     # tasdiq kelmaganlar qayta yuboriladi

    def _flush(self):
        """Bir siklda ko'pi bilan bitta qator."""
        if not self.connected:
            return
        now = time.ticks_ms()
        if self._stall_until is not None:
            if time.ticks_diff(self._stall_until, now) > 0:
                return
            self._stall_until = None
        if self._out:
            line = self._out[0][1]
        elif self._state_due:
            line = None
        else:
            return
        if not self.port.writable():
            return
        if line is None:
            st = self.ctrl.snapshot()
            st["t"] = "state"
            line = json.dumps(st)
            self._state_due = False
        else:
            self._out.pop(0)
        t0 = time.ticks_ms()
        try:
            self.port.write(line + "\n")
        except Exception:
            self._on_disconnect()
            return
        if time.ticks_diff(time.ticks_ms(), t0) >= config.TX_STALL_MS:
            # Kompyuter o'qimayapti (brauzer qotgan) - bloklanib qolmaylik
            self._stall_until = time.ticks_add(time.ticks_ms(),
                                               config.TX_BACKOFF_S * 1000)
        self.feed()


def _file_exists(path):
    import os
    try:
        os.stat(path)
        return True
    except OSError:
        return False


class App:
    """Asosiy sikl. Pico da run(), kompyuterdagi virtual panelda step()."""

    def __init__(self, use_wdt=True, port=None):
        self.store = Store()
        self.store.data["boot"] = self.store.data.get("boot", 0) + 1
        self.store.save()
        self.log = EventLog(self.store)

        self.rtc = DS3231(config.PIN_SDA, config.PIN_SCL)
        self.ctrl = Controller(self.store, self.log, self.rtc)
        self._init_time()

        self.wdt = None
        if use_wdt and not _file_exists(config.NO_WDT_FLAG):
            self.wdt = machine.WDT(timeout=config.WDT_TIMEOUT_MS)
        elif use_wdt:
            print("DIQQAT: %s fayli bor, WDT o'chiq" % config.NO_WDT_FLAG)
        self.log.feed = self.feed     # jurnal siqilayotganda WDT och qolmasin

        self.link = Link(self.ctrl, self.store, self.log,
                         port if port is not None else UsbPort(), feed=self.feed)
        self._last_hb = time.ticks_ms()
        self._last_state = self._last_hb
        self._key = None
        self._key_dirty = False

    def _init_time(self):
        """Vaqt manbalari: kompyuter (USB), ixtiyoriy DS3231.

        Ikkalasi ham yo'q bo'lsa soat kerak emas - davomiyliklar monoton
        hisoblagich bilan o'lchanadi."""
        if self.rtc.sync_to_system():
            self.ctrl.time_source = "rtc"
            self.ctrl.on_time_synced()

    def feed(self):
        if self.wdt is not None:
            self.wdt.feed()

    def step(self):
        self.feed()
        self.ctrl.tick()
        self.link.pump()

        now = time.ticks_ms()
        # Signal yoki holat o'zgarsa - 10 s kutmasdan yuboriladi
        c = self.ctrl
        key = (c.state, c.pwr.value, c.run.value, c.home.value,
               1 if c.maint_on else 0, 1 if c.relay_active else 0)
        if key != self._key:
            self._key = key
            self._key_dirty = True
        if self._key_dirty and time.ticks_diff(now, self._last_state) >= config.STATE_MIN_MS:
            self._key_dirty = False
            self._last_state = now
            self.link.notify_change()

        if time.ticks_diff(now, self._last_hb) >= config.HEARTBEAT_S * 1000:
            self._last_hb = now
            self._last_state = now
            self.link.heartbeat()

            # Jurnal to'lib qolgani (TZ 5.6) - faqat to'lgan paytda bir marta
            if self.log.check_full():
                self.log.append("LOG_FULL", None, None, 0, reason_required=False)

    def run(self):
        while True:
            self.step()
            time.sleep_ms(config.TICK_MS)


def main():
    # BOOTSEL tugmasini bosib turib yoqilsa dastur ishga tushmaydi (REPL qoladi)
    try:
        import rp2
        if rp2.bootsel_button():
            print("BOOTSEL bosilgan - xavfsiz rejim, dastur ishga tushmadi")
            return
    except ImportError:
        pass
    App().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("to'xtatildi")
    except Exception as e:
        # Rele boshlang'ich holatga qaytadi (TZ 4.3.3)
        sys.print_exception(e)
        time.sleep_ms(3000)
        machine.reset()
