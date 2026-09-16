# Texnik topshiriq

## CNC 2130 stanogi uchun avtomatik o'chirish va MES monitoring tizimi

**Versiya:** 1.0
**Sana:** 14.09.2026
**Kontroller:** Raspberry Pi Pico W
**Dasturlash tili:** MicroPython

---

## 1. Umumiy ma'lumot

### 1.1. Maqsad

Stanok yoqilgan, lekin haqiqiy ish bajarmayotgan holatni aniqlash, belgilangan vaqt o'tgach stanokni avtomatik o'chirish, stanok holatini va bo'sh turish sabablarini MES tizimida qayd etish.

### 1.2. Qamrov

Hozirda bitta CNC 2130 stanogi. Yaqin kelajakda ikkinchi stanok qo'shiladi, shuning uchun tizim boshidanoq ko'p stanokli ishlashga moslab quriladi.

### 1.3. Cheklovlar

Pico stanokni **faqat o'chira oladi**. Masofadan yoqish funksiyasi ko'zda tutilmagan va kelajakda ham qo'shilmaydi.

### 1.4. Atamalar

| Atama | Ma'nosi |
|---|---|
| MES | Ishlab chiqarishni boshqarish tizimi |
| ERP | Korxona resurslarini rejalashtirish tizimi (ish haqi, hisob-kitob) |
| VFD | Chastotnik, shpindel tezligini boshqaruvchi qurilma |
| 0 nuqta | Stanok ish tugagach borib turadigan parkovka koordinatasi |
| Hodisa | Jurnalga yoziladigan holat o'zgarishi |
| Sabab | Bo'sh turish yoki uzilish uchun operator kiritgan izoh |

---

## 2. Tizim arxitekturasi

```
Induktiv datchik ──┐
Stanok 24V DC ─────┼── optoparalar ── Raspberry Pi Pico W ── Wi-Fi/Ethernet ── MES server
Chastotnik (VFD) ──┘                          │                                    │
                                         rele modul                                │
                                              │                          ┌─────────┴─────────┐
                                    stanokdagi vaqt relesi        Operator paneli      Ofis paneli
                                              │                                            │
                                       stanok o'chadi                                  ERP tizimi
```

Pico W aloqa bor-yo'qligiga bog'liq emas. Barcha asosiy mantiq unda mustaqil ishlaydi, MES faqat ma'lumot yig'adi va sozlamalarni beradi.

---

## 3. Apparat qismi

### 3.1. Komponentlar ro'yxati

| Komponent | Model | Soni | Izoh |
|---|---|---|---|
| Kontroller | Raspberry Pi Pico W | 1 | |
| Soat moduli | **DS3231** | 1 | DS1307 to'g'ri kelmaydi, 3.2-bandga qarang |
| Optopara | PC817 yoki EL817 | 3 | Kirish signallari uchun |
| Rele modul | 1 kanalli, 5 V | 1 | NO/NC kontaktli |
| Induktiv datchik | NPN, NO, 5 mm, 6–36 V DC | 1 | 0 nuqtani aniqlash |
| Rezistor | 2.2 kΩ / 0.5 Vt | 3 | Optopara kirishiga |
| Rezistor | 10 kΩ | 1 | GP0 tortuvchi |
| Quvvat bloki | 220 V → 5 V, 2 A | 1 | Pico uchun alohida |
| Batareya | CR2032 | 1 | DS3231 uchun |
| Shkaf | Metall, IP54 | 1 | |
| Kabel | Ekranlangan, 4 tomirli | — | |

### 3.2. Soat moduli haqida muhim eslatma

**DS1307 ishlatilmaydi.** Ikkita sabab bor:

1. **Kuchlanish mosligi.** DS1307 ning ishlash diapazoni 4.5–5.5 V. 5 V da uning mantiqiy "bir" chegarasi 3.5 V, Pico esa 3.3 V beradi. I2C aloqa beqaror ishlaydi.
2. **Batareya sarfi.** DS1307 ni 3.3 V dan quvvatlaganda mikrosxema asosiy tok yo'q deb hisoblaydi va doimo batareyadan ishlaydi. Batareya bir necha oyda tugaydi.

