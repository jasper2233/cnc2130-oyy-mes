# CNC 2130 — avtomatik o'chirish va MES monitoring

Raspberry Pi Pico W asosidagi modul. Stanok bo'sh turganini aniqlaydi,
belgilangan vaqt o'tgach avtomatik o'chiradi va barcha hodisalarni
MES tizimiga uzatadi.

```
Datchiklar ── optoparalar ── Pico ── micro USB ── Kompyuter (brauzerda MES sahifasi) ── MES server
                               │
                          rele → stanok vaqt relesi
```

- Pico stanok yonidagi kompyuterga **micro USB** bilan ulanadi. Wi-Fi yo'q,
  kompyuterga qo'shimcha dastur o'rnatilmaydi: MES sahifasi Chrome/Edge
  ichida USB portni o'zi ochadi (Web Serial).
- **Soat moduli kerak emas.** Vaqt kompyuterdan keladi.
- Kompyuter o'chiq bo'lsa Pico to'xtamaydi: stanok mantig'i ishlaydi,
  hodisalar lokal jurnalga yoziladi va kompyuter yoqilganda yuboriladi.
- Pico dan ma'lumot kelmasa MES stanokni o'chiq deb hisoblaydi.

## Papkalar

```
firmware/     Pico ga yuklanadigan MicroPython kodi
  config.py         sozlamalar, pin ta'riflari
  clock.py          vaqt yordamchilari
  ds3231.py         soat moduli drayveri (ixtiyoriy)
  eventlog.py       avtonom hodisa jurnali
  controller.py     holat mashinasi, o'chirish mantig'i
  main.py           USB aloqa va asosiy sikl (protokol izohi shu yerda)

docs/
  TZ_CNC2130_MES_v1.0.md   texnik topshiriq
  ORNATISH_VA_SINOV.md     stanokka o'rnatish va sinash yo'riqnomasi

mes/          Ma'lumotlar bazasi sxemasi

tools/
  stanok.py         stanokda o'rnatish va sinash vositasi (USB orqali)
  usb_panel.html    MES dasturchilari uchun namunaviy sahifa (Web Serial)
  simulate.py       firmware mantig'ini kompyuterda sinash (25 ssenariy)
  pico_test.py      xuddi shu ssenariylarni Pico ning o'zida sinash
  pico_hwcheck.py   Pico apparat tekshiruvi (flesh, WDT, pinlar, USB)
  webpanel.py       virtual Pico + MES paneli brauzerda
  stubs/            soxta machine, USB va MES (sinov uchun)
```

## Stanokka o'rnatish

To'liq tartib: **[docs/ORNATISH_VA_SINOV.md](docs/ORNATISH_VA_SINOV.md)**.

```
pip install mpremote pyserial
python tools/stanok.py ornat       # firmware ni Pico ga o'rnatish
python tools/stanok.py rele-tur    # rele modulining turini aniqlash
python tools/stanok.py datchik     # datchiklarni jonli ko'rish
python tools/stanok.py kuzat 60    # firmware mantig'ini stanokda kuzatish
python tools/stanok.py aloqa 60    # MES sahifasi o'rniga USB aloqa
python tools/stanok.py jurnal      # hodisalarni CSV ga ko'chirish
```

## Apparatsiz sinov

Firmware mantig'ini o'zgartirgandan keyin albatta:

```
python tools/simulate.py           # kompyuterda, ~3 s
python tools/pico_test.py COM5     # Pico ning o'zida, ~100 s
```

Virtual panel (Pico va MES ni brauzerda sinash, stanok kerak emas):

```
python tools/webpanel.py           # http://127.0.0.1:8130
```

## Sozlamalar

Ish vaqtidagi qiymatlar MES sahifasidan USB orqali keladi va `state.json`
ga saqlanadi. `config.py` dagi `DEFAULT_CONFIG` faqat birinchi yoqilganda
ishlatiladi. MES ulanmagan paytda: `python tools/stanok.py sozla t1=15`.

Panellarda va `stanok.py` da bo'sh turish vaqtlari **daqiqada** kiritiladi,
texnik qiymatlar (signal filtri va rele) **soniyada**. Protokolda va
firmware ichida hammasi soniyada ketadi.

