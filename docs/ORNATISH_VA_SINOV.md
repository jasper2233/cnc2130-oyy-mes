# CNC 2130 — datchiklarni o'rnatish va Pico ni stanokda sinash

Bu yo'riqnoma MES ulanmagan holda Pico W ni stanokka o'rnatish, har bir
datchikni alohida tekshirish va avtomatik o'chirish mantig'ini haqiqiy
stanokda sinash uchun. Elektr talablari TZ 3-bo'limida.

Har bosqich oxirida **qabul mezoni** bor. Mezon bajarilmasa keyingi
bosqichga o'tilmaydi.

---

## 0. Tayyorgarlik

**Noutbuk:** Windows, Python 3. Bir marta o'rnatiladi:

```
pip install mpremote pyserial
```

**USB kabel** ma'lumot uzatadigan bo'lishi kerak (faqat quvvat beradigan
kabelda Pico topilmaydi).

**Asboblar:** multimetr, 24 V laboratoriya quvvat bloki (bo'lmasa stanokning
24 V i), TZ 3.1 dagi komponentlar.

**Aloqa.** Pico stanok yonidagi kompyuterga micro USB bilan doimiy ulanadi.
Wi-Fi yo'q, kompyuterga dastur o'rnatilmaydi: MES sahifasi Chrome/Edge da
portni o'zi ochadi. Sinov paytida shu kabelga noutbuk (vosita) ulanadi.

**Soat moduli (DS3231) kerak emas.** Vaqt kompyuterdan keladi. Kompyuter
o'chiq paytda Pico yoqiq turgan vaqtni hisoblagich bilan sanaydi.

**USB portni bir vaqtda faqat bitta dastur ochadi.** Vositani ishlatishdan
oldin kompyuterdagi MES sahifasini yoping (yoki Pico ga ulanishni uzing).

**Vosita.** Hamma amallar bitta buyruq bilan bajariladi (loyiha papkasida):

```
python tools/stanok.py BUYRUQ
```

| Buyruq | Nima qiladi |
|---|---|
| `holat` | Pico da nima bor: firmware, WDT, sozlamalar, jurnal, soat |
| `datchik [soniya]` | Datchiklarni jonli ko'rish, sakrash va shovqinni sanaydi |
| `rele-tur` | Rele moduli aktiv-LOW yoki aktiv-HIGH ekanini aniqlaydi |
| `rele [soniya]` | Stanokni rele orqali haqiqatan o'chirib ko'radi |
| `vaqt` | Kompyuter vaqtini Pico ga yozadi (odatda MES sahifasi o'zi yuboradi) |
| `ornat` | `firmware/` ni Pico ga o'rnatadi, WDT yoqiladi |
| `sozla t1=15 t2=15` | Taymerlarni o'zgartiradi (MES o'rniga). Bo'sh turish vaqtlari **daqiqada**, `t_merge` va `t_relay` **soniyada** |
| `kuzat [daqiqa]` | Firmware ni ishga tushirib, holat va hodisalarni jonli ko'rsatadi |
| `aloqa [daqiqa]` | MES sahifasi o'rniga: ishlayotgan firmware bilan USB aloqa, hodisalarni saqlaydi va tasdiqlaydi |
| `jurnal` | Hodisalarni kompyuterga ko'chiradi, `jurnal/*.csv` (Excel) |
| `tozala` | Sinov jurnalini o'chiradi, ID hisoblagichi saqlanadi |

Vosita ishlayotgan firmware ni o'zi xavfsiz to'xtatadi va ish tugagach qayta
ishga tushiradi. Shu sababli **har buyruqdan keyin bo'sh turish taymeri noldan
boshlanadi** (`aloqa` bundan mustasno).

> Vosita to'satdan uzilsa (kabel chiqib ketsa, oyna yopilsa), Pico to'xtagan
> holda qolishi mumkin. `python tools/stanok.py holat` ni bir marta ishga
> tushiring — firmware odatiy holatga qaytadi.

---

## 1. Xavfsizlik qoidalari

1. Barcha ulash ishlari stanok **tokdan uzilgan** holda bajariladi.
2. Pico GND i stanokning 0 V iga **ulanmaydi** (TZ 3.5.5). 24 V tomoni faqat
   optoparaning kirishida.
