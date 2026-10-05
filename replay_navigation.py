"""Replay captured world observations through the movement policy without input."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import cv2
import time

from navigation_safety import MovementArbiter, gas_boxes_mask, patch_share
from play import Play
from utils import load_toml_as_dict
from gas_config import validate_gas_config
from utils import resolve_project_path


def redetect_observations(observations, folder, config):
    """Re-run actual ONNX inference on saved JPEGs without opening a device."""
    from detect import Detect
    entities=Detect(str(resolve_project_path('models','mainInGameModel.onnx')),classes=['enemy','teammate','player'])
    gas=Detect(str(resolve_project_path('models','gasDetector.onnx')),classes=['gas','bush'])
    tiles=Detect(str(resolve_project_path('models','tileDetector.onnx')))
    for index,row in enumerate(observations):
        path=folder/f'frame_{index:03}.jpg'
        if not path.is_file():
            yield {'status':'NOT_EXECUTED','reason':f'Missing saved frame {path.name}'}
            continue
        image=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
        if image is None:
            yield {'status':'NOT_EXECUTED','reason':'Frame decode failed'}
            continue
        image=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        started=time.perf_counter()
        found=entities.detect_objects(image,conf_tresh=config.get('entity_detection_confidence',.55))
        clouds=gas.detect_objects(image,conf_tresh=config.get('gas_confidence',.25)).get('gas',[])
        terrain=tiles.detect_objects(image,conf_tresh=config.get('wall_detection_confidence',.5))
        walls=[box for name,boxes in terrain.items() if 'bush' not in name for box in boxes]
        captured=row.get('world') or {}
        world={**captured,**found,'player_present':bool(found.get('player')),
               'gas_boxes':clouds,'frame_size':list(image.shape[:2]),'wall':walls}
        if found.get('player'):
            geometry=Play.__new__(Play)
            geometry.window_controller=type('Controller',(),{'scale_factor':image.shape[1]/1920})()
            center,radius=geometry.get_player_hit_circle(found['player'][0])
            _,mask=gas_boxes_mask(image,clouds,config.get('gas_area_top',.21),config.get('gas_area_bottom',1.))
            world['gas_coverage']=patch_share(mask,*center,radius)
        result=replay(world,config)
        result.update(frame=path.name,actual_onnx_inference=True,model_ms=(time.perf_counter()-started)*1000,
                      observed_gas_boxes=clouds)
        result['limitations']='Fresh full-frame entity/gas/tile inference and new policy. Captured candidate retained; old playstyle, camera motion, temporal masks, abilities and device input are not simulated.'
        yield result


def replay(world, config=None):
    config = validate_gas_config(config or load_toml_as_dict('cfg/bot_config.toml'))
    if not world.get('player_present'):
        return {'status':'NOT_EXECUTED', 'reason':'No observed player'}
    height, width = world['frame_size']
    player = world['player'][0]
    # Runtime and replay use the same hit-circle geometry.
    controller = type('Controller', (), {'scale_factor':width/1920})()
    geometry = Play.__new__(Play); geometry.window_controller = controller
    center, radius = geometry.get_player_hit_circle(player)
    _, mask = gas_boxes_mask(np.zeros((height,width,3),np.uint8), world.get('gas_boxes',[]),
                            config.get('gas_area_top',.21), config.get('gas_area_bottom',1.))
    old = world.get('movement') or {}
    desired = old.get('candidate_before_safety', old.get('desired', (0,0)))
    final, reason, options, requested = MovementArbiter().choose(
        desired, mask=mask, center=center, radius=radius,
        walls_block=lambda m,d: geometry.is_path_blocked(player,m,world.get('wall',[]),d),
        frame_size=(width,height), reach=config.get('gas_reach',3.),
        lookahead=config.get('gas_lookahead',4.), sensitivity=config.get('gas_sensitivity',.05),
        escape=world.get('gas_coverage',0)>config.get('gas_danger_enter',.14),
        enemies=[Play.get_entity_pos(b) for b in world.get('enemy',[])],
        teammates=[Play.get_entity_pos(b) for b in world.get('teammate',[])],
        centre_bias=config.get('gas_centre_bias',.06), tile=config.get('perceived_tile_size',54)*width/1920,
        observed_y=(config.get('gas_area_top',.21)*height,config.get('gas_area_bottom',1.)*height),
        fallback_magnitude=100*width/1920)
    from baseline_navigation import BaselineNavigation
    baseline=BaselineNavigation.__new__(BaselineNavigation)
    baseline.window_controller=type('Controller',(),{'scale_factor':width/1920,'width_ratio':width/1920,
                                                    'height_ratio':height/1080,'width':width,'height':height})()
    baseline.TILE_SIZE=config.get('perceived_tile_size',54)
    baseline.gas_reach=config.get('gas_reach',3.);baseline.gas_lookahead=config.get('gas_lookahead',4.)
    baseline.gas_sensitivity=config.get('gas_sensitivity',.05);baseline.gas_centre_bias=config.get('gas_centre_bias',.06)
    baseline_mask=baseline.build_gas_mask(np.zeros((height,width,3),np.uint8),world.get('gas_boxes',[]))
    legacy_direction=baseline._clearest_escape(baseline_mask,player,world.get('wall',[])) if world.get('gas_coverage',0)>config.get('gas_danger_enter',.14) else desired
    legacy_final=baseline.clamp_movement(legacy_direction) if legacy_direction else (0,0)
    return {'status':'REPLAYED', 'timestamp':world.get('timestamp'), 'desired':desired,
            'captured_final':old.get('final'), 'replayed_final':final, 'override_reason':reason,
            'baseline_component_final':legacy_final,
            'baseline_comparison_scope':'Exact v0.8.15 reactive escape methods, using captured candidate/coverage and same boxes; old playstyle/hysteresis/input are not simulated.',
            'directions':options, 'requested':requested,
            'limitations':'Policy replay uses captured boxes; model accuracy, temporal masks and input timing are not reproduced.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--redetect',action='store_true',help='Re-run ONNX on frame_NNN.jpg beside the evidence JSON')
    args=parser.parse_args()
    document=json.loads(args.evidence.read_text(encoding='utf-8'))
    observations=document.get('observations',[{'world':document}])
    config=validate_gas_config(load_toml_as_dict('cfg/bot_config.toml'))
    rows=list(redetect_observations(observations,args.evidence.parent,config)) if args.redetect else [replay(row['world'],config) for row in observations]
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Replayed {len(rows)} observations; no device inputs sent.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
