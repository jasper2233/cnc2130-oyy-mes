"""MicroPython `machine` modulining soxta varianti (sinov uchun)."""

import simtime

CLOCK = simtime.CLOCK

# Pin raqami -> apparat darajasidagi qiymat (0 yoki 1)
PINS = {}
# Chiqish pinlariga yozilgan har bir qiymat: (pin, qiymat, ms)
PIN_LOG = []
# WDT kuzatuvi
WDT_STATE = {"timeout": None, "last": None, "max_gap": 0}


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    PULL_DOWN = 3

    def __init__(self, n, mode=IN, pull=None, value=None):
        self.n = n
        self.mode = mode
        if mode == Pin.OUT:
            # RP2040 da value berilmasa chiqish 0 bilan yoqiladi
            self.value(0 if value is None else value)
        elif n not in PINS:
            # Pull-up bo'lsa bo'sh pin 1 da turadi
            PINS[n] = 1 if pull == Pin.PULL_UP else 0

    def value(self, v=None):
        if v is None:
            return PINS.get(self.n, 0)
        v = 1 if v else 0
        PINS[self.n] = v
        if self.mode == Pin.OUT:
            PIN_LOG.append((self.n, v, CLOCK.now_ms))
        return None


class I2C:
    def __init__(self, bus, sda=None, scl=None, freq=100_000):
        pass

    def scan(self):
        return []          # DS3231 ulanmagan deb hisoblanadi

    def readfrom_mem(self, addr, reg, n):
        raise OSError("I2C yo'q")

    def writeto_mem(self, addr, reg, data):
        raise OSError("I2C yo'q")


class RTC:
    def datetime(self, dt=None):
        if dt is None:
            t = simtime.localtime()
            return (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0)
        simtime.set_datetime(dt[0], dt[1], dt[2], dt[4], dt[5], dt[6])
        return None


class WDT:
    def __init__(self, timeout=5000):
        WDT_STATE["timeout"] = timeout
        WDT_STATE["last"] = CLOCK.now_ms
        WDT_STATE["max_gap"] = 0

    def feed(self):
        gap = CLOCK.now_ms - WDT_STATE["last"]
        if gap > WDT_STATE["max_gap"]:
            WDT_STATE["max_gap"] = gap
        WDT_STATE["last"] = CLOCK.now_ms


def reset():
    raise SystemExit("machine.reset()")
