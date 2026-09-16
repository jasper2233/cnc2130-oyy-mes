"""Stanokda o'rnatish va sinash vositasi (kompyuterdan, USB orqali).

MES ulanmagan holda Pico ni o'rnatish, datchiklarni tekshirish va
firmware mantig'ini haqiqiy stanokda kuzatish uchun.

Ishlatish:
    python tools/stanok.py [PORT] BUYRUQ [parametrlar]

PORT ko'rsatilmasa Pico o'zi topiladi (masalan COM5).

Buyruqlar:
    holat                 Pico da nima bor: firmware, sozlamalar, jurnal, soat
    datchik [soniya]      Datchiklarni jonli ko'rish (standart 600 s)
    rele-tur              Rele moduli aktiv-LOW yoki aktiv-HIGH ekanini aniqlash
    rele [soniya]         O'chirish sinovi: releni yoqib, stanok o'chishini kutish
    vaqt                  Kompyuter vaqtini Pico ga (va bo'lsa DS3231 ga) yozish
    ornat [--sozlash]     firmware/ ni Pico ga o'rnatish (--sozlash: WDT o'chiq)
    sozla t1=15 t2=15     Taymerlarni o'zgartirish (MES o'rniga), daqiqada
    kuzat [daqiqa]        Firmware ni ishga tushirib, holatlarni jonli ko'rish
    apparat               Pico apparat tekshiruvi (pinlar, flesh, WDT, USB)
    aloqa [daqiqa]        MES sahifasi o'rniga: ishlayotgan firmware bilan USB aloqa
    jurnal [soni]         Hodisalar jurnalini kompyuterga ko'chirish (CSV ham)
    tozala                Sinov jurnalini o'chirish (ID hisoblagichi saqlanadi)

Pico da firmware WDT bilan ishlayotgan bo'lsa, vosita avval uni xavfsiz
to'xtatadi (vaqtincha /nowdt), ish tugagach firmware ni qayta ishga tushiradi.
Shu sababli har buyruqdan keyin bo'sh turish taymeri noldan boshlanadi.
`aloqa` bundan mustasno: u firmware ni to'xtatmaydi.

USB portni bir vaqtda faqat bitta dastur ochadi. Vositani ishlatishdan
oldin MES sahifasini (brauzerdagi ulanishni) yoping.
"""

import csv
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
sys.path.insert(0, FW)

import config  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

FIRMWARE_FILES = ["config.py", "clock.py", "ds3231.py", "eventlog.py",
                  "controller.py", "main.py"]
TEMP_FLAG = "/nowdt_vaqtincha"
TIME_FILE = "/vaqt_saqlandi"

# (nomi, GPIO, aktiv-LOW, izoh)
INPUTS = [
    ("S_PWR", config.PIN_PWR, config.INPUT_ACTIVE_LOW, "stanok tarmoqda (24 V)"),
    ("S_RUN", config.PIN_RUN, config.INPUT_ACTIVE_LOW, "shpindel aylanmoqda (VFD)"),
    ("S_HOME", config.PIN_HOME, config.INPUT_ACTIVE_LOW, "0 nuqtada (induktiv)"),
]
if config.PIN_MAINT is not None:
    INPUTS.append(("S_MAINT", config.PIN_MAINT, config.MAINT_ACTIVE_LOW, "ta'mirlash kaliti"))

# Foydalanuvchi daqiqada kiritadigan sozlamalar (firmware ichida - soniyada)
MINUTE_KEYS = ("t1", "t2", "t_reason")

REL_ON = 0 if config.RELAY_ACTIVE_LOW else 1
REL_OFF = 1 - REL_ON


# ---------------------------------------------------------------- mpremote

def mp(port, *args, capture=False, timeout=None):
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if capture:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 1, ""
        return r.returncode, r.stdout + r.stderr
    return subprocess.run(cmd, env=env).returncode, ""


def device(port, code, capture=False, timeout=None, **vals):
    """Pico da kod bajarish. {{nom}} o'rniga qiymat qo'yiladi."""
    for k, v in vals.items():
        code = code.replace("{{%s}}" % k, str(v))
    # Pico kodidagi matnlar bittalik qo'shtirnoqda, apostrof ` bilan yozilgan
    code = code.replace("`", "\\'")
    return mp(port, "exec", HELPERS + code, capture=capture, timeout=timeout)


HELPERS = r"""
import os, time
from machine import Pin
def _ex(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False
def _hm(s):
    # Bo'sh turish vaqtlari daqiqada ko'rsatiladi
    if s is None:
        return '-'
    if s % 60 == 0:
        return '%d daqiqa' % (s // 60)
    return '%d daqiqa %d s' % (s // 60, s % 60)
"""


def find_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        if p.vid == 0x2E8A:
            return p.device
    return None


def wait_ready(port, seconds=25):
    time.sleep(2)
    t_end = time.time() + seconds
    while time.time() < t_end:
        _, out = mp(port, "exec", "print('TAYYOR')", capture=True, timeout=10)
        if "TAYYOR" in out:
            return True
        time.sleep(1)
    return False


PREPARE = r"""
if _ex('/main.py') and not _ex('/nowdt'):
    open('/nowdt', 'w').close()
    open('{{flag}}', 'w').close()
    if time.localtime()[0] >= 2024:
        # DS3231 bo'lmasa qayta yuklanishda soat yo'qolmasin
        with open('{{timefile}}', 'w') as f:
            f.write(str(time.time()))
    print('QAYTA_YUKLASH')
    import machine
    time.sleep_ms(100)
    machine.reset()
else:
    print('TAYYOR')
"""

