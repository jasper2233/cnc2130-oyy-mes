"""MicroPython `time` modulining boshqariladigan varianti.

Vaqt faqat qo'lda suriladi (CLOCK.advance). sleep_ms ham vaqtni suradi -
shu orqali bloklovchi kod WDT ni och qoldirishi aniqlanadi.
ticks_* xuddi MicroPython dagidek 2^30 da aylanadi.

CPython da ham, Pico ning o'zida ham ishlaydi.
"""

import time as _rt

try:
    import calendar as _cal
except ImportError:
    _cal = None

_PERIOD = 1 << 30
_HALF = _PERIOD // 2

# Pico yangi yoqilganda soati 2021-01-01 00:00:00 dan boshlanadi
PICO_BOOT_EPOCH = 1609459200


class _Clock:
    def __init__(self):
        self.now_ms = 0
        self.base = PICO_BOOT_EPOCH

    def advance(self, ms):
        self.now_ms += int(ms)

    def reset(self, base=PICO_BOOT_EPOCH, now_ms=0):
        self.now_ms = now_ms
        self.base = base


CLOCK = _Clock()


def ticks_ms():
    return CLOCK.now_ms & (_PERIOD - 1)


def ticks_diff(a, b):
    return ((a - b + _HALF) & (_PERIOD - 1)) - _HALF


def ticks_add(a, b):
    return (a + b) & (_PERIOD - 1)


def sleep_ms(ms):
    CLOCK.advance(ms)


def sleep(s):
    CLOCK.advance(int(s * 1000))


def time():
    return CLOCK.base + CLOCK.now_ms // 1000


def localtime(secs=None):
    if secs is None:
        secs = time()
    t = _rt.gmtime(int(secs))
    return tuple(t[i] for i in range(8))


gmtime = localtime


def mktime(t):
    t = tuple(t)
    if _cal is not None:
        return _cal.timegm((t[0], t[1], t[2], t[3], t[4], t[5], 0, 0, 0))
    return _rt.mktime((t[0], t[1], t[2], t[3], t[4], t[5], 0, 0))


def set_datetime(year, month, day, hour, minute, sec):
    CLOCK.base = mktime((year, month, day, hour, minute, sec)) - CLOCK.now_ms // 1000
