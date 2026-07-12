# bladeRF Scanner — Pi Deploy Notes (Phase 1)

Deploying the bladeRF backend (`SCAN_SDR=bladerf`) on the scan Pi. Validated on the
Raspberry Pi 5 node (Debian 13 trixie, Python 3.13) with a **bladeRF 2.0 micro xA4**.

## 1. Host packages (apt)

```bash
sudo apt-get install -y bladerf libbladerf-dev python3-bladerf bladerf-fpga-hostedxa4
```

- `python3-bladerf` provides the `bladerf` Python binding (installed to
  `/usr/lib/python3/dist-packages`). `bladerf_source.py` imports it lazily inside
  `open_bladerf_capture`, so its absence only disables the bladeRF backend.
- `bladerf-fpga-hostedxa4` drops the FPGA bitstream at
  `/usr/share/Nuand/bladeRF/hostedxA4.rbf`. **This step is required** — without the
  `.rbf` in that path, `BladeRF()` opens but every RF op fails
  (`FPGA bitstream file not found` → `Board state ... requires "FPGA Loaded"`).
- **Pick the package for YOUR board.** Read the variant with the device open (no `.rbf`
  present, so open succeeds in "Firmware Loaded" state):
  `python3 -c "import bladerf; print(bladerf.BladeRF().get_fpga_size())"` →
  `49` = xA4 (`hostedxa4`), `301` = xA9 (`hostedxa9`). Install ONLY the matching
  package; having both `.rbf` in the autoload dir can make autoload pick the wrong
  (larger) image.

## 2. Link the binding into the venv

The service runs from the venv at `/opt/fpv-video-stream/agent/scan/.venv`, which does
not see system dist-packages. Symlink the binding in (same pattern as `lgpio`):

```bash
SP=/opt/fpv-video-stream/agent/scan/.venv/lib/python3.13/site-packages
ln -sf /usr/lib/python3/dist-packages/bladerf "$SP/bladerf"
```

Verify: `/opt/fpv-video-stream/agent/scan/.venv/bin/python -c "import bladerf, numpy"`.

## 3. Enable the backend

The repo unit already sets `Environment=SCAN_SDR=bladerf`. On a hand-diverged Pi unit,
add it and set the scanner id, then reload:

```bash
sudo sed -i 's/^Environment=SCAN_SDR=.*/Environment=SCAN_SDR=bladerf/' /etc/systemd/system/fpv-scan.service
# (register a `bladerf` scanner on the dashboard; set Environment=SCAN_ID=bladerf)
sudo systemctl daemon-reload && sudo systemctl restart fpv-scan
```

## 4. Verify

```bash
bladeRF-cli -e info            # must show a loaded FPGA version, not "Unknown (FPGA not loaded)"
```
Then confirm `fpv/bladerf/spectrum` + `fpv/bladerf/detection` flow on the broker and the
scanner shows online on the dashboard; with a known 5.8 VTX on air, confirm it classifies
`analog` and the RX5808 auto-tunes to it.

## POWER REQUIREMENT (learned the hard way, 2026-07-03)

The bladeRF 2.0 micro needs **USB 3** (40 Msps SC16 ≈ 160 MB/s exceeds USB 2) **and**
stable 5 V. On the Pi-5 node the bladeRF is on the Pi's own USB3 port, and with a
marginal supply (`vcgencmd pmic_read_adc EXT5V_V` ≈ 4.77 V) **FPGA configuration fails**:

```
Failed to read FPGA version[0]: Operation timed out
_bladerf2_initialize: get_fpga_version ... Operation timed out
```

The FPGA configures but its NIOS soft-core then can't be reached — a classic
brownout-during-config symptom. Fix: a proper **Raspberry Pi 5 27 W (5.1 V/5 A) USB-C
PSU**, or a **powered USB 3 hub** for the bladeRF (a USB 2 powered hub throttles it below
40 Msps). Check `vcgencmd get_throttled` — bit `0x1` (under-voltage now) or a low `EXT5V`
under load means power, not code.

Optional: flash the FPGA to the bladeRF's SPI flash so it autoloads at boot without a
per-open USB bitstream transfer (faster, more robust) — but flashing still needs stable
power to complete.

## bladeRF як вьювер + ролі SDR (2026-07-12)

Будь-який SDR може свіпити та/або стрімити. Роль юніта = два прапорці:

| env | дія | default |
|-----|-----|---------|
| `SCAN_ENABLED` | вмикає свіп-цикл | `1` |
| `VIEW_ENABLED` | вмикає вью-стрім | `0` |

- **Чистий свіпер:** `SCAN_ENABLED=1 VIEW_ENABLED=0` (напр. bladeRF на всі бенди).
- **Чистий вьювер:** `SCAN_ENABLED=0 VIEW_ENABLED=1 VIEW_PUSH_URL=rtsp://<pubuser>:<pass>@10.8.0.1:8554/<stream>`.
- **Обидва (один SDR):** `SCAN_ENABLED=1 VIEW_ENABLED=1` — свіпить, паузить на вью, повертається.

**bladeRF-вьювер:** виставити `SCAN_SDR=bladerf`, `VIEW_ENABLED=1`,
`VIEW_PUSH_URL=.../bladerf-view`. Підсилення береться з `BLADERF_GAIN`, sample_rate з
`VIEW_SAMPLE_RATE_HZ` (default 8 MS/s).

**Сервер (один раз):** зареєструвати publisher-девайс + MediaMTX-шлях `bladerf-view`
(як існуючий `hackrf-view`) у `devices.yml`, `node bin/gen-mediamtx.js`, перезапустити MediaMTX.
Дашборд підхопить новий стрім з ретейн-анонсу `fpv/<id>/view` — коду сервера/дашборда міняти НЕ треба.

**Два SDR на вузлі:** два systemd-юніти, по одному на пристрій, ролі призначаються вільно
(напр. bladeRF `SCAN_ENABLED=1 VIEW_ENABLED=0` + HackRF `SCAN_ENABLED=0 VIEW_ENABLED=1`, або навпаки).
Різні процеси/пристрої → свіп і стрім ідуть паралельно, без взаємних пауз. НЕ перезаписувати
hand-diverged unit-файли — редагувати їхні `Environment=`/`EnvironmentFile`.

**Відновлення чистого bladeRF-вьювера (`SCAN_ENABLED=0`):** транзієнтні стали лікує `recover()`
(close+reopen+retune) всередині сесії. Але стійкий USB-wedge (undervoltage-class reset → NoDevError
в тому ж процесі) чистий вьювер САМ не полікує: процес-екзит із рестартом спрацьовує лише на шляху
свіпу (`run_cycle`), який при `SCAN_ENABLED=0` не викликається — тож кожен вью падатиме з помилкою,
а процес ідлитиме. Рекомендація: тримати bladeRF свіпером + HackRF вьювером (основний профіль), або
додати зовнішній вотчдог на юніт-вьювер.
