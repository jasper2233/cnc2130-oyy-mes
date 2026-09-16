"""Avtonom hodisa jurnali (TZ 5-bo'lim).

Mantiq:
  - Har bir hodisa ketma-ket raqam (seq) oladi va faylga qo'shiladi.
  - MES qabul qilganini tasdiqlaganda last_ack yangilanadi.
    Tasdiq yig'ma: "seq=N" degani 1..N hammasi qabul qilindi (TZ 5.3).
  - Fayl to'lganda faqat tasdiqlangan yozuvlar o'chiriladi (TZ 5.5).
  - Tasdiqlanmagan yozuv hech qachon o'chirilmaydi.
  - Fleshga faqat hodisa bo'lganda yoziladi, davriy emas (TZ 5.7).

Fleshdagi format - bitta qatorda bitta JSON massiv (~110 bayt):
    [seq, type, start, end, duration, job, operator, reason, uncertain, extra]
To'liq lug'at ko'rinishida (TZ 15.3) saqlansa yozuv ~270 bayt bo'lib,
240 KB ga 2000 emas, atigi 880 ta yozuv sig'ardi (Pico W da o'lchangan).
MES ga yuborishda to_message() orqali TZ 15.3 formatiga qaytariladi.

Tasdiqlangan qatorlar JSON sifatida ochilmaydi - faqat boshidagi seq
raqami o'qiladi. Aks holda to'la jurnalda har yurak urishi 2 s,
siqish 6 s davom etib, WDT chegarasiga (8 s) yaqinlashardi.

Vaqt. Soat modul yo'q, vaqt kompyuterdan keladi. Kompyuter o'chiq paytda
soat noma'lum bo'lishi mumkin - unda start/end bo'sh (null), davomiylik
saqlanadi, extra ga {"boot": yuklanish raqami, "end_ms": monoton vaqt}
yoziladi. Pico qayta yuklanmasdan kompyuter vaqti kelsa, yuborishda
resolve_time() aniq vaqtni tiklaydi. Qayta yuklangan bo'lsa faqat
davomiylik boradi - MES uni kelgan kuni ichida hisoblaydi.
"""

import json
import os
import time

import config
from clock import iso_ts, time_is_sane


