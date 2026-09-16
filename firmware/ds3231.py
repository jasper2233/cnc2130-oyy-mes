"""DS3231 real vaqt soati drayveri.

DS3231 IXTIYORIY. Asosiy vaqt manbai - kompyuter (USB orqali).
Modul ulanmagan bo'lsa drayver jim turadi, hech narsa buzilmaydi.

DS1307 emas, DS3231. Sababi TZ 3.2 bandida yozilgan:
DS1307 3.3 V da ishonchli ishlamaydi va batareyani tez tugatadi.

Ulanish: SDA -> GP20, SCL -> GP21, VCC -> 3V3, GND -> GND
"""

import machine
import time

from clock import MIN_VALID_YEAR

ADDR = 0x68


def _bcd2dec(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _dec2bcd(d):
    return ((d // 10) << 4) | (d % 10)


class DS3231:
    def __init__(self, sda_pin, scl_pin):
        self.i2c = machine.I2C(0,
                               sda=machine.Pin(sda_pin),
                               scl=machine.Pin(scl_pin),
                               freq=100_000)
        self.ok = False
        self.check()

    def check(self):
        """Modul ulangan va batareya ishlayaptimi."""
        try:
            if ADDR not in self.i2c.scan():
                self.ok = False
                return False
            # 0x0F registridagi OSF biti: soat to'xtab qolgan bo'lsa 1
            status = self.i2c.readfrom_mem(ADDR, 0x0F, 1)[0]
            self.ok = not (status & 0x80)
            return self.ok
        except OSError:
            self.ok = False
            return False

    def read(self):
        """Vaqtni o'qish. (yil, oy, kun, soat, daqiqa, soniya) qaytaradi.

        Modul ishlamasa None qaytaradi.
        """
        if not self.ok:
            return None
        try:
            d = self.i2c.readfrom_mem(ADDR, 0x00, 7)
        except OSError:
            self.ok = False
            return None
        sec = _bcd2dec(d[0] & 0x7F)
        minute = _bcd2dec(d[1] & 0x7F)
        hour = _bcd2dec(d[2] & 0x3F)
        day = _bcd2dec(d[4] & 0x3F)
        month = _bcd2dec(d[5] & 0x1F)
        year = 2000 + _bcd2dec(d[6])
        # I2C shovqini yoki bo'sh soat noto'g'ri qiymat berishi mumkin
        if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24
                and minute < 60 and sec < 60 and year >= MIN_VALID_YEAR):
            return None
        return (year, month, day, hour, minute, sec)

    def write(self, year, month, day, hour, minute, sec, weekday=1):
        """Vaqtni yozish. MES yoki kompyuterdan sinxronlashda chaqiriladi."""
        data = bytes([
            _dec2bcd(sec),
            _dec2bcd(minute),
            _dec2bcd(hour),
            _dec2bcd(weekday),
            _dec2bcd(day),
            _dec2bcd(month),
            _dec2bcd(year - 2000),
        ])
        try:
            self.i2c.writeto_mem(ADDR, 0x00, data)
            # OSF bitini tozalash - soat endi to'g'ri yurmoqda
            status = self.i2c.readfrom_mem(ADDR, 0x0F, 1)[0]
            self.i2c.writeto_mem(ADDR, 0x0F, bytes([status & 0x7F]))
            self.ok = True
            return True
        except OSError:
            self.ok = False
            return False

    def sync_to_system(self):
        """DS3231 dagi vaqtni Pico ning ichki soatiga ko'chirish."""
        t = self.read()
        if t is None:
            return False
        machine.RTC().datetime((t[0], t[1], t[2], 0, t[3], t[4], t[5], 0))
        return True

    def sync_from_system(self):
        """Pico ning ichki soatidan DS3231 ga yozish."""
        t = time.localtime()
        return self.write(t[0], t[1], t[2], t[3], t[4], t[5])


