# Ручна частота + смуга (BW) при SDR-вью — Design

**Date:** 2026-07-12
**Status:** Approved design, ready to plan.
**Target:** агент (`agent/scan` + `agent/video`) + дашборд (`dashboard/public`). Без нових MQTT-топіків/ACL.
**Context:** розширює вью-стрім ([[sdr-view-stream]]) і панель вьюверів ([[fpv-viewer-multi-player]]).
Перша з двох під-фіч; друга (чутливість детекцій з дашборда) — окремо.

## Мета
Дати оператору при перегляді задавати не лише частоту, а й **смугу відео** (BW) — щоб підлаштувати
чіткість/шум картинки під конкретний сигнал, наживо.

## Семантика BW (важливо)
Для аналогового FM-відео **RF-фільтр захоплення має бути ширший за сигнал** (інакше ріже FM-бокові
смуги й псує демод). Тому «керована смуга» = **ширина демод-lowpass** (`lpf_cutoff_hz`) — саме вона
визначає, скільки відео-бейсбенду проходить: вужче = м'якше/менше шуму, ширше = більше деталей.
RF-захоплення лишається на фіксованому `view_sample_rate_hz` (широке).
- BW у МГц; агент клампить у **[0.5, fs/2]** МГц (демод-бейсбенд real @ fs, Nyquist = fs/2).
  bladeRF 8 MS/s → до 4 МГц; HackRF 6 MS/s → до 3 МГц.
- Немає BW у команді → агент бере дефолт (`vcfg.lpf_cutoff_hz`, як зараз).

**Скоуп-межа v1:** BW = лише демод-lpf; `sample_rate` фіксований (сигнал ширший за fs повністю не
захопиш — це окрема майбутня зміна sample_rate). Без нових топіків/ACL.

## Потік даних (розширюємо наявну вью-команду)
```
UI картка-вьювер: поле BW ─▶ publishView(id,'start',freq,bw) ─▶ fpv/<id>/rxcmd {view:start,freq_mhz,bandwidth_mhz}
                                                                      │
publisher.on_view_command(data) ─▶ ViewController.set_command(data)  │ читає freq_mhz + bandwidth_mhz
   _pending=(freq,bw) ─▶ run_view(freq,bw) ─▶ run(freq,bw,stop,max_s) │
      run_stream_source(..., lpf_cutoff_hz = clamp(bw*1e6) | vcfg default) ─▶ demod
   publish_view(..., bandwidth_mhz) ─▶ fpv/<id>/view (retained) ─▶ reduce ─▶ store[id].view.bandwidth_mhz ─▶ UI
```

## Компоненти

### Агент — `agent/scan/view_controller.py`
- `set_command(data)`: додатково читає `bandwidth_mhz` (число або None); зберігає `_pending` як
  пару `(freq_mhz, bw_mhz)` (замість самого freq). `pending()`/`has_pending()` без зміни семантики.
- `run_view(req)`: `req` тепер `(freq, bw)`; пробрасує обидва в `self._run_stream(freq, bw, stop, max_s)`.
  Ехо стану — `publish_view(..., bandwidth_mhz=bw)`.
- `announce()` / `_pub`: несуть `bandwidth_mhz` (None коли неактивно).

### Агент — `agent/scan/main.py`
- `run`-замикання: сигнатура `(freq, bw, stop, max_s)`; обчислює
  `lpf = clamp(bw*1e6, 0.5e6, fs/2)` якщо `bw` задано, інакше `vcfg.lpf_cutoff_hz`
  (`fs = viewcfg.view_sample_rate_hz`); передає `lpf_cutoff_hz=lpf` у `run_stream_source`.
  Однаково для hackrf і bladerf гілок (обидві вже йдуть у `run_stream_source`).

