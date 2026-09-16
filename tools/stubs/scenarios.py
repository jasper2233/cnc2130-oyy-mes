"""Sinov ssenariylari.

CPython da (tools/simulate.py) va Pico ning o'zida (tools/pico_test.py)
bir xil ishlaydi. Import qilishdan oldin simenv.install() chaqirilgan bo'lishi shart.
"""

import json
import os

import config
import simtime
import fakeusb
from fakemachine import PINS, PIN_LOG, WDT_STATE

import controller as ctl
from eventlog import EventLog, Store
import main as app_main
from mesmock import MES

CLOCK = simtime.CLOCK
USB = fakeusb.USB

TEST_CONFIG = {"t1": 60, "t2": 90, "t_reason": 30, "t_merge": 5,
               "t_relay": 30, "auto_shutdown": True}
# 2026-09-14 08:00:00 - kompyuter soati va soat to'g'ri bo'lgan holat uchun
REAL_EPOCH = simtime.mktime((2026, 9, 14, 8, 0, 0))

_SAVED = {}
VERBOSE = True


def say(*a):
    if VERBOSE:
        print(*a)


# ---------- yordamchi funksiyalar ----------

def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def reset_env(real_clock=False):
    if not _SAVED:
        for k in ("LOG_MAX_BYTES", "LOG_COMPACT_MIN", "NO_WDT_FLAG"):
            _SAVED[k] = getattr(config, k)
    for k, v in _SAVED.items():
        setattr(config, k, v)
    config.NO_WDT_FLAG = "/__sinovda_yoq__"
    config.DEFAULT_CONFIG = dict(TEST_CONFIG)
    PINS.clear()
    del PIN_LOG[:]
    CLOCK.reset(base=REAL_EPOCH if real_clock else simtime.PICO_BOOT_EPOCH)
    USB.reset()
    for p in (config.LOG_PATH, config.STATE_PATH):
        _rm(p)
        _rm(p + ".tmp")


def signal(pin, on):
    """Optopara mantig'i: signal bor bo'lsa pin LOW."""
    PINS[pin] = (0 if on else 1) if config.INPUT_ACTIVE_LOW else (1 if on else 0)


def maint_key(on):
    PINS[config.PIN_MAINT] = (0 if on else 1) if config.MAINT_ACTIVE_LOW else (1 if on else 0)


def relay_on():
    v = PINS.get(config.PIN_RELAY, 1)
    return v == 0 if config.RELAY_ACTIVE_LOW else v == 1


def relay_activations():
    active = 0 if config.RELAY_ACTIVE_LOW else 1
    n = 0
    prev = None
    for pin, v, _ in PIN_LOG:
        if pin != config.PIN_RELAY:
            continue
        if v == active and prev != active:
            n += 1
        prev = v
    return n


def advance(obj, seconds, step_ms=100):
    """Vaqtni surib, har qadamda tick/step chaqiradi. Holat o'zgarishini bosadi.

    Holat o'zgargan vaqtlar ro'yxatini qaytaradi: [(soniya, eski, yangi)].
    """
    ctrl = obj.ctrl if hasattr(obj, "ctrl") else obj
    run = obj.step if hasattr(obj, "step") else obj.tick
    steps = int(seconds * 1000 // step_ms)
    prev = ctrl.state
    changes = []
    for _ in range(steps):
        CLOCK.advance(step_ms)
        run()
        if ctrl.state != prev:
            t = CLOCK.now_ms / 1000
            say("    [%8.1fs] %-13s -> %-13s  rele=%s"
                % (t, prev, ctrl.state, "YONIQ" if relay_on() else "o'chiq"))
            changes.append((t, prev, ctrl.state))
            prev = ctrl.state
    return changes


def new_controller():
    store = Store()
    log = EventLog(store)
    ctrl = ctl.Controller(store, log, rtc=None)
    return store, log, ctrl


def types_of(log):
    return [e["type"] for e in log.pending(limit=500)]


def main_events(log):
    """POWER_ON (yoqiq davr) dan tashqari hodisalar."""
    return [e for e in log.pending(limit=500) if e["type"] != "POWER_ON"]


def pc_now():
    """Kompyuter soati - Pico soatidan mustaqil, doim to'g'ri."""
    return REAL_EPOCH + CLOCK.now_ms // 1000


def pico_power_loss():
    """Pico toki uzildi: ichki soat 2021-yilga qaytadi."""
    CLOCK.base = simtime.PICO_BOOT_EPOCH - CLOCK.now_ms // 1000


def show_events(log):
    evs = log.pending(limit=100)
    say("    --- jurnal: %d ta hodisa ---" % len(evs))
    for e in evs:
        say("    %-16s %7ss  sabab=%s  %s"
            % (e["type"], e["duration_sec"], e["reason_required"], e["id"]))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------- 1-6: asosiy mantiq ----------

def s01_normal():
    """Normal ish, keyin 0 nuqtada bo'sh turish -> t1 da o'chadi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 20)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, True)
    advance(ctrl, 75)
    check(ctrl.state == ctl.SHUTDOWN, "SHUTDOWN kutilgandi: %s" % ctrl.state)
    check(relay_on(), "rele yonishi kerak edi")
    signal(config.PIN_PWR, False)
    advance(ctrl, 3)
    check(not relay_on(), "rele bo'shatilishi kerak edi")
    check(ctrl.state == ctl.OFF, ctrl.state)
    show_events(log)
    ev = main_events(log)
    check(len(ev) == 1 and ev[0]["type"] == "AUTO_SHUTDOWN", types_of(log))
    check(ev[0]["duration_sec"] == 60, "davomiylik 60 s bo'lishi kerak: %s" % ev[0]["duration_sec"])


def s02_merge():
    """t_merge filtri: 3 soniyalik uzilish hodisa yaratmaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 10)
    signal(config.PIN_RUN, False)
    advance(ctrl, 3)
    signal(config.PIN_RUN, True)
    advance(ctrl, 10)
    check(ctrl.state == ctl.WORKING, ctrl.state)
    check(len(log.pending(10)) == 0, "hodisa yaratilmasligi kerak edi")