RESTORE_TIME = r"""
if _ex('{{timefile}}'):
    os.remove('{{timefile}}')
    if time.localtime()[0] < 2024:
        # Soat qayta yuklanishgacha to'g'ri edi - kompyuter vaqtidan tiklaymiz
        from machine import RTC
        lt = {{now}}
        RTC().datetime((lt[0], lt[1], lt[2], 0, lt[3], lt[4], lt[5], 0))
"""

FINISH = r"""
if _ex('{{flag}}'):
    os.remove('{{flag}}')
    if _ex('/nowdt'):
        os.remove('/nowdt')
if _ex('/main.py'):
    print('Firmware qayta ishga tushirildi' + ('' if _ex('/nowdt') else ' (WDT yoqiq)'))
    import machine
    time.sleep_ms(100)
    machine.reset()
"""


def prepare(port):
    """Ishlayotgan firmware ni (WDT bilan) xavfsiz to'xtatish."""
    code, out = device(port, PREPARE, capture=True, timeout=20, flag=TEMP_FLAG,
                       timefile=TIME_FILE)
    if "QAYTA_YUKLASH" in out:
        print("Firmware to'xtatilmoqda (WDT tufayli Pico qayta yuklanadi)...")
        if not wait_ready(port):
            sys.exit("Pico qayta ulanmadi. USB kabelni tekshiring.")
        device(port, RESTORE_TIME, capture=True, timeout=20, timefile=TIME_FILE,
               now=repr(tuple(time.localtime())[:6]))
    elif "TAYYOR" not in out:
        sys.exit("Pico bilan aloqa yo'q (%s):\n%s" % (port, out.strip()))


def finish(port):
    _, out = device(port, FINISH, capture=True, timeout=20, flag=TEMP_FLAG)
    for line in out.splitlines():
        if line.startswith("Firmware"):
            print(line)


def ask_yes(text):
    try:
        ans = input(text + " [ha/yo'q]: ").strip().lstrip("﻿").lower()
        return ans in ("ha", "h", "yes", "y")
    except EOFError:
        return False


# ---------------------------------------------------------------- holat

HOLAT = r"""
import json, sys, gc
print('MicroPython:', sys.version)
fw = [f for f in {{files}} if _ex('/' + f)]
print('Firmware fayllari: %d/%d %s' % (len(fw), len({{files}}), '' if len(fw) == len({{files}}) else '(TO`LIQ EMAS)'))
if not _ex('/main.py'):
    print('WDT: firmware o`rnatilmagan. O`rnatish: stanok.py ornat')
elif _ex('{{flag}}'):
    print('WDT: yoqiq (hozir vosita ishlashi uchun vaqtincha o`chirilgan)')
elif _ex('/nowdt'):
    print('WDT: O`CHIQ - /nowdt fayli bor (sozlash rejimi). Ishga tushirishdan oldin: stanok.py ornat')
else:
    print('WDT: yoqiq')
try:
    st = json.load(open('/state.json'))
    c = st.get('config', {})
    print('Sozlamalar: t1=%s, t2=%s, t_reason=%s' % (
        _hm(c.get('t1')), _hm(c.get('t2')), _hm(c.get('t_reason'))))
    print('            t_merge=%s s, rele ushlash t_relay=%s s, avto o`chirish: %s' % (
        c.get('t_merge'), c.get('t_relay'),
        'yoqiq' if c.get('auto_shutdown') else 'o`chiq'))
    print('Hodisalar: oxirgi seq=%s, MES tasdiqlagan=%s, yuborilmagan=%s' % (
        st.get('seq'), st.get('last_ack'), st.get('seq', 0) - st.get('last_ack', 0)))
    print('Ish: %s, operator: %s, rejali to`xtash: %s, blok: %s' % (
        st.get('job'), st.get('operator'), (st.get('planned') or {}).get('type'), st.get('blocked')))
except (OSError, ValueError):
    print('state.json yo`q (firmware hali ishlamagan)')
try:
    size = os.stat('/events.jsonl')[6]
    with open('/events.jsonl', 'rb') as f:
        f.seek(max(0, size - 700))
        tail = f.read().split(b'\n')
    print('Jurnal: %d bayt. Oxirgi yozuvlar:' % size)
    for line in tail[-6:]:
        if line.startswith(b'['):
            try:
                r = json.loads(line)
                print('   #%s %-16s %s  %s s' % (r[0], r[1], r[2], r[4]))
            except ValueError:
                pass
except OSError:
    print('Jurnal: bo`sh')
s = os.statvfs('/')
print('Flesh bo`sh: %d KB' % (s[0] * s[3] // 1024))
gc.collect()
print('RAM bo`sh: %d KB' % (gc.mem_free() // 1024))
from machine import I2C
i2c = I2C(0, sda=Pin({{sda}}), scl=Pin({{scl}}), freq=100000)
if 0x68 in i2c.scan():
    d = i2c.readfrom_mem(0x68, 0, 7)
    b = lambda x: (x >> 4) * 10 + (x & 15)
    osf = i2c.readfrom_mem(0x68, 0x0F, 1)[0] & 0x80
    print('DS3231: 20%02d-%02d-%02d %02d:%02d:%02d %s' % (b(d[6]), b(d[5] & 31), b(d[4]), b(d[2] & 63), b(d[1]), b(d[0] & 127),
          '(SOAT TO`XTAGAN - batareyani tekshiring, keyin: stanok.py vaqt)' if osf else ''))
else:
    print('DS3231: TOPILMADI (SDA=GP{{sda}}, SCL=GP{{scl}})')
t = time.localtime()
print('Pico soati: %04d-%02d-%02d %02d:%02d:%02d' % t[:6])
"""


