# FPV TX-генератор — Phase-A (керований із дашборда) — Design

**Date:** 2026-07-12
**Status:** Approved design, ready to plan.
**Target:** `agent/scan` (роль+контролер+роутинг), `agent/tx` (реєстр+контролер), `dashboard/` (екран+publish+reduce).
**Context:** Phase-0 спайк ([[fpv-tx-generator]], PR #37 merged) дав standalone `agent/tx` CLI (render→SC16_Q11→loop-transmit). Phase-A робить його **керованою продакшн-фічею**: роль TX на ноді, команда з дашборда, окремий екран, арбітрація зі свіпом, реєстр файлів. Шар керування **не залежить** від залізного гейту; від гейту залежить лише *нутро рендеру* (якщо треба неперервна модуляція через кадри — зміниться крок рендеру, НЕ контракти нижче).
**Референс-пламбінг (дзеркалимо):** `ViewController`/`ThresholdController`, `publisher._on_message`, `mqtt-scan.js`, `nodes.js`.

## Мета
Оператор із дашборда: бачить TX-здатні ноди й список відеофайлів на них → обирає файл + частоту + параметри → **Старт** → на реальному ефірі йде зациклене FPV-відео → **Стоп** (або автостоп за дедлайном). Частоту/gain міняє наживо без ре-рендеру.

## Архітектура (TX перехоплює пристрій, як view)
```
[dashboard] екран «Передавач»: dropdown файлу (з txfiles) + freq/gain/deviation/standard + Старт/Стоп
     → publishTx → fpv/<id>/rxcmd  {tx:{action, ...}}  (non-retained, як view)
[agent] publisher._on_message: гілка `if "tx" in data` → on_tx_command → TxController.set_command
     головний цикл: tx.pending() преемптить свіп → run_tx():
        1) звільнити свіповий bladeRF (_reset_bladerf_backend)
        2) render(файл, baked-параметри) → .bin  [кеш; reuse якщо параметри ті самі]   ← agent/tx/render.py
        3) open_bladerf_tx_radio(freq,gain) → transmit_loop(...) до stop/дедлайну       ← agent/tx/bladerf_tx.py
        4) close TX → свіп відновлюється (наступний cycle переоткриває backend)
     стан → retained fpv/<id>/txstate;  список файлів → retained fpv/<id>/txfiles
```
**Full-duplex нема** — кожна роль відкриває ексклюзивний `BladeRF()`. TX і view взаємовиключні; серіалізуються головним циклом (`run_*` блокує до виходу).

## Компоненти

### `agent/tx/registry.py` (нове)
- `scan_video_files(dir_path) -> list[dict]` — читає теку, повертає `[{name, size, mtime}]` лише для відео-розширень (allowlist: `.mp4 .mkv .avi .mov .ts .m4v .webm`), відсортовано за name; ігнорує приховані/`.cache`. Чисте (dir інжектований) → тестоване.

### `agent/tx/txconfig.py` (нове, дзеркало `agent/video/vconfig.py`)
- `TxConfig` dataclass + `load_tx_config(env) -> TxConfig`. Поля/env:
  - `tx_enabled: bool=False` ← `TX_ENABLED` (роль-гейт; тільки bladeRF-нода вмикає)
  - `tx_dir: str="/var/lib/fpv/tx"` ← `FPV_TX_DIR`
  - `tx_cache_bin: str="/var/lib/fpv/tx/.cache/current.bin"` ← `FPV_TX_CACHE_BIN`
  - `tx_max_s: float=120.0` ← `FPV_TX_MAX_S` (**автостоп-дедлайн — безпека**)
  - дефолти рендеру: `fs_hz=20e6`, `deviation_hz=4e6`, `standard="PAL"`, `width=640`, `height=512`, `fps=25`, `secs=3.0`, `vbi_lines=6`, `gain_db=30` ← `FPV_TX_*` (стартові, оператор перекриває на екрані)

### `agent/tx/tx_controller.py` (нове, дзеркало `ViewController`)
- `TxController(cfg, publisher, render_fn=..., open_tx_fn=..., transmit_fn=...)` — залізо-функції інжектовані (`render`, `open_bladerf_tx_radio`, `transmit_loop`) → тестується фейками БЕЗ ffmpeg/bladeRF.
- `set_command(data: dict)` — приймає `{tx:{...}}` з MQTT-колбека; НЕ кидає в колбек (лог+ігнор невалідного); валідує/кламп; ставить pending або оновлює живий ретюн.
- `pending() -> Optional[dict]` — чи є новий start (для преемпції в головному циклі), дзеркало `ViewController.pending()`.
- `run_tx(req)` — блокує до stop/дедлайну/помилки; кроки: publish txstate(status="rendering") → render у кеш (reuse якщо `_render_key` == попереднього) → open TX(freq,gain) → publish txstate(status="transmitting", `since_ts=now`, `until_ts=since_ts+tx_max_s`) → `transmit_loop(radio, bin, block_bytes=32768*4, stop_check)` де stop_check = (stop-прапор ∨ `now>=until_ts` дедлайн ∨ retune-потрібен-ре-рендер). На retune freq/gain — `radio.set_frequency`/gain наживо, оновити txstate, БЕЗ виходу з циклу. На вихід — close TX, publish txstate(status="idle", active=False). **`secs` у команді = довжина кліпу для РЕНДЕРУ (як Phase-0 `--secs`/`max_secs`), НЕ тривалість TX; тривалість TX = луп до stop або `tx_max_s`.**
- `announce()` — (пере)публікує retained txstate (capability-анонс на конекті + reconnect), дзеркало `ViewController.announce()`.
- `list_files()` / публікація txfiles — див. main.py cadence.
- `_render_key(req)` = кортеж baked-параметрів `(file, standard, fs, deviation, w, h, fps, secs, vbi)`; freq/gain НЕ входять (live-ретюн).

### `agent/scan/publisher.py` (модифікація — дзеркало наявних гілок)
- `__init__`: `self._t_txstate=f"fpv/{id}/txstate"`, `self._t_txfiles=f"fpv/{id}/txfiles"`, `self.on_tx_command=None`.
- `_on_message`: додати `if "tx" in data: (self.on_tx_command and self.on_tx_command(data)); return` — **після** `{view}`/`{thresholds}`, ПЕРЕД RX5808-fallthrough (гард на None, як інші).
- `publish_txstate(ts, state: dict)` → `_t_txstate` (retained, через `_publish`); `publish_txfiles(ts, files: list, dir: str)` → `_t_txfiles` (retained). Дзеркало `publish_view`/`publish_scancfg`.

### `agent/scan/main.py` (модифікація — дзеркало view/threshold wiring)
- Лениво `load_tx_config()`; `if txcfg.tx_enabled:` → сконструювати `TxController`, `publisher.on_tx_command = tx.set_command`, вчепити `tx.announce()` у композицію `on_connected` (як threshold_ctl, щоб анонси не клобали одне одного) + виклик після connect.
- Головний цикл (біля рядків 371-413): перевіряти `req = tx.pending()` — якщо є, `run_tx(req)` (звільнивши bladeRF, як `_run_blade_view`), потім `continue`. Порядок: TX і view преемптять свіп; взаємовиключні (обидва блокують). TX перевіряти поряд із view.
- **txfiles cadence:** публікувати на конекті (announce) + ре-скан кожні ~60с у циклі + одразу перед кожним start (щоб свіжий файл зʼявився). Дешевий `scan_video_files`.

### `dashboard/public/mqtt-scan.js` (модифікація)
- `buildTxCommand(action, {file, freqMhz, gainDb, deviationMhz, standard, secs}) -> obj` — чистий білдер (дзеркало `buildViewCommand`), юніт-тестований.
- `publishTx(id, action, params)` — publish `fpv/${id}/rxcmd`, `{qos:1, retain:false}` (як `publishView`).
- `subscribe`: додати `fpv/+/txstate`, `fpv/+/txfiles` (біля наявного списку).
- `reduce()`: гілки `txstate` (→ per-scanner tx-стан) і `txfiles` (→ per-scanner список), дзеркало гілки `view`.

### `dashboard/public/views/tx.js` (нове — екран «Передавач»)
- `render(container, ctx)` reconcile-based (build-once скелет + оновлення лише живих полів — як `nodes.js` viewControls/updateView, бо `live:true` ре-mount на кожен тік і не можна витирати введені оператором поля/відкритий `<select>`).
- На кожну TX-здатну ноду (має txstate): картка з dropdown файлу (з txfiles), поля freq (MHz), gain, deviation (MHz), standard, кнопки **Старт**/**Стоп**.
- **Активний TX:** помітний червоний банер «TX НА <freq> MHz · <file>» + зворотний відлік до `until_ts` (автостоп).
- **Старт вимагає підтвердження** (RF-запобіжник) перед `ctx.onTxStart(...)`.

### `dashboard/public/app.js` (модифікація)
- Роут `{ hash:'#/tx', label:'Передавач', mount: renderTx, live:true }` + nav-пункт.
- Аксесори `ctx.onTxStart(id, params)` / `ctx.onTxStop(id)` (дзеркало `onViewStart`/`onViewStop`, рядки 122-124) → `scanClient.publishTx(...)`; PREVIEW-гард як в інших.

## Контракти (0 змін ACL — усе під `pub fpv/#` / `sub fpv/+/rxcmd`)
- **Команда** (dashboard→agent, `fpv/<id>/rxcmd`, **non-retained**): `{tx:{action:"start"|"stop"|"retune", file, freq_mhz, gain_db, deviation_mhz, standard, secs}}`. Non-retained, бо retained-start реплеївся б і входив у TX на кожному reconnect Pi (той самий резон, що view).
- **Стан** (agent→dashboard, **retained** `fpv/<id>/txstate`): `{active:bool, status:"idle"|"rendering"|"transmitting", file, freq_mhz, gain_db, deviation_mhz, standard, since_ts, until_ts, error}`. Публікується на конекті (capability-анонс — за ним екран знає, що нода TX-здатна).
- **Файли** (agent→dashboard, **retained** `fpv/<id>/txfiles`): `{files:[{name,size,mtime}], dir, scanned_ts}`.

## Безпека RF (counter-drone контекст)
- **Автостоп-дедлайн** `tx_max_s` (дефолт 120с): агент сам глушить TX і повертає свіп навіть без `stop` (дзеркало `view_max_s`). `until_ts` у txstate → відлік на екрані.
- **Підтвердження на Старт** в UI (навмисна дія).
- Один передавач за раз (пристрій ексклюзивний; TX/view взаємовиключні).
- TX-authorization ворнінг у лозі лишається; легально/потужність/антена — відповідальність оператора.

## Тестування
- **pytest (agent, без заліза/ffmpeg):** `scan_video_files` (фейк-тека: фільтр розширень, сортування, поля); `TxController` з фейками (`render_fn`/`open_tx_fn`/`transmit_fn`) — start→rendering→transmitting→idle, дедлайн-автостоп, retune freq/gain БЕЗ ре-рендеру, `_render_key` reuse (той самий файл/параметри → render_fn не викликається вдруге), невалідна команда не кидає; `publisher._on_message` гілка `{tx}` (роут+гард на None).
- **node --test (dashboard):** `buildTxCommand` (усі поля/дефолти), `reduce` гілки txstate/txfiles.
- **Ручне (акцептація):** залізний гейт (RX5808 бачить картинку) + over-WG дашборд→Старт→картинка→Стоп/автостоп.

## Деплой
- **Pi (bladeRF-нода):** `git pull` + drop-in env `TX_ENABLED=1` (+ опц. `FPV_TX_*`) на `fpv-scan`; створити `/var/lib/fpv/tx/`, покласти тест-відео; restart. Свіп-нода без `TX_ENABLED` ігнорує `{tx}`.
- **Сервер traefik:** `git pull` + build+recreate `dashboard` (`--no-deps`; wg-easy/mediamtx/mosquitto НЕ чіпати). **0 змін ACL/MediaMTX** (TX — суто RF+MQTT, не MediaMTX-стрім).

## Ризики / нотатки
- **Гейт-залежність:** якщо гейт покаже розрив на частоті кадрів → неперервна модуляція через кадри = зміна ЛИШЕ кроку render (agent/tx/render.py); контракти/контролер/UI незмінні.
- `.bin` великий (~160-240МБ на 2-3с@20MS/s) — ОДИН кеш-слот (`current.bin`), перезапис; на диску Pi ок.
- Рендер блокує ~секунди — стан `rendering` показує це; TX стартує після.
- TX/view одночасно від двох операторів — пристрій ексклюзивний, серіалізується; другий чекає виходу першого (прийнятно для Phase-A).
- Phase-B (якщо треба): прогрес-бар рендеру, кілька кеш-слотів, upload-канал, кілька TX-нод.