| Parametr | Ma'nosi | Kiritiladi | Chegara |
|---|---|---|---|
| `t1` | 0 nuqtada bo'sh turish chegarasi | daqiqa | 0.5..1440 daqiqa |
| `t2` | 0 nuqtadan tashqarida bo'sh turish chegarasi | daqiqa | 0.5..1440 daqiqa |
| `t_reason` | Shundan uzun to'xtashga sabab so'raladi | daqiqa | 0..1440 daqiqa |
| `t_merge` | Shundan qisqa uzilishlar birlashtiriladi | soniya | 0..600 s |
| `t_relay` | Rele NO→NC holatida yonib turadigan vaqt (impuls) | soniya | 1..600 s |
| `auto_shutdown` | Avtomatik o'chirish (MES da yoqiladi/o'chiriladi) | — | true/false |

`t_relay` — rele qancha vaqt yoniq turishi. Shu vaqt o'tishi bilan rele
**albatta bo'shaydi**, ya'ni yoniq holatda qolib ketmaydi. Stanok shundan
oldin o'chsa yoki shpindel qayta yoqilsa, rele darhol bo'shatiladi (TZ 4.3).

Bu muhim: rele kontakti puskatel zanjirini uzadi. Rele bo'shamasa, stanokni
qayta yoqib bo'lmaydi. Pico ning toki uzilsa ham rele o'zi bo'shaydi.

Chegaradan tashqaridagi qiymat rad etiladi va Pico eski qiymat bilan ishlaydi.

## MES dasturchilari uchun

### Namuna

`tools/usb_panel.html` — Pico bilan to'g'ri ishlaydigan eng kichik sahifa:
ulanish, vaqt yuborish, hodisani saqlab tasdiqlash, sozlama va buyruqlar,
kun bo'yicha yoqiq vaqt. `saveEvent()` funksiyasini serverga yozadigan
qilish kifoya. Pico siz ko'rish: faylni `#demo` bilan oching.

`tools/stubs/mesmock.py` — xuddi shu qoidalar Python da (sinovlar uchun).

### Web Serial talablari

- Chrome yoki Edge. Firefox va Safari da Web Serial yo'q.
- Sahifa **xavfsiz manbadan** ochilishi shart: `https://`, `http://localhost`
  yoki fayl. Oddiy `http://192.168.x.x` da `navigator.serial` bo'lmaydi.
- Birinchi marta foydalanuvchi portni tanlaydi (`requestPort`), keyin
  `getPorts()` bilan sahifa o'zi ulanadi.
- Port parametrlari ahamiyatsiz (USB CDC), `baudRate: 115200`.

### Protokol

Har qator bitta JSON obyekt, oxirida `\n`. `t` — xabar turi. JSON bo'lmagan
qatorlar (Pico ning `print` xabarlari) e'tiborsiz qoldiriladi.

**Kompyuter → Pico**

| Xabar | Qachon |
|---|---|
| `{"t": "time", "ts": [2026, 9, 15, 14, 32, 11]}` | Har 10 s (mahalliy vaqt). Bu "sahifa ochiq" belgisi ham: 30 s hech qanday xabar kelmasa Pico aloqa yo'q deb biladi. Kun vaqti kerak bo'lmasa, o'rniga `{"t": "hello"}` yuborilsa ham bo'ladi — hodisalar davomiyligi baribir to'g'ri keladi |
| `{"t": "ack", "seq": 17}` | Hodisa serverga saqlangandan keyin |
| `{"t": "config", "t1": 900, "t2": 900, "t_reason": 900, "t_merge": 30, "t_relay": 30, "auto_shutdown": true}` | Sozlama o'zgarganda |
| `{"t": "jobs", "jobs": [...]}` | Rejalashtirilgan ishlar (TZ 9.1) |
| `{"t": "cmd", "cmd": "...", ...}` | Buyruqlar, pastda |

| Buyruq (`cmd`) | Maydonlar |
|---|---|
| `start_job` | `job`, `operator` |
| `end_job` | — |
| `planned_stop` | `type` (LUNCH, SETUP, ...), `planned_sec` — maksimal davomiylik (TZ 8.4) |
| `planned_stop_end` | — |
| `block` | `value`: true/false (TZ 10.3.2) |

**Pico → kompyuter**

