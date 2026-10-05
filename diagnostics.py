"""Read-only checks; never touch the device or restart its apps."""
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from utils import get_config_root, resolve_project_path, resolve_runtime_path


def health_check(serial=None, load_models=False):
    checks=[]
    def add(name, status, detail):
        checks.append({'name':name,'status':status,'detail':str(detail)})
    from setup_wizard import adb_path
    adb_binary=adb_path()
    add('ADB','OK' if adb_binary and Path(adb_binary).is_file() else 'ERROR', adb_binary)
    add('ONNX providers','OK',ort.get_available_providers())
    for name in ('mainInGameModel.onnx','gasDetector.onnx','tileDetector.onnx','closeTileDetector.onnx'):
        path=resolve_project_path('models',name)
        if not path.is_file():
            add(name,'ERROR','Missing file');continue
        if load_models:
            try:
                from detect import Detect
                model=Detect(str(path));inp=model.model.get_inputs()[0]
                shape=[int(x) if isinstance(x,int) else 1 for x in inp.shape]
                model.model.run(None,{inp.name:np.zeros(shape,np.float32)})
                add(name,'OK',model.device)
            except Exception as e:add(name,'ERROR',e)
        else:add(name,'OK',f'File present ({path.stat().st_size} bytes); inference not run')
    templates=list(resolve_project_path('images','states').glob('*'))
    bad=[p.name for p in templates if cv2.imdecode(np.fromfile(str(p),np.uint8),cv2.IMREAD_COLOR) is None]
    add('State templates','ERROR' if bad or not templates else 'OK',bad or f'{len(templates)} images decoded')
    add('Scrcpy server','OK' if resolve_project_path('scrcpy','scrcpy-server.jar').is_file() else 'ERROR','scrcpy-server.jar')
    try:
        import toml
        for name in ('bot_config.toml','general_config.toml','lobby_config.toml','buttons_config.toml'):
            if not (get_config_root()/name).is_file():
                raise FileNotFoundError(name)
        for p in get_config_root().glob('*.toml'):toml.load(p)
        from gas_config import validate_gas_config
        from utils import load_toml_as_dict
        validate_gas_config(load_toml_as_dict('cfg/bot_config.toml'))
        add('Configs','OK',get_config_root())
    except Exception as e:add('Configs','ERROR',e)
    add('UI','OK' if resolve_project_path('templates','panel.html').is_file() else 'ERROR','panel.html')
    try:
        from trophy_reader import OCR_AVAILABLE,TESSERACT_PATH
        add('OCR','OK' if OCR_AVAILABLE else 'ERROR',TESSERACT_PATH or 'Tesseract is unavailable')
    except Exception as e:add('OCR','ERROR',e)
    if serial:
        try:
            from window_controller import get_device_by_serial
            device=get_device_by_serial(serial)
            add('Device','OK',device.serial)
            add('Resolution','OK',device.shell(['wm','size'],timeout=5))
            from adb_connection import foreground_package
            add('Foreground','OK',foreground_package(device))
        except Exception as e:add('Device','ERROR',e)
    else:add('Device','NOT_EXECUTED','No serial requested')
    add('Frame','NOT_EXECUTED','Use the panel device selftest to verify capture')
    add('Input','NOT_EXECUTED','Read-only health check never sends touches')
    return {'ok':not any(c['status']=='ERROR' for c in checks),'checks':checks}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--serial');p.add_argument('--load-models',action='store_true')
    a=p.parse_args();result=health_check(a.serial,a.load_models)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['ok'] else 1)