def s03_away():
    """0 nuqtadan tashqarida uzoq to'xtash (t2=90s), bitta hodisa"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    store.data["job"] = "ORD-2026-0417"
    advance(ctrl, 10)
    signal(config.PIN_RUN, False)
    advance(ctrl, 100)
    check(ctrl.state == ctl.SHUTDOWN, ctrl.state)
    signal(config.PIN_PWR, False)
    advance(ctrl, 3)
    show_events(log)
    evs = main_events(log)
    check(len(evs) == 1, "bitta davr uchun bitta hodisa: %s" % types_of(log))
    check(evs[0]["type"] == "AUTO_SHUTDOWN" and evs[0]["had_job"] is True, evs[0])


def s04_planned():
    """Rejalashtirilgan to'xtash: o'chirish bloklanadi, ortiqcha vaqtga sabab"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 10)
    ctrl.set_planned_stop("LUNCH", 60)
    signal(config.PIN_RUN, False)
    advance(ctrl, 120)
    check(ctrl.state == ctl.PLANNED_STOP, ctrl.state)
    check(not relay_on(), "rejalashtirilgan to'xtashda o'chmasligi kerak")
    ctrl.clear_planned_stop()
    advance(ctrl, 1)
    show_events(log)
    t = types_of(log)
    check("PLANNED_STOP" in t and "SPINDLE_IDLE" in t, t)


def s05_power_cycle():
    """Kutilmagan tok uzilishi -> POWER_CYCLE"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 10)
    signal(config.PIN_PWR, False)
    signal(config.PIN_RUN, False)
    advance(ctrl, 20)
    signal(config.PIN_PWR, True)
    advance(ctrl, 5)
    evs = main_events(log)
    check(len(evs) == 1 and evs[0]["type"] == "POWER_CYCLE", types_of(log))
    check(19 <= evs[0]["duration_sec"] <= 21, evs[0]["duration_sec"])


def s06_spindle_guard():
    """Shpindel aylanayotganda hech qachon o'chirilmaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, True)
    store, log, ctrl = new_controller()
    advance(ctrl, 200)
    check(ctrl.state == ctl.WORKING, ctrl.state)
    check(relay_activations() == 0, "ishlayotganda rele yonmasligi kerak")


# ---------- 7-17: topilgan xatolar uchun ----------

