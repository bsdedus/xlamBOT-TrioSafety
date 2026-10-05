import argparse
import inspect
import os
import sys
import tomllib
from pathlib import Path

# Monkey-patch inspect.getfile to prevent Nuitka + PyTorch crash
_original_getfile = inspect.getfile
def _patched_getfile(obj):
    res = _original_getfile(obj)
    return res if res is not None else "<unknown_nuitka_file>"

inspect.getfile = _patched_getfile


if __name__ == "__main__" and len(sys.argv) >= 9 and sys.argv[1] == "--debug-viewer-worker":
    from debug_view import DEFAULT_DEBUG_VIEW_FPS, run_viewer_worker

    run_viewer_worker(
        shared_memory_name=sys.argv[2],
        debug_memory_name=sys.argv[3],
        height=int(sys.argv[4]),
        width=int(sys.argv[5]),
        channels=int(sys.argv[6]),
        dtype_text=sys.argv[7],
        title=sys.argv[8],
        clip_fps=float(sys.argv[9]) if len(sys.argv) >= 10 else DEFAULT_DEBUG_VIEW_FPS,
        record_clips=(len(sys.argv) >= 11 and sys.argv[10] == "1"),
    )
    sys.exit(0)

def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="xlamBOT",
        description="xlamBOT, the best free and open source brawl stars bot.",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Hide xlamBOT's own console and write output to a log file.",
    )
    interface_group = parser.add_mutually_exclusive_group()
    interface_group.add_argument(
        "--desktop",
        dest="interface_mode",
        action="store_const",
        const="desktop",
        help="Force the UI to open in the integrated pywebview window.",
    )
    interface_group.add_argument(
        "--web",
        "--browser",
        "--no-webapp",
        dest="interface_mode",
        action="store_const",
        const="browser",
        help="Force the UI to open in the system browser instead of pywebview.",
    )
    interface_group.add_argument(
        "--headless",
        dest="interface_mode",
        action="store_const",
        const="headless",
        help="Force headless mode: serve the local web UI without opening it.",
    )
    args, _unknown_args = parser.parse_known_args(argv)
    return args


INTERFACE_MODES = frozenset({"desktop", "browser", "headless"})


def _startup_project_root():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def load_saved_interface_mode(config_path=None):
    path = Path(config_path) if config_path is not None else _startup_project_root() / "cfg" / "general_config.toml"
    try:
        with path.open("rb") as config_file:
            configured_mode = str(tomllib.load(config_file).get("interface_mode", "desktop")).strip().lower()
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Could not read interface_mode from {path}: {error}. Using desktop mode.")
        return "desktop"

    if configured_mode not in INTERFACE_MODES:
        print(f"Unknown interface_mode {configured_mode!r} in {path}. Using desktop mode.")
        return "desktop"
    return configured_mode


def resolve_interface_mode(cli_args, config_path=None):
    return cli_args.interface_mode or load_saved_interface_mode(config_path)


# Parse these before the heavy application imports so console hiding happens as
# early as possible. Imported modules receive harmless default values.
CLI_ARGS = parse_cli_args(sys.argv[1:] if __name__ == "__main__" else [])
INTERFACE_MODE = resolve_interface_mode(CLI_ARGS)
CONSOLE_HIDDEN = False
CONSOLE_LOG_FILE = None

if CLI_ARGS.no_console:
    from desktop import console_log_path, hide_console

    CONSOLE_LOG_FILE = console_log_path()
    CONSOLE_HIDDEN = hide_console(CONSOLE_LOG_FILE)

if CLI_ARGS.no_console and not CONSOLE_HIDDEN:
    print(
        "--no-console ignored: this console belongs to the terminal xlamBOT was "
        "started from, so output stays here."
    )


from adbutils import AdbError
import socket
import threading
import time
from lobby_automation import LobbyAutomation
from play import Play
from stage_manager import StageManager
from state_finder import get_state
from time_management import TimeManagement
from utils import load_toml_as_dict, current_wall_model_is_latest, api_base_url, load_playstyle_script, save_brawler_data, \
    clean_queue, get_discord_link
from utils import get_brawler_list, update_missing_brawlers_info, check_version, notify_user, update_wall_model_classes, get_latest_wall_model_file, cprint
from window_controller import WindowController


from bot_instance import apply_play_order, run_bot_instance


def xlambot_main(discord_bot, queue_data, stop_event=None, runtime_control=None):
    return run_bot_instance(discord_bot, queue_data, stop_event, runtime_control)


all_brawlers = get_brawler_list()
if api_base_url != "localhost":
    update_missing_brawlers_info(all_brawlers)
    check_version()
    update_wall_model_classes()
    if not current_wall_model_is_latest():
        print("New Wall detection model found, downloading... (this might take a few minutes depending on your internet)")
        get_latest_wall_model_file()


def find_open_port(start_port=5185, host="127.0.0.1"):
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError("Could not find an open localhost port for the Flask UI.")


def open_browser_later(local_url):
    def _open():
        import webbrowser

        time.sleep(1.5)
        webbrowser.open(local_url)

    threading.Thread(target=_open, daemon=True, name="xlambot-browser-launcher").start()

def stop_on_window_close(app):
    """Ask a running bot instance to stop when its desktop window closes."""
    def _on_close():
        print("xlamBOT window closed, shutting down.")
        try:
            app.config["runtime_manager"].stop()
            app.config["device_manager"].stop_all()
        except Exception as error:
            print(f"Could not stop the bot cleanly: {error}")

    return _on_close


def run_interface(app, local_url, interface_mode):
    """Present the loopback web UI using the selected startup interface."""
    if interface_mode == "desktop":
        from desktop import import_webview, run_webview

        webview_module, webview_error = import_webview()
        if webview_module is not None:
            try:
                run_webview(app, local_url, webview_module, on_close=stop_on_window_close(app))
                return
            except Exception as error:
                print(f"Could not start pywebview ({error}); opening the system browser instead.")
        else:
            print(f"pywebview is unavailable ({webview_error}); opening the system browser instead.")
        interface_mode = "browser"

    if interface_mode == "browser":
        open_browser_later(local_url)
        print("xlamBOT is opening the local web UI in the system browser.")
    else:
        print(f"xlamBOT is running headless. Open {local_url} manually to use the local web UI.")

    app.run(host="127.0.0.1", port=int(local_url.rsplit(":", 1)[1]), debug=False, use_reloader=False)

if __name__ == "__main__":
    print("Starting xlamBOT, the best free and open source brawl stars bot")
    print("The only official discord is", get_discord_link())
    from webui import create_app

    port = find_open_port()
    app = create_app(xlambot_main, start_discord_bot=True)
    local_url = f"http://127.0.0.1:{port}"
    print(f"Starting xlamBOT web UI at {local_url}")
    if CONSOLE_HIDDEN:
        print(f"Console output is written to {CONSOLE_LOG_FILE}")
    if CLI_ARGS.interface_mode is not None:
        print(f"{INTERFACE_MODE.capitalize()} interface mode was forced by a command-line argument.")
    run_interface(app, local_url, INTERFACE_MODE)
