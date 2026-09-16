"""Vaqt yordamchilari. Apparatga bog'liq emas (kompyuterda ham import bo'ladi)."""

import time

MIN_VALID_YEAR = 2024


def time_is_sane():
    """Soat o'rnatilganmi. Pico yangi yoqilganda 2021-yildan boshlaydi."""
    return time.localtime()[0] >= MIN_VALID_YEAR


def iso_ts(ts):
    """Soniyalar (time.time() shkalasida) -> ISO 8601."""
    t = time.localtime(int(ts))
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])


def iso_now(offset_s=0):
    """Joriy vaqtni ISO 8601 ko'rinishida qaytaradi.

    offset_s manfiy bo'lsa, shuncha soniya oldingi vaqt.
    """
    return iso_ts(int(time.time()) + int(offset_s))
