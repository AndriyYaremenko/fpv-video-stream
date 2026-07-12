# bladeRF як вьювер + роль-конфіг SDR — Design

**Date:** 2026-07-12
**Status:** Approved design, ready to plan (own branch off `main`).
**Target:** агент `agent/scan` (+ `agent/video/stream_demod.py`); деплой на вузол з bladeRF/HackRF.
**Context:** розширює живий SDR-вью ([[sdr-view-stream]], `2026-07-05-sdr-view-stream-design.md`)
та мультибенд-воркфлоу ([[multiband-view-workflow]], `2026-07-07-...`). Нинішній вью — тільки HackRF;
у `hackrf_source.py` докстрінг прямо закладає «a later bladeRF backend only needs the same duck type».

## Мета
Зробити так, щоб **будь-який SDR вузла (bladeRF, HackRF або обидва) міг як свіпити, так і стрімити**,
а роль задавалась конфігом юніта — «без різниці хто свіпить хто стрімить». Головна відсутня
можливість: **bladeRF як джерело для вью-стріму**, симетрично до HackRF.

## Проблема (звірено в коді)
1. Вью-стрім працює лише для HackRF. У `main.py` гілка вью для `sdr != "hackrf"` іде в
   `run_stream_persistent`, яка запускає `hackrf_transfer` — тобто для bladeRF-юніта вью **зламаний**
   (не той інструмент). bladeRF-вьювера не існує.
2. Свіп не можна вимкнути окремо: `run_cycle` викликається в головному циклі безумовно. Немає
   способу зробити юніт «чистим вьювером» (без свіпу).
3. `run_stream_source` (спільний демод-цикл) хардкодить `iq_from_int8_fast` (int8, HackRF-формат);
   bladeRF дає SC16_Q11 (int16), тож той самий цикл його не декодує.

## Модель ролей (config-driven, симетрична для обох SDR)
Роль юніта = два незалежні прапорці:

| Прапорець | Дія | Стан |
|---|---|---|
| `SCAN_ENABLED` (**нове**, default `1`) | вмикає свіп-цикл `run_cycle` | зараз неявно завжди |
| `VIEW_ENABLED` (є) | вмикає вью-стрім | зараз лише HackRF |

Комбінації на один юніт / один SDR:
- `SCAN=1 VIEW=0` — чистий свіпер (напр. bladeRF, безперервний свіп усіх бендів).
- `SCAN=0 VIEW=1` — чистий вьювер (стрімить на вимогу, не свіпить).
- `SCAN=1 VIEW=1` — обидва в одному процесі: свіпить, на команду вью паузить свій свіп, стрімить,
  повертається (нинішня HackRF-поведінка). Режим «один SDR → або-або в часі».

Топологія:
- **Один SDR** → один процес `SCAN=1 VIEW=1`; потрібна арбітрація пристрою (див. нижче).
- **Два SDR** → **два процеси (два systemd-юніти), по одному на пристрій** (як зараз). Ролі
  призначаються вільно; різні процеси й пристрої → свіп і стрім **паралельні, без взаємних пауз**.

