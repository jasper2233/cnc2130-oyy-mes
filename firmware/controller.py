"""Holat mashinasi va o'chirish mantig'i (TZ 4-bo'lim).

Bu modul aloqaga bog'liq emas. Kompyuter o'chiq bo'lsa ham to'liq ishlaydi.

Hamma vaqtlar monoton hisoblagich (now_ms, Pico yoqilgandan beri) bilan
o'lchanadi. Kun vaqti (soat) kerak emas: hodisaga aniq vaqtni jurnal
qo'yadi, soat noma'lum bo'lsa faqat davomiylik saqlanadi.
"""

import time
from machine import Pin

import config
from clock import iso_now, time_is_sane

# Holatlar (TZ 4.1)
OFF = "OFF"
WORKING = "WORKING"
IDLE_HOME = "IDLE_HOME"
IDLE_AWAY = "IDLE_AWAY"
PLANNED_STOP = "PLANNED_STOP"
SHUTDOWN = "SHUTDOWN"
BLOCKED = "BLOCKED"

# Rejalashtirilgan to'xtash qayta yuklangandan keyin shundan uzoq bo'lsa
# tiklanmaydi (soat noto'g'ri bo'lishi mumkin)
PLANNED_RESTORE_MAX_S = 24 * 3600


class DebouncedInput:
    """100 ms debounce (TZ 4.4). value: 1 = signal bor."""

    def __init__(self, pin_no, active_low=True, debounce_ms=config.DEBOUNCE_MS):
        pull = Pin.PULL_UP if active_low else None
        self.pin = Pin(pin_no, Pin.IN, pull)
        self.active_low = active_low
        self.debounce_ms = debounce_ms
        self.value = self._raw()
        self._candidate = self.value
        self._since = time.ticks_ms()
        self.rose = False
        self.fell = False

    def _raw(self):
        v = self.pin.value()
        return (1 - v) if self.active_low else v

    def update(self):
        self.rose = False
        self.fell = False
        raw = self._raw()
        now = time.ticks_ms()
        if raw != self._candidate:
            self._candidate = raw
            self._since = now
            return
        if raw != self.value and time.ticks_diff(now, self._since) >= self.debounce_ms:
            self.value = raw
            self.rose = (raw == 1)
            self.fell = (raw == 0)