def cmd_holat(port, args):
    print("Kompyuterdagi config.py: %s, rele %s" % (
        config.MACHINE_ID, "aktiv-LOW" if config.RELAY_ACTIVE_LOW else "aktiv-HIGH"))
    print("-" * 60)
    device(port, HOLAT, files=FIRMWARE_FILES, flag=TEMP_FLAG,
           sda=config.PIN_SDA, scl=config.PIN_SCL)


# ---------------------------------------------------------------- datchik

DATCHIK = r"""
_rel = Pin({{relay}}, Pin.OUT, value={{rel_off}})
INPUTS = {{inputs}}
pins = [(n, no, al, d, Pin(no, Pin.IN, Pin.PULL_UP if al else None)) for n, no, al, d in INPUTS]
def val(e):
    v = e[4].value()
    return 1 - v if e[2] else v
def txt(v):
    return 'SIGNAL BOR' if v else 'signal yo`q'
cur = [val(e) for e in pins]
win = [0] * len(pins)
total = [0] * len(pins)
shown = 0
hidden = 0
t0 = time.ticks_ms()
tw = t0
def ts():
    return '%7.1f s' % (time.ticks_diff(time.ticks_ms(), t0) / 1000)
print('Datchiklar {{seconds}} soniya kuzatiladi. Firmware ishlamaydi, rele bo`sh.')
for i, e in enumerate(pins):
    print('  %-8s GP%-2d %-28s hozir: %s' % (e[0], e[1], e[3], txt(cur[i])))
print('-' * 64)
while time.ticks_diff(time.ticks_ms(), t0) < {{seconds}} * 1000:
    for i, e in enumerate(pins):
        v = val(e)
        if v != cur[i]:
            cur[i] = v
            win[i] += 1
            total[i] += 1
            if shown < 30:
                print('%s  %-8s -> %s' % (ts(), e[0], txt(v)))
                shown += 1
            else:
                hidden += 1
    if time.ticks_diff(time.ticks_ms(), tw) >= 5000:
        print('%s  [%s]' % (ts(), '  '.join('%s=%d' % (e[0][2:], cur[i]) for i, e in enumerate(pins))))
        for i, e in enumerate(pins):
            if win[i] > 20:
                print('           DIQQAT: %s 5 s da %d marta o`zgardi - sakrash yoki shovqin' % (e[0], win[i]))
        if hidden:
            print('           (%d ta o`zgarish qatori ko`rsatilmadi)' % hidden)
        win = [0] * len(pins)
        shown = 0
        hidden = 0
        tw = time.ticks_ms()
    time.sleep_ms(1)
print('-' * 64)
print('Jami o`zgarishlar: ' + ', '.join('%s %d' % (e[0], total[i]) for i, e in enumerate(pins)))
"""


def cmd_datchik(port, args):
    seconds = int(args[0]) if args else 600
    print("To'xtatish: Ctrl+C")
    device(port, DATCHIK, relay=config.PIN_RELAY, rel_off=REL_OFF,
           inputs=repr(INPUTS), seconds=seconds)


# ---------------------------------------------------------------- rele-tur

RELE_TUR = r"""
p = Pin({{relay}}, Pin.OUT, value=0)
for r in (1, 2):
    print('%d-marta.  1-BOSQICH: GP{{relay}} = 0 (0 V)    - 3 s' % r)
    p.value(0)
    time.sleep(3)
    print('%d-marta.  2-BOSQICH: GP{{relay}} = 1 (3.3 V)  - 3 s' % r)
    p.value(1)
    time.sleep(3)
Pin({{relay}}, Pin.IN)
print('GP{{relay}} kirish rejimiga o`tkazildi (holatni 10 kOm tortuvchi rezistor belgilaydi)')
"""


