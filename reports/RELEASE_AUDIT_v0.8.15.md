# RELEASE AUDIT v0.8.15

Проверен именно исходный релиз v0.8.15 (публикация Olegu621), а не текущий main. Source baseline: commit `9540ce82cef79c83a7711e6445e440df31542fa3`. Название: «xlamBOT 0.8.15 — для Windows». Публикация: 2026-10-04 10:33:59 UTC / 13:33:59 Europe/Minsk. Installer asset обновлён в 19:13:19 UTC того же дня: дата публикации страницы и дата файла различаются.

## Степень проверки

| Проверка | Результат | Основание |
|---|---|---|
| RELEASE PAGE REVIEWED | YES | API metadata, описание, список assets |
| ASSETS DOWNLOADED | YES | Установщик и оба автоматически предоставленных исходных архива |
| ASSETS INSPECTED | YES | Хеши, инвентаризация, сравнение ресурсов, PYZ inventory |
| PACKAGED BUILD EXECUTED | YES | Исходный EXE запущен вне developer cwd, без Python/ADB в PATH |
| CLEAN INSTALL TESTED | YES, с оговоркой | Чистая папка и новый профиль, но та же Windows с ранее установленным xlamBOT; полноценная чистая VM отсутствует |

## Файлы релиза

| Asset / тип | Размер, bytes | Назначение и содержимое | SHA256 |
|---|---:|---|---|
| xlamBOT-Setup-0.8.15.exe / Inno Setup | 208413423 | Единственный загруженный автором asset: EXE PyInstaller, Python runtime, 4 ONNX, scrcpy JAR, ADB, Tesseract, cfg, UI/templates/images/playstyles | `bf5e077d70180ae70ebcf6da1992510d78ef37577be4e10983326e948540fbba` |
| source.zip / GitHub archive | 42254973 | 342 файла тега, без собранного EXE и vendor Tesseract | `5ab1960363072a8e8a0c98675f8c7f31cd2798fcdb81895f760af012b3897f1f` |
| source.tar.gz / GitHub archive | 42134351 | Те же 342 файла тега | `5be1df407f18f5624e8a0b17fa448a79e4d216c577efea676fe58b3813b4c394` |

Отдельного portable, дополнительного EXE, набора моделей или конфигов в списке uploaded assets нет. ZIP/TAR.GZ — автоматические source archives GitHub. Ожидаемый источник установщика: `xlambot.spec`, `xlambot_launcher.py`, runtime модули, `installer.iss`, `install.ps1`, модели/ресурсы тега и внешняя переносимая сборка Tesseract.

Оба source archives совпали с git tag по содержимому всех файлов. Среди 302 сопоставленных ресурсов EXE 280 побайтово совпали, 22 различались. После нормализации CRLF осталось 6 содержательных различий: `cfg/bot_config.toml`, `static/css/panel.css`, `static/css/studio.css`, `static/js/panel.js`, `static/js/studio.js`, `templates/index.html`. Missing resources среди сравниваемых 302: 0. Все четыре ONNX совпали с тегом; scrcpy JAR цел и читается как ZIP.

Дополнительные файлы пакета: модуль `training_capture` в PYZ, `playstyles/heist_guard.xlambot`, `templates/training.html`, training CSS/JS и Python 3.13 pycache в scrcpy. Наличие скомпилированных модулей проверено, но побайтовое/семантическое соответствие всех Python-модулей EXE тегу не доказано. Поэтому утверждение «EXE собран точно из тега» не подтверждено.

## Release notes: проверяемые обещания

Описание обещает установку без администратора в профиль пользователя, первоначальный wizard устройства/игры, LDPlayer или ADB-телефон с запущенной игрой, автоматический Trio, выбор бойцов по сортировке, ротацию после редактируемой квоты (7 по умолчанию), панель/очередь/логи/статистику. Возможное отсутствие account total OCR прямо оговорено. Обещаний измеренной точности газовой модели, нулевых смертей в газе или доказанной устойчивости к stuck описание не содержит.