def _fsize(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return 0


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _flash_free():
    try:
        s = os.statvfs("/")
        return s[0] * s[3]
    except (AttributeError, OSError):
        return 1 << 30     # kompyuterda sinovda statvfs bo'lmaydi


def _dumps(obj):
    try:
        return json.dumps(obj, separators=(",", ":"))
    except TypeError:
        return json.dumps(obj)


def line_seq(line):
    """Qator boshidagi seq ni JSON ochmasdan o'qish. b'[1472,"...' -> 1472"""
    if not line.startswith(b"["):
        return None
    i = line.find(b",")
    if i < 2:
        return None
    try:
        return int(line[1:i])
    except ValueError:
        return None


def encode_row(seq, ev_type, start, end, duration, job, operator,
               reason_required, time_uncertain, extra=None):
    """Fleshga yoziladigan qator (bayt, oxirida \\n)."""
    row = [seq, ev_type, start, end, duration, job, operator,
           1 if reason_required else 0, 1 if time_uncertain else 0,
           extra or None]
    return (_dumps(row) + "\n").encode()


def to_message(row):
    """Fleshdagi massivdan MES ga yuboriladigan hodisa (TZ 15.3)."""
    seq = row[0]
    ev = {
        "seq": seq,
        "id": "%s/%09d" % (config.MACHINE_ID.replace("-", ""), seq),
        "machine": config.MACHINE_ID,
        "type": row[1],
        "start": row[2],
        "end": row[3],
        "duration_sec": row[4],
        "job": row[5],
        "operator": row[6],
        "reason_required": bool(row[7]),
        "time_uncertain": bool(row[8]),
    }
    if len(row) > 9 and isinstance(row[9], dict):
        ev.update(row[9])
    return ev


def resolve_time(ev, boot, now_ms):
    """Soatsiz yozilgan hodisaga vaqt qo'yish (MES ga yuborishdan oldin).

    Hodisa shu yuklanishda yozilgan va hozir soat ma'lum bo'lsa, monoton
    vaqtdan aniq vaqt tiklanadi. Aks holda start/end null qoladi.
    Ichki maydonlar (boot, end_ms) MES ga yuborilmaydi.
    """
    ev_boot = ev.pop("boot", None)
    end_ms = ev.pop("end_ms", None)
    if (ev.get("start") is None and ev_boot == boot
            and isinstance(end_ms, int) and time_is_sane()):
        end_ts = int(time.time()) - (now_ms - end_ms) // 1000
        ev["end"] = iso_ts(end_ts)
        ev["start"] = iso_ts(end_ts - (ev.get("duration_sec") or 0))
        ev["time_uncertain"] = False
        ev["time_restored"] = True
    return ev


def valid_config_value(key, value):
    """MES dan kelgan sozlama qiymatini tekshirish (TZ 14)."""
    if key == "auto_shutdown":
        return isinstance(value, bool)
    if key not in config.CONFIG_LIMITS:
        return False
    # bool ham int ning bir turi, uni alohida chiqarib tashlaymiz
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    lo, hi = config.CONFIG_LIMITS[key]
    return lo <= value <= hi


class Store:
    """state.json - sozlamalar, hisoblagich va oxirgi tasdiq."""

    def __init__(self):
        self.data = {
            "seq": 0,
            "last_ack": 0,
            "log_floor": 0,        # shu seq gacha yozuvlar fayldan o'chirilgan
            "config": dict(config.DEFAULT_CONFIG),
            "boot": 0,             # Pico yuklanishlar soni (soatsiz hodisalar uchun)
            "operator": None,
            "job": None,
            "jobs": [],
            "planned": None,       # joriy rejalashtirilgan to'xtash (TZ 8.5)
            "blocked": False,      # smena qabul qilinmagan (TZ 10.3.2)
        }
        self._load()

    def _load(self):
        saved = None
        # save() eski faylni o'chirib, keyin .tmp ni qayta nomlaydi.
        # Shu orada tok ketsa, faqat .tmp qoladi.
        for path in (config.STATE_PATH, config.STATE_PATH + ".tmp"):
            try:
                with open(path) as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    break
                saved = None
            except (OSError, ValueError):
                saved = None
        if saved is None:
            print("state.json topilmadi, boshlang'ich qiymatlar")
            self.save()
            return
        for k in self.data:
            if k in saved:
                self.data[k] = saved[k]
        # Fleshdagi sozlamalarni ham tekshiramiz
        cfg = dict(config.DEFAULT_CONFIG)
        stored = self.data.get("config")
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in cfg and valid_config_value(k, v):
                    cfg[k] = v
        self.data["config"] = cfg

    def save(self):
        tmp = config.STATE_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            # MicroPython da rename mavjud faylni almashtirmaydi
            try:
                os.remove(config.STATE_PATH)
            except OSError:
                pass
            os.rename(tmp, config.STATE_PATH)
        except OSError as e:
            print("state saqlanmadi:", e)

    def cfg(self, key):
        return self.data["config"].get(key, config.DEFAULT_CONFIG.get(key))

    def update_config(self, new_cfg):
        """MES dan kelgan sozlamalarni qabul qilish (TZ 14).

        Noto'g'ri qiymatlar rad etiladi. (o'zgardimi, rad etilganlar) qaytaradi.
        """
        changed = False
        rejected = []
        if not isinstance(new_cfg, dict):
            return False, ["<dict emas>"]
        for k, v in new_cfg.items():
            if k not in config.DEFAULT_CONFIG:
                continue
            if not valid_config_value(k, v):
                rejected.append(k)
                continue
            if self.data["config"].get(k) != v:
                self.data["config"][k] = v
                changed = True
        if changed:
            self.save()
        return changed, rejected


class _Uptime:
    """Monoton vaqt manbai. Controller o'zini log.clock ga ulaydi,
    bu faqat Controller siz ishlatilganda (sinovlarda) kerak."""

    @property
    def now_ms(self):
        return time.ticks_ms()


class EventLog:
    def __init__(self, store):
        self.store = store
        self.clock = _Uptime()       # now_ms atributi bor obyekt
        self.full = False
        self.feed = lambda: None     # App WDT ni shu yerga ulaydi
        self._cache = None           # (min_seq, bayt pozitsiyasi) - pending() uchun
        self._recover_files()
        self._recover_seq()

    # ---------- tiklash ----------

    def _recover_files(self):
        """Tok uzilishidan keyingi holatni tuzatish."""
        tmp = config.LOG_PATH + ".tmp"
        if _exists(tmp):
            # compact() eski faylni o'chirgan, lekin .tmp ni nomlashga ulgurmagan
            try:
                if _exists(config.LOG_PATH):
                    os.remove(tmp)    # asosiy fayl butun, chala .tmp keraksiz
                else:
                    os.rename(tmp, config.LOG_PATH)
                    print("jurnal .tmp dan tiklandi")
            except OSError as e:
                print("jurnal tiklash xatosi:", e)
        # Oxirgi qator chala qolgan bo'lsa, keyingi yozuv unga yopishib
        # ikkalasi ham buzilardi. Yangi qatordan boshlaymiz.
        size = _fsize(config.LOG_PATH)
        if size:
            try:
                with open(config.LOG_PATH, "rb") as f:
                    f.seek(size - 1)
                    last = f.read(1)
                if last != b"\n":
                    with open(config.LOG_PATH, "ab") as f:
                        f.write(b"\n")
                    print("jurnalda chala qator yopildi")
            except OSError:
                pass

    def _recover_seq(self):
        """state.json yo'qolsa seq noldan boshlanib, ID lar takrorlanardi.

        MES takroriy ID ni dublikat deb tashlab yuborardi (TZ 5.4).
        seq faylda o'sib boradi, shuning uchun faqat oxiri o'qiladi.
        """
        size = _fsize(config.LOG_PATH)
        if not size:
            return
        max_seq = 0
        try:
            with open(config.LOG_PATH, "rb") as f:
                f.seek(max(0, size - 1024))
                tail = f.read()
            for line in tail.split(b"\n")[1 if size > 1024 else 0:]:
                s = line_seq(line)
                if s is not None and s > max_seq:
                    max_seq = s
        except OSError:
            return
        if max_seq > self.store.data["seq"]:
            print("seq tiklandi:", self.store.data["seq"], "->", max_seq)
            self.store.data["seq"] = max_seq
            self.store.save()

    # ---------- yozish ----------

    def append(self, ev_type, start_ms, end_ms, duration=None,
               reason_required=False, extra=None):
        """Yangi hodisa yozish. MES formatidagi yozuvni qaytaradi.

        start_ms, end_ms - monoton vaqt (clock.now_ms shkalasida).
        """
        self.store.data["seq"] += 1
        seq = self.store.data["seq"]
        job = self.store.data.get("job")
        operator = self.store.data.get("operator")
        now = self.clock.now_ms
        if end_ms is None:
            end_ms = now
        if start_ms is None:
            start_ms = end_ms
        if time_is_sane():
            base = int(time.time())
            started_at = iso_ts(base - (now - start_ms) // 1000)
            ended_at = iso_ts(base - (now - end_ms) // 1000)
            time_uncertain = False
        else:
            # Kun vaqti noma'lum - faqat davomiylik (resolve_time ga qarang)
            started_at = ended_at = None
            time_uncertain = True
            extra = dict(extra) if extra else {}
            extra["boot"] = self.store.data.get("boot", 0)
            extra["end_ms"] = end_ms
        line = encode_row(seq, ev_type, started_at, ended_at, duration, job,
                          operator, reason_required, time_uncertain, extra)

        # Avval siqamiz, keyin yozamiz - joy bo'shasin
        self._maybe_compact()

        if _flash_free() < config.FLASH_RESERVE_BYTES:
            # state.json ni saqlab qolish uchun yozmaymiz
            print("flesh to'ldi, hodisa yozilmadi:", ev_type)
        else:
            try:
                with open(config.LOG_PATH, "ab") as f:
                    f.write(line)
            except OSError as e:
                print("jurnal yozilmadi:", e)

        self.store.save()
        return to_message([seq, ev_type, started_at, ended_at, duration, job,
                           operator, reason_required, time_uncertain, extra])

    # ---------- o'qish ----------

    def pending(self, limit=20):
        """Tasdiqlanmagan yozuvlar, xronologik tartibda (TZ 5.2)."""
        min_seq = self.store.data["last_ack"]
        out = []
        start = 0
        c = self._cache
        if c is not None and c[0] <= min_seq:
            start = c[1]      # bu pozitsiyadan oldingilar allaqachon tasdiqlangan
        first_pending = None
        pos = start
        try:
            with open(config.LOG_PATH, "rb") as f:
                if start:
                    f.seek(start)
                n = 0
                while True:
                    line = f.readline()
                    if not line:
                        break
                    s = line_seq(line)
                    if s is not None and s > min_seq and line.endswith(b"\n"):
                        if first_pending is None:
                            first_pending = pos
                        try:
                            out.append(to_message(json.loads(line)))
                        except (ValueError, TypeError, IndexError):
                            pass
                        if len(out) >= limit:
                            break
                    pos += len(line)
                    n += 1
                    if n % 200 == 0:
                        self.feed()
        except OSError:
            return out
        self._cache = (min_seq, first_pending if first_pending is not None else pos)
        return out

    def pending_count(self):
        return self.store.data["seq"] - self.store.data["last_ack"]

    def ack(self, seq):
        """MES tasdig'i. Faqat oldinga siljiydi, mavjud seq dan oshmaydi."""
        if seq > self.store.data["seq"]:
            seq = self.store.data["seq"]
        if seq > self.store.data["last_ack"]:
            self.store.data["last_ack"] = seq
            self.store.save()
            return True
        return False

    # ---------- to'lish va siqish ----------

    def _acked_in_file(self):
        """Fayldagi, lekin allaqachon tasdiqlangan yozuvlar soni."""
        return self.store.data["last_ack"] - self.store.data["log_floor"]

    def check_full(self):
        """Jurnal holatini yangilaydi (TZ 5.6).

        Yangi to'lish holatiga o'tilgan bo'lsa True - LOG_FULL yozish kerak.
        Shu orqali LOG_FULL har soatda emas, faqat bir marta yoziladi.
        """
        full = (_fsize(config.LOG_PATH) >= config.LOG_MAX_BYTES
                and self._acked_in_file() <= 0)
        became_full = full and not self.full
        self.full = full
        return became_full

    def _maybe_compact(self):
        size = _fsize(config.LOG_PATH)
        if size < config.LOG_MAX_BYTES * config.LOG_COMPACT_AT:
            return
        acked = self._acked_in_file()
        if acked <= 0:
            return   # o'chiradigan narsa yo'q, har safar fayl qayta yozilmasin
        if acked < config.LOG_COMPACT_MIN and size < config.LOG_MAX_BYTES:
            return
        self.compact()

    def compact(self):
        """Tasdiqlangan yozuvlarni tashlab, faylni qayta yozish.

        Qatorlar JSON ochilmasdan, bayt holida ko'chiriladi.
        """
        last_ack = self.store.data["last_ack"]
        tmp = config.LOG_PATH + ".tmp"
        kept = 0
        n = 0
        try:
            with open(config.LOG_PATH, "rb") as src:
                with open(tmp, "wb") as dst:
                    while True:
                        line = src.readline()
                        if not line:
                            break
                        n += 1
                        if n % 200 == 0:
                            self.feed()
                        s = line_seq(line)
                        if s is None or s <= last_ack or not line.endswith(b"\n"):
                            continue
                        dst.write(line)
                        kept += 1
            self.feed()
            try:
                os.remove(config.LOG_PATH)
            except OSError:
                pass
            os.rename(tmp, config.LOG_PATH)
            self._cache = None
            self.store.data["log_floor"] = last_ack
            self.store.save()
            print("jurnal siqildi, qolgan yozuv:", kept)
        except OSError as e:
            print("siqish xatosi:", e)