def cmd_rele_tur(port, args):
    print("DIQQAT: rele kontaktlari stanokka ULANMAGAN bo'lishi shart.")
    print("Rele moduli 5 V bilan quvvatlangan, IN kirishi GP%d ga ulangan bo'lsin." % config.PIN_RELAY)
    print("Rele modulidagi chiroqqa qarang va chertishini tinglang.")
    if not ask_yes("Rele stanokdan ajratilganmi?"):
        return True
    device(port, RELE_TUR, relay=config.PIN_RELAY)
    print()
    print("Rele qaysi bosqichda yondi (chertdi)?")
    print("  1 - faqat 1-bosqichda (GP = 0 V)")
    print("  2 - faqat 2-bosqichda (GP = 3.3 V)")
    print("  3 - ikkalasida ham yonib turdi")
    print("  4 - hech qaysisida yonmadi")
    try:
        ans = input("Javob [1-4]: ").strip().lstrip("﻿")
    except EOFError:
        ans = ""
    if ans == "1":
        want = True
    elif ans == "2":
        want = False
    elif ans == "3":
        print("Rele 3.3 V da ham to'liq o'chmayapti. Bu 5 V aktiv-LOW modullarda uchraydi:")
        print("  Pico 3.3 V beradi, modulga esa 5 V kerak. Yechim: 3.3 V ga mos rele moduli")
        print("  yoki GP0 va modul orasiga tranzistor (masalan 2N2222 + 1 kOm) qo'yish.")
        print("Bu holda firmware ni stanokka ULAMANG.")
        return False
    elif ans == "4":
        print("Rele umuman ishlamadi. Tekshiring: modul quvvati (VCC 5 V, GND), IN simi GP%d da,"
              " modul GND i Pico GND bilan ulanganmi." % config.PIN_RELAY)
        return True
    else:
        return True
    name = "aktiv-LOW (RELAY_ACTIVE_LOW = True)" if want else "aktiv-HIGH (RELAY_ACTIVE_LOW = False)"
    print("Rele moduli: %s" % name)
    print("GP%d tortuvchi rezistori (TZ 3.5.3): %s" % (
        config.PIN_RELAY, "10 kOm 3V3 ga (pull-up)" if want else "10 kOm GND ga (pull-down)"))
    if want == config.RELAY_ACTIVE_LOW:
        print("firmware/config.py dagi qiymat to'g'ri.")
        return True
    print("firmware/config.py dagi qiymat NOTO'G'RI. Shu holatda firmware stanokni doim o'chirib turardi!")
    if ask_yes("config.py ni tuzataymi?"):
        path = os.path.join(FW, "config.py")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"^RELAY_ACTIVE_LOW = (True|False)",
                      "RELAY_ACTIVE_LOW = %s" % want, text, flags=re.M)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("Tuzatildi. Endi Pico ga yuklang: python tools/stanok.py ornat")
    print("Firmware noto'g'ri sozlama bilan qayta ishga tushirilmadi.")
    return False


# ---------------------------------------------------------------- rele

RELE = r"""
rel = Pin({{relay}}, Pin.OUT, value={{rel_off}})
def mk(no, al):
    return (Pin(no, Pin.IN, Pin.PULL_UP if al else None), al)
def v(x):
    r = x[0].value()
    return 1 - r if x[1] else r
pwr = mk({{pwr}}, {{in_al}})
run = mk({{run}}, {{in_al}})
if v(pwr) == 0:
    print('BEKOR: S_PWR = 0 - stanok o`chiq yoki S_PWR datchigi ishlamayapti')
elif v(run) == 1:
    print('BEKOR: S_RUN = 1 - shpindel aylanmoqda. TZ 4.3.4: bu holatda o`chirilmaydi')
else:
    print('RELE YONDI (GP{{relay}} = {{rel_on}}). Stanok o`chishi kutilmoqda, ko`pi bilan {{seconds}} s...')
    t0 = time.ticks_ms()
    res = None
    low = None
    rel.value({{rel_on}})
    try:
        while time.ticks_diff(time.ticks_ms(), t0) < {{seconds}} * 1000:
            if v(run) == 1:
                res = 'run'
                break
            if v(pwr) == 0:
                if low is None:
                    low = time.ticks_ms()
                elif time.ticks_diff(time.ticks_ms(), low) >= 100:
                    res = 'off'
                    break
            else:
                low = None
            time.sleep_ms(5)
    finally:
        rel.value({{rel_off}})
    if res == 'off':
        print('NATIJA: STANOK O`CHDI - rele yonganidan %.1f s keyin. Rele bo`shatildi.' % (time.ticks_diff(low, t0) / 1000))
    elif res == 'run':
        print('NATIJA: shpindel yoqildi - rele darhol bo`shatildi (to`g`ri xatti-harakat)')
    else:
        print('NATIJA: {{seconds}} s ichida stanok O`CHMADI. Rele bo`shatildi. Firmware bu holatda SHUTDOWN_FAILED yozadi.')
"""


def cmd_rele(port, args):
    seconds = min(int(args[0]) if args else config.DEFAULT_CONFIG["t_relay"], 600)
    print("Bu sinov stanokni HAQIQATAN O'CHIRADI (rele -> stanokdagi vaqt relesi).")
    print("Rele %d s gacha ushlab turiladi (firmware dagi t_relay sozlamasi)." % seconds)
    print("Oldin: dastur tugagan, shpindel to'xtagan, stanok 0 nuqtada bo'lsin.")
    print("Shpindel aylanayotgan bo'lsa (S_RUN = 1) sinov boshlanmaydi.")
    if not ask_yes("Stanokni o'chirishga ruxsat berasizmi?"):
        return True
    device(port, RELE, relay=config.PIN_RELAY, rel_on=REL_ON, rel_off=REL_OFF,
           pwr=config.PIN_PWR, run=config.PIN_RUN, in_al=config.INPUT_ACTIVE_LOW,
           seconds=seconds)
    return True


# ---------------------------------------------------------------- vaqt