**DS3231 olinadi.** Ishlash diapazoni 2.3–5.5 V, Pico ning 3.3 V i bilan to'g'ridan-to'g'ri ulanadi. Termokompensatsiyalangan, aniqligi yiliga taxminan 2 daqiqa. I2C manzili DS1307 bilan bir xil (0x68), moduli tashqi ko'rinishidan ham farq qilmaydi.

Ulanish: `SDA → GP20`, `SCL → GP21`, `VCC → 3V3`, `GND → GND`.

### 3.3. Pin taqsimoti

| Pin | Nomi | Turi | Vazifasi |
|---|---|---|---|
| GP0 | `K_OFF` | Chiqish | Rele modul, o'chirish buyrug'i |
| GP1 | `S_PWR` | Kirish | Stanok tarmoqqa ulangan (24 V → optopara) |
| GP5 | `S_RUN` | Kirish | Chastotnik ishlamoqda (→ optopara) |
| GP9 | `S_HOME` | Kirish | Stanok 0 nuqtada (NPN datchik → optopara) |
| GP20 | `SDA` | I2C | DS3231 |
| GP21 | `SCL` | I2C | DS3231 |
| GP2–GP4, GP6–GP8, GP10–GP19, GP22, GP26–GP28 | — | — | Zaxira |

Zaxira pinlar kelajakdagi kengaytirishlar uchun ochiq qoldiriladi: qo'shimcha datchiklar, OLED displey, ogohlantirish chirog'i, Ethernet moduli.

### 3.4. Signal mantig'i

Optopara chiqishi teskari mantiq beradi: signal bor bo'lganda Pico pini LOW ga tushadi. Dasturda bu invertlanadi. Hujjatning qolgan qismida mantiqiy qiymat ishlatiladi, ya'ni `1 = signal bor`.

**Datchiklarning boshlang'ich holati:**

- Induktiv datchik: NO. Stanok 0 nuqtaga kelmaguncha ochiq. Kelganda yopiladi va `S_HOME = 1` bo'ladi.
- Rele modul: NO. `T1` yoki `T2` tugaganda NC ga o'tadi, stanok o'chgunicha shu holatda turadi, keyin NO ga qaytadi.

### 3.5. Elektr talablari

**3.5.1. Optoparalar.** Kirishga ketma-ket 2.2 kΩ / 0.5 Vt rezistor (24 V da taxminan 10 mA). Chiqish Pico ning ichki pull-up i bilan ishlaydi.

**3.5.2. Datchik ulanishi.** Jigarrang sim → +24 V, ko'k sim → GND, qora sim → optopara katodiga. Optopara anodi rezistor orqali +24 V ga. NPN chiqish minusga tortganda optopara yonadi.

**3.5.3. GP0 himoyasi.** Pico yuklanayotganda va reset paytida pinlar suzib qoladi. Rele tasodifan ishga tushmasligi uchun GP0 ga 10 kΩ tortuvchi rezistor qo'yiladi. Yo'nalishi rele modulining turiga bog'liq: aktiv-LOW modulda 3.3 V ga pull-up, aktiv-HIGH da GND ga pull-down. Standart holat har doim "rele NO, stanok ishlayveradi" bo'lishi shart.

**3.5.4. Quvvat.** Pico alohida 220 V → 5 V adapterdan quvvatlanadi, stanokning 24 V idan emas. Aks holda stanokni o'chirgan zahoti Pico ham o'chadi va MES ga xabar yubora olmaydi.

**3.5.5. Galvanik ajratish.** Barcha 24 V zanjirlari optopara orqali ajratiladi. Pico va stanok GND lari birlashtirilmaydi.

**3.5.6. Shovqindan himoya.** Chastotnik kuchli elektromagnit shovqin beradi. Signal kabellari ekranlangan bo'ladi, ekran bir tomondan yerga ulanadi. Signal kabellari kuch kabellaridan kamida 20 sm masofada yotqiziladi.