3. Rele kontaktlari stanokka faqat **7-bosqichda** ulanadi. Undan oldin rele
   hech narsaga ulanmagan bo'ladi.
4. `rele` va `kuzat` buyruqlari stanokni haqiqatan o'chiradi. Oldin dastur
   tugagan, shpindel to'xtagan bo'lsin. Stanok tokdan butunlay uzilganda DSP
   kontrolleri fayllari buzilishi mumkin (TZ 4.4).
5. Stanokda sinov paytida yonida odam turadi.

---

## 2. Ulanish sxemasi

### 2.1. Pico W pinlari

| Signal | GPIO | Pico oyog'i | Qayerga |
|---|---|---|---|
| `K_OFF` rele | GP0 | 1 | Rele modulining IN kirishi |
| `S_PWR` | GP1 | 2 | Optopara 1 kollektori |
| `S_RUN` | GP5 | 7 | Optopara 2 kollektori |
| `S_HOME` | GP9 | 12 | Optopara 3 kollektori |
| `S_MAINT` ta'mirlash kaliti | GP10 | 14 | Kalit, ikkinchi uchi GND ga |
| DS3231 SDA / SCL | GP20 / GP21 | 26 / 27 | Ixtiyoriy, hozir ulanmaydi |
| 3V3 | 3V3(OUT) | 36 | GP0 tortuvchi rezistori (aktiv-LOW modulda; aktiv-HIGH da GND ga — 2.3 ga qarang) |
| 5 V | VSYS | 39 | 5 V adapter (diod orqali), rele moduli VCC — 2.4 ga qarang |
| GND | GND | 3, 8, 13, 18, 23, 28, 33, 38 | Optoparalar emitteri, rele moduli, adapter minusi |

Optoparalar emitteri Pico GND iga ulanadi. Kirishlarda Pico ning ichki
pull-up rezistori ishlaydi: signal bo'lsa pin LOW, dastur buni "signal bor"
deb o'qiydi (TZ 3.4).

### 2.2. Optopara kirishlari (PC817, 2.2 kΩ / 0.5 Vt)

Rezistordan o'tadigan tok taxminan (24 − 1.2) / 2200 ≈ 10 mA.

**S_PWR — stanok tarmoqda.** Anod → 2.2 kΩ → stanokning +24 V, katod →
stanokning 0 V. 24 V manbai stanokning asosiy kaliti **orqasida** bo'lishi
shart: stanok o'chirilganda 24 V ham yo'qolsin.

**S_HOME — induktiv datchik (NPN, NO)**, TZ 3.5.2:
jigarrang → +24 V, ko'k → 0 V, qora → optopara katodi.
Optopara anodi → 2.2 kΩ → +24 V.

**S_RUN — chastotnik "ishlamoqda" chiqishi.** Chastotnik sozlamalarida
ko'p funksiyali chiqishga "RUN / ishlamoqda" funksiyasi beriladi
(parametr nomi chastotnik hujjatida). Ulanish chiqish turiga bog'liq:

- *Rele kontakti (quruq kontakt):* +24 V → kontakt → 2.2 kΩ → anod,
  katod → 0 V.
- *Ochiq kollektor (Y1/DO va COM):* anod → 2.2 kΩ → +24 V, katod → Y1,
  COM → shu 24 V ning 0 V i.

### 2.3. Rele moduli

VCC → 5 V, GND → Pico GND, IN → GP0.

**GP0 ga 10 kΩ tortuvchi rezistor qo'yiladi (TZ 3.5.3).** Yo'nalishi modul
turiga bog'liq, `rele-tur` buyrug'i aytadi:

| Modul turi | Rele yonadi | Rezistor |
|---|---|---|
| aktiv-HIGH (`RELAY_ACTIVE_LOW = False`) | GP0 = 3.3 V da | 10 kΩ GP0 dan **GND** ga |
| aktiv-LOW (`RELAY_ACTIVE_LOW = True`) | GP0 = 0 V da | 10 kΩ GP0 dan **3V3** ga |