VAQT = r"""
from machine import I2C, RTC
t = {{t}}
RTC().datetime((t[0], t[1], t[2], t[6] - 1, t[3], t[4], t[5], 0))
i2c = I2C(0, sda=Pin({{sda}}), scl=Pin({{scl}}), freq=100000)
if 0x68 in i2c.scan():
    e = lambda x: ((x // 10) << 4) | (x % 10)
    i2c.writeto_mem(0x68, 0, bytes([e(t[5]), e(t[4]), e(t[3]), e(t[6]), e(t[2]), e(t[1]), e(t[0] - 2000)]))
    st = i2c.readfrom_mem(0x68, 0x0F, 1)[0]
    i2c.writeto_mem(0x68, 0x0F, bytes([st & 0x7F]))
    d = i2c.readfrom_mem(0x68, 0, 7)
    b = lambda x: (x >> 4) * 10 + (x & 15)
    print('DS3231 ga yozildi: 20%02d-%02d-%02d %02d:%02d:%02d' % (b(d[6]), b(d[5] & 31), b(d[4]), b(d[2] & 63), b(d[1]), b(d[0] & 127)))
else:
    print('DS3231 TOPILMADI - faqat Pico ichki soati o`rnatildi, tok o`chsa yo`qoladi')
"""


def cmd_vaqt(port, args):
    lt = time.localtime()
    t = (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec, lt.tm_wday + 1)
    print("Kompyuter vaqti: %04d-%02d-%02d %02d:%02d:%02d" % t[:6])
    device(port, VAQT, t=repr(t), sda=config.PIN_SDA, scl=config.PIN_SCL)


# ---------------------------------------------------------------- ornat

ORNAT_CHECK = r"""
import config, clock, ds3231, eventlog, controller, main
print('IMPORT_OK', config.MACHINE_ID)
if _ex('/test'):
    for f in os.listdir('/test'):
        os.remove('/test/' + f)
    os.rmdir('/test')
    print('/test papkasi (sinov fayllari) o`chirildi')
"""

ORNAT_WDT = r"""
if _ex('{{flag}}'):
    os.remove('{{flag}}')
if {{keep}}:
    open('/nowdt', 'w').close()
elif _ex('/nowdt'):
    os.remove('/nowdt')
"""


def cmd_ornat(port, args):
    keep_nowdt = "--sozlash" in args
    print("O'rnatiladigan sozlamalar (firmware/config.py):")
    print("  Stanok: %s" % config.MACHINE_ID)
    print("  Pinlar: rele GP%d, S_PWR GP%d, S_RUN GP%d, S_HOME GP%d, kalit %s" % (
        config.PIN_RELAY, config.PIN_PWR, config.PIN_RUN, config.PIN_HOME,
        "GP%d" % config.PIN_MAINT if config.PIN_MAINT is not None else "yo'q"))
    print("  Rele: %s" % ("aktiv-LOW" if config.RELAY_ACTIVE_LOW else "aktiv-HIGH"))
    print("  Aloqa: micro USB (kompyuterdagi MES sahifasi)")
    files = []
    for f in FIRMWARE_FILES:
        files += ["fs", "cp", os.path.join(FW, f), ":" + f, "+"]
    print("Fayllar yuklanmoqda...")
    code, _ = mp(port, *files[:-1])
    if code:
        sys.exit("Yuklashda xato.")
    code, out = device(port, ORNAT_CHECK, capture=True, timeout=60)
    print(out.strip())
    if "IMPORT_OK" not in out:
        sys.exit("Firmware Pico da import bo'lmadi - yuqoridagi xatoni ko'ring. Pico to'xtatilgan holda qoldi.")
    device(port, ORNAT_WDT, capture=True, flag=TEMP_FLAG, keep=keep_nowdt)
    if keep_nowdt:
        print("Sozlash rejimi: WDT O'CHIQ (/nowdt). Stanokda doimiy ishlatishdan oldin: stanok.py ornat")
    else:
        print("WDT yoqiq.")
    return True


# ---------------------------------------------------------------- sozla

SOZLA = r"""
if not _ex('/eventlog.py'):
    print('Firmware o`rnatilmagan. Avval: stanok.py ornat')
else:
    import config
    from eventlog import Store
    s = Store()
    changed, rejected = s.update_config({{cfg}})
    if rejected:
        print('Rad etildi:', rejected)
    c = s.data['config']
    print('Pico dagi sozlamalar: t1=%s, t2=%s, t_reason=%s' % (
        _hm(c['t1']), _hm(c['t2']), _hm(c['t_reason'])))
    print('   t_merge=%s s, rele ushlash t_relay=%s s, avto o`chirish: %s' % (
        c['t_merge'], c['t_relay'], 'yoqiq' if c['auto_shutdown'] else 'o`chiq'))
"""


def parse_sozla(args):
    """Pico ga ulanishdan oldin tekshiriladi - noto'g'ri qiymatga qayta yuklanmasin.

    Bo'sh turish vaqtlari DAQIQADA kiritiladi (t1, t2, t_reason).
    Texnik qiymatlar soniyada: t_merge (signal filtri), t_relay (rele).
    Birlikni ochiq yozish ham mumkin: t1=15m, t_merge=45s.
    """
    if not args:
        print("Misol: stanok.py sozla t1=15 t2=15 t_reason=15 t_merge=30s t_relay=30s")
        print("  t1, t2, t_reason - daqiqada; t_merge, t_relay - soniyada")
        sys.exit("Chegaralar: " + ", ".join(
            "%s %s..%s" % (k, fmt_limit(k, lo), fmt_limit(k, hi))
            for k, (lo, hi) in config.CONFIG_LIMITS.items()))
    cfg = {}
    for a in args:
        if "=" not in a:
            sys.exit("Noto'g'ri parametr: %s (kalit=qiymat ko'rinishida yozing)" % a)
        k, v = a.split("=", 1)
        if k == "auto_shutdown":
            cfg[k] = v.lower() in ("1", "ha", "true", "yoq")
        elif k in config.CONFIG_LIMITS:
            cfg[k] = parse_time(k, v)
        else:
            sys.exit("Noma'lum sozlama: %s" % k)
    return cfg


