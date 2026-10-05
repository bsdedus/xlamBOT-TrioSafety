"""The same gas thresholds and validation for live control, audit and replay."""
import math

DEFAULTS={
    'gas_confidence':.25, 'gas_sensitivity':.05, 'gas_reach':3.,
    'gas_lookahead':4., 'gas_detect_interval':.2, 'gas_memory_ttl':.6,
    'gas_area_top':.21, 'gas_area_bottom':1., 'gas_danger_enter':.14,
    'gas_danger_exit':.05, 'gas_centre_bias':.06,
}


def validate_gas_config(config):
    values={key:float(config.get(key,default)) for key,default in DEFAULTS.items()}
    for key,value in values.items():
        if not math.isfinite(value):raise ValueError(f'{key} must be finite')
    for key in ('gas_confidence','gas_sensitivity','gas_danger_enter','gas_danger_exit','gas_centre_bias'):
        if not 0<=values[key]<=1:raise ValueError(f'{key} must be between 0 and 1')
    if not 0<=values['gas_area_top']<values['gas_area_bottom']<=1:
        raise ValueError('gas_area_top < gas_area_bottom must define a region within 0..1')
    if not 0<values['gas_reach']<=values['gas_lookahead']<=12:
        raise ValueError('0 < gas_reach <= gas_lookahead <= 12 is required')
    if not .01<=values['gas_detect_interval']<=values['gas_memory_ttl']<=.75:
        raise ValueError('0.01 <= gas_detect_interval <= gas_memory_ttl <= 0.75 seconds is required')
    if not values['gas_danger_exit']<=values['gas_danger_enter']:
        raise ValueError('gas_danger_exit must not exceed gas_danger_enter')
    return {**config, **values}
