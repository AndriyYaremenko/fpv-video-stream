# Авто-ретрансляція (свіп → захоплення → TX на 5771) — Design

**Date:** 2026-09-06
**Status:** Approved design, ready to plan.
**Target:** `agent/relay` (нове), `agent/scan` (хіти зі свіпу + арбітрація + роутинг), `dashboard/` (блок на екрані «Передавач»).
**Node:** orange_pi6 (bladeRF 2.0 micro xA4, FPGA **v0.16.0**, 12 ядер) — роль sweeper+viewer+tx+**relay**.
**Референс-пламбінг (дзеркалимо 1:1):** `agent/tx/{txconfig,tx_controller,bladerf_tx}.py`, `main.py` (арбітрація TX/view, `controller.update_targets`), `publisher.py` (`"tx" in data`, `publish_txstate`), `dashboard/public/{mqtt-scan.js,views/tx.js,app.js,fixtures.js}`.

## Контекст і доведене спайком
Спайк `relay_spike2.py` (2026-09-06, orange_pi6): full-duplex RX(джерело) → per-block AGC до повної шкали → TX(5771), 20 МС/с, TX gain 60, RX gain manual 40 →
**картинка на Skydroid-приймачі** для джерел 5865 (той самий бенд) і 3470 (3.3 ГГц); realtime рівно 1.0× (19.99–20.00 МС/с ефективно за 90 с) навіть із numpy-AGC.
Липневий вердикт «relay не працює» був хибним: старий FPGA 0.14.0 без TX-backpressure + passthrough без нормалізації амплітуди (вхід −13 дБ від шкали → приймач не бачив навіть несучої).

## Мета
Свіп працює як завжди. Коли режим **озброєно**, після кожного циклу агент бере джерела, на яких **захоплено кадр (синхро-лок)**, обирає найсильніше й **ретранслює його IQ на цільову частоту (5771 за замовчуванням)**, поки джерело живе; зникло — повертається до свіпу. Оператор озброює/знімає режим і задає ціль із дашборда; стан видно на екрані «Передавач».

## Архітектура
```
[sweep cycle] run_cycle(): кожне maybe_emit()=="published" → video_hits.append((band, center_mhz, sync_snr_db))
              → в кінці циклу relay.update_hits(video_hits)          (як controller.update_targets)
[main loop]   tx.pending() → view.pending() → relay.pending() → run_relay(req) → continue
              run_relay: reset(bladeRF свіпу) → open full-duplex RX@src / TX@dst → loop(блок→AGC→TX)
                         до: disarm | manual_pending() | джерело зникло | RELAY_MAX_S | помилка
                         → close → reset → свіп відновлюється (наступний цикл знову може захопити)
[dashboard]   «Передавач» → блок «Авто-ретрансляція»: ⚡ Озброїти / ■ Зняти + «Ціль, МГц» + рядок стану
              → fpv/<id>/rxcmd {relay:{action:"arm"|"disarm", dst_mhz?}}   (non-retained)
              ← fpv/<id>/relaystate (retained)
```
Ролі взаємовиключні щодо пристрою; серіалізуються головним циклом. **Ручні команди (TX-генератор, view) мають пріоритет:** relay стартує лише коли нічого ручного не висить, і зупиняється, щойно ручна команда з'являється.

## Компоненти

### `agent/relay/relayconfig.py` (нове, дзеркало `txconfig.py`)
`RelayConfig` dataclass + `load_relay_config(env) -> RelayConfig`:

| поле | env | дефолт | сенс |
|---|---|---|---|
| `relay_enabled` | `RELAY_ENABLED` | `False` | роль-гейт (як `TX_ENABLED`) |
| `armed` | `RELAY_ARMED` | `False` | стан озброєння після старту процесу |
| `dst_mhz` | `RELAY_DST_MHZ` | `5771.0` | ціль (оператор перекриває в arm) |
| `max_s` | `RELAY_MAX_S` | `600.0` | запобіжний дедлайн сесії |
| `guard_mhz` | `RELAY_GUARD_MHZ` | `25.0` | джерела в `[dst±guard]` ігноруються (самозбудження) |
| `lost_db` | `RELAY_LOST_DB` | `12.0` | «джерело зникло»: RMS входу впав на стільки дБ від опорного |
| `lost_s` | `RELAY_LOST_S` | `5.0` | …і тримається стільки секунд |
| `fs_hz` | `RELAY_FS_HZ` | `20e6` | sample rate RX і TX (bandwidth = fs) |
| `rx_gain_db` | `RELAY_RX_GAIN_DB` | `40` | manual RX gain |
| `tx_gain_db` | `RELAY_TX_GAIN_DB` | `60` | TX gain |
| `agc_target` | `RELAY_AGC_TARGET` | `1600.0` | пік після AGC (з 2047) |
| `block_samples` | — | `32768` | розмір блоку |