---

## 4. Holatlar va o'chirish mantig'i

### 4.1. Holatlar jadvali

| Holat | Shartlar | Taymer |
|---|---|---|
| `OFF` | `S_PWR = 0` | — |
| `WORKING` | `S_PWR = 1`, `S_RUN = 1` | Taymerlar nolga tushadi |
| `IDLE_HOME` | `S_PWR = 1`, `S_RUN = 0`, `S_HOME = 1` | `T1` sanaydi |
| `IDLE_AWAY` | `S_PWR = 1`, `S_RUN = 0`, `S_HOME = 0` | `T2` sanaydi |
| `PLANNED_STOP` | Operator oldindan belgilagan | Taymerlar to'xtaydi |
| `SHUTDOWN` | Taymer tugadi | Rele ishga tushadi |
| `BLOCKED` | Smena qabul qilinmagan | Ish boshlanmaydi |

### 4.2. Algoritm

1. `S_RUN` 1 ga o'tsa — `T1` va `T2` darhol nolga tushadi.
2. `S_RUN = 0` va `S_HOME = 1` bo'lsa — `T1` sanashni boshlaydi. Tugagach `SHUTDOWN`.
3. `S_RUN = 0` va `S_HOME = 0` bo'lsa — `T2` sanashni boshlaydi. Tugagach `SHUTDOWN`.
4. Rejalashtirilgan to'xtash belgilangan bo'lsa, taymerlar sanamaydi.
5. `SHUTDOWN` da GP0 relesi NC ga o'tadi va stanokdagi vaqt relesini ishga tushiradi.

### 4.3. O'chirish ketma-ketligi

| Qadam | Holat | GP0 relesi |
|---|---|---|
| 1 | Normal ish | NO — ochiq |
| 2 | `T1` yoki `T2` tugadi | NC — yopiladi |
| 3 | Stanokdagi vaqt relesi ishga tushdi | NC — ushlab turiladi |
| 4 | Stanok o'chdi, `S_PWR = 0` | NO — bo'shatiladi |
| 5 | Boshlang'ich holat | NO |

**4.3.1.** Rele impuls emas, ushlab turiladi. Bo'shatish sharti faqat bitta: `S_PWR` ning 0 ga tushishi.

**4.3.2.** Agar `S_PWR` 30 soniya ichida 0 ga tushmasa, rele baribir bo'shatiladi va MES ga `SHUTDOWN_FAILED` hodisasi yuboriladi.

**4.3.3.** Pico qayta yuklansa, rele boshlang'ich NO holatiga qaytadi.

**4.3.4.** O'chirish faqat `S_RUN = 0` bo'lganda amalga oshadi. Shpindel aylanayotganda hech qachon o'chirilmaydi.

### 4.4. Xavfsizlik cheklovlari

- Barcha kirishlarga 100 ms dasturiy debounce qo'yiladi.
- Pico da apparat watchdog (WDT) yoqiladi.
- Shkafga mexanik kalit qo'yiladi, u ta'mirlash vaqtida avtomatik o'chirishni butunlay bloklaydi.
- Stanok butunlay quvvatsiz qoladi. Shuning uchun DSP kontroller fayllari buzilmasligi uchun o'chirish faqat `S_RUN = 0` da bajariladi.

---

## 5. Avtonom jurnal

**5.1.** Har bir holat o'zgarishi darhol lokal jurnalga yoziladi, aloqa bo'lmasa ham.

**5.2.** Aloqa tiklanganda yuklanmagan yozuvlar xronologik tartibda MES ga uzatiladi. Yangi ma'lumot eskisidan oldin ketmaydi.

**5.3.** MES har bir yozuvni qabul qilganini tasdiqlaydi (`ack`). Tasdiq kelmaguncha yozuv jurnalda saqlanadi va qayta yuboriladi.

**5.4.** Har bir yozuvda takrorlanmas ID bo'ladi: `cnc2130-01/000001472`. Qayta yuborilganda MES dublikat yaratmaydi.