Незмінні рішення: один процес = один SDR (не об'єднуємо обидва SDR в один процес); частотний
діапазон вью лишається 100–6000 МГц (спільний); RX5808/телеметрія поза скоупом (config-driven).

## Архітектура / потік даних
```
bladeRF unit (SCAN=1 VIEW=0) ─ свіп усіх бендів ─▶ fpv/bladerf/detection ─┐
                                                                          ├▶ browser merge ─▶ «FPV Viewer»
hackrf unit  (SCAN=0 VIEW=1) ─ анонс fpv/hackrf/view {stream:"hackrf-view"}┘        │ клік
                                                                                    ▼
              dashboard ─▶ fpv/<viewer-id>/rxcmd {view:start, freq} ─▶ viewer-юніт
                                       │
   viewer: view_controller ─▶ run_stream_source(vcfg, SOURCE, ...) ─▶ ViewEncoder ─▶ RTSP ─▶ MediaMTX ─▶ WHEP
                                       └ SOURCE = HackrfSource | BladerfViewSource (за SCAN_SDR)
```
Дашборд/сервер-код — **без змін** (уже data-driven по анонсованому `stream`).

## Компоненти

### `agent/scan/iqring.py` (нове) — винесений `IqRing`
`IqRing` зараз у `hackrf_source.py`; generic-по-байтах, уже протестований. Виносимо в окремий модуль,
імпортуємо в `hackrf_source.py` і `bladerf_source.py`. Поведінка без змін (тест `test_hackrf_source`
лишається зеленим; за потреби — окремий `test_iqring`).

### `agent/scan/bladerf_source.py` — `BladerfViewSource` (нове)
Той самий duck-type, що `HackrfSource`: `tune(freq_hz)`, `read_chunk(n_bytes, timeout_s)`,
`recover()`, `close()`, `pending_bytes()`, `dropped_bytes`, а також **`bytes_per_sample = 4`** і
**`to_iq = iq_from_sc16q11`** (див. нижче).

Наповнення кільця — **власний reader-потік**: у циклі кличе `sync_rx` (блокуючий) фіксованими
під-чанками, пише сирі SC16_Q11 байти в `IqRing`; `read_chunk` дістає `n_bytes` у порядку прибуття
(overflow дропає найстаріше → `dropped_bytes`). Окремий потік обов'язковий: демод 0.5-с чанка триває
довше за внутрішні буфери libbladeRF, тож синхронний `sync_rx` у демод-циклі дав би overrun; reader-
потік дає перекриття захоплення/демодуляції (як callback у HackRF) і видимі дропи.

- `tune`: лінива відкрити пристрій (перший `tune`), виставити частоту, стартувати reader-потік,
  очистити кільце (транзієнт тюна не має дійти до демоду).
- `recover`: close + reopen + retune (сторож USB-wedge, як у HackRF).
- `close`: зупинити reader-потік, закрити пристрій (звільнити libusb-хендл — інакше reopen у тому
  ж процесі падає NoDevError).

Радіо/канал/enum'и інжектяться (як у `BladerfDevice`) → повна тестованість без заліза. Продакшн-
фабрика (напр. `open_bladerf_view_source(gain, sample_rate, bandwidth)`) — єдине місце з `import bladerf`.

### `agent/video/stream_demod.py` — формат IQ у `run_stream_source`
Замість хардкоду `iq_from_int8_fast`:
- `chunk_bytes = int(fs * source.bytes_per_sample * CHUNK_S)`;
- `iq = source.to_iq(buf)`.
`HackrfSource`: `bytes_per_sample = 2`, `to_iq = iq_from_int8_fast`. Зміна локальна й симетрична;
HackRF-шлях поведінково не міняється. Мейлбокс-математика (`pending_bytes // chunk_bytes`) лишається
консистентною для кожного джерела.

### `agent/scan/main.py` — wiring
- Гілка вью-движка по-SDR симетрична:
  ```
  if source == "live" and sdr == "hackrf":   source = HackrfSource(...);       reset = source.close
  elif source == "live" and sdr == "bladerf": source = BladerfViewSource(...); reset = <release_sweep + source.close>
  run = run_stream_source(vcfg, source, ...)
  ```
  Хибний `run_stream_persistent`-шлях для bladeRF прибрати.
- **`SCAN_ENABLED` gate**: у головному циклі, якщо `scan_enabled == 0`, не викликати `run_cycle` —
  лише обслуговувати pending-вью (і не крутити свіп вхолосту). За `scan_enabled=0 view=off` процес
  фактично idle (валідно, але безцільно — деплой такого не робить).

### Арбітрація пристрою — тільки `SCAN=1 VIEW=1` на одному bladeRF
Два-SDR вузол = два процеси = конфлікту нема. Один bladeRF, що і свіпить, і стрімить:
- вхід у вью → `_reset_bladerf_backend()` (закрити свіп-бекенд) → відкрити `BladerfViewSource`;
- `reset`-хук вью → закрити view-source; наступний свіп-цикл перевідкриє свіп-бекенд через
  `_get_bladerf_backend`.
Чисте чергування open/close в одному процесі валідне (NoDevError — це окремий USB-wedge після
reset, не наслідок чистого close). HackRF так уже робить (sweep = one-shot subprocess, view = in-proc).

### `agent/scan/config.py`
Додати `scan_enabled: bool = True` + парсинг `SCAN_ENABLED` (як інші булеві прапорці).

## Конфіг (env)
- `SCAN_ENABLED` (нове, default `1`).
- `VIEW_ENABLED`, `VIEW_PUSH_URL`, `SCAN_SDR`, `BLADERF_GAIN`, `VIEW_SAMPLE_RATE_HZ` — існуючі.
  bladeRF-вью бере підсилення з `bladerf_gain_db`, sample_rate з `view_sample_rate_hz` (8 MS/s default).

## Error handling
- bladeRF-overrun (демод відстав) → `dropped_bytes` у stats-лозі (та сама метрика акцептації, що HackRF).
- Тиша на джерелі > `SILENCE_RECOVER_S` → `source.recover()`; після `CAPTURE_STALL_LIMIT` — сесія
  завершується помилкою (існуюча логіка `run_stream_source`, працює й для bladeRF).
- Помилка тюна/відкриття → рядок помилки в стан вью → видно в панелі.

## Testing (pytest, без заліза)
- `BladerfViewSource` з фейковим radio: reader-потік наповнює кільце; `read_chunk` віддає байти в
  порядку прибуття; overflow рахує `dropped_bytes`; `recover` = close+reopen+retune; `close` зупиняє потік.
- `to_iq`/`bytes_per_sample` для обох джерел (int8 vs SC16_Q11) дають очікуваний complex64.
- `run_stream_source` з фейк-bladeRF-джерелом: SC16_Q11 байти → кадри (правильний `chunk_bytes`,
  `to_iq`); синтетичний сигнал з `agent/video/synth.py` за наявності.
- `SCAN_ENABLED` gate в головному циклі: `scan_enabled=0` → `run_cycle` не викликається, pending-вью
  обслуговується.
- `test_config`: парсинг `SCAN_ENABLED`.
- `IqRing` після виносу — зелений (`test_hackrf_source` + опційний `test_iqring`).

## Deploy (без змін коду сервера/дашборда)
- Сервер: зареєструвати publisher-девайс + MediaMTX-шлях `bladerf-view` (як існує `hackrf-view`);
  дашборд підхопить з ретейн-анонсу.
- Вузол: на bladeRF-юніті `VIEW_ENABLED=1` + `VIEW_PUSH_URL=.../bladerf-view` (+ `SCAN_ENABLED=0`,
  якщо чистий вьювер). systemd-юніти на Pi hand-diverged — не перезаписувати файл, редагувати env.
- Жива акцептація: bladeRF-детекція → клік у «FPV Viewer» → відео в плеєрі; на двох-SDR вузлі
  перевірити паралельність (свіп-список свіжий, поки bladeRF стрімить з іншого юніта, і навпаки).

## Ризики / нотатки
- bladeRF USB3 легко тягне 8 MS/s (свіп іде на 40 MS/s); демод встигає при 6–10 MS/s (пам'ять).
- Reader-потік мусить коректно зупинятись на `close`/`recover`, інакше leaked-хендл → NoDevError на reopen.
- На двох-SDR вузлі RX5808 (5.8G dual-action) лишається на тому юніті, де він фізично підключений
  (GPIO), керується `RX5808_ENABLED`; це деплой-рішення, не блокує фічу.
