# Итоговая проверка xlamBOT Trio Safety 0.8.15.post1

Сборка является проверочным кандидатом. Проведены 15 реальных полных матчей Trio с сохранением результатов и непрерывных кадров; исправления вводились между ревизиями. Требование «15 матчей одной финальной сборки без ошибок» не достигнуто. Не доказаны ноль добровольных входов в видимый газ и отсутствие P1 ошибок восприятия. Победы команды не используются как доказательство исправности AI.

## Доказательства и результаты

| Проверка | Результат | Доказательство |
|---|---|---|
| Source baseline | Tag v0.8.15 / 9540ce82cef79c83a7711e6445e440df31542fa3 | Git, независимые source archives |
| Release page, assets downloaded, inspected | YES | RELEASE_AUDIT_v0.8.15.md; .audit/release |
| Original packaged build executed | YES; bootstrap на новом профиле FAIL | .audit/release; original profile results |
| Изменения safety/input/profile/state/audit | Реализованы; 90 tests PASS, audioop deprecation warning | .audit/tests_DELIVERY.txt |
| Реальные матчи | 15 полных; 12 первых мест, 2 вторых, 1 третье | MATCH_REPORT_15.md, verified_results.json, result JPEG |
| Смерти / возрождения / жизни | 12 / 7 / 22, вручную сверены | Отдельные поправки raw, clips, личные счётчики |
| Все причины смертей | 5 ENEMY по kill feed, 7 UNKNOWN | 12 разборов по 18 вопросам; UNKNOWN не равен газовой смерти |
| Перед смертью | MJPG AVI + исходные JPEG и world telemetry | История с uncertain state дополнена непрерывной записью там, где ранний аудитор оставил короткий клип |
| Последняя игровая серия | Матчи 13–15: panel/auditor завершились 0; игра завершена, inputs stop | .audit/run_revision11.txt |
| Финальная сборка | PyInstaller 6.22.3 / Python 3.12; Windows x64 directory bundle | .audit/build_DELIVERY.txt |
| Версии | Python 0.8.15.post1, PE/Inno numeric 0.8.15.1 | version.py, build_assets |
| Installer isolation | Отдельный AppId, HKCU, current user, Administrator=No | Inno installer, install log |
| Installer / portable launch | Свежая папка, внешняя cwd с пробелами/Кириллицей, пустой профиль, Python/ADB отсутствуют в PATH | Smoke results и manifest поставки |
| ONNX | 4 модели реально загружены и исполнили inference | Smoke health; DML/CPU доступны, CUDA отсутствует |
| Read-only install tree | Запись запрещена адресным ACL; EXE/ресурсы остаются доступны для чтения | Read-only smoke и восстановление собственного ACL |
| Capture/input diagnostics | Capture реально проверен отдельно, input — unit + fault test; диагностический endpoint input не нажимает | .audit/live_faults.json; diagnostics сообщает NOT_EXECUTED для неисполняемой проверки |
| Uninstall data preservation | Ранняя сборка: exit 0, профильные файлы/хеши сохранены; финальная — отдельный результат | Uninstall result / packaging evidence |
| Offline replay | Сохранённые кадры повторно обработаны ONNX entity/gas/tile и arbiter; сравнен старый reactive escape component | replay_summary.json, per-death redetect_comparison.json |

Заключительные результаты установщика/распаковки, SHA256, CRC ZIP и исполняемого файла записаны в `deliverables/manifest.json` и `deliverables/packaging_validation.json`. Эти файлы формируются после последней сборки, поэтому не полагайтесь на устаревшие промежуточные installer logs.

## Исправления, введённые при продолжении