def s07_planned_during_idle():
    """[TUZATISH #2] Bo'sh turgan paytda tushlik: tushlikdan keyin darhol o'chmaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 10)
    signal(config.PIN_RUN, False)
    advance(ctrl, 50)                   # 50 s bo'sh (t2=90)
    ctrl.set_planned_stop("LUNCH", 600)
    advance(ctrl, 300)                  # 5 daqiqa tushlik
    ctrl.clear_planned_stop()
    t_end = CLOCK.now_ms / 1000
    ch = advance(ctrl, 30)
    check(ctrl.state == ctl.IDLE_AWAY, "tushlikdan keyin darhol o'chdi: %s" % ctrl.state)
    ch += advance(ctrl, 70)
    sd = [c[0] for c in ch if c[2] == ctl.SHUTDOWN]
    check(sd, "t2 dan keyin o'chishi kerak edi")
    check(sd[0] - t_end >= 90, "t2 to'liq kutilmadi: %.1f s" % (sd[0] - t_end))
    show_events(log)
    t = types_of(log)
    check(t[0] == "SPINDLE_IDLE" and "PLANNED_STOP" in t, t)


def s08_shutdown_failed_once():
    """[TUZATISH #5] SHUTDOWN_FAILED dan keyin rele qayta-qayta yonmaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, True)
    store, log, ctrl = new_controller()
    advance(ctrl, 70)
    check(ctrl.state == ctl.SHUTDOWN, ctrl.state)
    advance(ctrl, 35)                   # stanok o'chmadi
    check(not relay_on(), "rele bo'shatilishi kerak")
    advance(ctrl, 600)                  # 10 daqiqa
    check(relay_activations() == 1, "rele %d marta yondi" % relay_activations())
    check(types_of(log).count("SHUTDOWN_FAILED") == 1, types_of(log))
    check(ctrl.snapshot()["blocked_by"] == "failed", "panelga sabab yuborilmadi")
    # Ish qayta boshlansa, o'chirish yana ruxsat etiladi
    signal(config.PIN_RUN, True)
    advance(ctrl, 10)
    signal(config.PIN_RUN, False)
    advance(ctrl, 70)
    check(ctrl.state == ctl.SHUTDOWN, "ishdan keyin yana o'chishi kerak: %s" % ctrl.state)
    show_events(log)


def s09_no_backdate_after_power_on():
    """[TUZATISH #6] Yoqilgandan keyin t2 to'liq kutiladi (t_merge ayirilmaydi)"""
    signal(config.PIN_PWR, False)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 5)
    signal(config.PIN_PWR, True)
    ch = advance(ctrl, 100)
    on = [c[0] for c in ch if c[1] == ctl.OFF][0]
    sd = [c[0] for c in ch if c[2] == ctl.SHUTDOWN]
    check(sd, "o'chishi kerak edi")
    check(sd[0] - on >= 90, "t2=90 o'rniga %.1f s da o'chdi" % (sd[0] - on))


def s10_maint_key():
    """[TUZATISH #7] Ta'mirlash kaliti o'chirishni bloklaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, True)
    maint_key(True)
    store, log, ctrl = new_controller()
    advance(ctrl, 200)
    check(ctrl.state == ctl.IDLE_HOME and relay_activations() == 0,
          "kalit yoqiqligida o'chmasligi kerak: %s" % ctrl.state)
    check(ctrl.snapshot()["maint"] == 1, "snapshot da maint=1")
    check(ctrl.snapshot()["blocked_by"] == "maint", "panelga sabab yuborilmadi")
    maint_key(False)
    t_off = CLOCK.now_ms / 1000
    ch = advance(ctrl, 70)
    sd = [c[0] for c in ch if c[2] == ctl.SHUTDOWN]
    check(sd and sd[0] - t_off >= 59, "kalit o'chgach t1 qaytadan sanalishi kerak: %s" % sd)
    show_events(log)


def s11_run_during_shutdown():
    """[YANGI] Rele ushlab turilganda shpindel yoqilsa rele darhol bo'shaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, True)
    store, log, ctrl = new_controller()
    advance(ctrl, 70)
    check(ctrl.state == ctl.SHUTDOWN, ctrl.state)
    signal(config.PIN_RUN, True)
    advance(ctrl, 0.3)
    check(not relay_on(), "shpindel aylanayotganda rele ushlab turildi (TZ 4.3.4)")
    advance(ctrl, 1)
    check(ctrl.state == ctl.WORKING, ctrl.state)
    ev = [e for e in log.pending(10) if e["type"] == "SHUTDOWN_FAILED"]
    check(ev and ev[0]["cause"] == "run_started", types_of(log))


def s12_config_validation():
    """[YANGI] MES dan noto'g'ri sozlama kelsa rad etiladi, Pico yiqilmaydi"""
    store, log, ctrl = new_controller()
    changed, rejected = store.update_config(
        {"t1": "abc", "t2": -5, "t_merge": True, "t_reason": 45,
         "auto_shutdown": "yes", "nomalum": 1})
    check(changed, "t_reason qabul qilinishi kerak edi")
    check(sorted(rejected) == ["auto_shutdown", "t1", "t2", "t_merge"], rejected)
    check(store.cfg("t_reason") == 45 and store.cfg("t1") == 60, store.data["config"])
    # Fleshda buzuq sozlama bo'lsa ham yuklanganda tozalanadi
    with open(config.STATE_PATH, "w") as f:
        json.dump({"seq": 0, "config": {"t1": "buzuq", "t_merge": None}}, f)
    store2 = Store()
    check(store2.cfg("t1") == 60 and store2.cfg("t_merge") == 5, store2.data["config"])
    signal(config.PIN_PWR, True)
    ctrl2 = ctl.Controller(store2, EventLog(store2), None)
    advance(ctrl2, 2)                   # yiqilmasligi kerak