def parse_time(key, text):
    """Qiymatni soniyaga o'giradi (firmware soniyada ishlaydi)."""
    unit = "m" if key in MINUTE_KEYS else "s"
    t = text.strip().lower()
    if t.endswith("m") or t.endswith("s"):
        unit = t[-1]
        t = t[:-1]
    if not t.isdigit():
        sys.exit("%s = %s noto'g'ri: butun son kerak" % (key, text))
    sec = int(t) * (60 if unit == "m" else 1)
    lo, hi = config.CONFIG_LIMITS[key]
    if not lo <= sec <= hi:
        sys.exit("%s = %s noto'g'ri, chegara %s..%s"
                 % (key, text, fmt_limit(key, lo), fmt_limit(key, hi)))
    return sec


def fmt_limit(key, sec):
    if key in MINUTE_KEYS:
        return "%g daqiqa" % (sec / 60.0)
    return "%d soniya" % sec


def fmt_cfg(c):
    """Sozlamalarni odam o'qiydigan ko'rinishda (daqiqa/soniya)."""
    return ("t1=%s t2=%s t_reason=%s | t_merge=%s s, t_relay=%s s | avto o'chirish: %s"
            % (human_min(c.get("t1")), human_min(c.get("t2")), human_min(c.get("t_reason")),
               c.get("t_merge"), c.get("t_relay"),
               "yoqiq" if c.get("auto_shutdown") else "o'chiq"))


def human_min(sec):
    if sec is None:
        return "—"
    return "%g daqiqa" % (sec / 60.0)


def cmd_sozla(port, args):
    cfg = parse_sozla(args)
    print("Eslatma: MES ulanganda sozlamalarni MES yuboradi va bular almashtiriladi.")
    device(port, SOZLA, cfg=repr(cfg))
    return True


# ---------------------------------------------------------------- kuzat

KUZAT = r"""
import gc
if not _ex('/main.py'):
    print('Firmware o`rnatilmagan. Avval: stanok.py ornat')
else:
    import config
    import main as fw
    from clock import iso_now, time_is_sane
    # USB port shu vosita bilan band - firmware kompyuterga yozmaydi
    app = fw.App(use_wdt=False, port=fw.NoPort())
    c = app.ctrl
    def hms():
        if time_is_sane():
            return iso_now()[11:]
        s = c.now_ms // 1000
        return '+%02d:%02d:%02d' % (s // 3600, s // 60 % 60, s % 60)
    _append = app.log.append
    def _logged(t, *a, **k):
        ev = _append(t, *a, **k)
        extra = ''
        if t == 'POWER_ON':
            extra = ', shpindel %s s' % ev.get('run_sec')
        print('%s  >> HODISA #%d %s, davomiylik %s s%s, sabab %s' % (
            hms(), ev['seq'], t, ev['duration_sec'], extra, 'so`raladi' if ev['reason_required'] else 'kerak emas'))
        return ev
    app.log.append = _logged
    cfg = app.store.cfg
    print('Firmware ishga tushdi: WDT o`chiq, vaqt manbai %s%s' % (
        c.time_source, '' if time_is_sane() else ' (soat noma`lum - Pico yoqilgandan beri vaqt ko`rsatiladi)'))
    print('t1=%s, t2=%s, t_reason=%s, t_merge=%s s, t_relay=%s s, avto o`chirish: %s  | kuzatish {{minutes}} daqiqa' % (
        _hm(cfg('t1')), _hm(cfg('t2')), _hm(cfg('t_reason')), cfg('t_merge'), cfg('t_relay'),
        'yoqiq' if cfg('auto_shutdown') else 'o`chiq'))
    print('-' * 72)
    last = None
    t_last = 0
    t0 = time.ticks_ms()
    try:
        while time.ticks_diff(time.ticks_ms(), t0) < {{minutes}} * 60000:
            app.step()
            key = (c.state, c.pwr.value, c.run.value, c.home.value,
                   1 if c.maint_on else 0, 1 if c.relay_active else 0)
            now = time.ticks_ms()
            if key != last or time.ticks_diff(now, t_last) >= 30000:
                extra = ''
                if c.state in ('IDLE_HOME', 'IDLE_AWAY'):
                    lim = cfg('t1') if c.home.value else cfg('t2')
                    extra = '  bo`sh %d s (chegara %s)' % (c.idle_sec, _hm(lim))
                    if c.maint_on:
                        extra += ' (kalit: o`chirish bloklangan)'
                    elif not cfg('auto_shutdown'):
                        extra += ' (avto o`chirish o`chiq)'
                    elif c._shutdown_failed:
                        extra += ' (o`chmadi, qayta urinilmaydi)'
                    else:
                        extra += ', %d s dan keyin o`chiriladi' % max(0, lim - c.idle_sec)
                if last is not None and key[5] != last[5]:
                    print('%s  !! RELE %s' % (hms(), 'YONDI - stanok o`chirilmoqda' if key[5] else 'BO`SHADI'))
                print('%s  %-12s PWR=%d RUN=%d HOME=%d KALIT=%d RELE=%d%s' % ((hms(),) + key + (extra,)))
                last = key
                t_last = now
            time.sleep_ms(config.TICK_MS)
    finally:
        c._relay_off()
        print('Kuzatish tugadi, rele bo`shatildi.')
"""