| FILE / FUNCTION | Problem / root cause | Fix | Test / observed result | Risk |
|---|---|---|---|---|
| state_finder / result caption | Legacy result шаблон зависел от анимированной модели бойца; экран Match 12 стал unknown | Постоянные RU подписи 2/3; выбирается лучший score, чтобы 2 не выигрывал у похожего 3 | 6 resolution/animation regression tests; actual 9/10/12 result frames | Новые языки/редизайн UI требуют новых положительных шаблонов |
| state_finder, StageManager / team_panel | Открытое командное меню не распознавалось | Положительный constant-heading template + close X, stop/pause guard | Match 12 запущен после автоматического закрытия drawer; callback test | Координаты зависят от UI layout |
| TrophyObserver, match_audit / result delta | Старый «Observed trophy delta» из предыдущего боя заменял OCR текущего результата | Только текущий snapshot consensus или обновлённый account total; prediction отдельно | Actual +5/+11 frames; fake panel regression | OCR может вернуть None; это предпочтительнее придуманного числа |
| trophy_reader / read_result_delta | Яркая skin animation мешала grayscale OCR | Две согласованные области; дополнительный white-glyph pass | Реальные результаты 3–15; bounded OCR timeout | Consensus не является независимой оценкой OCR accuracy |
| navigation_safety / escape scoring | Обрезанная верхняя ROI маски была нулевой и выглядела безопасным выходом | First-step body не может выйти из наблюдаемой области; unobserved endpoint получает риск | 2 focused ROI tests; guard активен в матчах 13–15 | Консервативная граница может уменьшить число выходов; требует camera/route calibration |
| StageManager / require_trio_lobby | Первый lobby frame после login ещё содержит loading animation | До 15s ожидания unknown без input; подтверждённый Solo/Duo запрещён сразу | Transient/timeout tests; серия 13–15 проходит guard | Unknown после deadline останавливает bot с явной ошибкой |
| life_tracking / evidence history | История пополнялась только в state=match, теряя кадры при unknown | Все свежие кадры в bounded ring; inference по unknown по-прежнему сбрасывается | 20s history regression; full clips восстановлены из continuous frames | Runtime ring хранит JPEG; размер ограничен временем, частота требует disk/memory контроля |
| life_tracking / respawn | Товарищ ложно распознан как player между кадрами одного countdown; Match 15 получил duplicate death | Conservative minimum 12s после first countdown прежде повторного life confirmation | Reproduction regression; final personal result 1 и countdown 15..0 сверены вручную | Heuristic lower bound, не OCR таймера; поздно увиденный countdown может задержать учёт respawn |
| ADB/runtime recovery | Явный adbutils shell timeout 600 не ограничивал handshake/чтение; runtime падал на offline transport | Bounded client, release + finite pinned reconnect; нет app restart через broken ADB | Silent TCP test; offline runtime test; реальные быстрые foreground queries | Исчезновение physical device и два физических устройства не проверены |

## Remaining known issues

Качество gas/player/water perception остаётся P1 риском. Исходные модели сохранены; новая модель не обучена, размеченный тренировочный dataset отсутствует. Прямоугольная маска и короткая temporal memory не гарантируют точное совпадение с poison cloud. Обнаружение игрока иногда принимает teammate за собственного персонажа. Во время Match 13 виден AFK warning; итоговая победа его не оправдывает. Нужны собственное имя/цвет health bar, более надёжный identity tracking и разметка camera displacement.

OCR имени бойца многократно расходился с фактическим изображением/скином. Это влияет на выбор специфической боевой логики и не считается решённым по одному успешному переключению. Ротация не доказывает верность имени. Личная атака/урон в некоторых матчах нулевые; бойцовое мастерство не улучшено и не измерено сравнительным экспериментом.

State matching во время loading/result animation может кратко выдавать unknown или connection_lost. Положительный Trio guard и input release защищают от запуска неподтверждённого режима, но полнота UI classifier не доказана. Matchmaking/lobby переходы не входят в gameplay FPS. Счётчик результата читается из middle card и требует переоценки при смене UI layout.

Windows 10, чистая VM/физическая машина без установленного developer runtime, CUDA, два физических Android устройства, запуск ярлыком через реальный Program Files и acknowledgement Android touch-UP — NOT EXECUTED. Чистый профиль и PATH без Python/ADB на этой Windows 11 машине не эквивалентны чистой VM.

Offline replay сравнивает новый arbiter с точными AST-extracted navigation methods тега на тех же boxes/captured candidate. Он не запускает полный старый playstyle, hysteresis, temporal/camera dynamics, abilities и device input. Нельзя выдавать это за replay всей старой версии либо доказательство физического предотвращения конкретной смерти.

Непрерывный recorder ограничен 15000 JPEG / 2GB на процесс. Per-death auditor evidence не имеет глобального disk rotation; длительная эксплуатация требует quota/retention. Веб-панель и некоторые старые зависимости сохранены; warning audioop документирован. Unit tests дают покрытие конкретных инвариантов, не доказывают отсутствие всех P0/P1.

## Следующая итерация

Сначала разметить сохранённые реальные Trio кадры около тела и газовой границы, water/wall и собственную identity; измерить false negatives/positives и калибровать motion. Затем заменить/переобучить perception при доказанной пользе, валидировать на отдельном наборе. После этого провести ещё 15 матчей одной неизменной сборки с проверенными physical gas entry/death cause и отдельным baseline gameplay экспериментом. Эти работы не отмечены выполненными.