Rezistor mantiq uchun emas, **xavfsizlik uchun** kerak. Pico yuklanayotganda,
reset paytida va firmware to'xtatilgan paytlarda GP0 "suzib" qoladi
(hech kim boshqarmaydi). O'shanda rele tasodifan yonib, ishlayotgan stanokni
o'chirib qo'yishi mumkin. Rezistor pinni doim xavfsiz tomonga tortib turadi.

Kontaktlar (COM/NO) stanokdagi vaqt relesining ishga tushirish zanjiriga
ulanadi. Zanjirni stanok elektrigi belgilaydi.

**Ishlash tartibi.** Taymer tugagach rele NO dan NC ga o'tadi va
**`t_relay` soniya yonib turadi** (standart 30 s), keyin o'zi bo'shaydi.
Stanok shundan oldin o'chsa yoki shpindel qayta yoqilsa, rele darhol
bo'shaydi. Ya'ni rele impuls beradi va hech qachon yoniq qolib ketmaydi —
aks holda puskatel zanjiri uzilgan holda qolib, stanokni qayta yoqib
bo'lmasdi. Pico ning toki uzilsa ham rele bo'shaydi.

Vaqtni puskatel ishonchli uzilishiga yetadigan qilib tanlang:

```
python tools/stanok.py sozla t_relay=45     # soniyada
```

### 2.4. Quvvat

Pico ikki manbadan quvvat oladi va ikkalasi ham kerak:

1. **Kompyuter USB si** — ma'lumot va quvvat (micro USB ulagichi).
2. **Alohida 220 V → 5 V adapter** — kompyuter o'chganda ham Pico ishlashda
   davom etib, yoqiq turgan vaqtni sanashi uchun. Stanokning 24 V idan emas
   (TZ 3.5.4).

Adapter **VSYS (39-oyoq) ga Schottky diod orqali** ulanadi (masalan 1N5819:
anod adapterning +5 V ga, katod VSYS ga), minusi GND ga. Pico platasida
USB (VBUS) va VSYS orasida o'z diodi bor, shuning uchun ikki manba bir-biriga
zarar bermaydi. **Diodsiz ulash mumkin emas** — adapter kompyuterning USB
portiga kuchlanish beradi.

Rele moduli VCC ham shu 5 V dan (VSYS) oladi.

USB kabel:
- ma'lumot uzatadigan, ekranlangan, iloji boricha qisqa (5 m dan oshmasin),
  ikki uchida ferrit halqa bilan;
- chastotnik va kuch kabellaridan uzoqda yotqiziladi.

Kompyuterda: *Boshqaruv paneli → Quvvat → Rejani o'zgartirish →
Qo'shimcha → USB → USB selective suspend → O'chirilgan*. Aks holda Windows
USB portni uxlatib, aloqa uzilib qoladi.

MES sahifasi (brauzer) doim ochiq turishi kerak. Sahifa yopilsa, Pico
ma'lumotni fleshga yig'ib turadi (2000 tagacha hodisa) va sahifa ochilganda
hammasini tartib bilan yuboradi. Lekin shu davrda MES stanokni o'chiq deb
ko'rsatadi.

### 2.5. Shovqindan himoya (TZ 3.5.6)

- Signal kabellari ekranlangan bo'ladi, ekran bir tomondan yerga ulanadi.
- Signal kabellari kuch kabellaridan kamida 20 sm masofada yotqiziladi.

---

## 3. Bosqichlar

### 1-bosqich. Stolda: Pico + rele moduli + USB aloqa

Stanok ishtirok etmaydi.

```
python tools/stanok.py ornat
python tools/stanok.py holat
python tools/stanok.py rele-tur
python tools/stanok.py aloqa 2
```

`aloqa` ishlayotgan firmware ga ulanadi: `ALOQA: cnc-2130-01, yuklanish #..`
qatori va holat chiqadi, "Pico soati" kompyuter vaqtiga teng bo'ladi.

`rele-tur` releni 0 V va 3.3 V bilan navbatma-navbat yoqadi. Rele qaysi
bosqichda chertganini javob sifatida kiritasiz, vosita `config.py` ni
tekshiradi. Qiymat noto'g'ri bo'lsa, tuzatish taklif qilinadi. Tuzatilgandan
keyin `ornat` qayta ishga tushiriladi.

