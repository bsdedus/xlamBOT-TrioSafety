# Запуск и воспроизводимость

Версия кандидата — 0.8.15.post1 (Windows PE / installer 0.8.15.1), база v0.8.15 commit 9540ce82cef79c83a7711e6445e440df31542fa3. Пакет предназначен для Trio Showdown. Файлы поставки находятся в `deliverables/`; SHA256 и проверки — в manifest.json и packaging_validation.json.

Installer устанавливает отдельное приложение «xlamBOT Trio Safety» без замены исходной установки. Для portable распакуйте весь архив: `xlamBOT.exe` должен оставаться рядом с `_internal`. Не переносите только EXE. Устройство Android/LDPlayer должно работать, быть доступно ADB и иметь открытый Brawl Stars с выбранным Trio. Исходные ONNX модели не переобучены; ограничения приведены в FINAL_VALIDATION.md.

Данные пользователя записываются в `%LOCALAPPDATA%/xlamBOT`, отдельно от ресурсов программы. Для изолированной проверки задайте `XLAMBOT_DATA_DIR` с абсолютным путём и `XLAMBOT_PORT` со свободным портом. Одно устройство должно иметь одного владельца capture/input. Старые версии, не использующие OS lease, не участвуют в этой блокировке: не запускайте их одновременно на том же устройстве.

Команды готового EXE: `xlamBOT.exe --headless` открывает HTTP panel без pywebview; `xlamBOT.exe --diagnostics --serial emulator-5554 --load-models` проверяет ресурсы и реальные ONNX inference без gameplay input. Health check сообщает NOT_EXECUTED там, где capture/input фактически не исполнялись. Тест захвата в панели выполняется отдельно. Start требует положительного подтверждения Trio.

Для сборки используйте Python 3.12 x64, зависимости `requirements-build-lock.txt`, команду `python -m PyInstaller xlambot.spec --noconfirm`, затем Inno Setup compiler `ISCC.exe installer.iss`. Исходный архив содержит четыре модели, JAR, UI/templates/config/playstyles и portable `vendor/tesseract`; точный состав перечислен в source_manifest.json. Inno compiler и установленный Python не включены в исходный архив. Lock фиксирует реально использованный набор Python packages; сведения о версии компилятора и ОС есть в отчёте и manifest.

Проверки исходников: `python -m pytest tests -q --basetemp <отдельный writable каталог>`. 90 tests PASS на использованной Windows 11. Не запускайте повторно live validation harness поверх работающего бота. Скрипты проверки и первичные результаты приложены к evidence archive; они предназначены для воспроизведения теста, а не для обычного запуска продукта.

Evidence archive содержит реальные кадры игры и никнеймы. Он сохранён локально; upload, GitHub Release/PR, push и отправка другим людям не выполнялись. Raw audit и rejected false events оставлены рядом с ручными поправками. Source snapshot не содержит `.audit`, `.venv`, runtime profiles или собранные пользовательские архивы.

Исходный архив — snapshot текущих файлов, без `.git`. `SOURCE_CHANGES.patch` показывает tracked diff от тега; новые модули находятся в source ZIP и перечислены в source manifest. Для применения всех исправлений используйте полный source snapshot: один patch не содержит всех untracked новых модулей.
