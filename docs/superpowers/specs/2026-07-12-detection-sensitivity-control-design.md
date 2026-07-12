# Керовані пороги детекцій з дашборда (чутливість) — Design

**Date:** 2026-07-12
**Status:** Approved design, ready to plan.
**Target:** агент (`agent/scan`) + дашборд (`dashboard/public`). Без зміни ACL (нового командного топіка нема).
**Context:** друга з двох під-фіч «чутливості/BW» (перша — [[view-bandwidth-control]]). Пороги зараз
захардкоджені у `config.Thresholds` + `cfg.rx5808_carrier_*`, не тюняться наживо.

## Мета
Дати оператору **регулювати чутливість детекцій наживо з дашборда** (без перезбірки/рестарту):
5 порогів, керованих з екрана «Детекції», з персистом на диску агента.

## Керовані пороги (5)
| поле cfg | роль | дефолт | clamp |
|---|---|---|---|
| `thresholds.snr_threshold_db` | строгий детектор: SNR-гейт кандидата | 20 | [3, 60] |
| `thresholds.min_bandwidth_mhz` | строгий детектор: мін. ширина | 5 | [0.1, 30] |
| `thresholds.occupancy_snr_db` | метрика зайнятості бенду | 10 | [3, 40] |
| `rx5808_carrier_snr_db` | вільний carrier-finder (FPV-відео-демод + RX5808) | 15 | [3, 60] |
| `rx5808_carrier_min_bw_mhz` | carrier-finder: мін. ширина | 0.5 | [0.1, 10] |

`run_cycle` читає ці поля з `cfg` щоцикл — тож мутація застосовується з наступного циклу.

## Обмеження (звірено)
- **ACL:** `pub` (агент) читає ЛИШЕ `fpv/+/rxcmd`; `sub` (дашборд) пише ЛИШЕ `fpv/+/rxcmd`, читає
  `fpv/#`. → **команда порогів іде через `rxcmd`** (новий командний топік потребував би ACL+рестарт
  mosquitto). **Ехо-стан — новий топік `fpv/<id>/scancfg`** (агент пише fpv/#, дашборд читає fpv/# —
  БЕЗ зміни ACL).
- **RX5808-команди retained** (займають retained-слот `rxcmd`). → команда порогів **NON-retained**
  (як вью), щоб не клобати RX5808. Персист MQTT неможливий → персист **на диску агента**.

## Потік даних
```
UI «Детекції» (панель по-сканеру: 5 інпутів + Застосувати + Скинути)
  → publishThresholds(id,{...}) → fpv/<id>/rxcmd {thresholds:{...}}  (qos1, retain:FALSE)
publisher._on_message: {thresholds:...} → on_thresholds_command → ThresholdController.apply
  → clamp кожного поля → мутує cfg → пише thresholds.json (StateDir) → publish RETAINED fpv/<id>/scancfg
dashboard reduce fpv/<id>/scancfg → store[id].scancfg → UI показує активні значення
agent boot: load_config(env) → overlay thresholds.json (якщо є); on connect → announce scancfg
```

## Компоненти

### Агент — `agent/scan/threshold_controller.py` (нове)
- `ThresholdController(cfg, publisher, scanner_id, persist_path, clock)` — тримає `cfg` (мутує його),
  дефолти (знімок при старті для reset), шлях персисту.
- `apply(data)`: `data["thresholds"]` = dict полів (частковий дозволено) АБО рядок `"reset"`.
  - для кожного відомого ключа: `float(v)` → clamp у діапазон → присвоїти у `cfg` (thresholds.* або
    rx5808_carrier_*); невідомі ключі/невалідні значення ігнорувати.
  - `"reset"` → відновити захоплені дефолти.
  - записати активні пороги у `persist_path` (json, атомарний write-through tmp+rename).
  - `announce()`.
- `announce()`: `publisher.publish_scancfg(ts, active_thresholds_dict)` (retained). Ніколи не кидає.
- `load(persist_path, cfg)` (модульна фн): якщо файл є — overlay значень у `cfg` (env<file). Викл. на boot.

### Агент — `agent/scan/publisher.py`
- `_on_message`: додати гілку `if "thresholds" in data:` (ПЕРЕД RX5808) → `on_thresholds_command(data)`
  (guarded). Новий hook `self.on_thresholds_command = None`.
