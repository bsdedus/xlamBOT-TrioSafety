"""Validated brawler-menu coordinates stored in the device's button profile.

Behavior recovered from the published 0.8.18 module; original comments are unavailable.
"""
import math
import time
import device_profiles
import utils

WIDTH, HEIGHT = 1920, 1080
POINTS = [
    ('brawlers_menu', 'Открыть список бойцов', 'Лобби'),
    ('brawlers_sort_button', 'Открыть сортировку', 'Список бойцов'),
    ('brawlers_sort_least_trophies', 'Минимальные трофеи', 'Меню сортировки'),
    ('brawlers_sort_most_trophies', 'Максимальные трофеи', 'Меню сортировки'),
    ('brawlers_sort_closest_to_next_tier', 'Ближе к новому рангу', 'Меню сортировки'),
    ('brawlers_sort_power_level', 'Уровень силы', 'Меню сортировки'),
    ('brawlers_sort_power_level_low_to_high', 'Уровень силы по возрастанию', 'Меню сортировки'),
    ('brawlers_sort_name', 'По имени', 'Меню сортировки'),
    ('brawler_search', 'Поле поиска', 'Список бойцов'),
    ('first_brawler_icon', 'Первый результат поиска', 'Результаты поиска'),
    ('select_brawler', 'Кнопка «Выбрать»', 'Карточка бойца'),
    ('brawlers_card_00', 'Первая карточка после сортировки', 'Список бойцов'),
]
REGIONS = [
    ('card_name', 'Имя на первой карточке', [540, 350, 170, 38]),
    ('card_trophies', 'Трофеи на первой карточке', [380, 388, 85, 34]),
    ('account_total', 'Общие кубки аккаунта', [436, 38, 96, 34]),
]

def key_for(key):
    if not isinstance(key, str) or key in {'', '.', '..'} or key != device_profiles.sanitize_key(key):
        raise ValueError('Недопустимый профиль устройства.')
    return key

def validate(values, allowed, rectangle=False):
    if not isinstance(values, dict) or set(values) - set(allowed):
        raise ValueError('Неизвестная точка или область калибровки.')
    clean = {}
    for name, coords in values.items():
        size = 4 if rectangle else 2
        if not isinstance(coords, (list, tuple)) or len(coords) != size:
            raise ValueError('Неверный формат координат: ' + name)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in coords):
            raise ValueError('Координаты должны быть конечными числами.')
        x, y = coords[:2]
        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            raise ValueError('Точка находится вне экрана.')
        if rectangle and not (coords[2] >= 1 and coords[3] >= 1 and x + coords[2] <= WIDTH and y + coords[3] <= HEIGHT):
            raise ValueError('Область должна помещаться на экране.')
        clean[name] = [float(v) for v in coords]
    return clean

def read(key):
    key_for(key)
    default = utils.load_toml_as_dict(utils.resolve_project_path('cfg', 'buttons_config.toml'))
    with device_profiles.use_profile(key):
        buttons = {**default, **utils.load_toml_as_dict('cfg/buttons_config.toml')}
    meta = buttons.get('brawler_calibration') or {}
    return {'ok': True, 'key': key, 'width': WIDTH, 'height': HEIGHT,
            'points': [{'id': name, 'label': label, 'screen': screen,
                        'value': buttons.get(name) or buttons.get('brawlers_first_card'),
                        'calibrated': name in meta.get('point_keys', [])} for name, label, screen in POINTS],
            'regions': [{'id': name, 'label': label, 'value': meta.get('regions', {}).get(name, default_value),
                         'screen': 'Лобби' if name == 'account_total' else 'Список бойцов',
                         'calibrated': name in meta.get('regions', {})} for name, label, default_value in REGIONS],
            'saved_at': meta.get('saved_at'), 'capture_size': meta.get('capture_size')}

def save(key, points, regions, capture_size):
    key_for(key)
    points = validate(points, [p[0] for p in POINTS])
    regions = validate(regions, [r[0] for r in REGIONS], rectangle=True)
    if not points and not regions:
        raise ValueError('Сначала отметьте хотя бы одну точку или область.')
    with device_profiles.use_profile(key):
        buttons = utils.load_toml_as_dict('cfg/buttons_config.toml')
        meta = dict(buttons.get('brawler_calibration') or {})
        meta.update(saved_at=time.time(), capture_size=list(capture_size),
                    point_keys=sorted((set(meta.get('point_keys', [])) | set(points)) & {p[0] for p in POINTS}),
                    regions={**meta.get('regions', {}), **regions})
        updates = {**points, 'brawler_calibration': meta}
        if 'brawlers_card_00' in points:
            updates['brawlers_first_card'] = points['brawlers_card_00']
        device_profiles.update_settings(key, 'buttons_config', updates)
    return read(key)

def reset(key):
    key_for(key)
    default = utils.load_toml_as_dict(utils.resolve_project_path('cfg', 'buttons_config.toml'))
    with device_profiles.use_profile(key):
        buttons = dict(utils.load_toml_as_dict('cfg/buttons_config.toml'))
        for name, _, _ in POINTS:
            if name in default:
                buttons[name] = default[name]
            else:
                buttons.pop(name, None)
        if 'brawlers_first_card' in default:
            buttons['brawlers_first_card'] = default['brawlers_first_card']
        buttons.pop('brawler_calibration', None)
        utils.save_dict_as_toml(buttons, 'cfg/buttons_config.toml')
        utils.invalidate_toml_cache('cfg/buttons_config.toml')
    return read(key)

def region_for(kind, default=None):
    cfg = utils.load_toml_as_dict('cfg/buttons_config.toml')
    custom = (cfg.get('brawler_calibration') or {}).get('regions', {}).get(kind)
    if custom is not None:
        try:
            return tuple(validate({kind: custom}, [r[0] for r in REGIONS], rectangle=True)[kind])
        except (ValueError, TypeError):
            pass
    return default

def card_region(region, index, kind):
    """Only the first card is used; old callers may still pass a grid index."""
    return tuple(region_for(kind, region))