def cmd_kuzat(port, args):
    minutes = int(args[0]) if args and args[0].isdigit() else 120
    print("Haqiqiy firmware mantig'i ishlaydi: taymer tugasa stanok O'CHIRILADI.")
    print("To'xtatish: Ctrl+C. Tugagach firmware odatiy (WDT bilan) qayta ishga tushadi.")
    device(port, KUZAT, minutes=minutes)
    return True


# ---------------------------------------------------------------- apparat

def cmd_apparat(port, args):
    """pico_hwcheck.py ni Pico da ishga tushiradi (fayllari /test da bo'lishi kerak)."""
    code, out = device(port, "print('TEST_BOR' if _ex('/test/config.py') else 'TEST_YOQ')",
                       capture=True, timeout=20)
    if "TEST_BOR" not in out:
        print("Sinov fayllari yuklanmoqda...")
        files = []
        for f in FIRMWARE_FILES:
            files += ["fs", "cp", os.path.join(FW, f), ":test/" + f, "+"]
        mp(port, "fs", "mkdir", ":test", capture=True)
        mp(port, *files[:-1], capture=True)
    print("Apparat tekshiruvi (oxirida Pico o'zi qayta yuklanadi)...")
    mp(port, "run", os.path.join(ROOT, "tools", "pico_hwcheck.py"))
    time.sleep(10)          # WDT Pico ni qayta yuklaydi
    wait_ready(port)
    return True


# ---------------------------------------------------------------- aloqa

def cmd_aloqa(port, args):
    """MES sahifasi o'rniga ishlaydi: ishlayotgan firmware bilan USB orqali.

    Firmware to'xtatilmaydi. Har 10 s kompyuter vaqti yuboriladi, holat va
    hodisalar ko'rsatiladi, hodisalar faylga saqlanib tasdiqlanadi.
    """
    import serial
    minutes = int(args[0]) if args and args[0].isdigit() else 10
    out_dir = os.path.join(ROOT, "jurnal")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "aloqa_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        s = serial.Serial(port, 115200, timeout=0.1)
    except serial.SerialException as e:
        sys.exit("Port ochilmadi (%s). Brauzer yoki boshqa dastur portni band qilgan bo'lishi mumkin." % e)
    print("USB aloqa %d daqiqa. To'xtatish: Ctrl+C" % minutes)
    print("-" * 72)

    def send(obj):
        s.write((json.dumps(obj) + "\n").encode())

    received = set()
    acked = None
    last_time = 0.0
    last_key = None
    t_state = 0.0
    got_json = False
    warned = False
    n_events = 0
    buf = b""
    t0 = time.time()
    try:
        with open(path, "a", encoding="utf-8") as saved:
            while time.time() - t0 < minutes * 60:
                now = time.time()
                if now - last_time >= 10:
                    lt = time.localtime()
                    send({"t": "time", "ts": [lt.tm_year, lt.tm_mon, lt.tm_mday,
                                              lt.tm_hour, lt.tm_min, lt.tm_sec]})
                    last_time = now
                buf += s.read(4096)
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        text = raw.decode(errors="replace").strip()
                        if text:
                            print("   [pico] %s" % text)
                        continue
                    if not isinstance(msg, dict):
                        continue
                    got_json = True
                    kind = msg.get("t")
                    stamp = time.strftime("%H:%M:%S")
                    if kind == "hello":
                        print("%s  ALOQA: %s, yuklanish #%s, hodisalar seq=%s tasdiqlangan=%s%s" % (
                            stamp, msg.get("machine"), msg.get("boot"), msg.get("seq"),
                            msg.get("last_ack"), ", vaqt so'radi" if msg.get("need_time") else ""))
                        acked = msg.get("last_ack")
                    elif kind == "state":
                        if acked is None or msg.get("last_ack", 0) > acked:
                            acked = msg.get("last_ack", 0)
                        key = (msg.get("state"), msg.get("pwr"), msg.get("run"), msg.get("home"),
                               msg.get("maint"), msg.get("relay"))
                        if key != last_key or now - t_state >= 60:
                            print("%s  %-12s PWR=%s RUN=%s HOME=%s KALIT=%s RELE=%s  bo'sh %ss, yoqiq %ss"
                                  " (shpindel %ss)  Pico soati: %s" % (
                                      (stamp,) + key + (msg.get("idle_sec"), msg.get("on_sec"),
                                                        msg.get("run_sec"), msg.get("ts") or "noma'lum")))
                            last_key = key
                            t_state = now
                    elif kind == "event":
                        seq = msg.get("seq")
                        if isinstance(seq, int) and seq not in received:
                            saved.write(json.dumps(msg, ensure_ascii=False) + "\n")
                            saved.flush()
                            received.add(seq)
                            n_events += 1
                            when = msg.get("end") or "vaqti noma'lum"
                            print("%s  >> HODISA #%s %s, %s s, %s%s" % (
                                stamp, seq, msg.get("type"), msg.get("duration_sec"),
                                when.replace("T", " "),
                                " (vaqt tiklandi)" if msg.get("time_restored") else ""))
                        if acked is None:
                            acked = min(received) - 1
                        # Faqat uzilishsiz saqlanganlar tasdiqlanadi (TZ 5.3)
                        n = acked
                        while n + 1 in received:
                            n += 1
                        acked = n
                        send({"t": "ack", "seq": acked})
                    elif kind == "log":
                        print("%s  [pico] %s" % (stamp, msg.get("msg")))
                if not got_json and not warned and time.time() - t0 > 12:
                    warned = True
                    print("Pico dan javob yo'q. Firmware ishlamayotgan bo'lishi mumkin:"
                          " python tools/stanok.py holat")
    except KeyboardInterrupt:
        print("\nTo'xtatildi.")
    finally:
        s.close()
    print("-" * 72)
    print("Qabul qilingan hodisalar: %d. Saqlandi: %s" % (n_events, path))
    return True


