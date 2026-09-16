"""Pico W apparat tekshiruvi. Pico ning o'zida bajariladi (soxta modullarsiz).

Oldin tools/pico_test.py bilan /test papkasiga fayllar yuklangan bo'lishi kerak.
Ishga tushirish:
    python -m mpremote connect COM5 run tools/pico_hwcheck.py

DIQQAT: oxirida WDT yoqiladi. Tekshiruv tugagach Pico 8 soniyada
o'zi qayta yuklanadi - bu kutilgan holat. GP0 ga stanok ulanmagan bo'lsin.
"""

import gc
import json
import os
import sys
import time

sys.path.insert(0, "/test")

import config  # noqa: E402

config.LOG_PATH = "/test/hw_events.jsonl"
config.STATE_PATH = "/test/hw_state.json"
config.NO_WDT_FLAG = "/__yoq__"

from machine import Pin, I2C  # noqa: E402

RESULTS = []


def res(name, ok, info=""):
    RESULTS.append((name, ok))
    print("  [%s] %s %s" % ("OK " if ok else "XATO", name, info))


def rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


for p in (config.LOG_PATH, config.STATE_PATH):
    rm(p)
    rm(p + ".tmp")

print("\n=== 1. Pinlar ===")
from controller import Controller, OFF  # noqa: E402
from eventlog import EventLog, Store  # noqa: E402

store = Store()
log = EventLog(store)
ctrl = Controller(store, log, None)
relay_level = Pin(config.PIN_RELAY).value()
expected = 1 if config.RELAY_ACTIVE_LOW else 0
res("GP0 rele pini boshlang'ich darajada", relay_level == expected,
    "(qiymat=%d, kutilgan=%d)" % (relay_level, expected))
res("Rele faol emas", not ctrl.relay_active)
for _ in range(10):
    ctrl.tick()
    time.sleep_ms(20)
raw = {n: Pin(n, Pin.IN, Pin.PULL_UP).value()
       for n in (config.PIN_PWR, config.PIN_RUN, config.PIN_HOME, config.PIN_MAINT)}
res("Kirishlar ulanmagan: signal yo'q", ctrl.pwr.value == 0 and ctrl.run.value == 0
    and ctrl.home.value == 0 and not ctrl.maint_on, "(xom pinlar %s)" % raw)
res("Holat OFF", ctrl.state == OFF, ctrl.state)

print("\n=== 2. I2C / DS3231 ===")
i2c = I2C(0, sda=Pin(config.PIN_SDA), scl=Pin(config.PIN_SCL), freq=100_000)
found = i2c.scan()
from ds3231 import DS3231  # noqa: E402
rtc = DS3231(config.PIN_SDA, config.PIN_SCL)
print("  I2C qurilmalar:", [hex(a) for a in found])
if 0x68 in found:
    res("DS3231 topildi va ishlaydi", rtc.ok, str(rtc.read()))
else:
    res("DS3231 yo'q - drayver yiqilmaydi", rtc.ok is False and rtc.read() is None,
        "(modul hali ulanmagan)")