def s13_log_compaction():
    """[TUZATISH #3] Jurnal to'lganda siqish takrorlanmaydi, LOG_FULL bir marta"""
    from eventlog import encode_row
    one = len(encode_row(10, "TEST", "2026-09-14T08:00:00", None, 10, None, None, 0, 0))
    config.LOG_MAX_BYTES = 26 * one      # ~26 ta yozuv sig'adi
    config.LOG_COMPACT_MIN = 5
    store, log, ctrl = new_controller()
    runs = [0]
    orig = log.compact

    def counting():
        runs[0] += 1
        orig()
    log.compact = counting

    # 3 ta tasdiqlangan, keyin aloqa uzildi va 40 ta tasdiqlanmagan keldi.
    # Eski kod last_ack > 0 bo'lgani uchun har yangi yozuvda faylni qayta yozardi.
    for i in range(3):
        log.append("TEST", None, None, i)
    log.ack(3)
    for i in range(40):
        log.append("TEST", None, None, i)
    check(runs[0] == 1, "siqish %d marta ishladi (1 kutilgan)" % runs[0])
    check(len(log.pending(500)) == 40, "tasdiqlanmagan yozuv yo'qoldi")
    check(log.check_full() is True, "to'lgan deb belgilanishi kerak")
    check(log.check_full() is False, "LOG_FULL faqat bir marta")
    log.ack(38)
    log.append("TEST", None, None, 44)
    check(runs[0] == 2, "tasdiqdan keyin bir marta siqilishi kerak: %d" % runs[0])
    log.append("TEST", None, None, 45)
    check(runs[0] == 2, "qayta siqilmasligi kerak: %d" % runs[0])
    p = [e["seq"] for e in log.pending(500)]
    check(p == [39, 40, 41, 42, 43, 44, 45], p)
    check(log.check_full() is False, "siqilgandan keyin to'la emas")
    say("    45 yozuv -> 2 marta siqildi, 7 ta tasdiqlanmagan qoldi")


def s14_seq_recovery():
    """[YANGI] state.json yo'qolsa seq jurnaldan tiklanadi (ID takrorlanmaydi)"""
    store, log, ctrl = new_controller()
    for i in range(3):
        log.append("TEST", None, None)
    _rm(config.STATE_PATH)
    store2 = Store()
    log2 = EventLog(store2)
    ev = log2.append("TEST", None, None)
    check(ev["seq"] == 4, "seq 4 bo'lishi kerak edi: %s" % ev["seq"])
    # Tok save() o'rtasida ketgan: faqat .tmp qolgan
    os.rename(config.STATE_PATH, config.STATE_PATH + ".tmp")
    store3 = Store()
    check(store3.data["seq"] == 4, "state .tmp dan tiklanishi kerak: %s" % store3.data["seq"])


def s15_persist_planned_blocked():
    """[YANGI] Rejalashtirilgan to'xtash va blok qayta yuklanishda saqlanadi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    ctrl.set_planned_stop("LUNCH", 600)
    advance(ctrl, 120)
    store2, log2, ctrl2 = new_controller()      # Pico qayta yuklandi
    check(ctrl2.planned and ctrl2.planned["type"] == "LUNCH", "tushlik yo'qoldi")
    advance(ctrl2, 200)
    check(ctrl2.state == ctl.PLANNED_STOP and relay_activations() == 0, ctrl2.state)
    ctrl2.clear_planned_stop()
    ev = [e for e in log2.pending(10) if e["type"] == "PLANNED_STOP"][0]
    check(315 <= ev["duration_sec"] <= 325, "davomiylik ~320: %s" % ev["duration_sec"])
    ctrl2.set_blocked(True)
    store3, log3, ctrl3 = new_controller()
    check(ctrl3.blocked, "blok yo'qoldi")


def s16_relay_no_boot_pulse():
    """[YANGI] Yuklanishda rele bir lahza ham yonmaydi"""
    store, log, ctrl = new_controller()
    entries = [v for p, v, _ in PIN_LOG if p == config.PIN_RELAY]
    check(entries and relay_activations() == 0, "rele pini: %s" % entries)


def s17_long_off_ticks_wrap():
    """[YANGI] Stanok 14 kun o'chiq turdi (ticks_ms aylanadi) - vaqt to'g'ri"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 5)
    signal(config.PIN_PWR, False)
    signal(config.PIN_RUN, False)
    advance(ctrl, 14 * 86400, step_ms=60_000)
    signal(config.PIN_PWR, True)
    advance(ctrl, 1)
    evs = main_events(log)
    check(evs and evs[0]["type"] == "POWER_CYCLE", types_of(log))
    check(abs(evs[0]["duration_sec"] - 14 * 86400) <= 120, evs[0]["duration_sec"])
    say("    14 kun = %s s" % evs[0]["duration_sec"])


