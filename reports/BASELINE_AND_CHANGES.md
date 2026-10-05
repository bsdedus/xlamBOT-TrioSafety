# Baseline и изменения Trio Safety

Source baseline: git tag v0.8.15 (`9540ce82cef79c83a7711e6445e440df31542fa3`), branch `codex/trio-safety-v0.8.15`. Release baseline и его независимые расхождения описаны в `RELEASE_AUDIT_v0.8.15.md`. Версия доработки: `0.8.15.post1` / PE numeric `0.8.15.1`.

## Архитектура baseline

`xlambot_launcher.py` загружает конфиги/зависимости, wizard и Flask runtime. `webui/device_manager.py` создаёт runtime по serial и передаёт его в `bot_instance.run_bot_instance`. В теге оставался второй игровой цикл в `main.py`. `WindowController` связывает ADB/scrcpy, кадры, нормализованные координаты и touch input. `state_finder` использует шаблоны; `StageManager` ведёт lobby, выбор бойца, matchmaking и result/reward. `Play` получает entities/tiles/gas через `Detect`, выполняет `.xlambot` playstyle, корректирует движение и управляет attack/abilities. `trophy_reader/observer` и UI services записывают OCR/историю/очередь. `match_audit` отдельно опрашивает panel snapshots/logs. `xlambot.spec` + Inno `installer.iss` собирают Windows asset.

## Найденные ошибки

| FILE / FUNCTION | PROBLEM и ROOT CAUSE | FIX | TEST / RESULT |
|---|---|---|---|
| play.py / play, navigation | После газовой проверки hysteresis/unstuck могли заменить movement; fallback random опасен | Final `MovementArbiter` после всех кандидатов, 8 направлений, проверка корпуса и коридора | Geometry/priority/NaN/wall tests PASS; реальные veto записываются |
| showdown_survivor.xlambot / gas logic | Дублированный HSV конфликтует с core NN; преследование override | Удалён дубль, playstyle выдаёт только desired; локальное выживание выше цели | Policy integration tests PASS |
| window_controller.py / move, attack, stop | Held touches при stale frame/decision; unsafe socket retries | .75s input freshness, watchdog .1s, pointer UP/disable, sendall, no retry | Live socket fault/reconnect и unit PASS; Android ACK не подтверждён |
| scrcpy/core.py / start, stop | Несколько owners одного устройства | OS lease, cleanup и единый lease для emulator/localhost aliases | Unit exclusive alias ownership PASS |
| utils.py / paths, configs | Относительные ресурсы, записи в install tree, общий mutable cache | Absolute resources, DATA_ROOT, scoped deep copies/atomic fsync replace | Packaged external cwd и profile tests PASS |
| device_profiles.py / update_settings | Basename cfg мог записаться в install root | Canonical known config path в scoped profile | Basename write isolation test PASS |
| webui/services.py / bootstrap | Missing first history ломает новый UI | Пустая история корректна | Unit и packaged bootstrap 200 |
| bot_instance.py / game loop | Повторная обработка одного кадра, огромный ложный FPS | Unique frame timestamp gate и elapsed processed FPS | Unit PASS; live измерения отдельно |
| adb_connection.py, webui/device_manager.py / foreground | Android 14 `app_current()` требует ~5.2s, таймер повторяет сразу | Window displays focus, finite transport timeout; timer после завершения | Реальный опрос .14–.17s, silent TCP handshake test PASS |
| detect.py / infer | Недостаточный fallback providers, stale letterbox pixels | Actual failed GPU inference -> CPU; fill padding, finite clip, locks | Unit CPU fallback и 4 реальных модели PASS |
| state_finder.py / get_state | Actual RU HUD/result/modal отсутствуют; unknown сбрасывает input | Live-derived constant-label templates; positive Trio lobby icon; 3 distinct HUD confirmations | Template 3 resolutions + dim modal negative PASS |
| StageManager / lobby, start | Initial lobby считался сыгранным; mode неверифицирован | Started flag и обязательная Trio confirmation | Source inspection; rotation live отдельно |
| match_audit.py / run | Lobby return считался match, time включал idle, detector miss считался death | Result required, interrupted log, end timestamp, LifeTracker conservative evidence | Unit PASS; первый live record имеет отдельную ручную поправку |
| main.py / runBot | Второй старый цикл обходил общие исправления | Делегирование единому bot_instance | Shared entry test PASS |
| launcher / shutdown | Потоки/UI и inputs оставались активны | stop_all, protected shutdown, clean server exit | Packaged stop/shutdown/relaunch PASS |
| installer/spec / build | Models/resources/version/profile separation неполны | 4 ONNX, cfg JSON/TOML, bundled OCR, единая версия, отдельный AppId | Final build/installer выполнены; итоговые результаты в FINAL_VALIDATION и packaging_validation.json |