### Агент — `agent/video/stream_demod.py`
- `run_stream_source(...)` отримує опційний `lpf_cutoff_hz=None`; коли None → `vcfg.lpf_cutoff_hz`
  (незмінна поведінка). Використовує це значення у `pick_standard(...)` і `chunk_to_frames(...)`
  замість прямого `vcfg.lpf_cutoff_hz`. (Чиста, локальна зміна; HackRF/bladeRF-шляхи однакові.)

### Агент — `agent/scan/publisher.py`
- `publish_view(ts, active, freq_mhz=None, until_ts=None, error=None, stream=None, bandwidth_mhz=None)`
  — додає `bandwidth_mhz` у payload `fpv/<id>/view`.

### Дашборд — `dashboard/public/mqtt-scan.js`
- `buildViewCmd(action, freqMhz, bwMhz)` → `{view:'start', freq_mhz, bandwidth_mhz}` (bw опційний;
  omit коли не задано). `publishView(id, action, freqMhz, bwMhz)`.
- `reduce` (гілка `view`): `s.view.bandwidth_mhz = data.bandwidth_mhz == null ? null : Number(...)`.

### Дашборд — `dashboard/public/views/viewer.js`
- Кожна картка отримує поле **BW (МГц)** поряд із полем частоти. Старт на цей вьювер — з ▶дивитись,
  степера, або кнопки `▶<вьювер>` у рядку — бере `bw` з поля **саме цієї картки**.
- Активний BW підтягується з `view.bandwidth_mhz` у бейдж/плейсхолдер (не churn'имо введене поле —
  reconcile-safety як у [[fpv-viewer-multi-player]]).
- `viewerRowClick(freq, band, viewerId)` тепер читає BW цільової картки (через ctx-хелпер або
  data-атрибут), щоб клік по рядку теж ніс смугу.

### Дашборд — `dashboard/public/viewer.js` (за потреби)
- Дрібний чистий хелпер `clampViewBw(mhz)` / дефолт, якщо знадобиться (тестований).

## Error handling
- Некоректний `bandwidth_mhz` (не число / поза [0.5, fs/2]) → агент клампить; зовсім невалідне → дефолт.
- Порожнє поле BW у UI → команда без `bandwidth_mhz` → агент-дефолт.
- BW-зміна активної сесії = session restart (як retune) через існуючий механізм `_pending`+`_stop`.

## Testing
- pytest: `set_command` парсить `bandwidth_mhz` (+ відсутність → None); `run_view` пробрасує пару
  у `run_stream`; `run_stream_source` застосовує переданий `lpf_cutoff_hz` (і None → vcfg-дефолт);
  clamp у `main` (bw поза [0.5, fs/2] → межі; None → дефолт); `publish_view` кладе `bandwidth_mhz`.
- node (`test/mqtt-scan.js`/відповідні): `buildViewCmd`/`publishView` з bw; `reduce` несе
  `bandwidth_mhz`; `clampViewBw` якщо додано.
- Візуальний гейт (dev-preview): BW-поле на картці; ▶/степер/рядок-кнопка формують команду з `bandwidth_mhz`
  (перевірити через перехоплений publish у preview або спостереження за станом); reconcile-safety поля BW.
- `npm test` зелений.

## Deploy
- Pi (`rpi-4` @192.168.1.204, key fpv_deploy): `sudo git pull` + `restart fpv-scan` та
  `fpv-scan-hackrf` (обидва йдуть у run_stream_source).
- Сервер `traefik`: rebuild+recreate dashboard (`--no-deps`; wg-easy/mediamtx/mosquitto НЕ чіпати).
- Over-WG акцептація: у вью виставити частоту + різні BW → бачити зміну чіткості/шуму картинки наживо
  на обох SDR.

## Ризики / нотатки
- BW нижче кількох сотень кГц зробить картинку дуже м'якою — це очікувано (оператор підбирає).
- Зміна BW рестартить вью-сесію (WHEP пере-під'єднається ~1с) — прийнятно, як retune.
- sample_rate лишається фіксованим; «захопити ширший VTX» — свідомо поза v1.
