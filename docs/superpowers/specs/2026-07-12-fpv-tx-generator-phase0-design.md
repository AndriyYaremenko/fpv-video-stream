# FPV відео-передавач із файлу (bladeRF TX) — Phase-0 спайк — Design

**Date:** 2026-07-12
**Status:** Approved design (Phase-0 spike), ready to plan.
**Target:** новий пакет `agent/tx` (standalone CLI). Залізо: bladeRF (TX) + RX5808-граббер (гейт).
**Context:** нова фіча — bladeRF як передавач аналогового FPV-відео з файлу. Це **Phase-0
феазибіліті-гейт** (довести, що bladeRF TX синтезованого FM-відео приймається реальним RX5808).
Продакшн-обгортка (режим/команда/UI) — окрема Phase-A спека ПІСЛЯ успішного гейту.
Референс TX-обв'язки: спайк `agent/scan/tools/relay_spike.py` на гілці `feat/bladerf-video-relay`.
Генерація сигналу вже є: `agent/video/synth.py` (`make_cvbs` + `fm_modulate`).

## Мета (Phase-0)
Standalone CLI: **(1)** відрендерити відеофайл у зациклюваний IQ-бін (SC16_Q11), **(2)** крутити
його по колу через bladeRF TX на заданій частоті; підібрати `deviation_hz`/`fs`/gain так, щоб
**RX5808 + граббер показали картинку** з кліпу. Мінімум коду, без інтеграції у свіп-агент/UI.

## Архітектура (pre-render + луп)
```
[render, раз] file → ffmpeg (fps, scale WxH, gray) → сірі кадри (0..255)
              → /255 → make_cvbs(standard, frame, fs, interlaced, vbi_lines)  [synth, є]
              → fm_modulate(bb, fs, deviation_hz)                              [synth, є]
              → to_sc16q11 (нове: int16 ×2047)  → append → iq.bin (весь кліп)
[transmit]    bladeRF TX (CHANNEL_TX(0), TX_X1, SC16_Q11): читає iq.bin блоками,
              sync_tx у циклі, БЕЗШОВНИЙ wrap на кінці файлу → freq/gain/rate.
              Частоту/gain міняємо ретюном без ре-рендеру; fs МУСИТЬ збігатися з render.
```

## Компоненти

### `agent/tx/render.py` (нове)
- `to_sc16q11(iq) -> bytes` — квантування complex IQ у SC16_Q11 (int16, ×2047, clip [-2048,2047],
  інтерлівд I/Q). Дзеркало `iq_from_sc16q11` навпаки. Чисте, тестоване.
- `frame_to_iq(frame_gray, standard, fs, deviation_hz, interlaced, vbi_lines) -> bytes` — один
  сірий кадр (numpy 0..255) → `/255` → `make_cvbs` → `fm_modulate` → `to_sc16q11`. Перевикористовує
  synth; тестоване без ffmpeg/заліза.
- `build_ffmpeg_decode_cmd(path, fps, width, height) -> list[str]` — argv:
  `ffmpeg -i <path> -vf "fps=<fps>,scale=<w>:<h>,format=gray" -f rawvideo -` (сірі кадри в stdout).
  Чиста, тестована.
- `render(path, out_bin, cfg)` — прогнати ffmpeg, читати кадри `w*h` байт, `frame_to_iq` кожен,
  дописувати у `out_bin`; обмежити довжину `max_secs` (луп короткий). Інтеграція (ffmpeg).

### `agent/tx/bladerf_tx.py` (нове, єдине bladeRF-touching)
- `open_bladerf_tx_radio(freq_hz, fs_hz, gain_db, bandwidth_hz) -> BladeRfTxRadio` — за референсом
  relay_spike: `BladeRF()`, `CHANNEL_TX(0)`, `set_sample_rate/set_bandwidth/set_frequency/set_gain`,
  `sync_config(layout=TX_X1, fmt=SC16_Q11, num_buffers=16, buffer_size=8192, num_transfers=8,
  stream_timeout=3500)`, `enable_module(tx, True)`. Єдина фн, що імпортує `bladerf`.
- `BladeRfTxRadio`: `write(buf: bytes)` (sync_tx block), `set_frequency(hz)`, `close()`
  (enable_module False + close). Радіо інжектоване → тестовано з фейком.