## GAS SYSTEM BEFORE / AFTER

До: NN gas в core и отдельный HSV в playstyle; настройки asset/source отличаются; endpoint/line проверки недостаточны для тела; последующее изменение движения может обойти veto. Центр экрана и random fallback не доказывают проходимость.

После: свежий frame -> gas inference -> bounded temporal mask (TTL .6s, max .75s) -> hit circle и overlapping corridor samples -> desired/style/hysteresis/unstuck candidate -> final arbiter -> свежесть decision -> WindowController. Immediate/near/far/terminal risk разделены, путь с газом или стеной veto; при отсутствии чистого варианта escape выбирает наблюдаемый вариант с меньшим газом, а safe state удерживает position. 8 направлений и desired получают scores; враги/видимые союзники/локальная свободная зона — мягкие критерии после hard veto. Центр — слабый bias, не гарантия. Исчезновение player освобождает все inputs и сбрасывает движение; stale mask не выдаётся за свежую.

Газовый escape подавляет attack/gadget; super dash/charge/unknown и hyper veto при опасности/неуверенности. Абсолютная гарантия невозможна: arbiter защищает только по наблюдаемой маске, а NN может пропускать газ. Нулевая ошибка unit geometry не доказывает качество detector. Темпоральный cache — защита от коротких пропусков, не долгосрочная карта.

World telemetry хранит frame/detection time, player/entities/walls, gas boxes/confidences/mask age, desired/candidate/final, риски 8 направлений, scores/reasons, safety overrides и ability veto. Вне экрана состояние жизни союзника UNKNOWN. Motion state осторожно учитывает background shift, но ещё не калиброван по размеченному набору stuck.

## Изменённые файлы

Игровой pipeline: `play.py`, `playstyles/showdown_survivor.xlambot`, `detect.py`, `navigation_safety.py`, `gas_config.py`, `bot_instance.py`, `main.py`, `StageManager`, `state_finder.py`, live UI templates. Capture/input: `window_controller.py`, `adb_connection.py`, `device_lease.py`, `scrcpy/core.py`, `scrcpy/control.py`. Профили/UI: `utils.py`, `device_profiles.py`, `trophy_observer.py`, `webui/app.py`, `webui/device_manager.py`, `webui/services.py`, `static/js/studio.js`, `debug_view.py`, cfg. Аудит: `match_audit.py`, `life_tracking.py`, `replay_navigation.py`, `diagnostics.py`, tests. Упаковка: launcher/setup/wizard/spec/install/Inno/version metadata/build lock. Полный snapshot и git diff должны сопровождать итоговую поставку.

## Проверки и ограничения

90 tests PASS (2026-10-05), 1 deprecated audioop warning. Первоначальный pytest вызов имел 4 fixture errors из-за недоступного общего TEMP каталога; запуск с новым workspace basetemp прошёл. Это не скрытый PASS первого вызова.

Model microbenchmark DML, 30 samples, один сохранённый lobby frame: baseline median/p95 main 5.61/6.44ms, gas 5.27/5.99ms, tile 5.07/5.72ms, close 5.06/5.86ms; early patch 5.71/6.51, 5.56/6.55, 5.40/6.58, 5.52/6.44ms. Модели не ускорены; overhead немного вырос. Эти числа не gameplay FPS и не end-to-end latency. Реальные live FPS относятся к конкретной ревизии и матчам, итог отдельно.

Реально доступны Windows 11 build 26300, RTX 4060, DML/CPU, один Android 14 LDPlayer, 1600x900 / decoded 1600x896. Windows 10, чистая VM, два физических устройства и CUDA provider не проверены. Изоляция нескольких профилей и owners проверена тестами; это не dual-device gameplay.


## Финальные результаты

15 реальных матчей завершены несколькими ревизиями: 12 первых, 2 вторых, 1 третье место; 12 личных смертей, 7 возрождений, 22 жизни, +147 кубков по результатам. 5 lethal enemy events подтверждены kill feed, 7 причин UNKNOWN. Эти показатели не доказывают отсутствие P1 или нулевые avoidable gas deaths. Подробные 18-вопросные разборы и latency/FPS приведены в MATCH_REPORT_15.md.

Последние исправления state captions, trophy delta, bounded recovery, team drawer, ROI escape и life evidence/respawn debounce описаны с test/risk в FINAL_VALIDATION.md. Полная исходная ветка остаётся без commit/push.