print("\n=== 3. Flesh: to'la jurnal (%d KB) ===" % (config.LOG_MAX_BYTES // 1000))
del ctrl, log, store
gc.collect()
rm(config.LOG_PATH)
rm(config.STATE_PATH)
from eventlog import encode_row  # noqa: E402
n = 0
size = 0
t = time.ticks_ms()
with open(config.LOG_PATH, "wb") as f:
    while size < config.LOG_MAX_BYTES:
        n += 1
        # TZ 15.3 dagi namunaviy hodisa
        line = encode_row(n, "SPINDLE_IDLE", "2026-09-14T12:04:00",
                          "2026-09-14T12:31:00", 1620, "ORD-2026-0417",
                          "OP-1142", True, False)
        f.write(line)
        size += len(line)
print("  %d yozuv, %d bayt, bitta yozuv ~%d bayt, yozish %d ms"
      % (n, size, size // n, time.ticks_diff(time.ticks_ms(), t)))
res("2000 ga yaqin yozuv sig'adi (TZ 5.5)", n >= 1500, "(%d)" % n)

store = Store()
t = time.ticks_ms()
log = EventLog(store)          # seq ni jurnaldan tiklaydi
dt = time.ticks_diff(time.ticks_ms(), t)
res("Yuklanishda seq tiklash < 8 s", dt < 8000 and store.data["seq"] == n, "(%d ms)" % dt)

t = time.ticks_ms()
p = log.pending(10)
dt = time.ticks_diff(time.ticks_ms(), t)
res("pending(10) tez", dt < 2000 and len(p) == 10, "(%d ms)" % dt)

store.data["last_ack"] = n // 2
t = time.ticks_ms()
p = log.pending(10)
dt = time.ticks_diff(time.ticks_ms(), t)
res("pending(10) yarmi tasdiqlanganda", dt < 3000, "(%d ms) - har yurak urishida" % dt)

t = time.ticks_ms()
log.append("TEST", None, None)   # siqish shu yerda ishga tushadi
dt = time.ticks_diff(time.ticks_ms(), t)
left = os.stat(config.LOG_PATH)[6]
res("Siqish + yozish WDT (8 s) dan ancha qisqa", dt < 4000,
    "(%d ms, fayl %d -> %d bayt)" % (dt, size, left))
res("Tasdiqlanmaganlar saqlandi", log.pending_count() == n - n // 2 + 1
    and log.pending(1)[0]["seq"] == n // 2 + 1)
t = time.ticks_ms()
p = log.pending(10)
p = log.pending(10)
dt = time.ticks_diff(time.ticks_ms(), t)
res("Keyingi pending(10) pozitsiya keshidan", dt < 300, "(ikki marta %d ms)" % dt)
m = p[0]
res("MES formati (TZ 15.3)", m["id"] == "cnc213001/%09d" % m["seq"]
    and m["reason_required"] is True and m["duration_sec"] == 1620, str(m))
st = os.statvfs("/")
print("  Flesh bo'sh: %d KB" % (st[0] * st[3] // 1024))
del log, store
gc.collect()
rm(config.LOG_PATH)
rm(config.STATE_PATH)

print("\n=== 4. Asosiy sikl: haqiqiy USB port + WDT, 40 s ===")
print("  mpremote portga ma'lumot yubormaydi - kompyuter yo'q holat tekshiriladi")
import app_main  # noqa: E402
app = app_main.App(use_wdt=True)
res("WDT yoqildi", app.wdt is not None)
res("USB port (select.poll) ishlaydi", isinstance(app.link.port.writable(), bool)
    and app.link.port.read(16) == b"")
worst = 0
steps = 0
t_end = time.ticks_add(time.ticks_ms(), 40_000)
while time.ticks_diff(t_end, time.ticks_ms()) > 0:
    t = time.ticks_ms()
    app.step()
    dt = time.ticks_diff(time.ticks_ms(), t)
    if dt > worst:
        worst = dt
    steps += 1
    time.sleep_ms(config.TICK_MS)
gc.collect()
print("  %d qadam, eng uzun qadam %d ms, aloqa=%s, holat=%s, RAM bo'sh=%d"
      % (steps, worst, app.link.connected, app.ctrl.state, gc.mem_free()))
res("Eng uzun qadam WDT dan ancha qisqa", worst < 4000, "(%d ms)" % worst)
res("Sikl tezligi yetarli (debounce 100 ms)", steps > 40_000 // 60, "(%d qadam)" % steps)

print("\n" + "=" * 50)
bad = [r[0] for r in RESULTS if not r[1]]
print("Apparat tekshiruvi: %d OK, %d XATO %s" % (len(RESULTS) - len(bad), len(bad), bad or ""))
for p in (config.LOG_PATH, config.STATE_PATH):
    rm(p)
print("WDT yoqilgan - Pico ~8 s dan keyin o'zi qayta yuklanadi.")