Bool-парсинг як у `txconfig` (`not in ("0","false","no","")`).

### `agent/relay/bladerf_relay.py` (нове, дзеркало `bladerf_tx.py`)
- `agc_block(iq_int16: np.ndarray, gain: float, target: float) -> tuple[np.ndarray, float, float]` — чиста: `peak=max|iq|` (0→1), `gain = min(gain*0.9 + (target/peak)*0.1, 64.0)`, `out = clip(iq*gain, -2047, 2047).astype(int16)`, повертає `(out, gain, rms_raw)` де `rms_raw = sqrt(mean(iq²))` СИРОГО входу (до AGC).
- `relay_loop(radio, block_samples, stop_check, on_level, agc_target)` — `while not stop_check(): buf = radio.read(block) → out,gain,rms = agc_block(...) → radio.write(out.tobytes()) → on_level(rms)`. `on_level` викликається кожен блок; сам loop нічого не вирішує.
- `BladeRfRelayRadio(radio, rx_ch, tx_ch, block_samples)` — `read(n) -> np.ndarray[int16]` (sync_rx у попередньо виділений bytearray, `np.frombuffer`), `write(bytes)` (sync_tx), `close()` (enable_module False на обох + close, кожен крок у try, як `BladeRfTxRadio.close`).
- `open_bladerf_relay_radio(src_hz, dst_hz, fs_hz, rx_gain_db, tx_gain_db, block_samples) -> BladeRfRelayRadio` — **єдина функція, що імпортує `bladerf`**: обидва канали `set_sample_rate/set_bandwidth(fs)`, `set_frequency`, RX `GainMode.Manual`+gain, TX gain, `sync_config` окремо для `RX_X1` і `TX_X1` (`SC16_Q11`, `num_buffers=32, buffer_size=16384, num_transfers=16, stream_timeout=3500`), `enable_module` обох.

### `agent/relay/relay_controller.py` (нове, дзеркало `tx_controller.py`)
`RelayController(cfg, publisher, open_fn, loop_fn, reset=None, manual_pending=None, clock=None)`; усе залізо інжектоване; **ніколи не кидає**.
- `set_command(data)` (MQTT-потік): `data["relay"]` dict; `action=="arm"`: `dst_mhz` (число в 100..6000, інакше лишаємо поточний) → `armed=True`, публікує стан; `action=="disarm"`: `armed=False`, скидає pending, `_stop.set()`, публікує стан. Інше — warning.
- `update_hits(hits)` (scan-потік, у кінці циклу): `hits = [(band, center_mhz, sync_snr_db), ...]`; якщо `armed` і `manual_pending()` False: відфільтрувати `|center-dst| <= guard` → взяти max за `sync_snr_db` → `_pending = {"band","src_mhz","dst_mhz"}`; інакше `_pending=None`. Дублікати/порожній список → `None`.
- `pending()/has_pending()` — як у TxController.
- `run_relay(req)` (scan loop, блокує): `_stop.clear()` → `reset()` → `open_fn(...)` → публікує `relaying` (`since_ts`, `until_ts=since+max_s`) → `loop_fn(radio, block, stop_check, on_level, agc_target)` де:
  - `on_level(rms)`: перші `1 с` (за clock) накопичує опорний `ref_rms` (медіана); далі, якщо `20*log10(ref/rms) >= lost_db` — рахує час «нижче»; ≥ `lost_s` → `_lost=True`; вище порогу — обнуляє. Раз на ~2 с оновлює `rx_level_db = 20*log10(rms/2047)` у стані (публікація не частіше 2 с).
  - `stop_check()`: `_stop.is_set() or not armed or manual_pending() or _lost or clock() >= deadline`.
  - `finally`: `radio.close()`, `reset()`, публікує `idle` з `error` (якщо був виняток — його текст, як у `run_tx`), `_stop.clear()`.