# ---------------------------------------------------------------- jurnal

def cmd_jurnal(port, args):
    from eventlog import to_message
    count = int(args[0]) if args else 30
    out_dir = os.path.join(ROOT, "jurnal")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw = os.path.join(out_dir, "events_%s.jsonl" % stamp)
    code, out = device(port, "print('BOR' if _ex('/events.jsonl') else 'YOQ')", capture=True, timeout=20)
    if "BOR" not in out:
        print("Pico da jurnal yo'q (hali hodisa bo'lmagan).")
        return True
    mp(port, "fs", "cp", ":/events.jsonl", raw, capture=True, timeout=120)
    rows = []
    with open(raw, "rb") as f:
        for line in f:
            try:
                rows.append(to_message(json.loads(line)))
            except (ValueError, IndexError, TypeError):
                pass
    csv_path = raw[:-6] + ".csv"
    cols = ["seq", "type", "start", "end", "duration_sec", "job", "operator",
            "reason_required", "time_uncertain", "id"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols + ["qo'shimcha"])
        for r in rows:
            extra = {k: v for k, v in r.items() if k not in cols and k != "machine"}
            w.writerow([r.get(k) for k in cols] + [json.dumps(extra, ensure_ascii=False) if extra else ""])
    print("%d ta hodisa. Oxirgi %d tasi:" % (len(rows), min(count, len(rows))))
    print("%6s  %-16s %-19s %8s  %-6s %-6s %s" % ("seq", "turi", "boshlanish", "davom,s", "sabab", "vaqt", "qo'shimcha"))
    for r in rows[-count:]:
        extra = {k: v for k, v in r.items() if k not in cols and k != "machine"}
        print("%6s  %-16s %-19s %8s  %-6s %-6s %s" % (
            r["seq"], r["type"], (r["start"] or "").replace("T", " "),
            "-" if r["duration_sec"] is None else r["duration_sec"],
            "kerak" if r["reason_required"] else "-",
            "noaniq" if r["time_uncertain"] else "aniq",
            json.dumps(extra, ensure_ascii=False) if extra else ""))
    print("Saqlandi: %s" % csv_path)
    return True


# ---------------------------------------------------------------- tozala

TOZALA = r"""
import json
for p in ('/events.jsonl', '/events.jsonl.tmp'):
    if _ex(p):
        os.remove(p)
try:
    st = json.load(open('/state.json'))
    seq = st.get('seq', 0)
    st['last_ack'] = seq
    st['log_floor'] = seq
    st.update({'planned': None, 'blocked': False, 'job': None, 'operator': None})
    with open('/state.json', 'w') as f:
        json.dump(st, f)
    print('Jurnal o`chirildi. Sozlamalar saqlandi, keyingi hodisa ID si #%d dan davom etadi.' % (seq + 1))
except (OSError, ValueError):
    print('Jurnal o`chirildi.')
"""


def cmd_tozala(port, args):
    print("Pico dagi hodisalar jurnali o'chiriladi (sinovdan keyin, ishga tushirishdan oldin).")
    print("ID hisoblagichi saqlanadi - MES da ID lar takrorlanmaydi.")
    if not ask_yes("Oldin 'jurnal' buyrug'i bilan nusxa oldingizmi? O'chiraymi?"):
        return True
    device(port, TOZALA)
    return True


# ---------------------------------------------------------------- main

COMMANDS = {
    "holat": cmd_holat,
    "datchik": cmd_datchik,
    "rele-tur": cmd_rele_tur,
    "rele": cmd_rele,
    "vaqt": cmd_vaqt,
    "ornat": cmd_ornat,
    "sozla": cmd_sozla,
    "kuzat": cmd_kuzat,
    "apparat": cmd_apparat,
    "aloqa": cmd_aloqa,
    "jurnal": cmd_jurnal,
    "tozala": cmd_tozala,
}


def main():
    argv = sys.argv[1:]
    port = None
    if argv and (argv[0].upper().startswith("COM") or argv[0].startswith("/dev/")):
        port = argv.pop(0)
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    name, args = argv[0], argv[1:]
    if name == "sozla":
        parse_sozla(args)
    if port is None:
        port = find_port()
        if port is None:
            sys.exit("Pico topilmadi. USB kabelni ulang yoki portni ko'rsating: stanok.py COM5 %s" % name)
    print("Pico: %s" % port)
    if name == "aloqa":
        return 0 if cmd_aloqa(port, args) else 1
    prepare(port)
    restart = True
    try:
        restart = COMMANDS[name](port, args) is not False
    except KeyboardInterrupt:
        print("\nTo'xtatildi.")
    finally:
        if restart:
            finish(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