# ---------- 18-25: kompyuter bilan micro USB aloqa ----------

class Rig:
    """Pico (App) + kompyuterdagi MES sahifasi."""

    def __init__(self):
        self.app = app_main.App(use_wdt=True, port=fakeusb.Port())
        self.mes = MES(USB, config.MACHINE_ID, now_fn=pc_now)
        self.ctrl = self.app.ctrl

    def step(self):
        self.app.step()
        self.mes.poll()


def mes_types(mes):
    return [e["type"] for e in mes.event_list()]


def mes_event(mes, ev_type):
    ev = [e for e in mes.event_list() if e["type"] == ev_type]
    check(ev, "%s MES ga yetmadi: %s" % (ev_type, mes_types(mes)))
    return ev[-1]


def iso_pc(offset_s=0):
    t = simtime.localtime(pc_now() + offset_s)
    return "%04d-%02d-%02dT%02d:%02d:%02d" % tuple(t[:6])


def s18_usb_link():
    """[USB] Kompyuter vaqti keladi, hodisalar yuboriladi va tasdiqlanadi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 5)
    check(rig.app.link.connected, "aloqa o'rnatilishi kerak")
    check(rig.ctrl.time_source == "pc", "vaqt kompyuterdan: %s" % rig.ctrl.time_source)
    check(rig.mes.hello_count == 1 and rig.mes.state_count >= 1, "hello/state kelmadi")
    # Signal o'zgarsa holat xabari 10 s kutmasdan darhol kelishi kerak
    n0 = rig.mes.state_count
    signal(config.PIN_HOME, True)
    advance(rig, 1)
    check(rig.mes.state_count > n0 and rig.mes.last_state["home"] == 1,
          "signal o'zgarganda holat darhol yuborilmadi (%d -> %d)" % (n0, rig.mes.state_count))
    signal(config.PIN_HOME, False)
    advance(rig, 1)
    check(rig.mes.last_state["home"] == 0, "0 nuqtadan chiqqani darhol bildirilmadi")
    signal(config.PIN_RUN, False)
    advance(rig, 60)                    # t_reason=30 dan uzun, t2=90 dan qisqa
    signal(config.PIN_RUN, True)
    advance(rig, 15)
    ev = mes_event(rig.mes, "SPINDLE_IDLE")
    check(ev["start"] and ev["time_uncertain"] is False, ev)
    check(ev["end"][:16] == iso_pc(-15)[:16], "vaqt kompyuter soatiga mos emas: %s" % ev["end"])
    check(rig.mes.acked == rig.app.store.data["seq"] and rig.app.log.pending_count() == 0,
          "hammasi tasdiqlanishi kerak")
    check(WDT_STATE["max_gap"] < 1000, "WDT %d ms" % WDT_STATE["max_gap"])


def s19_pc_off_counter():
    """[USB] Kompyuter o'chiq: Pico ishlaydi, yoqilgach aniq vaqt bilan yuboradi"""
    USB.set_pc(False)
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 10)
    check(not rig.app.link.connected and rig.ctrl.time_source == "none", "aloqa yo'q bo'lishi kerak")
    signal(config.PIN_RUN, False)
    advance(rig, 100)                   # t2=90 - avtomatik o'chirish
    check(rig.ctrl.state == ctl.SHUTDOWN, rig.ctrl.state)
    signal(config.PIN_PWR, False)
    t_off = pc_now()
    advance(rig, 3)
    check(USB.writes == 0, "kompyuter o'chiq paytda %d marta yozildi" % USB.writes)
    loc = [e for e in rig.app.log.pending(10) if e["type"] == "POWER_ON"]
    check(loc and loc[0]["start"] is None and loc[0]["time_uncertain"], "soatsiz yozilishi kerak")
    advance(rig, 600)                   # 10 daqiqadan keyin kompyuter yoqildi
    USB.set_pc(True)
    advance(rig, 15)
    t = mes_types(rig.mes)
    check("AUTO_SHUTDOWN" in t and "POWER_ON" in t, t)
    on = mes_event(rig.mes, "POWER_ON")
    check(on.get("time_restored") and on["time_uncertain"] is False, on)
    check(95 <= on["duration_sec"] <= 110 and 8 <= on["run_sec"] <= 16, on)
    end_ts = simtime.mktime(tuple(int(x) for x in (on["end"][0:4], on["end"][5:7], on["end"][8:10],
                                                   on["end"][11:13], on["end"][14:16], on["end"][17:19])))
    check(abs(end_ts - t_off) <= 3, "tiklangan vaqt %s s farq qiladi" % (end_ts - t_off))
    check("boot" not in on and "end_ms" not in on, "ichki maydonlar MES ga ketmasin")
    check(rig.app.log.pending_count() == 0, "hammasi tasdiqlanishi kerak")
    say("    POWER_ON: %s s yoqiq, shpindel %s s, vaqt tiklandi: %s" % (on["duration_sec"], on["run_sec"], on["end"]))