- `announce()` — републікує останній стан (capability announce на (re)connect; чистить stale `active:true` після краху).
- Стан (retained `fpv/<id>/relaystate`): `{armed, active, status:"idle"|"relaying", src_mhz, band, dst_mhz, since_ts, until_ts, rx_level_db, error}` + `scanner_id, ts` від publisher.

### `agent/scan` (правки)
- `video_emit.py`: `self.last_sync_snr_db = round(float(vf.sync_snr_db), 1)` поряд із `last_frame_path` при публікації (None при скиданні).
- `main.py`:
  - `run_cycle(..., relay=None)`: `video_hits=[]`; у **обох** місцях `maybe_emit(...)=="published"` (строгий шлях і carrier-шлях) → `video_hits.append((band, c.center_mhz, emitter.last_sync_snr_db))`; перед `payload` — `if relay is not None: try: relay.update_hits(video_hits) except: LOG.exception(...)`.
  - `main()`: як TX-блок — `load_relay_config()`; якщо `relay_enabled`: `sys.path.append(../relay)`, `RelayController(..., open_fn=open_bladerf_relay_radio, loop_fn=relay_loop, reset=_reset_bladerf_backend, manual_pending=lambda: (view has_pending) or (tx has_pending))`, `publisher.on_relay_command = relay.set_command`, лог `Auto-relay enabled (dst=%.0f MHz armed=%s)`; `announce()` на connect/reconnect поряд із `tx_ctl.announce()`.
  - головний цикл: після блоку view, перед `if not cfg.scan_enabled`: `rreq = relay.pending()` → лог `entering RELAY %s %.1f -> %.1f MHz (sweep paused)` → `relay.run_relay(rreq)` → лог `RELAY ended; sweep resumes` → `continue`. `run_cycle(..., relay=relay)`. `abort`-лямбда НЕ змінюється (relay не абортить цикл).
- `publisher.py`: `self.on_relay_command=None`; в `_on_message` гілка `if "relay" in data:` (після `tx`, той самий try/except+return); `self._t_relaystate = f"fpv/{scanner_id}/relaystate"`; `publish_relaystate(ts, state)` як `publish_txstate`.

