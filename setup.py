import sys
import platform
import subprocess
import os
import shutil
import glob
import importlib

# --- LOOP-PROOF BOOTSTRAP ---

def bootstrap():
    if os.environ.get("XLAMBOT_BOOTSTRAP") == "1":
        return
    try:
        import jaraco.functools
        import wheel
    except ImportError:
        print("\nDetected missing core tools. Stabilizing environment...")
        os.environ["XLAMBOT_BOOTSTRAP"] = "1"
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        print("Environment stabilized. Restarting setup...\n")
        subprocess.run([sys.executable] + sys.argv)
        sys.exit(0)

if any(cmd in sys.argv for cmd in ["install", "develop"]):
    bootstrap()

from setuptools import setup, find_packages

def get_requirement_name(req):
    req = req.strip()

    if " @ " in req:
        name = req.split(" @ ", 1)[0].strip()
    else:
        name = req
        for sep in ["~=", ">=", "<=", "==", "!=", ">", "<"]:
            name = name.split(sep, 1)[0]

    name = name.split("[", 1)[0].strip()
    return name.replace("-", "_").lower()

def check_base_requirements(req_list):
    print("\nVerifying base requirements...")
    for req in req_list:
        pkg_name = get_requirement_name(req)
        mapping = {
            "opencv_python": "cv2",
            "discord.py": "discord",
            "pillow": "PIL",
            "pywin32": "win32api",
            "onnxruntime_directml": "onnxruntime",
            "pycryptodome": "Crypto",
            "pywebview": "webview",
            "flask": "flask",
        }
        import_name = mapping.get(pkg_name, pkg_name)

        try:
            importlib.import_module(import_name)
            print(f"  [OK] {req}")
        except ImportError:
            print(f"  [INSTALLING] {req}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", req])

def get_gpu_info():
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader,nounits"],
            encoding='utf-8'
        )
        cc = float(output.strip().split('\n')[0])
        return "nvidia", cc
    except:
        return "other", 0.0

# --- MAIN SETUP ---
from pathlib import Path
from version import __version__
install_requires = [line.strip() for line in Path(__file__).with_name('requirements.txt').read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]

setup(
    name="xlamBOT",
    version=__version__,
    packages=find_packages(exclude=["api", "cfg", "images", "models"]),
    install_requires=install_requires,
)

if any(cmd in sys.argv for cmd in ["install", "develop"]):
    try:
        check_base_requirements(install_requires)

        installed_onnx = 'ONNX Runtime (DirectML with CPU fallback)'

        os.system('cls' if os.name == 'nt' else 'clear')
        print("\n" + "="*50 + "\n              SETUP COMPLETED!                \n" + "="*50)
        print(
            f"  - ONNX Engine:      {installed_onnx}\n"
            + "="*50 + "\n"
        )

    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
