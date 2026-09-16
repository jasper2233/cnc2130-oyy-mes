"""Sinov ssenariylarini Pico W ning o'zida ishga tushirish.

Firmware kodi haqiqiy MicroPython da, haqiqiy flesh xotirada ishlaydi.
Faqat pinlar, vaqt va tarmoq soxta - stanok ulanmagan holda xavfsiz.

Ishga tushirish (kompyuterdan):
    python tools/pico_test.py COM5          # fayllarni yuklab, sinovni boshlaydi
    python tools/pico_test.py COM5 s07 s13  # tanlanganlari
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIRMWARE = ["config.py", "clock.py", "ds3231.py", "eventlog.py", "controller.py"]
STUBS = ["simtime.py", "fakemachine.py", "fakeusb.py",
         "mesmock.py", "simenv.py", "scenarios.py"]

# Pico da bajariladigan kod. main.py /test ga app_main nomi bilan yuklanadi,
# aks holda Pico yoqilganda uni avtomatik ishga tushirib yuboradi.
DEVICE_CODE = r"""
import sys, gc, time
t0 = time.ticks_ms()
sys.path.insert(0, '/test')
import simenv
simenv.install()
sys.modules['main'] = __import__('app_main')
import config
config.LOG_PATH = '/test/events.jsonl'
config.STATE_PATH = '/test/state.json'
import scenarios
scenarios.VERBOSE = False
gc.collect()
print('RAM bo\'sh:', gc.mem_free())
fail = scenarios.run_all(sys.print_exception, only=%s)
gc.collect()
print('RAM bo\'sh:', gc.mem_free(), ' vaqt: %%d s' %% (time.ticks_diff(time.ticks_ms(), t0) // 1000))
"""


def mpremote(port, *args, check=True):
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    return subprocess.run(cmd, check=check)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    port = sys.argv[1]
    only = sys.argv[2:] or None

    # Pico da firmware WDT bilan ishlayotgan bo'lsa, yuklash o'rtasida
    # qayta yuklanib ketmasin. Oxirida firmware odatiy holatiga qaytadi.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stanok
    stanok.prepare(port)
    try:
        return run(port, only)
    finally:
        stanok.finish(port)


def run(port, only):
    print("Fayllar Pico ga yuklanmoqda (%s)..." % port)
    args = ["fs", "mkdir", ":test", "+"]
    mpremote(port, *args[:3], check=False)
    cp = []
    for f in FIRMWARE:
        cp += ["fs", "cp", os.path.join(ROOT, "firmware", f), ":test/" + f, "+"]
    cp += ["fs", "cp", os.path.join(ROOT, "firmware", "main.py"), ":test/app_main.py", "+"]
    for f in STUBS:
        cp += ["fs", "cp", os.path.join(ROOT, "tools", "stubs", f), ":test/" + f, "+"]
    mpremote(port, *cp[:-1])

    print("Sinov boshlandi (bir necha daqiqa)...")
    code = DEVICE_CODE % repr(only)
    r = mpremote(port, "exec", code, check=False)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