### Дашборд
- `mqtt-scan.js`: `emptyStore` без змін (per-id ключ `relaystate` додається reduce'ом як `txstate`); reduce `fpv/<id>/relaystate` → `store[id].relaystate = payload`; `buildRelayCommand(action, params={})`: `'disarm'` → `{relay:{action:'disarm'}}`; `'arm'` → `{relay:{action:'arm'}}` + `dst_mhz` якщо `Number.isFinite(Number(params.dstMhz))`; `publishRelay(id, action, params)` — як `publishTx` (retain:false).
- `views/tx.js`: у картці ноди після `.tx-actions` блок `<div class="tx-relay" data-role="relay">` (рендериться лише якщо `store[id].relaystate` є): `<label>Ціль, МГц<input class="tx-relay-dst" type="number" min="100" max="6000" step="1" placeholder="5771"></label>`, `<button class="btn tx-relay-arm">⚡ Озброїти</button>`, `<button class="btn tx-relay-disarm">■ Зняти</button>`, `<span data-role="relay-status">`. Кнопки: arm → `ctx.onRelayArm(id, {dstMhz})` (порожнє поле → undefined), disarm → `ctx.onRelayDisarm(id)`. `updateCard`: `arm.disabled = armed`, `disarm.disabled = !armed`; статус: `relaying` → `⇄ ${src_mhz} → ${dst_mhz} · ${band} · ⏱ countdown(until_ts) · вхід ${rx_level_db} дБ`; `armed && idle` → `озброєно · чекаю синхро-лок`; `!armed` → `вимкнено`; `error` → у наявний `[data-role=err]`. Reconcile-правила ті самі (не перебудовувати input, який редагує оператор).
- `app.js`: `onRelayArm/onRelayDisarm` → `mqtt.publishRelay(...)` (як `onTxStart/onTxStop`).
- `fixtures.js`: `relaystate` для bladeRF-ноди (armed, relaying 3470→5771).
- `styles.css`: `.tx-relay` — той самий ряд/відступи, що `.tx-actions`.

## Контракти (0 змін ACL/брокера)
- `fpv/<id>/rxcmd` ← `{"relay":{"action":"arm","dst_mhz":5771}}` | `{"relay":{"action":"disarm"}}` — non-retained; sub пише `fpv/+/rxcmd` ✓.
- `fpv/<id>/relaystate` (retained, QoS як txstate) → `{"scanner_id","ts","armed":bool,"active":bool,"status":"idle"|"relaying","src_mhz":number|null,"band":string|null,"dst_mhz":number,"since_ts":number|null,"until_ts":number|null,"rx_level_db":number|null,"error":string|null}`.

## Поведінка / правила
- Тригер = **тільки** синхро-локнуті джерела (кадр опубліковано у цьому циклі). Cooldown емітера (10 с) < тривалість циклу (~33–45 с), тож джерело потрапляє в хіти щоциклу.
- Вибір: найбільший `sync_snr_db` серед хітів поза guard-зоною; за рівності — перший.
- Стоп: disarm | ручна TX/view команда | втрата джерела (`lost_db`/`lost_s`) | `max_s` | помилка пристрою. Після будь-якого стопу — свіп; при `max_s` наступний цикл захопить те саме джерело знову (короткий розрив ~1 цикл).
- Під час relay спектр/детекції не публікуються (як TX/view); стан видно в `relaystate`.
- `armed` живе в пам'яті процесу (+`RELAY_ARMED` як стартове значення); після рестарту агент анонсує стан — дашборд показує правду.

## Деплой (orange_pi6)
- drop-in `/etc/systemd/system/fpv-scan.service.d/relay.conf`: `Environment=RELAY_ENABLED=1`, `Environment=SCAN_BANDS=1.2G:1080-1360,2.4G:2370-2510,3.3G:3200-3500,5.8G:5645-5945` (без 3.3-бенду свіп не бачить 3470).
- Сервер: `git pull` + `docker compose build dashboard` + `up -d --no-deps dashboard` (wg-easy/mediamtx/mosquitto не чіпати).

## Тести
- `agent/relay/tests/test_relayconfig.py`: дефолти; env-перекриття (bool/float/int).
- `agent/relay/tests/test_bladerf_relay.py`: `agc_block` — нормалізує до target, кліпує ±2047, повертає сирий rms, `peak=0` не ділить на нуль, gain cap 64; `relay_loop` з фейковим radio — читає/пише блоки до stop, кличе `on_level` щоблок.
- `agent/relay/tests/test_relay_controller.py`: `update_hits` — вибір найсильнішого, guard-зона, порожньо, не armed → None, manual_pending → None; `arm/disarm` публікують стан, disarm скидає pending і ставить stop; `run_relay` → `relaying` потім `idle`, `until_ts=since+max_s`; стоп по втраті (фейковий loop годує `on_level` rms, що падає на 20 дБ, clock стрибає на 6 с) ; стоп по дедлайну; стоп по `manual_pending`; помилка `open_fn` → `idle` з `error`, не кидає; `announce` републікує.
- `agent/scan/tests/test_run_cycle.py`: `run_cycle(relay=fake)` викликає `update_hits` з `(band, center, sync_snr)` для опублікованих кадрів (обидва шляхи) і `[]` коли нічого.
- `test/mqtt-scan.test.js`: `buildRelayCommand` arm/disarm (з/без dst), reduce `relaystate`.
- **Ручний гейт (acceptance):** дашборд → Озброїти (5771) → увімкнути VTX 3470 → ≤1 цикл свіпу → `relaystate: relaying 3470→5771` + **картинка на Skydroid B3** → вимкнути VTX → ≤~6 с `idle`, свіп продовжив (спектр оновлюється) → увімкнути VTX знову → повторне захоплення. Додатково: під час relay натиснути Старт TX-генератора → relay зупиняється, TX іде.

## Поза скоупом (YAGNI)
- Демод→ремод, зміна девіації/стандарту джерела.
- Персистенція `armed` на диску; кілька цілей; черга джерел; ретрансляція одночасно кількох.
- Абортити свіп-цикл заради relay (чекаємо кінець циклу).