Rele **ikkala bosqichda ham** yonib tursa, 5 V modul Pico ning 3.3 V i bilan
to'liq o'chmayapti. Bu holda modul almashtiriladi yoki GP0 va modul orasiga
tranzistor qo'yiladi. Shunday modul stanokka ulanmaydi.

Oxirida USB ni 1 daqiqaga uzib, qayta ulang va `holat` ni qayta ishga tushiring.

**Qabul mezoni:**
- [ ] `holat`: firmware fayllari 6/6, WDT yoqiq
- [ ] `aloqa`: Pico javob beradi, soati kompyuter vaqtiga teng
- [ ] Adapter diod orqali ulangan: USB kabelni 1 daqiqaga uzib qayta ulang,
      `aloqa 1` dagi `yuklanish #` raqami o'zgarmagan (Pico o'chmagan)
- [ ] `rele-tur`: rele faqat bitta bosqichda yonadi, `config.py` mos
- [ ] Pico yoqilayotgan paytda rele bir lahza ham chertmaydi (USB ni 5 marta uzib ulang)

### 2-bosqich. Optoparalarni stolda tekshirish

Shkafda yig'ilgan plata, stanokka hali ulanmagan. Har optoparaning kirishiga
navbat bilan laboratoriya blokidan 24 V beriladi.

```
python tools/stanok.py datchik 300
```

**Qabul mezoni:**
- [ ] 24 V berilganda tegishli signal `SIGNAL BOR`, olinganda `signal yo'q`
- [ ] Boshqa kirishlar o'zgarmaydi (simlar almashib ketmagan)
- [ ] Har ulash-uzishga aynan 1 ta o'zgarish, `DIQQAT` qatori yo'q

### 3-bosqich. S_PWR — stanok tarmoqda

Stanok tokdan uzilgan holda ulanadi, keyin:

```
python tools/stanok.py datchik 300
```

Stanokni asosiy kalit bilan 5 marta yoqib o'chiring.

**Qabul mezoni:**
- [ ] Stanok yoqiq: `PWR=1`, o'chiq: `PWR=0`
- [ ] Har yoqish-o'chirishga aynan 1 ta o'zgarish
- [ ] Stanok o'chirilganda Pico ishlashda davom etadi (alohida quvvat)

### 4-bosqich. S_HOME — 0 nuqta datchigi

Datchik stanok 0 nuqtaga kelganda metall bayroqcha ro'parasida bo'ladigan
qilib o'rnatiladi. Oraliq datchik hujjatidagi ish masofasining taxminan
80% i (5 mm datchik uchun ~4 mm).

```
python tools/stanok.py datchik 600
```

Stanokni 0 nuqtaga 10 marta olib boring va olib keting. Keyin shpindelni
yoqib, 0 nuqtada 2 daqiqa turing.

**Qabul mezoni:**
- [ ] 0 nuqtada har safar `HOME=1`, undan 2–3 sm chetda `HOME=0`
- [ ] Shpindel aylanayotganda `HOME` o'z-o'zidan o'zgarmaydi (chastotnik shovqini yo'q)
- [ ] Ish paytida bayroqcha datchikka tegmaydi

### 5-bosqich. S_RUN — shpindel ishlamoqda

```
python tools/stanok.py datchik 900
```

Tekshiriladi:
1. Shpindelni qo'lda yoqib o'chirish, 10 marta.
2. Past aylanishda (eng kichik tezlik) yoqish.
3. Haqiqiy dastur: tezlik o'zgarishi, asbob almashtirish.

Asbob almashtirish yoki yo'nalish o'zgarishida `RUN` bir necha soniyaga 0 ga
tushishi mumkin. Bu normal — `t_merge` (30 s) shunday qisqa uzilishlarni
birlashtiradi. `datchik` chiqishidagi eng uzun uzilishni yozib qo'ying:
u `t_merge` dan qisqa bo'lishi kerak.

**Qabul mezoni:**
- [ ] Shpindel aylansa `RUN=1`, to'liq to'xtaganda `RUN=0`
- [ ] Past tezlikda ham `RUN=1`
- [ ] Doimiy aylanishda `DIQQAT ... sakrash yoki shovqin` qatori chiqmaydi
- [ ] Dastur ichidagi eng uzun `RUN=0` uzilishi: ____ s (t_merge dan qisqa)

