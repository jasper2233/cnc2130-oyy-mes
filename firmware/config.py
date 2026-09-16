"""CNC 2130 monitoring moduli - sozlamalar.

Bu fayldagi qiymatlar stanokka mos ravishda o'zgartiriladi.
Ish vaqtidagi sozlamalar (t1, t2, ...) MES dan keladi va
state.json fayliga saqlanadi. Bu yerdagilar boshlang'ich qiymat.
"""

MACHINE_ID = "cnc-2130-01"

# --- Pinlar (TZ 3.3) ---
PIN_RELAY = 0    # K_OFF   - rele modul, chiqish
PIN_PWR = 1      # S_PWR   - stanok tarmoqqa ulangan
PIN_RUN = 5      # S_RUN   - chastotnik ishlamoqda
PIN_HOME = 9     # S_HOME  - stanok 0 nuqtada
PIN_MAINT = 10   # S_MAINT - shkafdagi ta'mirlash kaliti (TZ 4.4), None = yo'q
PIN_SDA = 20     # DS3231 (ixtiyoriy, vaqt kompyuterdan keladi)
PIN_SCL = 21     # DS3231

# --- Signal mantig'i (TZ 3.4) ---
# Optopara chiqishi teskari: signal bor bo'lsa pin LOW.
INPUT_ACTIVE_LOW = True

# Ta'mirlash kaliti GP10 va GND orasiga ulanadi (optoparasiz).
# Kalit ochiq - pin ichki pull-up bilan HIGH - bloklash yo'q.
MAINT_ACTIVE_LOW = True

# Rele modulining turi. Ko'pchilik xitoy modullari aktiv-LOW.
# Modulni tekshiring: agar 3.3V berganda rele YONSA -> False qiling.
# 2026-09-16: shu stanokdagi modul 3.3 V da yondi, 0 V da o'chdi -> aktiv-HIGH.
RELAY_ACTIVE_LOW = False

# --- Vaqt sozlamalari (soniyada) ---
DEFAULT_CONFIG = {
    "t1": 900,           # 0 nuqtada bo'sh turish chegarasi
    "t2": 900,           # 0 nuqtadan tashqarida (TZ: 15 daqiqa)
    "t_reason": 900,     # shundan uzun to'xtashga sabab so'raladi
    "t_merge": 30,       # shundan qisqa uzilishlar birlashtiriladi
    # Rele NO -> NC holatida ushlab turiladigan eng uzun vaqt (TZ 4.3.2).
    # Stanok shundan oldin o'chsa rele darhol bo'shatiladi. Vaqt stanokdagi
    # vaqt relesiga qarab tanlanadi.
    "t_relay": 30,
    "auto_shutdown": True,
}

# MES dan kelgan qiymatlar shu chegarada bo'lishi shart.
# Noto'g'ri qiymat fleshga yozilsa Pico har yuklanishda yiqilardi.
CONFIG_LIMITS = {
    "t1": (30, 86400),
    "t2": (30, 86400),
    "t_reason": (0, 86400),
    "t_merge": (0, 600),
    "t_relay": (1, 600),
}

# --- Texnik parametrlar ---
DEBOUNCE_MS = 100          # TZ 4.4
HEARTBEAT_S = 10           # TZ 15.4
TICK_MS = 20               # asosiy sikl qadami
WDT_TIMEOUT_MS = 8000      # TZ 4.4 (RP2040 da maksimum 8388)
NO_WDT_FLAG = "/nowdt"     # shu fayl bo'lsa WDT yoqilmaydi (faqat sozlashda)

# --- Jurnal (TZ 5.5) ---
LOG_PATH = "/events.jsonl"
STATE_PATH = "/state.json"
LOG_MAX_BYTES = 240_000    # ~2000 ta yozuv
LOG_COMPACT_AT = 0.9       # shu darajaga yetganda siqiladi
LOG_COMPACT_MIN = 20       # kamida shuncha tasdiqlangan yozuv bo'lsa siqiladi
FLASH_RESERVE_BYTES = 16_000  # state.json uchun doim bo'sh qoldiriladi

# --- Kompyuter bilan aloqa: micro USB ---
# Kompyuterdagi MES sahifasi (brauzer, Web Serial) USB portni ochadi.
# Wi-Fi va kompyuterga qo'shimcha dastur ishlatilmaydi.
LINK_TIMEOUT_S = 30        # shuncha vaqt xabar kelmasa - kompyuter yo'q
# Signal yoki holat o'zgarsa holat xabari darhol yuboriladi (10 s kutilmaydi).
# Shovqinli signal aloqani to'ldirmasligi uchun eng qisqa oraliq:
STATE_MIN_MS = 500
EVENTS_PER_BATCH = 10      # bir martada navbatga qo'yiladigan hodisalar
RX_BYTES_PER_STEP = 512    # bir siklda o'qiladigan eng ko'p bayt
RX_MAX_LINE = 2048         # bundan uzun qator rad etiladi
TX_QUEUE_MAX = 40          # yuborish navbati
# Port ochiq, lekin kompyuter o'qimasa (brauzer qotgan) bitta yozish
# 1 s gacha bloklanadi (Pico W da o'lchangan). Shunday bo'lsa tanaffus.
TX_STALL_MS = 200
TX_BACKOFF_S = 5