def s20_lost_event_ack_gap():
    """[TUZATISH #4] Saqlanmagan hodisadan keyingilar tasdiqlanmaydi, qayta yuboriladi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    rig = Rig()
    advance(rig, 12)
    base = rig.app.store.data["seq"]
    rig.mes.drop_next_event = 1
    for i in range(3):
        rig.app.log.append("TEST", None, None, i)
    rig.app.link.queue_events()
    advance(rig, 3)
    check(rig.mes.acked == base, "yo'qolgan yozuvdan keyingilar tasdiqlandi: %s" % rig.mes.acked)
    advance(rig, 25)
    check(rig.mes.acked == base + 3 and rig.app.log.pending_count() == 0,
          "qayta yuborilib tasdiqlanishi kerak: ack=%s" % rig.mes.acked)
    check(mes_types(rig.mes).count("TEST") == 3, "dublikat saqlandi")
    say("    dublikat qabul qilinmadi: %d marta" % rig.mes.duplicates)


def s21_stalled_browser():
    """[USB] Brauzer qotdi (port ochiq, o'qimaydi): WDT och qolmaydi, mantiq ishlaydi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 12)
    check(rig.app.link.connected, "ulanishi kerak")
    USB.stalled = True
    w0 = USB.writes
    # Yuborish navbati to'la bo'lsin - aks holda himoyasiz kod ham kam yozadi
    for i in range(20):
        rig.app.log.append("TEST", None, None, i)
    signal(config.PIN_RUN, False)
    advance(rig, 120)                   # t2=90 - o'chirish aloqasiz ham bo'ladi
    check("AUTO_SHUTDOWN" in types_of(rig.app.log), "mantiq to'xtab qoldi: %s" % rig.ctrl.state)
    check(WDT_STATE["max_gap"] < 1500, "WDT %d ms och qoldi" % WDT_STATE["max_gap"])
    check(not rig.app.link.connected, "30 s dan keyin aloqa yo'q deb bilishi kerak")
    check(USB.writes - w0 <= 10, "tanaffussiz %d marta yozishga urindi" % (USB.writes - w0))
    signal(config.PIN_PWR, False)
    advance(rig, 3)
    USB.stalled = False
    advance(rig, 20)
    check(rig.app.link.connected and "AUTO_SHUTDOWN" in mes_types(rig.mes), mes_types(rig.mes))
    check(rig.app.log.pending_count() == 0, "hammasi yetib borishi kerak")
    say("    qotgan paytda yozish urinishlari: %d, WDT eng uzun: %d ms"
        % (USB.writes - w0, WDT_STATE["max_gap"]))


def s22_bad_messages():
    """[YANGI] Kompyuterdan buzuq xabar kelsa aloqa uzilmaydi"""
    signal(config.PIN_PWR, True)
    rig = Rig()
    advance(rig, 12)
    mes = rig.mes
    mes.push_raw("{buzuq json")
    mes.push_raw("[1, 2, 3]")
    mes.push_raw('{"t": "ack", "seq": "katta"}')
    mes.push_raw('{"t": "jobs", "jobs": "ro\'yxat emas"}')
    mes.push_raw('{"t": "time", "ts": [1, 2]}')
    mes.push_raw('{"t": "cmd", "cmd": "planned_stop", "planned_sec": "ko\'p"}')
    mes.push_raw("x" * (config.RX_MAX_LINE + 900))   # juda uzun qator
    mes.push_raw("oddiy matn")
    mes.push_config({"t1": "abc", "t2": 120})
    advance(rig, 3)
    check(rig.app.link.connected, "buzuq xabar tufayli aloqa uzildi")
    check(rig.app.store.cfg("t1") == 60 and rig.app.store.cfg("t2") == 120, rig.app.store.data["config"])
    check(len(mes.logs) >= 2, "rad etish xabarlari kelmadi: %s" % mes.logs)
    rig.mes.command("planned_stop_end")
    advance(rig, 2)


def s23_untimed_after_reboot():
    """[USB] Soatsiz hodisa, keyin Pico toki uzildi: faqat davomiylik, kelgan kun ichida"""
    USB.set_pc(False)
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 10)
    signal(config.PIN_RUN, False)
    advance(rig, 45)
    signal(config.PIN_PWR, False)
    advance(rig, 2)
    pico_power_loss()
    signal(config.PIN_PWR, False)
    rig2 = Rig()
    USB.set_pc(True)
    advance(rig2, 15)
    on = mes_event(rig2.mes, "POWER_ON")
    idle = mes_event(rig2.mes, "SPINDLE_IDLE")
    check(on["start"] is None and on["end"] is None and on["time_uncertain"], on)
    check(50 <= on["duration_sec"] <= 60 and 35 <= idle["duration_sec"] <= 50, (on, idle))
    days = rig2.mes.on_seconds_by_day()
    check(days.get(iso_pc()[:10]) == on["duration_sec"], "kun bo'yicha hisob: %s" % days)
    say("    kun ichida yoqiq: %s" % days)