| Обещание | Код | Реальная проверка оригинального release |
|---|---|---|
| Установка без администратора / per-user | installer допускает режим, но существующая установка/AllUsers может изменить контекст | В этой системе первоначальный silent install затронул HKLM uninstall/shortcuts существующей установки; исходные записи и три ярлыка восстановлены. Универсальное обещание не подтверждено |
| Wizard и bundled dependencies | launcher/setup_wizard, vendor | EXE стартовал из внешнего cwd; `/panel` 200 |
| Рабочая панель сразу после установки | webui bootstrap читает history | Новый профиль: `/api/bootstrap` 400 из-за отсутствующего `cfg/match_history.csv` — FAIL |
| Trio | gamemode config и выбор UI | Код есть; надёжность актуального UI требует отдельной положительной проверки иконки |
| Сортировка/ротация | StageManager и OCR | Код есть; OCR может вернуть неправильное имя/пустые трофеи. 15 baseline матчей не проводились |
| Account total | trophy OCR | Значение оставалось null; предупреждение notes соответствует наблюдению |

## Проблемы и исправления

| ISSUE | SOURCE OR RELEASE | SEVERITY | ROOT CAUSE | FIX в post1 | VERIFIED |
|---|---|---|---|---|---|
| Версия EXE 1.0.0 при tag 0.8.15 | Release | P1 | Отдельные несогласованные version constants | Единый `version.py`, PE/Inno metadata | PE metadata и тест сборки |
| Bootstrap 400 на новом профиле | Both | P1 | History обязательна до первой игры | Пустая история допустима, defaults seed | Unit и packaged API 200 |
| Writes в install tree / относительные пути | Both | P1 | cwd используется для ресурсов и состояния | PROJECT_ROOT и writable DATA_ROOT, atomic per-device profiles | Внешний cwd, Кириллица, ACL write-deny; итоговый smoke отдельно |
| gas_avoidance=no в asset, yes в source | Release | P0 | Пакет содержит другой config | Shared mandatory safety, cfg validation | Unit; качество наблюдения газа оценивается в матчах |
| Две конфликтующие газовые логики | Source | P0 | HSV playstyle и core NN не согласованы | Один final arbiter после всех кандидатов | Unit geometry/priority; live telemetry |
| Старый пакет игнорирует --headless | Release | P2 | Нет соответствующего CLI dispatch | Headless/diagnostics/audit/replay dispatch | Packaged runs |
| Models/providers/OCR | Release | — | 4 модели присутствуют, DML/CPU доступны | Проверяемые diagnostics и CPU fallback | Реальные model inference DML/CPU; не CUDA |

Комментарий автора о gas training: 9 уникальных Heist/Nexus кадров, отсутствие реального газа, 8355/22848 некорректных координат. Это утверждение автора, а не независимая проверка отсутствующего dataset. Оно объясняет необходимость проверки модели в реальных Trio кадрах, но не заменяет её.

Первичные доказательства сохранены в `.audit/release/`: `metadata.json`, `manifest.json`, `source_archives.json`, resource comparison, clean-profile stdout/stderr/result. `.audit/restore_result.json` подтверждает восстановление registry/shortcuts исходной установки. Эти локальные материалы содержат пути и игровые данные; source archive не должен включать их автоматически.

Оригинальный release: installer и EXE реально выполнены; config/model loading проверены; UI bootstrap FAIL; ADB discovery и scrcpy проверяются отдельно на доступном устройстве. Полный gameplay original EXE не засчитан как baseline experiment. Windows 10, чистая VM и два физических устройства отсутствуют — NOT POSSIBLE в текущей среде. Новый installer имеет отдельный AppId и имя «xlamBOT Trio Safety», чтобы не подменять существующую установку.


## Итог нового кандидата post1

Собран новый directory EXE + portable ZIP + отдельный per-user installer с неизменной версией 0.8.15.post1/0.8.15.1. Установка, два запуска панели, четыре ONNX inference, capture selftest, запрет записи install tree с восстановлением ACL и сохранность профиля при uninstall проверяются на итоговом asset; первичные результаты и SHA256 в deliverables/packaging_validation.json и manifest.json. Gameplay выполнен готовым frozen EXE во внешней папке; 15 полных результатов и выявленные ошибки перечислены в MATCH_REPORT_15.md. Последний аудитный debounce/history fix проверен unit regression и заново собран; он не выдаётся за новую повторную серию из 15 игр.