class Controller:
    def __init__(self, store, log, rtc):
        self.store = store
        self.log = log
        self.rtc = rtc
        log.clock = self             # jurnal vaqtni shu hisoblagichdan oladi

        # Pin darhol "rele bo'sh" darajasida yaratiladi. Aks holda Pin.OUT
        # bir lahza 0 beradi va aktiv-LOW rele chertib qo'yadi.
        self.relay = Pin(config.PIN_RELAY, Pin.OUT, value=self._relay_level(False))
        self._relay_off()

        al = config.INPUT_ACTIVE_LOW
        self.pwr = DebouncedInput(config.PIN_PWR, al)
        self.run = DebouncedInput(config.PIN_RUN, al)
        self.home = DebouncedInput(config.PIN_HOME, al)
        self.maint = None
        if config.PIN_MAINT is not None:
            self.maint = DebouncedInput(config.PIN_MAINT, config.MAINT_ACTIVE_LOW)

        # Monoton soat (ms). ticks_ms ~12 kunda aylanadi, ticks_diff esa
        # 6 kundan uzun oraliqni noto'g'ri hisoblaydi. Bu hisoblagich aylanmaydi.
        self._last_ticks = time.ticks_ms()
        self.now_ms = 0

        self.state = OFF
        self.time_source = "none"    # none | pc | rtc

        # t_merge filtridan o'tgan RUN signali (TZ 6.2)
        self.run_stable = bool(self.run.value)
        self._fall_at = None
        # RUN haqiqatda tushgan payt (t_merge kechikishisiz)
        self._fell_ms = None

        # Bo'sh turish davri
        self._idle_start_ms = None
        self._idle_had_job = False
        self._idle_reported = False

        # Rejalashtirilgan to'xtash (TZ 8), qayta yuklanishda tiklanadi
        self.planned = None
        self._planned_untimed = False
        self.restore_planned()
        self.blocked = bool(store.data.get("blocked"))

        # O'chirish
        self._shutdown_at = None
        self._shutdown_failed = False
        self._expected_off = False

        # Tok nazorati
        self._pwr_off_ms = None
        # Yoqiq davr (POWER_ON hodisasi): stanok qancha yoqiq turdi,
        # shundan shpindel qancha aylandi. Pico stanok yoqiq paytda yoqilsa,
        # davr Pico yoqilgan paytdan sanaladi.
        self._on_ms = 0 if self.pwr.value else None
        self._on_from_boot = bool(self.pwr.value)
        self._run_ms = 0

    # ---------- rele ----------

    def _relay_level(self, active):
        if config.RELAY_ACTIVE_LOW:
            return 0 if active else 1
        return 1 if active else 0

    def _relay_on(self):
        self.relay.value(self._relay_level(True))

    def _relay_off(self):
        self.relay.value(self._relay_level(False))

    @property
    def relay_active(self):
        return self.relay.value() == self._relay_level(True)

    # ---------- yordamchi ----------

    def _clock(self):
        """now_ms ni yangilaydi, oxirgi chaqiruvdan beri o'tgan ms ni qaytaradi."""
        now = time.ticks_ms()
        dt = time.ticks_diff(now, self._last_ticks)
        self.now_ms += dt
        self._last_ticks = now
        return dt

    def _elapsed(self, start_ms):
        if start_ms is None:
            return 0
        return (self.now_ms - start_ms) // 1000

    @property
    def idle_sec(self):
        return self._elapsed(self._idle_start_ms)

    @property
    def maint_on(self):
        return bool(self.maint is not None and self.maint.value)

    # ---------- tashqi buyruqlar ----------

    def set_planned_stop(self, stop_type, planned_sec):
        """Operator paneldan rejalashtirilgan to'xtash belgiladi (TZ 8)."""
        if isinstance(planned_sec, bool) or not isinstance(planned_sec, int) \
                or planned_sec < 0:
            planned_sec = 0
        if self.planned:
            self.clear_planned_stop()    # oldingisi yo'qolib ketmasin
        self._clock()
        self.planned = {
            "type": stop_type,
            "planned_sec": planned_sec,
            "start_ms": self.now_ms,
        }
        self._planned_untimed = False
        self.store.data["planned"] = {
            "type": stop_type,
            "planned_sec": planned_sec,
            # Qayta yuklanishdan keyin davomiylikni tiklash uchun
            "start_ts": int(time.time()) if time_is_sane() else None,
        }
        self.store.save()

    def restore_planned(self):
        p = self.store.data.get("planned")
        if not isinstance(p, dict):
            return
        elapsed = 0
        # Soat hali kelmagan bo'lsa, kompyuter vaqti kelganda qayta hisoblanadi
        self._planned_untimed = True
        if time_is_sane() and isinstance(p.get("start_ts"), int):
            e = int(time.time()) - p["start_ts"]
            if 0 <= e <= PLANNED_RESTORE_MAX_S:
                elapsed = e
                self._planned_untimed = False
        self.planned = {
            "type": p.get("type"),
            "planned_sec": p.get("planned_sec", 0),
            "start_ms": self.now_ms - elapsed * 1000,
        }
        print("rejalashtirilgan to'xtash tiklandi:", p.get("type"))

    def on_time_synced(self):
        """Kompyuterdan (yoki DS3231 dan) vaqt keldi."""
        p = self.store.data.get("planned")
        if not self.planned or not isinstance(p, dict):
            return
        self._clock()
        st = p.get("start_ts")
        if self._planned_untimed and isinstance(st, int):
            # Qayta yuklanishgacha o'tgan vaqt ham hisobga olinadi
            e = int(time.time()) - st
            if 0 <= e <= PLANNED_RESTORE_MAX_S \
                    and e * 1000 > self.now_ms - self.planned["start_ms"]:
                self.planned["start_ms"] = self.now_ms - e * 1000
        elif not isinstance(st, int):
            p["start_ts"] = int(time.time()) - self._elapsed(self.planned["start_ms"])
            self.store.save()
        self._planned_untimed = False

    def clear_planned_stop(self):
        if not self.planned:
            return
        self._clock()
        p = self.planned
        self.planned = None
        self.store.data["planned"] = None
        elapsed = self._elapsed(p["start_ms"])
        self.log.append("PLANNED_STOP", p["start_ms"], self.now_ms, elapsed,
                        reason_required=False, extra={"stop_type": p["type"]})
        # Belgilangan vaqtdan oshgan qismi uchun sabab so'raladi (TZ 8.4)
        excess = elapsed - p["planned_sec"]
        if excess > 0:
            self.log.append("SPINDLE_IDLE", self.now_ms - excess * 1000, self.now_ms,
                            excess, reason_required=True,
                            extra={"note": "rejalashtirilgan vaqtdan oshdi",
                                   "stop_type": p["type"]})

    def set_blocked(self, blocked):
        """Smena qabul qilinmagan holat (TZ 10.3.2)."""
        blocked = bool(blocked)
        if blocked != self.blocked:
            self.blocked = blocked
            self.store.data["blocked"] = blocked
            self.store.save()

    # ---------- asosiy sikl ----------

    def tick(self):
        dt = self._clock()
        self.pwr.update()
        self.run.update()
        self.home.update()
        if self.pwr.value and self.run.value:
            self._run_ms += dt
        if self.maint is not None:
            self.maint.update()
            if self.maint.fell:
                # Ta'mirlash tugadi: taymer noldan boshlanadi, aks holda
                # kalit burilishi bilan stanok darhol o'chib qolardi
                self._close_idle()

        self._track_power()
        self._filter_run()

        if self.state == SHUTDOWN:
            self._tick_shutdown()
            return

        if self.pwr.value == 0:
            self.state = OFF
            self._close_idle()
            self._clear_fall()
            return

        if self.run_stable:
            self._close_idle()
            self._shutdown_failed = False
            self.state = WORKING
            return

        # Rejalashtirilgan to'xtash yoki blok boshlanganda joriy bo'sh turish
        # davri yopiladi. Aks holda taymer tushlik paytida ham sanab,
        # tushlik tugashi bilan stanok darhol o'chib qolardi.
        if self.planned:
            self._close_idle()
            self._clear_fall()
            self.state = PLANNED_STOP
            return

        if self.blocked:
            self._close_idle()
            self._clear_fall()
            self.state = BLOCKED
            return

        self._open_idle()
        self.state = IDLE_HOME if self.home.value else IDLE_AWAY
        self._check_timers()

    # ---------- tok nazorati ----------

    def _track_power(self):
        if self.pwr.fell:
            self._pwr_off_ms = self.now_ms
            if self._shutdown_at is not None:
                # Biz o'chirdik, kutilgan holat (ichida bo'sh turish yopiladi)
                self._finish_shutdown(True)
            else:
                self._close_idle()
            self._close_on_period()
        elif self.pwr.rose:
            off_sec = self._elapsed(self._pwr_off_ms)
            self._shutdown_failed = False
            if self._expected_off:
                # AUTO_SHUTDOWN dan keyingi yonish, alohida hodisa emas
                self._expected_off = False
            elif self._pwr_off_ms is not None:
                self.log.append("POWER_CYCLE", self._pwr_off_ms, self.now_ms,
                                off_sec, reason_required=True)
            self._pwr_off_ms = None
            self._on_ms = self.now_ms
            self._on_from_boot = False
            self._run_ms = 0
            self._relay_off()

    def _close_on_period(self):
        """Stanok o'chdi: shuncha vaqt yoqiq turdi (kun vaqti shart emas)."""
        if self._on_ms is None:
            return
        extra = {"run_sec": self._run_ms // 1000}
        if self._on_from_boot:
            extra["from_boot"] = True      # boshlanishi Pico yoqilgan payt
        self.log.append("POWER_ON", self._on_ms, self.now_ms,
                        self._elapsed(self._on_ms), reason_required=False,
                        extra=extra)
        self._on_ms = None
        self._on_from_boot = False
        self._run_ms = 0

    # ---------- RUN filtri (TZ 6.2) ----------

    def _filter_run(self):
        if self.run.value == 1:
            self._fall_at = None
            self.run_stable = True
            return
        if not self.run_stable:
            return
        if self._fall_at is None:
            self._fall_at = self.now_ms
        elapsed = self.now_ms - self._fall_at
        if elapsed >= self.store.cfg("t_merge") * 1000:
            self.run_stable = False
            self._fell_ms = self._fall_at
            self._fall_at = None

    def _clear_fall(self):
        self._fell_ms = None

    # ---------- bo'sh turish davri ----------

    def _open_idle(self):
        if self._idle_start_ms is not None:
            return
        if self._fell_ms is not None:
            # Ishdan to'xtadi: boshlanish RUN tushgan payt (t_merge kechikishsiz)
            self._idle_start_ms = self._fell_ms
        else:
            # Yoqilgandan, tushlikdan yoki blokdan keyin - hozirdan boshlanadi
            self._idle_start_ms = self.now_ms
        self._clear_fall()
        self._idle_had_job = self.store.data.get("job") is not None
        self._idle_reported = False

    def _close_idle(self):
        if self._idle_start_ms is None:
            return
        duration = self._elapsed(self._idle_start_ms)
        start_ms = self._idle_start_ms
        had_job = self._idle_had_job
        reported = self._idle_reported

        self._idle_start_ms = None
        self._idle_had_job = False
        self._idle_reported = False

        # AUTO_SHUTDOWN allaqachon yozilgan bo'lsa, takror hodisa yaratilmaydi.
        # Aks holda operatordan bitta narsa uchun ikki marta sabab so'raladi.
        if reported:
            return
        if duration < self.store.cfg("t_reason"):
            return
        ev_type = "UNPLANNED_STOP" if had_job else "SPINDLE_IDLE"
        self.log.append(ev_type, start_ms, self.now_ms, duration,
                        reason_required=True)

    # ---------- taymerlar ----------

    def _check_timers(self):
        if not self.store.cfg("auto_shutdown"):
            return
        if self.maint_on:
            return      # TZ 4.4 - ta'mirlash kaliti o'chirishni bloklaydi
        if self._shutdown_failed:
            return      # bir marta urinildi, holat o'zgarguncha qayta urinilmaydi
        limit = self.store.cfg("t1") if self.home.value else self.store.cfg("t2")
        if self.idle_sec >= limit:
            self._start_shutdown(limit)

    # ---------- o'chirish (TZ 4.3) ----------

    def _start_shutdown(self, limit):
        if self.run_stable or self.run.value or self.pwr.value == 0:
            return  # TZ 4.3.4 - shpindel aylanayotganda hech qachon
        self.state = SHUTDOWN
        self._shutdown_at = self.now_ms
        self._relay_on()
        self.log.append("AUTO_SHUTDOWN", self._idle_start_ms, self.now_ms,
                        self.idle_sec, reason_required=True,
                        extra={"limit_sec": limit,
                               "at_home": bool(self.home.value),
                               "had_job": self._idle_had_job})
        self._idle_reported = True

    def _tick_shutdown(self):
        """Rele ushlab turiladi (TZ 4.3.1). Bo'shatish shartlari: stanok o'chdi,
        shpindel yoqildi yoki t_relay vaqti tugadi."""
        if self.pwr.value == 0:
            self._finish_shutdown(True)
            return
        if self.run.value == 1:
            # Operator rele ushlab turilgan paytda shpindelni yoqdi.
            # TZ 4.3.4: shpindel aylanayotganda o'chirish davom etmaydi.
            self._finish_shutdown(False, cause="run_started")
            return
        if self._elapsed(self._shutdown_at) >= self.store.cfg("t_relay"):
            self._finish_shutdown(False, cause="pwr_still_on")

    def _finish_shutdown(self, ok, cause=None):
        self._relay_off()          # TZ 4.3.2 - rele abadiy yopiq qolmaydi
        self._shutdown_at = None
        self._close_idle()
        if ok:
            self._expected_off = True
            self.state = OFF
            return
        self._shutdown_failed = True
        self.log.append("SHUTDOWN_FAILED", self.now_ms, self.now_ms, 0,
                        reason_required=(cause != "run_started"),
                        extra={"cause": cause})
        self.state = WORKING if self.run.value else IDLE_AWAY

    # ---------- holat xabari (TZ 15.2) ----------

    def snapshot(self):
        return {
            "machine": config.MACHINE_ID,
            "state": self.state,
            "pwr": self.pwr.value,
            "run": self.run.value,
            "home": self.home.value,
            "maint": 1 if self.maint_on else 0,
            "relay": 1 if self.relay_active else 0,
            "idle_sec": self.idle_sec,
            "on_sec": self._elapsed(self._on_ms) if self._on_ms is not None else 0,
            "run_sec": self._run_ms // 1000,
            "job": self.store.data.get("job"),
            "operator": self.store.data.get("operator"),
            "planned": self.planned["type"] if self.planned else None,
            "planned_sec": self._elapsed(self.planned["start_ms"]) if self.planned else 0,
            "blocked": self.blocked,
            "t1": self.store.cfg("t1"),
            "t2": self.store.cfg("t2"),
            "t_reason": self.store.cfg("t_reason"),
            "t_merge": self.store.cfg("t_merge"),
            "t_relay": self.store.cfg("t_relay"),
            "auto_shutdown": self.store.cfg("auto_shutdown"),
            # O'chirish nega bloklangani (panel shuni yozadi)
            "blocked_by": ("maint" if self.maint_on else
                           "config" if not self.store.cfg("auto_shutdown") else
                           "failed" if self._shutdown_failed else None),
            "seq": self.store.data["seq"],
            "last_ack": self.store.data["last_ack"],
            "log_full": self.log.full,
            "ts": iso_now() if time_is_sane() else None,
            "time_source": self.time_source,
            "uptime_sec": self.now_ms // 1000,
            "boot": self.store.data.get("boot", 0),
        }
