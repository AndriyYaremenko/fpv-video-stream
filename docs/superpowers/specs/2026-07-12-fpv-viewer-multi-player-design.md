# «FPV Viewer» — плеєр на кожен вьювер + зручне перемикання каналів — Design

**Date:** 2026-07-12
**Status:** Approved design, ready to plan.
**Target:** dashboard front-end only (`dashboard/public/*`). НУЛЬ змін сервера/агента/MQTT.
**Context:** тепер два вьювери (bladeRF + HackRF, обидва анонсують `fpv/<id>/view`), але
панель показує лише один авто-обраний плеєр. Будує на [[multiband-view-workflow]] +
[[bladerf-viewer-role]] + tactical UI v2 ([[tactical-ui-redesign]]).

## Мета
Дати оператору **керувати кожним доступним вьювером окремо** й зручно перемикати канали:
один плеєр із контролами на кожен онлайн view-capable SDR, пліч-о-пліч.

## Проблема (звірено в коді)
- `views/viewer.js` + `app.js syncViewerPlayer()` тримають ОДИН `<video id="viewer-video">`,
  привʼязаний до `displayId = activeViewer() || pickViewer()`. Оператор не може обрати, який SDR
  стрімить, і не бачить обидва.
- `pickViewer()` авто-обирає (prefer idle); з двома вьюверами старт іде на будь-який вільний.
- Перемикання = клік детекції або ручний МГц; кнопок «куди саме» нема.

## Рішення (від оператора)
- **Компонування:** зліва — той самий зведений список детекцій; справа — **сітка карток-плеєрів,
  по одній на кожен онлайн view-capable вьювер**, одночасно.
- **Канал-контроли на картці:** поле МГц + **◀▶ = стрибок на попередню/наступну ДЕТЕКЦІЮ** (по
  частоті зі зведеного списку), `▶ дивитись`, `■ свіп`, бейдж активної сесії.
- **Маршрут детекції:** кожен рядок списку має **кнопки вьюверів** (`▶bladeRF ▶HackRF`, лише
  онлайн) — клік по потрібній тюнить саме той SDR на цю частоту. 5.8G-детекція додатково штовхає
  RX5808 (як зараз, через `pickRxScanner`).
- **Міні-спектр — по картці** (видно, що бачить кожен SDR). **Мініатюру відновленого кадру
  прибираємо з вьювера** (лишається в екрані «Кадри»).

## Застереження (свідомо прийняте)
Поки SDR стрімить — він не свіпить (single-SDR арбітрація). Якщо дивишся обидва одночасно — список
детекцій не оновлюється (свіжий лишається від того SDR, що ще свіпить). Обидва RTSP-стріми завжди
існують (persistent-engine пушить чорне з завантаження), тож плеєри просто підключаються/чорніють.

## Архітектура / потік даних
```
fpv/+/detection ─▶ viewerState (merge) ─▶ список (зліва)
fpv/<id>/view (retained, per SDR) ─▶ scanStore[id].view {active,freq_mhz,until_ts,stream,error}
                                        │
   views/viewer.js: сітка карток (reconcile по id) ── картка = <video id="viewer-video-<id>"> + контроли
                                        │
   app.js syncViewerPlayers(): Map WHEP-плеєрів по id (add/remove/resync) ─▶ MediaMTX <stream>
   рядок ▶<id> / картка ◀▶/МГц/▶/■ ─▶ onViewStart(id,freq) | onViewStop(id) ─▶ fpv/<id>/rxcmd
```
MQTT/store/сервер/агент — **без змін**.

## Компоненти

### `dashboard/public/viewer.js` (pure, тестується) — нові helpers
- `viewerCards(store)` → відсортований (стабільно, за `id`) масив `{id, label, stream, view}` для
  онлайн view-capable вьюверів (`store[id].online && store[id].view`). Замінює single `pickViewer`
  для РЕНДЕРУ; `pickViewer`/`activeViewer` лишаються (сумісність/інші виклики).
- `stepDetectionFreq(rows, curFreq, dir)` → частота сусідньої детекції (`dir` = ±1) у списку,
  відсортованому за частотою; wrap або clamp на краях; `null` якщо рядків нема. Чиста, тестована.