**5.5. Xotira.** Bitta yozuv taxminan 120 bayt. Pico W flesh xotirasida 2000 tagacha hodisa saqlanadi. Jurnal halqasimon bufer, to'lganda eng eski **yuklangan** yozuvlar o'chiriladi. Yuklanmagan yozuv hech qachon o'chirilmaydi.

**5.6.** Bufer yuklanmagan yozuvlar bilan to'lsa, Pico `LOG_FULL` holatiga o'tadi va buni alohida hodisa sifatida belgilaydi.

**5.7.** Fleshga faqat holat o'zgarganda yoziladi, davriy emas.

### 5.8. Vaqt manbalari

Ustunlik tartibida:

1. MES serveridan yoki kompyuterdan (aloqa bor paytda) — DS3231 har sinxronlashda tuzatiladi
2. DS3231 dan (aloqa yo'q paytda)
3. Ikkalasi ham yo'q bo'lsa — Pico ning ichki hisoblagichi, oxirgi ma'lum vaqtdan boshlab. Bunday yozuvlarga `time_uncertain: true` belgisi qo'yiladi va MES ularni operator tasdiqlashiga yuboradi.

---

## 6. Hodisa turlari

| Kod | Qachon yoziladi | Sabab so'raladimi |
|---|---|---|
| `WORK` | Normal ish davri | Yo'q |
| `NET_LOST` | Aloqa uzildi, stanok ishlashda davom etdi | Ha |
| `POWER_CYCLE` | `S_PWR` 0 ga tushib qayta 1 bo'ldi | Ha |
| `UNPLANNED_STOP` | Ish paytida `S_RUN` to'satdan 0 bo'ldi | Ha |
| `SPINDLE_IDLE` | `S_RUN = 0` holati `T_reason` dan uzoq davom etdi | Ha |
| `AUTO_SHUTDOWN` | Pico `T1`/`T2` bo'yicha o'chirdi | Ha |
| `SHUTDOWN_FAILED` | Rele ishladi, lekin stanok o'chmadi | Ha |
| `PLANNED_STOP` | Oldindan belgilangan to'xtash | Yo'q |
| `LOG_FULL` | Jurnal to'ldi | Yo'q |

**6.1. Tok uzilishi.** Sexdan tok ketsa, Pico ham MES ham to'xtaydi. Bu davr "stanok o'chiq, ish yo'q" deb hisoblanadi, alohida hodisa yaratilmaydi va sabab so'ralmaydi.

**6.2. Mayda uzilishlarni birlashtirish.** `T_merge` dan qisqa uzilishlar alohida hodisa qilinmaydi, oldingi hodisaga qo'shiladi. Bu chastotnik signalidagi sakrashlar navbatni to'ldirib yuborishining oldini oladi.

---

## 7. Sabablar navbati

**7.1. Tartib — FIFO.** Navbat eng eski yopilmagan hodisadan boshlanadi. Operator uni o'tkazib yuborib keyingisiga o'ta olmaydi.

**7.2. Nima bloklanadi.** Sabab kiritilmaguncha smena yopilmaydi va hisobot chiqarilmaydi. Yangi ish boshlash bloklanmaydi.

**7.3. Ogohlantirish.** Navbatda yopilmagan yozuv paydo bo'lishi bilan operator panelida ko'rsatkich chiqadi.

**7.4. Guruhlab javob berish.** Navbatda ketma-ket bir xil turdagi hodisalar bo'lsa, "hammasiga bir xil sabab" tugmasi orqali bir necha yozuv birdan yopiladi.

**7.5. Sabablar klassifikatori.** Erkin matn emas, oldindan tayyorlangan ro'yxat:

| Kod | Sabab | Kimga yoziladi |
|---|---|---|
| `TOOL_BREAK` | Asbob singan | Stanok |
| `NO_MATERIAL` | Material tugagan | Ishlab chiqarish |
| `POWER_OUT` | Elektr uzilgan | Tashqi |
| `PROGRAM_ERR` | Dastur xatosi | Ishlab chiqarish |
| `BREAKDOWN` | Stanok buzilgan | Stanok |
| `REPAIR` | Ta'mirlash | Stanok |
| `LUNCH` | Tushlik | Rejalashtirilgan |
| `HANDOVER` | Smena topshirish | Rejalashtirilgan |
| `SETUP` | Dastur tayyorlash, sozlash | Rejalashtirilgan |
| `OTHER` | Boshqa | Matn majburiy |

---

## 8. Rejalashtirilgan to'xtashlar

**8.1.** Operator o'z panelidan oldindan belgilaydi: tushlik, asbob almashtirish, ta'mirlash, smena topshirish, dastur tayyorlash.

**8.2.** Belgilangan oraliqda `SPINDLE_IDLE` hodisasi yaratilmaydi va sabab so'ralmaydi.

**8.3.** Avtomatik o'chirish bloklanadi, `T1`/`T2` sanamaydi.

**8.4.** Har bir turning maksimal davomiyligi belgilanadi. Undan oshsa, ortiqcha vaqt alohida hodisa sifatida yoziladi va sabab so'raladi. Masalan tushlik 60 daqiqa deb belgilangan, 90 daqiqa turgan bo'lsa, 30 daqiqa uchun sabab so'raladi.

**8.5.** Bu belgilar Pico ga oldindan uzatiladi, aloqa uzilsa ham ishlaydi.

---

## 9. Ish topshiriqlari

**9.1.** MES rejalashtirilgan ishlar ro'yxatini oldindan Pico ga yuboradi.

**9.2.** Aloqa uzilganda operator ro'yxatdagi ishni boshlashi mumkin. Pico ish kodi bilan vaqtni lokal yozib boradi.

**9.3.** Aloqa tiklanganda hammasi sinxronlanadi, sabab so'ralmaydi.

**9.4.** Aloqa yo'q paytda ro'yxatda bo'lmagan ish bajarilsa, MES "bu davrda qaysi ish bajarildi" savolini beradi va bu sabab navbatiga qo'shiladi.

---

## 10. Smena va ishni topshirish

### 10.1. Kirish

Har bir operator MES ga o'z kodi bilan kiradi. Kod stanok yonidagi panelda kiritiladi.

### 10.2. Topshirish tartibi

**10.2.1.** Topshirayotgan operator MES dagi ketma-ketlik bo'yicha qaysi ishgacha bajarganini belgilaydi.

**10.2.2.** Tugallanmagan ishni keyingi operatorga o'tkazadi yoki kelishuv asosida o'zida qoldiradi.

**10.2.3.** Tasdiqlash ikki tomonlama. Har bir operator **o'z panelidan, o'z kodi bilan kirib** topshirish yoki qabul qilish tugmasini bosadi.

**10.2.4.** Tugallanmagan ish ikki operatordan biriga biriktirilmaguncha **hech kimga yozilmaydi**. Bu davr "stanok ishladi" holatiga o'tadi, vaqt stanokka yoziladi. ERP da hisob-kitob buzilmaydi, ish haqi stanokning o'ziga hisoblanadi.

### 10.3. Qabul qilmaslik

**10.3.1.** Sabab bartaraf etilmagan bo'lsa (odatda stanok buzilganda), 2-smena operatori qabul qilishi shart emas.

**10.3.2.** Qabul qilmasa, 2-smena boshlanmaydi. Stanok `BLOCKED` holatiga o'tadi.

**10.3.3.** Blokni **smenani qabul qilayotgan operatorning o'zi** ochadi, faqat 1-smenadagi ishlar masalasi aniq qilib olingandan keyin.

**10.3.4.** Sabab bartaraf bo'lmasa, smena tugatiladi. Stanok "ta'mirda" holatiga o'tadi.

### 10.4. Operator ishga chiqmagan holat

**10.4.1.** Operator kasal bo'lsa yoki ishga chiqmasa, MES administratori yoki shunday huquq berilgan foydalanuvchi uning o'rniga boshqa operatorni belgilaydi.

**10.4.2.** Tugallanmagan ishlar yangi operatorga topshiriladi.

**10.4.3.** Qabul qilib olgan operator javobgarlikni o'z zimmasiga oladi. Shu paytdan boshlab ish va vaqt unga yoziladi.

**10.4.4.** Bunday almashtirishda ikki tomonlama tasdiq talab qilinmaydi, chunki birinchi operator mavjud emas. Almashtirish audit jurnaliga yoziladi: kim, kimning o'rniga, qachon va qaysi ishlar.

### 10.5. Rollar va huquqlar

| Rol | Huquqlar |
|---|---|
| Operator | Ish boshlash va tugatish, sabab kiritish, rejalashtirilgan to'xtash belgilash, smena topshirish va qabul qilish, blokni ochish (10.3.3 bo'yicha) |
| Administrator | Operatorning hamma huquqlari, qo'shimcha: operator almashtirish, sozlamalarni o'zgartirish (14-bo'lim), hisobotlar, avtomatik o'chirishni bloklash |

Har bir administrator amali audit jurnaliga yoziladi.

---

## 11. Javobgarlik va ERP

**11.1. Bo'sh turish vaqti.** Stanok buzilgani sababli bo'sh turgan vaqt **stanokka** yoziladi, operatorga emas. Sababni operator kiritib qo'yadi: buzildi, ta'mirda va hokazo.

**11.2. Brak.** Brak chiqsa, bevosita **shu ishni boshlagan** operatorga yoziladi.

**11.3. Ish haqi.** Ish qaysi operatorga biriktirilgan bo'lsa, o'shanga hisoblanadi. Biriktirilmagan ish stanokka yoziladi.

**11.4. ERP ga uzatiladigan yozuv:**

```json
{
  "job": "ORD-2026-0417",
  "machine": "cnc-2130-01",
  "operator": "OP-1142",
  "shift": 1,
  "start": "2026-09-14T08:12:00",
  "end": "2026-09-14T15:47:00",
  "work_sec": 21300,
  "idle_sec": 5820,
  "started_by": "OP-1142",
  "finished_by": "OP-1155",
  "responsible": "OP-1142",
  "handover": {
    "from": "OP-1142",
    "to": "OP-1155",
    "at": "2026-09-14T15:47:00",
    "accepted": true
  }
}
```

`started_by` maydonining alohida saqlanishi shart, chunki brak javobgarligi shunga bog'liq.

---

## 12. Operator paneli

Panel o'rta ma'lumotli xodim uchun mo'ljallangan. Loyihalash tamoyillari:

**12.1. Jadval yo'q.** Operator paneliga jadval, filtr, saralash qo'yilmaydi. Faqat katta tugma va aniq matn.

**12.2. Rangli holat.** Ekranning yuqori qismida stanok holati katta harflar va rang bilan ko'rsatiladi:

| Rang | Holat | Matn |
|---|---|---|
| Yashil | Ishlamoqda | ISHLAYAPTI |
| Sariq | Bo'sh turibdi | BO'SH TURIBDI — 12 daqiqa |
| Ko'k | Rejalashtirilgan to'xtash | TUSHLIK |
| Qizil | Bloklangan yoki sabab kutilmoqda | SABAB KIRITING |
| Kulrang | O'chiq | O'CHIQ |

**12.3. Asosiy tugmalar.** Bosh ekranda ko'pi bilan 5 ta tugma:

- Ishni boshlash
- Ishni tugatish
- To'xtatish (rejalashtirilgan)
- Sabab kiritish
- Smena topshirish

**12.4. Menyu chuqurligi.** Ko'pi bilan 2 daraja. Uch marta bosishdan ko'p talab qiladigan amal bo'lmasligi kerak.

**12.5. Sabab tanlash.** Bitta ekranda 8 tadan ortiq variant ko'rsatilmaydi. Har biri katta tugma, qisqa matn va belgi bilan. "Boshqa" tanlanganda klaviatura ochiladi va matn majburiy bo'ladi.

**12.6. Tasdiqlash.** Muhim amallar (ishni tugatish, smena topshirish, qabul qilmaslik) uchun "Rostdan ham?" oynasi chiqadi.

**12.7. Til.** Interfeys o'zbek tilida. Texnik atamalar minimumga tushiriladi.

**12.8. Raqamlar.** Vaqt "12 daqiqa" ko'rinishida yoziladi, "00:12:34" emas.

**12.9. Xato xabarlari.** Xato kodi ko'rsatilmaydi, nima qilish kerakligi yoziladi. Masalan "Aloqa yo'q. Ishlashda davom eting, ma'lumot keyinroq saqlanadi."

**12.10. Apparat.** Sensorli ekran, kamida 10 dyuym, sanoat sharoitiga mos korpus. Chang va namlik hisobga olinadi.

---

## 13. Ofis paneli

Ofis foydalanuvchilari uchun to'liq funksional interfeys:

- Jadval ko'rinishida barcha hodisalar, filtr va saralash bilan
- Stanoklar bo'yicha kunlik, haftalik, oylik hisobotlar
- OEE va bo'sh turish tahlili
- Sabablar bo'yicha statistika va diagrammalar
- Operatorlar kesimida ko'rsatkichlar
- Sozlamalar paneli (14-bo'lim)
- Eksport: Excel va PDF

---

## 14. Sozlash paneli

Har bir stanok uchun alohida sozlanadigan qiymatlar:

| Parametr | Ma'nosi | Boshlang'ich qiymat |
|---|---|---|
| `T1` | 0 nuqtada bo'sh turish, keyin o'chirish | sozlanadi |
| `T2` | 0 nuqtadan tashqarida bo'sh turish, keyin o'chirish | 15 daqiqa |
| `T_reason` | Shundan uzoq to'xtashga sabab so'raladi | sozlanadi |
| `T_merge` | Shundan qisqa uzilishlar birlashtiriladi | 30 soniya |
| Blokirovka | Avtomatik o'chirishni vaqtincha o'chirish | o'chiq |
| Rejalashtirilgan to'xtashlar | Turlari va maksimal davomiyligi | sozlanadi |

O'zgartirish huquqi faqat ustada. Har bir o'zgarish kim va qachon qilgani audit jurnaliga yoziladi.

Pico sozlamalarni flesh xotirada saqlaydi va qayta yuklangandan keyin ham eslab qoladi.

---

## 15. Aloqa protokoli

**Transport:** MQTT, Wi-Fi yoki Ethernet (W5500) orqali.

Sexda chastotnik shovqini kuchli bo'lgani uchun **Ethernet afzal ko'riladi**. Wi-Fi zaxira variant sifatida qoldiriladi.

### 15.1. Topiklar

| Topik | Yo'nalish | Mazmun |
|---|---|---|
| `mes/{machine}/state` | Pico → MES | Joriy holat, har 10 soniyada |
| `mes/{machine}/event` | Pico → MES | Hodisa yozuvi |
| `mes/{machine}/ack` | MES → Pico | Hodisa qabul qilingani tasdig'i |
| `mes/{machine}/config` | MES → Pico | Sozlamalar |
| `mes/{machine}/jobs` | MES → Pico | Rejalashtirilgan ishlar ro'yxati |
| `mes/{machine}/cmd` | MES → Pico | Buyruqlar (blokirovka, vaqt sinxronlash) |

### 15.2. Holat xabari

```json
{
  "machine": "cnc-2130-01",
  "state": "IDLE_HOME",
  "pwr": 1,
  "run": 0,
  "home": 1,
  "idle_sec": 412,
  "job": "ORD-2026-0417",
  "operator": "OP-1142",
  "t1": 900,
  "t2": 900,
  "t_reason": 900,
  "ts": "2026-09-14T14:32:11",
  "time_source": "ntp"
}
```

### 15.3. Hodisa xabari

```json
{
  "id": "cnc2130-01/000001472",
  "machine": "cnc-2130-01",
  "type": "SPINDLE_IDLE",
  "start": "2026-09-14T12:04:00",
  "end": "2026-09-14T12:31:00",
  "duration_sec": 1620,
  "job": "ORD-2026-0417",
  "operator": "OP-1142",
  "reason_required": true,
  "time_uncertain": false
}
```

### 15.4. Ishonchlilik

Heartbeat har 10 soniyada. Aloqa uzilsa Pico oxirgi saqlangan sozlamalar bilan mustaqil ishlashda davom etadi.

---

## 16. Ma'lumotlar bazasi sxemasi

```sql
machines (
  id, code, name, status, created_at
)

operators (
  id, code, name, role, active
)

machine_config (
  machine_id, t1, t2, t_reason, t_merge,
  auto_shutdown_enabled, updated_by, updated_at
)

shifts (
  id, machine_id, operator_id, shift_no,
  started_at, ended_at, status
)

jobs (
  id, code, name, planned_qty, status, created_at
)

job_runs (
  id, job_id, machine_id, shift_id,
  started_by, finished_by, responsible_id,
  started_at, ended_at,
  work_sec, idle_sec, qty_ok, qty_scrap,
  status
)

events (
  id, external_id, machine_id, job_run_id,
  type, started_at, ended_at, duration_sec,
  reason_required, reason_code, reason_text,
  closed_by, closed_at,
  time_uncertain, uploaded_at
)

reason_codes (
  code, name_uz, category, charge_to, active
)

planned_stops (
  id, machine_id, shift_id, type,
  planned_sec, started_at, ended_at
)

handovers (
  id, shift_from_id, shift_to_id, job_run_id,
  from_operator_id, to_operator_id,
  from_confirmed_at, to_confirmed_at,
  accepted, note
)

audit_log (
  id, entity, entity_id, action,
  user_id, old_value, new_value, created_at
)
```

**Indekslar:** `events(machine_id, started_at)`, `events(reason_required, closed_at)`, `job_runs(machine_id, started_at)`, `events(external_id)` — takrorlanmas.

---

## 17. Ishlab chiqish bosqichlari

| № | Bosqich | Muddat |
|---|---|---|
| 1 | Sxema, plata, shkaf yig'ish | 2 hafta |
| 2 | Pico dasturi: signallar, holatlar, rele | 2 hafta |
| 3 | Avtonom jurnal, DS3231, flesh saqlash | 1 hafta |
| 4 | MQTT aloqa, sinxronlash | 1 hafta |
| 5 | MES backend, ma'lumotlar bazasi | 3 hafta |
| 6 | Operator paneli | 2 hafta |
| 7 | Ofis paneli va hisobotlar | 2 hafta |
| 8 | ERP integratsiyasi | 1 hafta |
| 9 | Stanokda sinov, sozlash | 2 hafta |

Ikkinchi stanok qo'shilganda 1 va 9-bosqichlar takrorlanadi, qolgani o'zgarmaydi.

---

## 18. Ochiq savollar

Bu savollar Pico dasturini yozishga to'sqinlik qilmaydi. Ular MES tomonini ishlab chiqishdan oldin hal qilinishi kerak.

**18.1.** Administrator ishni boshqa operatorga o'tkazsa (10.4), brak javobgarligi kimda qoladi? 11.2 bandga ko'ra ishni boshlagan operatorda, lekin u ishga chiqmagan. Shu holatda qoidani o'zgartiramizmi?

**18.2.** Operator paneli qanday qurilmada ishlaydi — sanoat sensorli paneli, planshet, yoki oddiy monitor va sichqoncha?

**18.3.** Brak sonini kim va qachon kiritadi — operator ish tugaganda paneldan kiritadimi, yoki OTK alohida tekshiradimi?

**18.4.** MES serveri qayerda turadi — sexdagi kompyuterdami, ofisdagi serverdami, yoki bulutdami? Aloqa ishonchliligi shunga bog'liq.

**18.5.** ERP tizimi qaysi? Integratsiya usuli (API, fayl eksporti, to'g'ridan-to'g'ri baza) shundan kelib chiqadi.