- `transmit_loop(radio, iq_path, block_samples, stop_check)` — читає iq.bin блоками
  `block_samples*4` байт, `radio.write` кожен; на EOF **wrap на початок** (безшовний луп); поки не
  stop. Чиста логіка над інжектованим radio + файлом → тестовано (фейк-radio ловить порядок/wrap).

### `agent/tx/main.py` (CLI)
- `python -m main render <file> <out.bin> [--standard PAL] [--fs 20e6] [--dev 4e6] [--w 640 --h 512]
  [--fps 25] [--secs 3]` → iq.bin.
- `python -m main transmit <iq.bin> <freq_mhz> [--fs 20e6] [--gain 30]` → крутить по колу (Ctrl-C стоп).
- Друкує розмір/тривалість .bin, ефективний MS/s, попередження про частоту/потужність.

## Параметри та дефолти (стартові, гейт тюнить)
- `standard` PAL (15625 Hz/625 ліній) | NTSC. `interlaced=True` + `vbi_lines` (кілька) — щоб RX5808
  зловив вертикальну синхру; **фідельність синхри — змінна гейту** (якщо граббер не локається — рафінуємо
  синхро у CVBS).
- `fs` 20 MS/s (як relay-спайк), `deviation_hz` ~4 MHz (стартово; **головний knob гейту**), `gain` 30.
- Кадр `640×512` grayscale, `fps` 25 (PAL). Кліп `--secs` 2-3 (луп короткий; розмір .bin = fs×4Б×secs
  ≈ 160–240 МБ на 20 MS/s — ок на диску Pi).

## Phase-0 гейт (ручний, залізо — НЕ автоматизується)
1. На Pi: зупинити свіп-юніт (звільнити bladeRF). `render <clip.mp4> iq.bin` → `transmit iq.bin <ch5.8>`.
2. RX5808 навести на той самий 5.8-канал; дивитись граббер-стрім (уже в системі).
3. **Успіх = у граббері видно картинку з кліпу** (нехай нечітку). Тюнити `deviation_hz` (± навколо
   4 MHz), `fs`, `gain`, `vbi_lines`, поки не зловиться.
4. Якщо ніяк — задокументувати як дед-енд (девіація/синхра/потужність), як зробили з
   [[bladerf-relay-not-viable]]. Але тут ми контролюємо девіацію → шанси кращі.

## Testing (pytest, без заліза/ffmpeg)
- `to_sc16q11`: complex → int16 SC16_Q11 (масштаб/clip/інтерлів); round-trip проти `iq_from_sc16q11`.
- `frame_to_iq`: сірий кадр → IQ ненульове, правильна довжина (frames×spl×4), синтетичний патерн
  дає структуру (не константа).
- `build_ffmpeg_decode_cmd`: argv містить fps/scale/format=gray/rawvideo, шлях.
- `transmit_loop`: фейк-radio — читає всі блоки в порядку, **wrap** на EOF (2-й прохід = ті самі
  байти), stop зупиняє, часткові хвости обробляються.
- `open_bladerf_tx_radio` — НЕ юніт-тест (імпортує bladerf; дзеркало open_bladerf_view_radio).

## Deploy (Phase-0 — ручний спайк, без systemd/UI)
- Pi: `git pull`; для гейту ТИМЧАСОВО зупинити `fpv-scan` (bladeRF вільний), `sudo` запуск CLI
  (bladeRF потребує прав; venv). Після гейту — повернути свіп.
- **Не чіпає** дашборд/сервер/інші юніти. Не деплоїмо як сервіс.

## Ризики / нотатки
- bladeRF TX у цьому ригу — нове (але спайк relay довів, що TX-обв'язка стартує).
- Синхро make_cvbs може бути недостатньо «справжнім» для RX5808-локу → рафінувати (реальні PAL
  vsync/еквалайзинг) — частина гейту.
- Розмір .bin: тримати кліп коротким; писати у скретч (`/var/lib/fpv` або `/tmp`).
- Легально/потужність/антена — відповідальність оператора (екранований стенд).
- Phase-A (режим+команда+UI+арбітрація свіпу+реєстр файлів) — окрема спека ПІСЛЯ гейту.
