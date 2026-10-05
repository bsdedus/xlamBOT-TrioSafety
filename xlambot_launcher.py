"""Точка входа собранной программы xlamBOT.

Задача этого файла - сделать так, чтобы в готовом .exe всё работало без
Python, терминала и настроек. Отсюда три вещи:

1. Каталог ресурсов. В собранной программе файлы лежат во временной папке
   PyInstaller, поэтому пути к моделям, картинкам и настройкам надо уметь
   находить и там. utils.PROJECT_ROOT настроен на это же, но проверяем ещё раз
   и предупреждаем, если что-то не так.

2. Мастер настройки при первом запуске. Если ADB не найден или устройство не
   видно, пользователь должен получить понятный список шагов, а не
   traceback. Запускаем мастер и ждём его завершения.

3. Панель. Поднимаем её на свободном порту, печатаем адрес и открываем браузер.
   На macOS и Linux окно открывает pywebview, на Windows - системный браузер.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

APP_NAME = "xlamBOT"
from version import __version__ as VERSION
DEFAULT_PORT = 5195
WIZARD_MARKER = "setup_done.json"


def bundled_root() -> str:
    """Каталог ресурсов: временная папка PyInstaller или папка рядом."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.abspath(__file__))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def free_port(preferred: int) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 25)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def data_dir() -> str:
    """Куда складывать настройки пользователя, логи и состояние."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get('XLAMBOT_DATA_DIR') or os.path.join(base, APP_NAME)


def check_assets(root: str) -> list[str]:
    """Что из необходимого лежит рядом с программой."""
    problems = []
    required = [
        ("cfg", "папка настроек"),
        ("images", "шаблоны экрана"),
        ("models", "файлы моделей"),
        ("playstyles", "плейстайлы"),
        ("scrcpy", "связь с устройством"),
        ("static", "панель"),
        ("templates", "панель"),
    ]
    for name, what in required:
        if not os.path.isdir(os.path.join(root, name)):
            problems.append(f"нет папки {name} - {what}")
    for model in ("mainInGameModel.onnx", "tileDetector.onnx", "closeTileDetector.onnx", "gasDetector.onnx"):
        if not os.path.isfile(os.path.join(root, "models", model)):
            problems.append(f"нет модели {model}")
    return problems


def run_wizard() -> int:
    print("\nЗапускаю мастер настройки.\n")
    try:
        import setup_wizard

        return setup_wizard.main()
    except Exception as error:  # noqa: BLE001
        print(f"Мастер настройки не отработал: {error}")
        print("Разбираться можно в панели: статус устройства и логи.")
        return 1


def open_ui(url: str) -> None:
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    root = bundled_root()
    from utils import DATA_ROOT, initialize_user_data
    initialize_user_data()
    os.chdir(DATA_ROOT)
    if '--debug-viewer-worker' in sys.argv:
        from debug_view import run_viewer_worker
        args = sys.argv[2:]
        run_viewer_worker(args[0], args[1], int(args[2]), int(args[3]), int(args[4]),
                          args[5], args[6], float(args[7]), args[8]=='1')
        return 0
    if '--diagnostics' in sys.argv:
        from diagnostics import health_check
        import json
        serial = sys.argv[sys.argv.index('--serial')+1] if '--serial' in sys.argv else None
        result = health_check(serial, '--load-models' in sys.argv)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    if '--audit' in sys.argv:
        from match_audit import main as audit_main
        sys.argv.remove('--audit')
        return audit_main()
    if '--replay' in sys.argv:
        from replay_navigation import main as replay_main
        sys.argv.remove('--replay')
        return replay_main()

    print("=" * 64)
    print(f"  {APP_NAME} {VERSION}")
    print("=" * 64)

    problems = check_assets(root)
    if problems:
        print("\nПрограмма собрана неполно, не хватает файлов:")
        for line in problems:
            print(f"  - {line}")
        print("\nЗапустите setup.py из исходников либо обратитесь в канал.")
        return 2

    # Мастер настройки: один раз, и только если есть повод
    marker = os.path.join(data_dir(), WIZARD_MARKER)
    needs_wizard = True
    if os.path.isfile(marker):
        try:
            import json

            with open(marker, encoding="utf-8") as handle:
                state = json.load(handle)
            needs_wizard = state.get("wizard_exit_code") != 0
        except Exception:
            needs_wizard = True

    if needs_wizard and os.environ.get("XLAMBOT_SKIP_WIZARD") != "1":
        code = run_wizard()
        try:
            import json

            os.makedirs(data_dir(), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"wizard_exit_code": code}, handle)
        except Exception:
            pass
        # Мастер не обязан пройти полностью: дальше панель покажет, что не так
        if code not in (0, 1):
            return code

    port = free_port(int(os.environ.get("XLAMBOT_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}"

    print(f"\nПанель управления: {url}")
    print("Закрыть программу - нажмите Стоп или закройте это окно.\n")

    if '--headless' not in sys.argv:
        threading.Thread(target=open_ui, args=(url,), daemon=True).start()

    try:
        from webui.app import create_app

        # None вместо main: панель сама поднимает ботов на найденных
        # устройствах и следит за их состоянием.
        app = create_app(None, start_discord_bot=False)
        app.extensions['resource_updater'].start()
        # threaded=True иначе панель подвисает на долгих опросах устройства
        from werkzeug.serving import make_server
        server = make_server('127.0.0.1', port, app, threaded=True)
        app.extensions['shutdown_server'] = server.shutdown
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    except Exception as error:  # noqa: BLE001
        print(f"Панель не запустилась: {error}")
        return 3
    finally:
        if 'app' in locals():
            app.extensions['resource_updater'].close()
            app.config['device_manager'].stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
