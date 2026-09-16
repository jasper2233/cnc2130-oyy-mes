"""Kompyuterdagi MES sahifasining soddalashtirilgan varianti (sinov va virtual panel).

Haqiqiy MES sahifasi (brauzer, Web Serial) shu qoidalarga amal qilishi kerak:
  - Har 10 s kompyuter vaqtini yuboradi: {"t": "time", "ts": [...]}.
    Bu Pico uchun "kompyuter yoqiq" belgisi ham (30 s kelmasa - aloqa yo'q).
  - Hodisa id bo'yicha dublikatsiz saqlanadi (TZ 5.4). Istisno: saqlangan
    nusxada start null bo'lsa, qayta kelgan nusxadagi aniq vaqt yoziladi.
  - Tasdiq yig'ma: {"t": "ack", "seq": N}, N - uzilishsiz saqlangan eng katta
    seq. Oraliqda yozuv yo'qolgan bo'lsa undan keyingilar tasdiqlanmaydi va
    Pico ularni qayta yuboradi (TZ 5.3). Tasdiq faqat server saqlagandan keyin.
  - start/end null bo'lgan hodisa (kun vaqti noma'lum) kelgan kuni ichida
    hisoblanadi, davomiylik duration_sec dan olinadi.
  - Sabablar navbati FIFO, o'tkazib yuborib bo'lmaydi (TZ 7.1).
"""

import json

import simtime

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

TIME_PERIOD_S = 10


class MES:
    def __init__(self, usb, machine_id, now_fn=None):
        self.usb = usb
        self.machine = machine_id
        self.now_fn = now_fn or simtime.time    # kompyuter soati
        self.events = {}          # id -> hodisa
        self.order = []           # kelish tartibi
        self.received_seq = set()
        self.acked = 0
        self.duplicates = 0
        self.drop_next_event = 0  # sinov: shuncha hodisani saqlamaslik
        self.last_state = None
        self.state_count = 0
        self.hello_count = 0
        self.time_syncs = 0
        self.logs = []
        self._last_time_ms = None
        self.send_time = True

    # ---------- har siklda ----------

    def poll(self):
        now = simtime.CLOCK.now_ms
        if self.usb.stalled:
            return                    # brauzer qotgan: na yozadi, na o'qiydi
        if self.send_time and self.usb.pc_on and (
                self._last_time_ms is None
                or now - self._last_time_ms >= TIME_PERIOD_S * 1000):
            self._last_time_ms = now
            self.sync_time()
        if not self.usb.pc_on:
            self._last_time_ms = None
        for line in self.usb.pc_read_lines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue              # print() qatorlari
            if isinstance(msg, dict):
                self._on_message(msg)

    def sync_time(self):
        t = simtime.localtime(self.now_fn())
        self.time_syncs += 1
        self._send({"t": "time", "ts": [t[0], t[1], t[2], t[3], t[4], t[5]]})

    # ---------- Pico dan ----------

    def _on_message(self, msg):
        t = msg.get("t")
        if t == "hello":
            self.hello_count += 1
            if msg.get("need_time"):
                self.sync_time()
        elif t == "state":
            self.last_state = msg
            self.state_count += 1
            if msg.get("last_ack", 0) > self.acked:
                self.acked = msg["last_ack"]
        elif t == "event":
            self._on_event(msg)
        elif t == "log":
            self.logs.append(msg.get("msg"))

    def _on_event(self, ev):
        if self.drop_next_event > 0:
            self.drop_next_event -= 1       # saqlashda xato bo'ldi
            return
        eid = ev.get("id")
        if eid in self.events:
            self.duplicates += 1
            old = self.events[eid]
            if old.get("start") is None and ev.get("start"):
                # Avval vaqtsiz kelgan, endi Pico vaqtini tiklab yubordi
                for k in ("start", "end", "time_uncertain", "time_restored"):
                    old[k] = ev.get(k)
        else:
            ev["_closed"] = False
            ev["_reason"] = None
            ev["_reason_text"] = None
            ev["_received"] = self.now_fn()
            self.events[eid] = ev
            self.order.append(eid)
        seq = ev.get("seq")
        if isinstance(seq, int):
            self.received_seq.add(seq)
        # Uzilishsiz ketma-ketlik bo'yicha tasdiq
        n = self.acked
        while (n + 1) in self.received_seq:
            n += 1
        if n > self.acked:
            self.acked = n
        self._send({"t": "ack", "seq": self.acked})

    # ---------- MES dan ----------

    def _send(self, obj):
        self.usb.pc_send(json.dumps(obj))

    def command(self, cmd, **kw):
        kw["t"] = "cmd"
        kw["cmd"] = cmd
        self._send(kw)

    def push_config(self, cfg):
        msg = dict(cfg)
        msg["t"] = "config"
        self._send(msg)

    def push_raw(self, text):
        self.usb.pc_send(text)

    # ---------- sabablar navbati (TZ 7) ----------

    def event_list(self):
        return [self.events[i] for i in self.order]

    def pending_reasons(self):
        return [e for e in self.event_list()
                if e.get("reason_required") and not e["_closed"]]

    def close_reason(self, event_id, code, text=None, same_for_group=False):
        """Sabab kiritish. (ok, xabar) qaytaradi."""
        queue = self.pending_reasons()
        if not queue:
            return False, "Navbat bo'sh"
        if queue[0]["id"] != event_id:
            return False, "Avval eng eski hodisaga sabab kiriting"   # TZ 7.1
        if code not in REASONS:
            return False, "Noma'lum sabab"
        if code == "OTHER" and not (text and text.strip()):
            return False, "Boshqa tanlanganda matn majburiy"          # TZ 7.5
        targets = [queue[0]]
        if same_for_group:                                            # TZ 7.4
            for e in queue[1:]:
                if e["type"] != queue[0]["type"]:
                    break
                targets.append(e)
        for e in targets:
            e["_closed"] = True
            e["_reason"] = code
            e["_reason_text"] = text
        return True, "%d ta hodisa yopildi" % len(targets)

    # ---------- oylik hisobot uchun ----------

    def on_seconds_by_day(self):
        """POWER_ON hodisalari: kun -> yoqiq soniya. Vaqti noma'lum bo'lsa
        kelgan kuni ichida hisoblanadi."""
        out = {}
        for e in self.event_list():
            if e.get("type") != "POWER_ON":
                continue
            if e.get("end"):
                day = e["end"][:10]
            else:
                t = simtime.localtime(e["_received"])
                day = "%04d-%02d-%02d" % (t[0], t[1], t[2])
            out[day] = out.get(day, 0) + (e.get("duration_sec") or 0)
        return out