- `viewerLabel(id)` → людська назва (`bladerf`→`bladeRF`, `hackrf`→`HackRF`, інакше сам `id`).

### `dashboard/public/views/viewer.js` (екран) — сітка карток із reconcile
- `.viewer-stage` тримає N карток; **картка будується раз на `id`** (keyed) і живе між рендерами —
  НЕ re-innerHTML (містить живий `<video>` + user-input МГц). Reconcile як node-strip у
  `dashboard.js`: додати картку для нового вьювера, прибрати для зниклого, оновити наявну in-place
  (бейдж/active/err/стан кнопок). `<video id="viewer-video-<id>">`.
- Список: рядок отримує кнопки вьюверів (делеговані кліки; кнопка знає `id` вьювера + `freq`).
- ◀▶ на картці: `onViewStart(cardId, stepDetectionFreq(rows, curFreq, dir))`.
- Поле МГц + `▶ дивитись`: `onViewStart(cardId, Number(input))`. `■ свіп`: `onViewStop(cardId)`.
- Міні-спектр на картці для активного бенду того SDR (reuse `renderMiniSpectrum`). Тумбнейл — геть.

### `dashboard/public/app.js` — багатоплеєрний lifecycle
- `syncViewerPlayers()` замінює `syncViewerPlayer()`: ітерує поточні картки (`viewerCards`), тримає
  `Map viewerPlayers[id] = {video, retryToken}`; для кожної картки привʼязує WHEP до
  `viewStream(store,id)` через той самий gen-token механізм (узагальнений із single-плеєра);
  прибирає плеєр коли картка зникла (stop WHEP, звільнити). Викликається екраном після кожного
  DOM-оновлення (як зараз).
- Хендлери `onViewStart(id,freq)`/`onViewStop(id)`/`onScanCmd(id,cmd)` уже per-id — лишаються.
  `viewerRowClick` → тепер приймає явний `viewerId` з кнопки рядка (не `pickViewer`).

## Error handling
- Вьювер офлайн/зник → його картка прибирається, WHEP-плеєр зупиняється (без «мертвого» video).
- `view.error` показується на картці того вьювера.
- Нема онлайн-вьюверів → сітка показує підказку «SDR view недоступний», список рендериться без
  кнопок вьюверів.
- WHEP reconnect — існуючий backoff (`whepRetryDelay`), per-плеєр gen-token (стара сесія не гонить).

## Reconcile-safety (критично — [[tactical-ui-redesign]] гоча)
Картка з живим `<video>` та полем МГц НЕ перебудовується innerHTML щотіка. Створюється раз, далі
оновлюються лише текст-вузли (бейдж/err) і стан кнопок. Введений у полі МГц текст переживає рендер.

## Testing
- Node-юніти (`test/viewer.test.js`): `viewerCards` (фільтр online+view, стабільне сортування,
  stream/ label); `stepDetectionFreq` (±1, wrap/clamp, порожньо, одна детекція, поточна не в списку);
  `viewerLabel`.
- Візуальний гейт (dev-preview `?preview=1` + `fixtures.js`): 2 вьювери онлайн, один активний →
  дві картки, кнопки вьюверів у рядках, ◀▶ стрибає по детекціях; reconcile: `__rerender` двічі не
  churне живий `<video>` і введений МГц; вьювер офлайн → картка зникла.
- `npm test` зелений (решта не зачеплена).

## Deploy
Front-end only: rebuild+recreate dashboard-контейнера (`--no-deps`, wg-easy/mediamtx/mosquitto НЕ
чіпати), як у [[tactical-ui-redesign]]. Без змін агента/сервера/MQTT.

## Ризики / нотатки
- Два одночасні WHEP у браузері + два стріми — на Pi обидва persistent-ffmpeg уже пушать; браузер
  тягне 2 low-latency H264 480×288 легко.
- Reconcile карток — головний ризик регресії; візуальний гейт обовʼязковий.
- Якщо вьюверів >2 у майбутньому — сітка масштабується (flex-wrap).