### 6-bosqich. Ta'mirlash kaliti

Kalit shkaf eshigiga o'rnatiladi, GP10 va GND orasiga ulanadi
(optoparasiz, faqat shkaf ichida).

**Qabul mezoni:**
- [ ] `datchik`: kalit burilganda `MAINT=1`, qaytarilganda `MAINT=0`

### 7-bosqich. Rele → stanokning vaqt relesi

Rele kontaktlari stanok elektrigi bilan birga ulanadi. Keyin:

1. Stanok yoqiq, shpindel **aylanmoqda**:
   `python tools/stanok.py rele` → `BEKOR: S_RUN = 1` chiqishi kerak.
2. Stanok yoqiq, dastur tugagan, shpindel to'xtagan:
   `python tools/stanok.py rele` → `NATIJA: STANOK O'CHDI - ... s keyin`.
3. Stanokni odatdagidek qayta yoqing.

**Qabul mezoni:**
- [ ] Shpindel aylanayotganda sinov boshlanmaydi
- [ ] Stanok o'chdi, vaqt ____ s (vaqt relesi sozlamasiga mos)
- [ ] O'chish vaqti `t_relay` dan uzun bo'lsa, `sozla t_relay=...` bilan oshiring
- [ ] Stanok o'chgandan keyin rele bo'shaydi, stanok normal qayta yoqiladi
- [ ] Stanok yoqilgan zahoti o'chib qolmaydi

### 8-bosqich. To'liq mantiq sinovi (qisqa taymerlar)

```
python tools/stanok.py sozla t1=2 t2=3 t_reason=1 t_merge=30s t_relay=30s auto_shutdown=1
python tools/stanok.py kuzat 90
```

`kuzat` haqiqiy firmware ni ishga tushiradi va har holat o'zgarishini,
bo'sh turish vaqtini va yozilgan hodisalarni ko'rsatadi. Quyidagilarni
navbat bilan bajaring:

| № | Harakat | Kutilgan natija |
|---|---|---|
| 1 | Dastur tugab, stanok 0 nuqtada to'xtaydi | 120 s dan keyin `RELE YONDI`, stanok o'chadi, `AUTO_SHUTDOWN` |
| 2 | Stanokni yoqib, 0 nuqtadan tashqarida qoldiring | 180 s dan keyin o'chadi |
| 3 | Shpindelni 10 s ga to'xtatib yana yoqing | Hodisa yozilmaydi (t_merge) |
| 4 | Shpindelni 90 s ga to'xtatib yana yoqing | `SPINDLE_IDLE` yoki `UNPLANNED_STOP`, 90 s |
| 5 | Shpindel 5 daqiqadan uzoq aylanadi | Hech qachon o'chirilmaydi |
| 6 | Ta'mirlash kaliti yoqiq, 4 daqiqa bo'sh | O'chirilmaydi: `kalit: o'chirish bloklangan` |
| 7 | Stanokni kalit bilan o'chirib, yoqing | `POWER_CYCLE` |
| 8 | Stanok ishlayotganda Pico quvvatini (USB va adapter) 10 s ga uzing | Stanok ishlayveradi, rele chertmaydi |

8-bandda `kuzat` uziladi. Pico ni qayta ulang va `holat` ni ishga tushiring.
Keyin kompyuter o'chiq holatni tekshiring:

| № | Harakat | Kutilgan natija |
|---|---|---|
| 9 | USB kabelni uzing (kompyuter o'chdi yoki brauzer yopildi — Pico uchun bir xil). Stanokni yoqib 5 daqiqa qoldiring, keyin o'chiring. USB ni qayta ulab `aloqa 2` | `POWER_ON` ~300 s, `(vaqt tiklandi)`, vaqti haqiqiy o'chirilgan paytga mos |
| 10 | USB uzilgan holda stanokni 3 daqiqa yoqib, o'chiring. Adapterni ham 10 s uzing (Pico toki ketdi). USB ni ulab `aloqa 2` | `POWER_ON` ~180 s, `vaqti noma'lum` — MES uni shu kun ichida hisoblaydi |

```
python tools/stanok.py jurnal
```

**Qabul mezoni:**
- [ ] 1–10 bandlar jadvaldagidek
- [ ] `jurnal` dagi hodisalar va davomiyliklar qilingan harakatlarga mos
- [ ] Soxta o'chirish bo'lmadi