| Xabar | Mazmun |
|---|---|
| `hello` | Aloqa boshlanganda: `machine`, `boot`, `need_time`, `seq`, `last_ack` |
| `state` | Har 10 s, buyruq va sozlamadan keyin darhol: TZ 15.2 maydonlari + `on_sec`, `run_sec`, `uptime_sec`, `boot`, `time_source` (`pc`/`rtc`/`none`), `ts` (soat noma'lum bo'lsa null) |
| `event` | Tasdiqlanmagan hodisalar (TZ 15.3) + `seq` |
| `log` | Rad etilgan xabar haqida matn |

### Tasdiq qoidasi

`{"t": "ack", "seq": N}` — N gacha **uzilishsiz** saqlangan eng katta seq.
Oraliqda saqlanmagan yozuv bo'lsa, undan keyingilar tasdiqlanmaydi, Pico
ularni har 10 s qayta yuboradi. Hodisa `id` bo'yicha dublikatsiz saqlanadi.
Istisno: saqlangan nusxada `start` null bo'lsa, qayta kelgan nusxadagi aniq
vaqt yoziladi (sahifa vaqt yuborishga ulgurmay hodisa kelgan holat).

### Vaqt qoidasi

Asosiysi — **davomiylik** (`duration_sec`). U hamma holatda to'g'ri keladi,
chunki Pico o'z hisoblagichi bilan o'lchaydi. Kun vaqti (`start`, `end`) —
qo'shimcha: kompyuterdan vaqt kelgan bo'lsa qo'yiladi.

| Holat | Hodisada |
|---|---|
| Sahifa ochiq, vaqt yuborilgan | `start`, `end` aniq |
| Sahifa yopiq (yoki kompyuter o'chiq), Pico toki uzilmagan | Sahifa ochilgach aniq vaqt tiklanib keladi, `time_restored: true` |
| Sahifa yopiq va Pico toki ham uzilgan | `start`, `end` = null, `time_uncertain: true`, faqat `duration_sec`. MES uni **qabul qilingan kun ichida** hisoblaydi |

Pico uchun "kompyuter o'chiq" va "brauzer yopiq" bir xil: xabar kelmasa
hodisalar fleshda (2000 tagacha) yig'iladi va sahifa ochilganda xronologik
tartibda yuboriladi.

### Hodisa turlari va qo'shimcha maydonlar

| Tur | Ma'nosi | Qo'shimcha |
|---|---|---|
| `POWER_ON` | Stanok shuncha vaqt yoqiq turdi (o'chganda yoziladi). Oylik hisobot shundan | `run_sec` — shundan shpindel aylangan vaqt; `from_boot` — boshlanishi Pico yoqilgan payt |
| `SPINDLE_IDLE` / `UNPLANNED_STOP` | Bo'sh turish (ish bor/yo'q) | rejadan oshganda: `note`, `stop_type` |
| `AUTO_SHUTDOWN` | Pico taymer bo'yicha o'chirdi | `limit_sec`, `at_home`, `had_job` |
| `SHUTDOWN_FAILED` | Rele ishladi, stanok o'chmadi | `cause`: `pwr_still_on` / `run_started` |
| `POWER_CYCLE` | Stanok tokdan uzilib, qayta yoqildi | — |
| `PLANNED_STOP` | Rejali to'xtash | `stop_type` |
| `LOG_FULL` | Jurnal to'ldi | — |

Hodisa ID si: `cnc213001/000001472` (stanok kodidan `-` olib tashlanadi).
Sxema: `mes/schema.sql` (`event_day`, `machine_on_time_daily` view).

### TZ dan farqlar

- MQTT/Wi-Fi/Ethernet o'rniga micro USB + brauzer (TZ 15).
- DS3231 majburiy emas (TZ 3.2, 5.8). Vaqt kompyuterdan.
- `NET_LOST` yo'q: ma'lumot kelmagan davr — o'chiq.
- Yangi `POWER_ON` hodisasi (stanok yoqiq turgan vaqt).
- `WORK` hodisasi yozilmaydi: ish vaqti `POWER_ON.run_sec` va holat xabarlaridan.
- `jobs` ro'yxati saqlanadi, lekin ishlatilmaydi (TZ 9.2, 9.4).

## Hujjat

Texnik topshiriq: `docs/TZ_CNC2130_MES_v1.0.md`

Kodda har bir mantiqiy blok yonida TZ bandi ko'rsatilgan, masalan
`# TZ 4.3.2`. Shu orqali qaysi talab qayerda bajarilganini topish oson.