- `publish_scancfg(ts, thresholds)`: retained payload на `fpv/<id>/scancfg`
  `{scanner_id, ts, snr_threshold_db, min_bandwidth_mhz, occupancy_snr_db, carrier_snr_db, carrier_min_bw_mhz}`.
- Топік `self._t_scancfg = f"fpv/{scanner_id}/scancfg"`.

### Агент — `agent/scan/main.py`
- Перед конектом: `threshold_controller.load(persist_path, cfg)` (overlay файлу). Інстанс
  `ThresholdController`, `publisher.on_thresholds_command = tc.apply`, `publisher.on_connected` вже
  анонсує вью — додати анонс scancfg (обгорнути існуючий on_connected або окремий). На старті —
  `tc.announce()` теж (початкові активні пороги). Persist-path у StateDirectory (`/var/lib/fpv/...`),
  керований env (напр. `FPV_THRESHOLDS_PATH`).

### Дашборд — `dashboard/public/mqtt-scan.js`
- Subscribe: додати `fpv/+/scancfg`. `reduce`: гілка `scancfg` → `store[id].scancfg = {ts, snr_threshold_db,
  min_bandwidth_mhz, occupancy_snr_db, carrier_snr_db, carrier_min_bw_mhz}` (числа, null коли відсутні).
- `buildThresholdCommand(obj)` → `{thresholds: {...}}` (лише валідні числові поля). `publishThresholds(id, obj)`
  → `fpv/<id>/rxcmd`, qos1, **retain:false**. Reset: `publishThresholds(id, 'reset')` → `{thresholds:'reset'}`.

### Дашборд — `dashboard/public/views/detections.js` + `viewer.js`(pure, за потреби)
- Панель порогів **по онлайн-сканеру** (зверху/збоку списку детекцій): 5 числових полів + «Застосувати»
  + «Скинути». Плейсхолдери/поточні значення — зі `store[id].scancfg` (reconcile-safe: не затирати ввід,
  показувати активні як placeholder/підпис). Reconcile як у [[fpv-viewer-multi-player]] (панель будується
  раз, оновлюються лише текст/placeholder; інпути не churn'яться).
- Pure-хелпери (тестовані): `scannerThresholdCards(store)` (онлайн-сканери зі scancfg), `clampThreshold(field,v)`
  (дзеркало агентних діапазонів, для UI-підказки), `buildThresholdCommand`.

## Error handling
- Невалідне/поза-діапазоном значення → агент клампить; нечислове/невідомий ключ → ігнор.
- Персист-запис падає → лог, не блокує apply/announce.
- Файл персисту побитий на boot → ігнорувати (env-дефолти), лог.
- scancfg не прийшов (агент офлайн/стара версія) → UI показує панель із дефолт-плейсхолдерами, без «поточних».

## Testing
- pytest: `ThresholdController.apply` (частковий набір; clamp кожного поля меж/поза; невідомі ключі;
  `"reset"`→дефолти; persist write + reload через `load`); `_on_message` роутить `thresholds` (не в
  RX5808); `publish_scancfg` payload; boot-overlay `load`.
- node: `buildThresholdCommand` (валідні поля/omit); `reduce` scancfg (числа/null); `clampThreshold`;
  `scannerThresholdCards`.
- Візуальний гейт (dev-preview): панель порогів на «Детекції» з фікстур-scancfg; Apply формує команду;
  reconcile-safety інпутів; Reset.
- `npm test` + pytest зелені.

## Deploy
- Pi: `git pull` + restart `fpv-scan` (+`fpv-scan-hackrf`). Persist-файл у StateDirectory створюється при
  першому Apply.
- Сервер `traefik`: rebuild+recreate dashboard (`--no-deps`; wg-easy/mediamtx/mosquitto НЕ чіпати).
- **ACL НЕ змінюється** (rxcmd для команд, scancfg — новий read-топік під наявним `fpv/#`).
- Over-WG акцептація: змінити SNR/carrier наживо → бачити зміну к-сті детекцій/відео-демодів; Reset;
  рестарт Pi → тюнінг зберігся (persist).

## Ризики / нотатки
- Мутація cfg з MQTT-потоку читається scan-потоком: незалежні float-поля, GIL-атомарні; максимум один
  цикл може змішати старе/нове поле — безпечно. Без локу.
- Занизький SNR → лавина хибних детекцій/відео-демодів (оператор підбирає; Reset рятує).
- scancfg — новий retained STATE-топік; дашборд-subscribe/reduce мусять його додати, інакше UI без «поточних».