### 9-bosqich. Kuzatuv davri (1–2 hafta, haqiqiy taymerlar)

```
python tools/stanok.py sozla t1=15 t2=15 t_reason=15 t_merge=30s
python tools/stanok.py holat
```

Stanok odatdagidek ishlaydi, Pico mustaqil. Operator daftarga yozib boradi:
qachon to'xtatdi, nima sababdan, stanok kutilmaganda o'chdimi, kompyuter
qachon o'chirilgan.

MES sahifasi hali bo'lmasa, hodisalar Pico da yig'iladi (2000 tagacha).
Har hafta:

```
python tools/stanok.py jurnal
```

CSV faylni daftar bilan solishtiring.

**Qabul mezoni:**
- [ ] Kutilmagan (noto'g'ri) o'chirish yo'q
- [ ] Har bir haqiqiy to'xtash jurnalda bor
- [ ] Har yoqish-o'chirish uchun `POWER_ON` bor, jami vaqti daftarga mos

### 10-bosqich. Ishga topshirish

```
python tools/stanok.py jurnal
python tools/stanok.py tozala
python tools/stanok.py holat
```

- [ ] Kuzatuv jurnallari (`jurnal/*.csv`) saqlandi va MES dasturchilariga berildi
- [ ] `holat`: WDT yoqiq, jurnal bo'sh
- [ ] MES sahifasi kompyuterda Chrome/Edge da ochiq, Pico ga ulangan
      (namuna: `tools/usb_panel.html`), USB selective suspend o'chirilgan

---

## 4. Muammolar

| Belgi | Sabab va yechim |
|---|---|
| `Pico topilmadi` | Kabel faqat quvvat beradi, boshqasini oling. Port: `stanok.py COM5 holat` |
| Signal hech o'zgarmaydi | Optopara teskari ulangan yoki 24 V yo'q. Multimetr: rezistordan keyin ~22 V, optopara kirishida ~1.2 V |
| Signal doim `SIGNAL BOR` | Optopara chiqishi (kollektor-emitter) teskari yoki qisqa tutashuv |
| `DIQQAT ... shovqin` | Ekran ulanmagan yoki kabel kuch kabeli yonida. Qo'shimcha: pin va 3V3 orasiga 4.7 kΩ, pin va GND orasiga 100 nF |
| Rele `rele-tur` da ikkala bosqichda yonadi | 5 V modul 3.3 V bilan o'chmaydi — modulni almashtiring yoki tranzistor qo'ying |
| Stanok yoqilishi bilan o'chib qoladi | `RELAY_ACTIVE_LOW` noto'g'ri. Darhol rele simini uzing, `rele-tur` |
| `rele`: `stanok O'CHMADI` | Vaqt relesi zanjiri, yoki uning vaqti `t_relay` dan uzun: `sozla t_relay=60` |
| `holat`: `DS3231: TOPILMADI` | Normal — soat moduli ishlatilmaydi |
| `holat`: `Pico soati: 2021-...` | Normal — Pico hali kompyuterdan vaqt olmagan. MES sahifasi yoki `aloqa` ulanganda to'g'rilanadi |
| `aloqa`: `Pico dan javob yo'q` | Firmware to'xtagan (`stanok.py holat` ni bir marta ishga tushiring) |
| `Port ochilmadi ... band` | MES sahifasi yoki boshqa dastur portni ochgan. Sahifani yoping |
| Aloqa vaqti-vaqti bilan uziladi | USB selective suspend yoqiq (2.4), kabel uzun yoki chastotnik yonida |
| USB uzilganda Pico o'chib qoladi | Adapter ulanmagan yoki diod teskari (2.4) |
| `holat`: `WDT: O'CHIQ` | `ornat --sozlash` ishlatilgan. Stanokda doimiy ish uchun `ornat` |
| Vosita uzilib qoldi | `stanok.py holat` ni bir marta ishga tushiring |
| Firmware ishga tushmaydi | `stanok.py ornat` chiqishidagi xatoni ko'ring. Oxirgi chora: BOOTSEL ni bosib USB ulang, MicroPython ni qayta yozing, keyin `ornat` |