def s24_power_on_period():
    """[USB] POWER_ON: stanok qancha yoqiq turdi, shundan shpindel qancha aylandi"""
    signal(config.PIN_PWR, False)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, False)
    store, log, ctrl = new_controller()
    advance(ctrl, 5)
    signal(config.PIN_PWR, True)
    advance(ctrl, 10)
    signal(config.PIN_RUN, True)
    advance(ctrl, 40)
    signal(config.PIN_RUN, False)
    advance(ctrl, 20)
    signal(config.PIN_PWR, False)
    advance(ctrl, 2)
    ev = [e for e in log.pending(20) if e["type"] == "POWER_ON"]
    check(len(ev) == 1 and 69 <= ev[0]["duration_sec"] <= 71, ev)
    check(39 <= ev[0]["run_sec"] <= 41 and "from_boot" not in ev[0], ev)
    # Pico stanok yoqiq paytda yoqildi (masalan Pico toki qaytdi)
    signal(config.PIN_PWR, True)
    store2, log2, ctrl2 = new_controller()
    advance(ctrl2, 30)
    signal(config.PIN_PWR, False)
    advance(ctrl2, 2)
    ev2 = [e for e in log2.pending(20) if e["type"] == "POWER_ON"]
    check(ev2 and ev2[-1].get("from_boot") is True and 29 <= ev2[-1]["duration_sec"] <= 31, ev2)
    check(ctrl2.snapshot()["on_sec"] == 0, "o'chiq stanokda on_sec 0")


def s25_planned_restore_after_pc_time():
    """[USB] Tushlikda Pico toki uzildi: kompyuter vaqti kelgach davomiylik tiklanadi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 12)
    rig.mes.command("planned_stop", type="LUNCH", planned_sec=3600)
    advance(rig, 100)
    check(rig.ctrl.state == ctl.PLANNED_STOP, rig.ctrl.state)
    pico_power_loss()
    USB.set_pc(False)
    rig2 = Rig()
    advance(rig2, 20)
    check(rig2.ctrl.planned and rig2.ctrl.planned["type"] == "LUNCH", "tushlik yo'qoldi")
    USB.set_pc(True)
    advance(rig2, 30)
    rig2.mes.command("planned_stop_end")
    advance(rig2, 3)
    ev = mes_event(rig2.mes, "PLANNED_STOP")
    check(145 <= ev["duration_sec"] <= 160,
          "davomiylik ~153 bo'lishi kerak (100 oldin + 53 keyin): %s" % ev["duration_sec"])
    say("    tushlik davomiyligi: %s s" % ev["duration_sec"])


def s26_browser_closed_backlog():
    """[USB] Brauzer yopiq: Pico yig'ib turadi, ochilganda hammasi yetib boradi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, True)
    signal(config.PIN_HOME, False)
    rig = Rig()
    advance(rig, 12)
    check(rig.app.link.connected, "avval ulanishi kerak")
    USB.set_pc(False)                   # brauzer yopildi (kompyuter yoqiq)
    w0 = USB.writes
    # Stanok ishlashda davom etadi: 15 ta to'xtash-ishlash sikli
    for i in range(15):
        signal(config.PIN_RUN, False)
        advance(rig, 40)                # t_reason=30 dan uzun, t2=90 dan qisqa
        signal(config.PIN_RUN, True)
        advance(rig, 5)
    pending = rig.app.log.pending_count()
    check(pending >= 15, "hodisalar jurnalda yig'ilishi kerak: %d" % pending)
    check(USB.writes == w0, "brauzer yopiq paytda %d marta yozildi" % (USB.writes - w0))
    check(WDT_STATE["max_gap"] < 1000, "WDT %d ms" % WDT_STATE["max_gap"])
    USB.set_pc(True)                    # brauzer ochildi
    advance(rig, 60)
    check(rig.app.log.pending_count() == 0,
          "hammasi yuborilishi kerak, qolgan: %d" % rig.app.log.pending_count())
    seqs = [e["seq"] for e in rig.mes.event_list()]
    check(len(seqs) >= pending and seqs == sorted(seqs), "tartib buzildi: %s" % seqs)
    check(rig.mes.acked == rig.app.store.data["seq"], "hammasi tasdiqlanishi kerak")
    say("    brauzer yopiq paytda %d hodisa yig'ildi, %d s da yetib bordi" % (pending, 60))


def s27_relay_hold_configurable():
    """[YANGI] Rele NO->NC ushlab turish vaqti (t_relay) sozlanadi"""
    signal(config.PIN_PWR, True)
    signal(config.PIN_RUN, False)
    signal(config.PIN_HOME, True)
    store, log, ctrl = new_controller()
    store.update_config({"t_relay": 5})
    advance(ctrl, 61)                   # t1=60, rele endi yondi
    check(ctrl.state == ctl.SHUTDOWN and relay_on(), "rele yonishi kerak: %s" % ctrl.state)
    advance(ctrl, 2)
    check(relay_on(), "t_relay=5 s tugamasdan bo'shatildi")
    advance(ctrl, 5)
    check(not relay_on(), "t_relay=5 s dan keyin bo'shatilishi kerak")
    check("SHUTDOWN_FAILED" in types_of(log), types_of(log))
    # Uzunroq vaqt: 60 s
    store.update_config({"t_relay": 60})
    signal(config.PIN_RUN, True)
    advance(ctrl, 10)
    signal(config.PIN_RUN, False)
    advance(ctrl, 70)
    check(ctrl.state == ctl.SHUTDOWN and relay_on(), ctrl.state)
    advance(ctrl, 40)
    check(relay_on(), "60 s tugamaguncha ushlab turilishi kerak")
    signal(config.PIN_PWR, False)       # stanok o'chdi - darhol bo'shaydi
    advance(ctrl, 2)
    check(not relay_on() and ctrl.state == ctl.OFF, ctrl.state)
    check(types_of(log).count("SHUTDOWN_FAILED") == 1, types_of(log))


ALL = [s01_normal, s02_merge, s03_away, s04_planned, s05_power_cycle,
       s06_spindle_guard, s07_planned_during_idle, s08_shutdown_failed_once,
       s09_no_backdate_after_power_on, s10_maint_key, s11_run_during_shutdown,
       s12_config_validation, s13_log_compaction, s14_seq_recovery,
       s15_persist_planned_blocked, s16_relay_no_boot_pulse,
       s17_long_off_ticks_wrap, s18_usb_link, s19_pc_off_counter,
       s20_lost_event_ack_gap, s21_stalled_browser, s22_bad_messages,
       s23_untimed_after_reboot, s24_power_on_period,
       s25_planned_restore_after_pc_time, s26_browser_closed_backlog,
       s27_relay_hold_configurable]

# Soat to'g'ri bo'lishi kerak bo'lgan ssenariylar
REAL_CLOCK = (s15_persist_planned_blocked, s25_planned_restore_after_pc_time)


def _titles():
    """Ssenariy sarlavhalari docstring dan. MicroPython da __doc__ yo'q,
    shuning uchun fayl matnidan o'qiladi."""
    out = {}
    name = None
    try:
        with open(__file__) as f:
            for line in f:
                if line.startswith("def s"):
                    name = line[4:line.index("(")]
                elif name and line.strip().startswith('"""'):
                    out[name] = line.strip().strip('"')
                    name = None
    except (OSError, NameError, ValueError):
        pass
    return out


def run_all(print_exc, only=None):
    ok = 0
    fail = []
    titles = _titles()
    for fn in ALL:
        name = fn.__name__
        if only and not any(o in name for o in only):
            continue
        reset_env(real_clock=fn in REAL_CLOCK)
        print("\n=== %s: %s ===" % (name, titles.get(name, "")))
        try:
            fn()
            ok += 1
            print("    OK")
        except Exception as e:
            print("    XATO:", e)
            if not isinstance(e, AssertionError):
                print_exc(e)
            fail.append(name)
    reset_env()
    print("\n" + "=" * 64)
    print("Natija: %d o'tdi, %d xato %s" % (ok, len(fail), fail if fail else ""))
    return fail
