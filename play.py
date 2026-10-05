import math
import random
import time
import cv2
import numpy as np
import os
from collections import deque
from navigation_safety import GasMemory, MovementArbiter, corridor_share, evaluate_gas_risk, gas_boxes_mask

from detect import Detect
from gas_config import validate_gas_config
from state_finder import get_state, is_respawning
from utils import load_toml_as_dict, count_hsv_pixels, load_brawlers_info, interpret_playstyle_code, \
    count_mask_pixels, JOYSTICK_RADIUS, clamp, config_bool, is_safe_ast, resolve_project_path


brawl_stars_width, brawl_stars_height = 1920, 1080
super_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['super']
gadget_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['gadget']
hypercharge_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['hypercharge']
POISON_LOW_HSV = np.array((30, 90, 221), dtype=np.uint8)
POISON_HIGH_HSV = np.array((57, 114, 235), dtype=np.uint8)
PLAYER_HIT_CIRCLE_RADIUS = 53
# Escape directions are screen coordinates, so y grows downwards and 90 degrees
# is "down the screen". Eight directions is what the joystick can be steered
# into without a diagonal that slides.
GAS_ESCAPE_ANGLES = tuple(math.radians(angle) for angle in (0, 45, 90, 135, 180, 225, 270, 315))
# Patches sampled along one escape corridor. More samples see a narrow gap
# between two clouds, fewer of them are cheaper but step over it.
GAS_CORRIDOR_SAMPLES = 5
# What counts towards the score from the corridor past gas_reach, up to
# gas_lookahead: a cloud further along the way is worth noticing, but not worth
# as much as one the player is about to walk into.
GAS_FAR_FIELD_WEIGHT = 0.5

class Play:

    def __init__(self, main_info_model, tile_detector_model, close_tile_detector_model, window_controller, playstyle_code):
        bot_config = validate_gas_config(load_toml_as_dict("cfg/bot_config.toml"))
        time_config = load_toml_as_dict("cfg/time_tresholds.toml")
        self.fix_movement_keys = {
            "delay_to_trigger": bot_config["unstuck_movement_delay"],
            "duration": bot_config["unstuck_movement_hold_time"],
            "toggled": False,
            "started_at": time.time(),
            "fixed": (0, 0),
            "last_direction_key": None,
            "rotation_sign": 1,
            "rotation_angle_step": 1,
            "max_rotation_angle_step": 4,
        }
        self.super_treshold = time_config["super"]
        self.gadget_treshold = time_config["gadget"]
        self.hypercharge_treshold = time_config["hypercharge"]
        self.walls_treshold = time_config["wall_detection"]
        self.last_walls_data = []
        self.last_bushes_data = []
        self.keys_hold = []
        self.time_since_different_movement = time.time()
        self.time_since_gadget_checked = time.time()
        self.is_gadget_ready = False
        self.time_since_hypercharge_checked = time.time()
        self.is_hypercharge_ready = False
        self.time_since_super_checked = time.time()
        self.is_super_ready = False
        self.window_controller = window_controller
        self.TILE_SIZE = bot_config.get("perceived_tile_size", 54)
        self.centered_wall_detection = config_bool(bot_config.get("centered_wall_detection"), False)
        self.centered_wall_crop_size = 640

        bot_config = validate_gas_config(load_toml_as_dict("cfg/bot_config.toml"))
        time_config = load_toml_as_dict("cfg/time_tresholds.toml")
        self.verbose_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('verbose_debug'), False)
        if self.verbose_debug:
            if not os.path.exists("debug_frames"):
                os.makedirs("debug_frames")
        self.Detect_main_info = Detect(main_info_model, classes=['enemy', 'teammate', 'player'])
        self.tile_detector_model_classes = bot_config["wall_model_classes"]
        self.Detect_tile_detector = None if self.centered_wall_detection else Detect(
            tile_detector_model,
            classes=self.tile_detector_model_classes
        )
        self.Detect_centered_tile_detector = Detect(
            close_tile_detector_model,
            classes=self.tile_detector_model_classes
        ) if self.centered_wall_detection else None

        # Showdown gas is found with a model of its own instead of a colour
        # window: it is a translucent green cloud lying over grass, dirt and
        # water, so no hue range separates it from the map. The model also
        # knows the bushes, so a bush never gets read as a cloud. All of this
        # is optional - with no model file the bot simply plays as if the
        # arena had no gas, and nothing else has to know about it.
        self.gas_model_path = bot_config.get("gas_model", "models/gasDetector.onnx")
        self.gas_classes = bot_config.get("gas_classes", ["gas", "bush"])
        self.gas_confidence = float(bot_config.get("gas_confidence", 0.25))
        self.gas_sensitivity = float(bot_config.get("gas_sensitivity", 0.05))
        self.gas_reach = float(bot_config.get("gas_reach", 3.0))
        self.gas_lookahead = float(bot_config.get("gas_lookahead", 4.0))
        self.gas_detect_interval = float(bot_config.get("gas_detect_interval", 0.2))
        self.gas_area_top = float(bot_config.get("gas_area_top", 0.21))
        self.gas_area_bottom = float(bot_config.get("gas_area_bottom", 1.0))
        self.gas_danger_enter = float(bot_config.get("gas_danger_enter", 0.14))
        self.gas_danger_exit = float(bot_config.get("gas_danger_exit", 0.05))
        self.gas_centre_bias = float(bot_config.get("gas_centre_bias", 0.06))
        self.gas_avoidance = config_bool(bot_config.get("gas_avoidance"), True)
        self.gas_memory_ttl = float(bot_config.get('gas_memory_ttl', 0.6))
        self.gas_memory = GasMemory(self.gas_memory_ttl)
        self.movement_arbiter = MovementArbiter()
        self.prevented_gas_entries = 0
        self.safety_telemetry = {}
        self.world_state = {}
        self.gas_detection_ok = False
        self.gas_observed_at = 0.0
        self.gas_state = 'SAFE'
        self.position_history = deque(maxlen=120)
        self.motion_state = 'DETECTION_UNCERTAIN'
        self._motion_frame = None
        self.latency = {}
        self._last_safety_reason = None
        self._gas_was_in_danger = False
        self.gas_events = deque(maxlen=100)

        self.Detect_gas = None
        gas_model_path = resolve_project_path(self.gas_model_path or "")
        if self.gas_avoidance and self.gas_model_path and os.path.exists(gas_model_path):
            try:
                self.Detect_gas = Detect(str(gas_model_path), classes=self.gas_classes)
            except Exception as error:
                raise RuntimeError(f"Required Trio gas model could not be loaded: {error}") from error
        if self.Detect_gas is None:
            raise RuntimeError('Trio safety requires gas_avoidance and models/gasDetector.onnx')

        self.gas_boxes = []
        self.gas_mask = None
        self.gas_mask_time = 0.0
        self.gas_coverage = 0.0
        self.gas_danger = 0.0
        self.gas_in_danger = False
        self.gas_escape_direction = None
        self.gas_escape_index = None
        self.gas_escape_time = 0.0
        self.gas_escapes = 0
        self.gas_danger_escapes = 0
        self.gas_player_box = None

        self.time_since_walls_checked = 0
        self.time_since_player_last_found = time.time()
        self.current_brawler = None
        self.brawlers_info = load_brawlers_info()
        self.brawler_ranges = None
        self.time_since_detections = {
            "player": time.time(),
            "enemy": time.time(),
        }
        self.time_since_last_proceeding = time.time()

        self.last_movement = ''
        self.last_movement_change_time = time.time()
        self.minimum_movement_delay = bot_config["minimum_movement_delay"]
        self.no_detection_proceed_delay = time_config["no_detection_proceed"]
        self.gadget_pixels_minimum = bot_config["gadget_pixels_minimum"]
        self.hypercharge_pixels_minimum = bot_config["hypercharge_pixels_minimum"]
        self.ability_crop_areas = load_toml_as_dict('cfg/lobby_config.toml')['pixel_counter_crop_area']
        self.super_pixels_minimum = bot_config["super_pixels_minimum"]
        self.wall_detection_confidence = bot_config["wall_detection_confidence"]
        self.entity_detection_confidence = bot_config["entity_detection_confidence"]
        self.seconds_to_hold_attack_after_reaching_max = load_toml_as_dict("cfg/bot_config.toml")["seconds_to_hold_attack_after_reaching_max"]
        self.persistent_data = {"time_since_holding_attack": None}
        if isinstance(playstyle_code, str):
            is_safe, error_msg = is_safe_ast(playstyle_code)
            if not is_safe:
                print(f"Security/Syntax Validation Failed for playstyle: {error_msg}")
                self.playstyle_code = compile("", "<string>", "exec")
            else:
                self.playstyle_code = compile(playstyle_code, "<playstyle>", "exec")
        else:
            self.playstyle_code = playstyle_code
        self.context = None
        self.frame = None
        self._match_confirmations = 0
        self._confirmation_stamp = None
        self.ability_vetoes = []

    def observed_safe_target(self, player_box, walls):
        """A local target from observed corridors; never an inferred map centre."""
        position = self.get_entity_pos(player_box)
        if self.gas_mask is None or not self.gas_mask.any():
            return position
        direction = self._clearest_escape(self.gas_mask, player_box, walls)
        if not direction or not math.hypot(*direction):
            return position
        center, radius = self.get_player_hit_circle(player_box)
        return center[0]+direction[0]*radius*2*self.gas_reach, center[1]+direction[1]*radius*2*self.gas_reach

    def battle_hud_visible(self, frame):
        """Additional match evidence: saturated red attack control at its calibrated position."""
        x,y=self.window_controller.press_coords['attack']
        h,w=frame.shape[:2];x=int(x*w/1920);y=int(y*h/1080);r=max(1,int(65*w/1920))
        crop=frame[max(0,y-r):min(h,y+r),max(0,x-r):min(w,x+r)]
        if not crop.size:return False
        hsv=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)
        red=((hsv[:,:,0]<14)|(hsv[:,:,0]>170))&(hsv[:,:,1]>120)&(hsv[:,:,2]>100)
        return float(red.mean())>.12

    @staticmethod
    def get_entity_pos(entity):
        return (entity[0] + entity[2]) / 2, (entity[1] + entity[3]) / 2

    @staticmethod
    def get_distance(enemy_coords, player_coords):
        return math.hypot(enemy_coords[0] - player_coords[0], enemy_coords[1] - player_coords[1])

    @staticmethod
    def is_there_enemy(enemy_data):
        if not enemy_data:
            return False
        return True

    def attack(self, touch_up=True, touch_down=True):
        self.window_controller.press("attack", touch_up=touch_up, touch_down=touch_down)

    def use_hypercharge(self):
        if self.gas_in_danger or not self.gas_detection_ok:
            self.ability_vetoes.append('HYPERCHARGE_DURING_ESCAPE_OR_UNCERTAINTY')
            return
        print("Using hypercharge")
        self.window_controller.press("hypercharge")
        self.time_since_hypercharge_checked = time.time()
        self.is_hypercharge_ready = False

    def use_gadget(self):
        # Gadget effects are not modelled per loadout. Do not autoactivate a
        # possible dash/teleport next to an observed gas corridor.
        if self.ability_movement_is_uncertain():
            self.ability_vetoes.append('GADGET_MOVEMENT_UNCERTAIN')
            return
        print("Using gadget")
        self.window_controller.press("gadget")
        self.time_since_gadget_checked = time.time()
        self.is_gadget_ready = False

    def use_super(self):
        kind=(self.brawlers_info.get(self.current_brawler) or {}).get('super_type')
        if kind in ('charge','dash',None) and self.ability_movement_is_uncertain():
            self.ability_vetoes.append('SUPER_MOVEMENT_UNCERTAIN')
            return
        print("Using super")
        self.window_controller.press("super")
        self.time_since_super_checked = time.time()
        self.is_super_ready = False

    def ability_movement_is_uncertain(self):
        if not self.gas_detection_ok or self.gas_in_danger or not self.gas_player_box:
            return True
        if time.time()-self.gas_observed_at>self.gas_memory_ttl:
            return True
        center,radius=self.get_player_hit_circle(self.gas_player_box)
        return any(evaluate_gas_risk(self.gas_mask,center,radius,
                    (math.cos(angle),math.sin(angle)),self.gas_reach,
                    self.gas_lookahead,self.gas_sensitivity).near>self.gas_sensitivity
                   for angle in GAS_ESCAPE_ANGLES)

    @staticmethod
    def get_random_movement():
        random_movement = random.randint(-75, 75), random.randint(-75, 75)
        return random_movement

    @staticmethod
    def movement_to_vector(movement):
        if not isinstance(movement, (tuple, list)) or len(movement) != 2:
            return None

        x, y = movement
        if x is None or y is None:
            return None

        try:
            x, y = float(x), float(y)
            return (x, y) if math.isfinite(x) and math.isfinite(y) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def rotate_movement(movement, angle_radians):
        x, y = movement
        cos_angle = math.cos(angle_radians)
        sin_angle = math.sin(angle_radians)
        return (
            x * cos_angle - y * sin_angle,
            x * sin_angle + y * cos_angle,
        )

    @staticmethod
    def movement_direction_key(movement):
        x, y = movement
        magnitude = math.hypot(x, y)
        if magnitude < 1:
            return None

        angle = math.atan2(y, x)
        return round(angle / (math.pi / 8)) % 16

    def unstuck_movement_if_needed(self, movement, current_time=None):
        if current_time is None:
            current_time = time.time()

        # A long straight run is not proof of being stuck. The camera often
        # tracks the player, so background displacement is measured as well.
        if self.motion_state != 'STUCK':
            self.fix_movement_keys['toggled'] = False
            self.time_since_different_movement = current_time
            return movement

        movement_vector = self.movement_to_vector(movement)
        if movement_vector is None:
            self.fix_movement_keys["toggled"] = False
            self.fix_movement_keys["last_direction_key"] = None
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time
            return movement

        direction_key = self.movement_direction_key(movement_vector)
        if direction_key is None:
            self.fix_movement_keys["toggled"] = False
            self.fix_movement_keys["last_direction_key"] = None
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time
            return movement_vector

        if self.fix_movement_keys['toggled']:
            if current_time - self.fix_movement_keys['started_at'] > self.fix_movement_keys['duration']:
                self.fix_movement_keys['toggled'] = False
                self.fix_movement_keys["last_direction_key"] = direction_key
                self.time_since_different_movement = current_time
                return movement_vector

            return self.fix_movement_keys['fixed']

        if self.fix_movement_keys["last_direction_key"] != direction_key:
            self.fix_movement_keys["last_direction_key"] = direction_key
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time

        if current_time - self.time_since_different_movement > self.fix_movement_keys["delay_to_trigger"]:
            self.fix_movement_keys["rotation_sign"] *= -1
            angle_step = self.fix_movement_keys["rotation_angle_step"]
            rotated_movement = self.rotate_movement(
                movement_vector,
                self.fix_movement_keys["rotation_sign"] * angle_step * math.pi / 4
            )
            if self.fix_movement_keys["rotation_sign"] > 0:
                self.fix_movement_keys["rotation_angle_step"] += 1
                if self.fix_movement_keys["rotation_angle_step"] > self.fix_movement_keys["max_rotation_angle_step"]:
                    self.fix_movement_keys["rotation_angle_step"] = 1

            self.fix_movement_keys['fixed'] = rotated_movement
            self.fix_movement_keys['toggled'] = True
            self.fix_movement_keys['started_at'] = current_time
            return rotated_movement

        return movement_vector

    def load_brawler_ranges(self, brawlers_info=None):
        if not brawlers_info:
            brawlers_info = load_brawlers_info()
        screen_size_ratio = self.window_controller.scale_factor
        ranges = {}
        for brawler, info in brawlers_info.items():
            attack_range = info['attack_range']
            safe_range = info['safe_range']
            super_range = info['super_range']
            v = [safe_range, attack_range, super_range]
            ranges[brawler] = [int(v[0] * screen_size_ratio), int(v[1] * screen_size_ratio), int(v[2] * screen_size_ratio)]
        return ranges

    @staticmethod
    def can_attack_through_walls(brawler, skill_type, brawlers_info=None):
        if not brawlers_info: brawlers_info = load_brawlers_info()
        if skill_type == "attack":
            return brawlers_info[brawler]['ignore_walls_for_attacks']
        elif skill_type == "super":
            return brawlers_info[brawler]['ignore_walls_for_supers']
        raise ValueError("skill_type must be either 'attack' or 'super'")

    @staticmethod
    def must_brawler_hold_attack(brawler, brawlers_info=None):
        if not brawlers_info: brawlers_info = load_brawlers_info()
        return brawlers_info[brawler]['hold_attack'] > 0

    @staticmethod
    def walls_block_line_of_sight(p1, p2, walls):
        if not walls:
            return False

        p1_t = (int(p1[0]), int(p1[1]))
        p2_t = (int(p2[0]), int(p2[1]))
        min_x, max_x = min(p1_t[0], p2_t[0]), max(p1_t[0], p2_t[0])
        min_y, max_y = min(p1_t[1], p2_t[1]), max(p1_t[1], p2_t[1])
        for wall in walls:
            x1, y1, x2, y2 = wall

            if max_x < x1 or min_x > x2 or max_y < y1 or min_y > y2:
                continue

            rect = (int(x1), int(y1), int(x2 - x1), int(y2 - y1))
            if cv2.clipLine(rect, p1_t, p2_t)[0]:
                return True
        return False

    def get_player_hit_circle(self, player_box):
        radius = PLAYER_HIT_CIRCLE_RADIUS * (self.window_controller.scale_factor or 1)
        if player_box and len(player_box) >= 4:
            x1, y1, x2, y2 = player_box[:4]
            return ((x1 + x2) / 2, y2 - radius), radius

        return None, radius

    def get_actual_player_box(self, player_box):
        center, radius = self.get_player_hit_circle(player_box)
        if center is None:
            return None
        return [
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ]

    @staticmethod
    def point_rect_distance_sq(point, rect):
        x, y = point
        x1, y1, x2, y2 = rect
        dx = max(x1 - x, 0, x - x2)
        dy = max(y1 - y, 0, y - y2)
        return dx * dx + dy * dy

    @staticmethod
    def walls_block_swept_circle(p1, p2, radius, walls):
        if not walls:
            return False

        p1_t = (int(p1[0]), int(p1[1]))
        p2_t = (int(p2[0]), int(p2[1]))
        min_x, max_x = min(p1_t[0], p2_t[0]), max(p1_t[0], p2_t[0])
        min_y, max_y = min(p1_t[1], p2_t[1]), max(p1_t[1], p2_t[1])
        radius = int(math.ceil(radius))

        for wall in walls:
            x1, y1, x2, y2 = wall[:4]
            wall_rect = (x1, y1, x2, y2)
            expanded_x1 = int(x1 - radius)
            expanded_y1 = int(y1 - radius)
            expanded_x2 = int(x2 + radius)
            expanded_y2 = int(y2 + radius)

            if max_x < expanded_x1 or min_x > expanded_x2 or max_y < expanded_y1 or min_y > expanded_y2:
                continue

            rect = (
                expanded_x1,
                expanded_y1,
                max(1, expanded_x2 - expanded_x1),
                max(1, expanded_y2 - expanded_y1),
            )
            if cv2.clipLine(rect, p1_t, p2_t)[0]:
                radius_sq = radius * radius
                start_distance_sq = Play.point_rect_distance_sq(p1, wall_rect)
                end_distance_sq = Play.point_rect_distance_sq(p2, wall_rect)
                if start_distance_sq <= radius_sq and end_distance_sq > start_distance_sq:
                    continue
                return True

        return False

    def is_enemy_hittable(self, player_pos, enemy_pos, walls, skill_type):
        if self.can_attack_through_walls(self.current_brawler, skill_type, self.brawlers_info):
            return True
        if self.walls_block_line_of_sight(player_pos, enemy_pos, walls):
            return False
        return True

    def find_closest_enemy(self, enemy_data, player_coords, walls, skill_type):
        player_pos_x, player_pos_y = player_coords
        closest_hittable_distance = float('inf')
        closest_unhittable_distance = float('inf')
        closest_hittable = None
        closest_unhittable = None
        for enemy in enemy_data:
            enemy_pos = self.get_entity_pos(enemy)
            distance = self.get_distance(enemy_pos, player_coords)
            if self.is_enemy_hittable((player_pos_x, player_pos_y), enemy_pos, walls, skill_type):
                if distance < closest_hittable_distance:
                    closest_hittable_distance = distance
                    closest_hittable = [enemy_pos, distance]
            else:
                if distance < closest_unhittable_distance:
                    closest_unhittable_distance = distance
                    closest_unhittable = [enemy_pos, distance]
        if closest_hittable:
            return closest_hittable
        elif closest_unhittable:
            return closest_unhittable

        return None, None

    def find_closest_teammate(self, teammate_data, player_coords, walls):
        closest_distance = float('inf')
        closest_teammate = None
        for teammate in teammate_data:
            teammate_pos = self.get_entity_pos(teammate)
            distance = self.get_distance(teammate_pos, player_coords)
            if distance < closest_distance:
                closest_distance = distance
                closest_teammate = teammate_pos
        return closest_teammate, closest_distance

    def is_there_poison_gas(self, player_data, threshold=7000, area_from_player_checked=1.5):
        """Gas pixels on each side of the player, as up/down/left/right counts.

        Kept for the playstyles and the debug overlay, so the shape of the
        answer never changes: a count of gas pixels in the region around the
        player, or 0 for a side with nothing on it.

        The pixels come from the gas model when one is loaded, because showdown
        gas is green and translucent and a colour window cannot tell it apart
        from the grass under it. Without a model the old colour scan is used, so
        a missing model means "no gas seen" rather than an error.
        """
        mask = self.gas_mask
        if mask is None or self.frame is None or mask.shape[:2] != self.frame.shape[:2]:
            return self.scan_poison_colour(player_data, threshold, area_from_player_checked)

        return self.collect_gas_sides(
            lambda x1, y1, x2, y2: count_mask_pixels(mask, x1, y1, x2, y2),
            player_data,
            threshold,
            area_from_player_checked,
            "Gas model",
            # The model's mask is dense, so a side only a few hundred pixels
            # across never reaches the old 7000 pixel gate. A side also counts
            # as gassed once a real share of it is covered, which keeps the
            # number the playstyles compare meaningful at any resolution.
            min_share=self.gas_sensitivity
        )

    def scan_poison_colour(self, player_data, threshold=7000, area_from_player_checked=1.5):
        """Colour scan for gas, kept for a setup without a gas model.

        The gas in the game is green, not violet, so this window mostly matches
        the map; it is only the fallback now that the model does the work.
        """
        frame = self.frame
        if frame is None:
            return {"up": 0, "down": 0, "left": 0, "right": 0}

        def count_colour(x1, y1, x2, y2):
            roi = frame[y1:y2, x1:x2]
            if roi.size == 0:
                return 0
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            return count_mask_pixels(
                cv2.inRange(hsv_roi, POISON_LOW_HSV, POISON_HIGH_HSV),
                0, 0, roi.shape[1], roi.shape[0]
            )

        return self.collect_gas_sides(
            count_colour,
            player_data,
            threshold,
            area_from_player_checked,
            "Poison"
        )

    def collect_gas_sides(self, counter, player_data, threshold, area_from_player_checked, label, min_share=None):
        """Split the area around the player into four sides and count gas.

        `counter` is handed frame coordinates and answers with the number of gas
        pixels in that rectangle, which lets the model mask and the colour scan
        share everything except how they find the gas themselves. A side is
        reported when it passes the pixel `threshold`, or - if `min_share` is
        given - when that much of it is covered.
        """
        empty = {"up": 0, "down": 0, "left": 0, "right": 0}

        if not player_data or len(player_data) < 4 or self.frame is None:
            return empty

        actual_player_box = self.get_actual_player_box(player_data) or player_data
        px1, py1, px2, py2 = actual_player_box
        player_width = max(px2 - px1, 1)
        player_height = max(py2 - py1, 1)
        min_x = int(max(px1 - player_width*area_from_player_checked, 0))
        max_x = int(min(px2 + player_width*area_from_player_checked, self.window_controller.width))
        min_y = int(max(py1 - player_height*area_from_player_checked, 0))
        max_y = int(min(py2 + player_height*area_from_player_checked, self.window_controller.height))

        if min_x >= max_x or min_y >= max_y:
            return empty

        x, y = self.get_entity_pos(actual_player_box)
        roi_w = int(max_x - min_x)
        roi_h = int(max_y - min_y)
        local_px = int(clamp(x - min_x, 0, roi_w))
        local_py = int(clamp(y - min_y, 0, roi_h))

        areas = {
            "up": roi_w * local_py,
            "down": roi_w * (roi_h - local_py),
            "left": local_px * roi_h,
            "right": (roi_w - local_px) * roi_h,
        }

        counts = {
            "up": counter(min_x, min_y, max_x, min_y + local_py),
            "down": counter(min_x, min_y + local_py, max_x, max_y),
            "left": counter(min_x, min_y, min_x + local_px, max_y),
            "right": counter(min_x + local_px, min_y, max_x, max_y),
        }

        result = {}
        for direction, count in counts.items():
            gassed = count > threshold
            if not gassed and min_share is not None and areas[direction] > 0:
                gassed = count / areas[direction] > min_share
            result[direction] = count if gassed else 0

        if self.verbose_debug:
            print(f"{label} gas pixels:", counts)

            ts = int(time.time())

            roi = self.frame[min_y:max_y, min_x:max_x]
            debug_regions = {
                "up": roi[0:local_py, 0:roi_w],
                "down": roi[local_py:roi_h, 0:roi_w],
                "left": roi[0:roi_h, 0:local_px],
                "right": roi[0:roi_h, local_px:roi_w],
            }

            for direction, img in debug_regions.items():
                if img.size > 0:
                    cv2.imwrite(
                        f"debug_frames/poison_gas_{direction}_debug_{ts}.png",
                        cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    )

        return result

    def detect_gas(self, image):
        self.gas_mask_time = time.time()
        try:
            detections = self.Detect_gas.detect_objects(image, conf_tresh=self.gas_confidence)
        except Exception as error:
            self.gas_detection_ok = False
            if self._last_safety_reason != 'GAS_DETECTION_FAILURE':
                print(f'Gas detection failed; input paused: {error}')
            self._last_safety_reason = 'GAS_DETECTION_FAILURE'
            return self.gas_boxes
        self.gas_boxes, self.gas_mask = self.gas_memory.update(
            image, (detections or {}).get('gas', []), self.gas_mask_time,
            self.gas_area_top, self.gas_area_bottom)
        self.gas_detection_ok = True
        self.gas_observed_at = self.gas_mask_time
        return self.gas_boxes

    @staticmethod
    def build_gas_mask(image, boxes):
        return gas_boxes_mask(image, boxes, top=0., bottom=1.)[1]

    def gas_mask_is_usable(self, image):
        """True when the mask in hand belongs to this frame."""
        if self.gas_mask is None or image is None or getattr(image, "ndim", 0) < 2:
            return False
        return self.gas_mask.shape[:2] == image.shape[:2]

    def gas_share_on_player(self, mask, player_box):
        """Share of the player's body (0..1) that the gas mask covers."""
        if mask is None or not player_box or len(player_box) < 4:
            return 0.0

        box = self.get_actual_player_box(player_box) or player_box
        x1, y1, x2, y2 = [float(value) for value in box[:4]]
        height, width = mask.shape[:2]
        # Rounded before the area is taken: the pixel counter works on whole
        # pixels, so measuring a fractional box would report over 1.0.
        x1 = int(clamp(x1, 0, width))
        x2 = int(clamp(x2, 0, width))
        y1 = int(clamp(y1, 0, height))
        y2 = int(clamp(y2, 0, height))

        area = (x2 - x1) * (y2 - y1)
        if area <= 0:
            return 0.0

        return min(1.0, count_mask_pixels(mask, x1, y1, x2, y2) / area)

    def gas_on_player(self, image, player_box):
        """Share of the player standing in gas, stored in `gas_coverage`.

        This is the number the danger latch and the panel both read. With no
        model, no mask or no player to measure there is nothing to cover, so the
        answer is 0.
        """
        self.refresh_gas(image, player_box)
        return self.gas_coverage

    def refresh_gas(self, image, player_box=None, force=False):
        """Bring the gas state of this frame up to date.

        The model is the expensive half, so it only runs once every
        `gas_detect_interval` seconds. The cheap half - how much of the player
        the mask covers, and whether that counts as standing in gas - is worked
        out from the mask that is already there on every call, so the reading is
        never half a second behind the joystick.
        """
        now = time.time()

        if self.Detect_gas is None or image is None or getattr(image, "size", 0) == 0:
            self.clear_gas_state()
            return

        if player_box and len(player_box) >= 4:
            self.gas_player_box = [float(value) for value in player_box[:4]]

        if force or not self.gas_mask_is_usable(image) or now - self.gas_mask_time >= self.gas_detect_interval:
            self.detect_gas(image)

        if self.gas_player_box and self.gas_mask is not None:
            self.gas_coverage = self.gas_share_on_player(self.gas_mask, self.gas_player_box)
        else:
            self.gas_coverage = 0.0

        self.update_gas_danger(self.gas_coverage)

        if self.verbose_debug:
            print(
                f"Gas: {len(self.gas_boxes)} cloud(s), "
                f"coverage {self.gas_coverage:.3f}, danger {self.gas_danger:.3f}"
            )

    def clear_gas_state(self):
        """Forget the gas reading, for when there is nothing to measure.

        Called when the player cannot be found: a coverage from a player that
        is no longer on screen would keep the panel showing danger forever.
        """
        self.gas_boxes = []
        self.gas_mask = None
        self.gas_player_box = None
        self.gas_coverage = 0.0
        self.gas_escape_direction = None
        self.gas_escape_index = None
        self.gas_memory.observations.clear()
        self.gas_detection_ok = False
        self.update_gas_danger(0.0)

    def update_gas_danger(self, coverage):
        """Latch "the player is standing in gas" with hysteresis.

        Showdown gas comes as scattered patches, so running on the first sign of
        it made the bot thrash and never fight. The flight starts once
        `gas_danger_enter` of the body is covered and only ends after the share
        drops back to `gas_danger_exit`, so the edge of a patch no longer flips
        the bot back and forth.
        """
        if self.gas_in_danger:
            self.gas_in_danger = coverage > self.gas_danger_exit
        else:
            self.gas_in_danger = coverage > self.gas_danger_enter

        self.gas_danger = coverage if self.gas_in_danger else 0.0
        return self.gas_in_danger

    def is_in_gas(self):
        """True while the player is standing in gas, hysteresis latch included."""
        return bool(self.gas_in_danger)

    def map_centre(self, image=None):
        """Middle of the arena, in the coordinates the boxes are in."""
        if image is not None and getattr(image, "ndim", 0) >= 2:
            height, width = image.shape[:2]
            return width / 2, height / 2
        width = self.window_controller.width or brawl_stars_width
        height = self.window_controller.height or brawl_stars_height
        return width / 2, height / 2

    def gas_direction_share(self, mask, x, y, player_width, player_height, direction_x, direction_y, reach, start=0.0):
        return corridor_share(mask, x, y, player_width, player_height,
                              direction_x, direction_y, reach, start)

    @staticmethod
    def gas_direction_index(direction):
        """Which of the eight escape directions a vector points at."""
        if not direction:
            return None
        angle = math.atan2(direction[1], direction[0])
        return int(round(angle / (math.pi / 4))) % len(GAS_ESCAPE_ANGLES)

    def _clearest_escape(self, mask, player_box, walls=None):
        center, radius = self.get_player_hit_circle(player_box)
        if mask is None or center is None:
            return None
        direction, reason, options, requested = self.movement_arbiter.choose(
            (0.,0.), mask=mask, center=center, radius=radius,
            walls_block=lambda m, d: self.is_path_blocked(player_box, m, walls or [], d),
            frame_size=(mask.shape[1], mask.shape[0]), reach=self.gas_reach,
            lookahead=self.gas_lookahead, sensitivity=self.gas_sensitivity,
            escape=True, centre_bias=self.gas_centre_bias,
            tile=self.TILE_SIZE*self.window_controller.scale_factor,
            fallback_magnitude=JOYSTICK_RADIUS*self.window_controller.scale_factor)
        return direction

    def evaluate_gas_risk(self, movement, player_box=None):
        center, radius = self.get_player_hit_circle(player_box or self.gas_player_box)
        if center is None:
            return None
        from dataclasses import asdict
        return asdict(evaluate_gas_risk(self.gas_mask, center, radius, movement,
                                       self.gas_reach, self.gas_lookahead, self.gas_sensitivity))

    def arbitrate_movement(self, movement, player_box, data):
        center, radius = self.get_player_hit_circle(player_box)
        if not self.gas_detection_ok or time.time()-self.gas_observed_at > self.gas_memory_ttl:
            self.safety_telemetry = {'desired': movement, 'final': (0.,0.),
                                     'override_reason': 'GAS_DETECTION_UNCERTAIN'}
            return (0.,0.)
        final, reason, directions, requested = self.movement_arbiter.choose(
            movement, mask=self.gas_mask, center=center, radius=radius,
            walls_block=lambda m, d: self.is_path_blocked(player_box, m, data['wall'], d),
            frame_size=(self.frame.shape[1], self.frame.shape[0]),
            reach=self.gas_reach, lookahead=self.gas_lookahead,
            sensitivity=self.gas_sensitivity, escape=self.gas_in_danger,
            enemies=[self.get_entity_pos(b) for b in data['enemy']],
            teammates=[self.get_entity_pos(b) for b in data['teammate']],
            centre_bias=self.gas_centre_bias,
            observed_y=(self.gas_area_top*self.frame.shape[0],self.gas_area_bottom*self.frame.shape[0]),
            tile=self.TILE_SIZE*self.window_controller.scale_factor,
            fallback_magnitude=JOYSTICK_RADIUS*self.window_controller.scale_factor)
        if reason == 'GAS_PREVENTION':
            self.prevented_gas_entries += 1
        if reason != self._last_safety_reason and reason not in ('NONE','HOLD'):
            print(f'Movement override: {reason}; desired={movement}; final={final}')
        self._last_safety_reason = reason
        final = self.clamp_movement(final)
        final_risk = self.evaluate_gas_risk(final, player_box)
        self.safety_telemetry = {'desired':movement, 'final':final, 'override_reason':reason,
                                'gas_risk_desired':requested, 'gas_risk_final':final_risk,
                                'directions':directions,
                                'escape_options':sum(not o['wall_collision'] and not o['blocked'] for o in directions)}
        if self.gas_in_danger:
            self.gas_state = 'ESCAPE'
        elif requested and requested['near'] > self.gas_sensitivity:
            self.gas_state = 'CRITICAL' if requested['near'] > self.gas_danger_enter else 'DANGER'
        elif any(o['far'] > self.gas_sensitivity for o in directions):
            self.gas_state = 'CAUTION'
        else:
            self.gas_state = 'SAFE'
        if self.gas_in_danger and not self._gas_was_in_danger:
            self.gas_events.append({'timestamp':time.time(), 'player_position':center,
                                    'walls':data['wall'], 'enemies':data['enemy'],
                                    **self.safety_telemetry})
            print('Entered gas danger; evidence added to telemetry')
        self._gas_was_in_danger = self.gas_in_danger
        return final

    def update_motion(self, frame, player_box, now):
        gray = cv2.cvtColor(cv2.resize(frame, (160,90)), cv2.COLOR_RGB2GRAY).astype(np.float32)
        position = self.get_entity_pos(player_box)
        delta, confidence = (0.,0.), 0.
        if self._motion_frame is not None:
            delta, confidence = cv2.phaseCorrelate(self._motion_frame, gray)
        self._motion_frame = gray
        self.position_history.append((now, position, delta, confidence,
                                      bool(self.movement_to_vector(self.last_movement) and math.hypot(*self.last_movement)>1)))
        recent = [r for r in self.position_history if now-r[0] <= self.fix_movement_keys['delay_to_trigger']]
        if len(recent)<3 or recent[-1][0]-recent[0][0] < self.fix_movement_keys['delay_to_trigger']*.8:
            self.motion_state = 'DETECTION_UNCERTAIN'
        elif not all(r[4] for r in recent) or any(r[3]<.3 for r in recent):
            self.motion_state = 'DETECTION_UNCERTAIN'
        else:
            displacement = math.hypot(position[0]-recent[0][1][0], position[1]-recent[0][1][1])
            background = sum(math.hypot(*r[2]) for r in recent)
            self.motion_state = 'STUCK' if displacement < 5*self.window_controller.scale_factor and background < 2 else 'MOVING'

    def _toward_centre(self, player_box, image=None):
        """Screen-centre proposal only; it carries no claim about safe map space."""
        if not player_box or len(player_box) < 4:
            return None

        centre_x, centre_y = self.map_centre(image)
        player_x, player_y = self.get_entity_pos(player_box)
        dx = centre_x - player_x
        dy = centre_y - player_y
        length = math.hypot(dx, dy)
        if length < 1:
            return None

        return dx / length, dy / length

    def avoid_gas(self, image=None, player_box=None, walls=None, current_time=None):
        """Movement that walks the player out of the gas, or None for none.

        A direction is only picked while the player is actually in gas (see
        `update_gas_danger`); the rest of the time this returns None and the
        playstyle's own movement is used untouched. Inside a cloud the eight
        directions are scored as in `_clearest_escape`. Every new frame
        revalidates the exit against walls and gas.
        """
        if image is None:
            image = self.frame
        if player_box is None:
            player_box = self.gas_player_box
        if current_time is None:
            current_time = time.time()

        self.refresh_gas(image, player_box)

        if not self.gas_in_danger:
            self.gas_escape_direction = None
            self.gas_escape_index = None
            return None

        direction = self._clearest_escape(self.gas_mask, player_box, walls)
        if direction is None:
            return None

        index = self.gas_direction_index(direction)
        previous = self.gas_escape_direction

        if previous is None or index != self.gas_escape_index:
            self.gas_escape_index = index
            self.gas_escape_time = current_time
            self.gas_escapes += 1
            if self.gas_in_danger:
                self.gas_danger_escapes += 1

        self.gas_escape_direction = direction
        return direction

    def get_main_data(self, frame):
        data = self.Detect_main_info.detect_objects(frame, conf_tresh=self.entity_detection_confidence)
        return data

    def is_path_blocked(self, player_box, move_direction, walls, distance=None):
        if distance is None:
            distance = self.TILE_SIZE*self.window_controller.scale_factor
        movement = self.movement_to_vector(move_direction)
        if movement is None:
            return False

        magnitude = math.hypot(movement[0], movement[1])
        if magnitude < 1e-9:
            return False

        dx = movement[0] / magnitude * distance
        dy = movement[1] / magnitude * distance
        hit_circle_center, hit_circle_radius = self.get_player_hit_circle(player_box)
        if hit_circle_center is None:
            return False

        new_pos = (hit_circle_center[0] + dx, hit_circle_center[1] + dy)
        return self.walls_block_swept_circle(hit_circle_center, new_pos, hit_circle_radius, walls)

    @staticmethod
    def validate_game_data(data):
        incomplete = False
        if "player" not in data.keys():
            incomplete = True  # This is required so track_no_detections can also keep track if enemy is missing

        if "enemy" not in data.keys():
            data['enemy'] = []

        if "teammate" not in data.keys():
            data['teammate'] = []

        if 'wall' not in data.keys() or not data['wall']:
            data['wall'] = []

        if 'bush' not in data.keys() or not data['bush']:
            data['bush'] = []

        return False if incomplete else data

    def track_no_detections(self, data):
        if not data:
            data = {
                "enemy": None,
                "player": None
            }
        for key in self.time_since_detections:
            if key in data and data[key]:
                self.time_since_detections[key] = time.time()

    def do_movement(self, movement):
        movement_vector = self.movement_to_vector(movement)
        if movement_vector is None or math.hypot(*movement_vector) < 1e-9:
            self.window_controller.release_movement()
            return
        self.window_controller.move(*movement_vector)

    def get_brawler_range(self, brawler):
        if self.brawler_ranges is None:
            self.brawler_ranges = self.load_brawler_ranges(self.brawlers_info)
        return self.brawler_ranges.get(brawler, (0, 0, 0))

    @staticmethod
    def normalize_move(x, y, radius=JOYSTICK_RADIUS):
        length = math.hypot(x, y)
        if length <= 0:
            return (0.0, 0.0)
        scale = radius / length
        return (x * scale, y * scale)

    def clamp_movement(self, movement):
        x, y = movement
        length = math.hypot(x, y)
        if length <= 0:
            return (0.0, 0.0)
        scale = JOYSTICK_RADIUS / length
        target_x = x * scale * self.window_controller.width_ratio
        target_y = y * scale * self.window_controller.height_ratio
        return target_x, target_y

    def loop(self, brawler, data, current_time, gas_movement=None):
        self.ability_vetoes = []
        self.context = {
                'player_data': data['player'][0],
                'enemy_data': data['enemy'],
                'teammate_data': data['teammate'],
                'brawler': brawler,
                'walls': data['wall'],
                'bushes': data['bush'],
                'brawlers_info': self.brawlers_info,
                'must_brawler_hold_attack': self.must_brawler_hold_attack,
                'is_gadget_ready': self.is_gadget_ready and not self.gas_in_danger,
                'is_hypercharge_ready': self.is_hypercharge_ready and not self.gas_in_danger,
                'is_super_ready': self.is_super_ready and not self.gas_in_danger,
                'TILE_SIZE': self.TILE_SIZE*self.window_controller.scale_factor,
                'get_entity_pos': self.get_entity_pos,
                'get_distance': self.get_distance,
                'get_actual_player_box': self.get_actual_player_box,
                'get_brawler_range': self.get_brawler_range,
                'is_there_enemy': self.is_there_enemy,
                'attack': (lambda **kwargs: None) if self.gas_in_danger else self.attack,
                'use_hypercharge': (lambda: None) if self.gas_in_danger else self.use_hypercharge,
                'use_super': (lambda: None) if self.gas_in_danger else self.use_super,
                'use_gadget': (lambda: None) if self.gas_in_danger else self.use_gadget,
                'get_random_movement': self.get_random_movement,
                'current_brawler': self.current_brawler,
                'last_movement': self.last_movement,
                'last_movement_change_time': self.last_movement_change_time,
                'seconds_to_hold_attack_after_reaching_max': self.seconds_to_hold_attack_after_reaching_max,
                "width": self.window_controller.width,
                "height": self.window_controller.height,
                'observed_safe_target': self.observed_safe_target(data['player'][0], data['wall']),
                'find_closest_enemy': self.find_closest_enemy,
                'find_closest_teammate': self.find_closest_teammate,
                'is_there_poison_gas': self.is_there_poison_gas,
                'avoid_gas': self.avoid_gas,
                'is_in_gas': self.is_in_gas,
                'gas_coverage': self.gas_coverage,
                'gas_danger': self.gas_danger,
                'gas_boxes': self.gas_boxes,
                'is_path_blocked': self.is_path_blocked,
                'is_enemy_hittable': self.is_enemy_hittable,
                'time': time,
                'random': random,
                "persistent_data": self.persistent_data,
                'debug': self.verbose_debug,
                'JOYSTICK_RADIUS': JOYSTICK_RADIUS,
                'rotate_movement': self.rotate_movement,
                'normalize_move': self.normalize_move,
                'width_ratio': self.window_controller.width_ratio,
                'height_ratio': self.window_controller.height_ratio
            }
        decision_started = time.perf_counter()
        desired = self.movement_to_vector(self.get_movement()) or (0.,0.)
        candidate = self.clamp_movement(desired)
        # Hysteresis and unstuck are proposals. Safety always has the last word.
        if not self.gas_in_danger:
            if candidate != self.last_movement and current_time-self.last_movement_change_time < self.minimum_movement_delay:
                candidate = self.movement_to_vector(self.last_movement) or candidate
            candidate = self.unstuck_movement_if_needed(candidate, current_time)
        final = self.arbitrate_movement(candidate, data['player'][0], data)
        self.safety_telemetry['candidate_before_safety'] = candidate
        self.safety_telemetry['gas_risk_candidate'] = self.safety_telemetry.get('gas_risk_desired')
        self.safety_telemetry['desired'] = desired
        self.safety_telemetry['gas_risk_desired'] = self.evaluate_gas_risk(desired, data['player'][0])
        if final != self.last_movement:
            self.last_movement_change_time = current_time
        self.last_movement = final
        self.latency['decision_ms'] = (time.perf_counter()-decision_started)*1000
        return final

    def check_if_hypercharge_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(self.ability_crop_areas['hypercharge'][0] * wr), int(self.ability_crop_areas['hypercharge'][1] * hr)
        x2, y2 = int(self.ability_crop_areas['hypercharge'][2] * wr), int(self.ability_crop_areas['hypercharge'][3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        purple_pixels = count_hsv_pixels(screenshot, (137, 158, 159), (179, 255, 255), self.window_controller)
        if self.verbose_debug:
            print("hypercharge purple pixels:", purple_pixels, "(if > ", self.hypercharge_pixels_minimum, " then hypercharge is ready)")
            try:
                cv2.imwrite(f"debug_frames/hypercharge_debug_{purple_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
            except Exception:
                pass

        if purple_pixels > self.hypercharge_pixels_minimum:
            return True
        return False

    def check_if_gadget_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(self.ability_crop_areas['gadget'][0] * wr), int(self.ability_crop_areas['gadget'][1] * hr)
        x2, y2 = int(self.ability_crop_areas['gadget'][2] * wr), int(self.ability_crop_areas['gadget'][3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        green_pixels = count_hsv_pixels(screenshot, (57, 219, 165), (62, 255, 255), self.window_controller)
        if self.verbose_debug:
            print("gadget green pixels:", green_pixels, "(if > ", self.gadget_pixels_minimum, " then gadget is ready)")
            try:
                cv2.imwrite(f"debug_frames/gadget_debug_{green_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
            except Exception:
                pass

        if green_pixels > self.gadget_pixels_minimum:
            return True
        return False

    def check_if_super_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(self.ability_crop_areas['super'][0] * wr), int(self.ability_crop_areas['super'][1] * hr)
        x2, y2 = int(self.ability_crop_areas['super'][2] * wr), int(self.ability_crop_areas['super'][3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        yellow_pixels = count_hsv_pixels(screenshot, (17, 170, 200), (27, 255, 255), self.window_controller)
        if self.verbose_debug:
            print("super yellow pixels:", yellow_pixels, "(if > ", self.super_pixels_minimum, " then super is ready)")
            try:
                cv2.imwrite(f"debug_frames/super_debug_{yellow_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
            except Exception:
                pass

        if yellow_pixels > self.super_pixels_minimum:
            return True
        return False

    def get_centered_wall_crop(self, frame, player_data=None):
        frame_height, frame_width = frame.shape[:2]
        crop_size = self.centered_wall_crop_size

        if player_data:
            center_x, center_y = self.get_entity_pos(player_data[0])
        else:
            center_x, center_y = frame_width / 2, frame_height / 2

        crop_x1 = int(clamp(round(center_x - crop_size / 2), 0, frame_width - crop_size))
        crop_y1 = int(clamp(round(center_y - crop_size / 2), 0, frame_height - crop_size))
        crop_x2 = crop_x1 + crop_size
        crop_y2 = crop_y1 + crop_size

        return frame[crop_y1:crop_y2, crop_x1:crop_x2], crop_x1, crop_y1

    @staticmethod
    def offset_tile_data(tile_data, offset_x, offset_y):
        if not offset_x and not offset_y:
            return tile_data

        offset_data = {}
        for class_name, boxes in tile_data.items():
            offset_data[class_name] = [
                [box[0] + offset_x, box[1] + offset_y, box[2] + offset_x, box[3] + offset_y]
                for box in boxes
            ]
        return offset_data

    def get_tile_data(self, frame, player_data=None):
        if self.centered_wall_detection and self.Detect_centered_tile_detector is not None:
            crop, offset_x, offset_y = self.get_centered_wall_crop(frame, player_data)
            tile_data = self.Detect_centered_tile_detector.detect_objects(
                crop,
                conf_tresh=self.wall_detection_confidence
            )
            return self.offset_tile_data(tile_data, offset_x, offset_y)

        tile_data = self.Detect_tile_detector.detect_objects(frame, conf_tresh=self.wall_detection_confidence)
        return tile_data

    def process_tile_data(self, tile_data):
        walls = []
        bushes = []
        for class_name, boxes in tile_data.items():
            if 'bush' not in class_name:
                walls.extend(boxes)
            else:
                bushes.extend(boxes)
        return walls, bushes

    def get_movement(self):
        movement, updated_globals = interpret_playstyle_code(self.playstyle_code, self.context)
        return movement

    def publish_debug_view(self, frame, data, state, movement=None):
        if not hasattr(self.window_controller, "debug_view"):
            return

        self.frame = frame
        advanced_visuals = bool(getattr(self.window_controller.debug_view, "advanced_visuals", False))
        debug_data = {
            "state": state,
            "player": [],
            "enemy": [],
            "teammate": [],
            "wall": [],
            "attack_range": 0,
            "super_range": 0,
            "poison_gas": {},
            "movement": None,
            "joystick": [self.window_controller.movement_joystick_x, self.window_controller.movement_joystick_y],
            "advanced_visuals": advanced_visuals,
            "joystick_radius": int(JOYSTICK_RADIUS * (self.window_controller.scale_factor or 1)),
            "joystick_directions": [],
            "enemy_los_lines": [],
            "teammate_los_lines": [],
            "player_hit_circle": None,
        }

        if data:
            for key in ["player", "enemy", "teammate", "wall"]:
                debug_data[key] = [[int(v) for v in box[:4]] for box in (data.get(key) or []) if len(box) >= 4]
            try:
                _, attack_range, super_range = self.get_brawler_range(self.current_brawler)
                debug_data["attack_range"] = int(attack_range)
                debug_data["super_range"] = int(super_range)
            except Exception:
                pass
            if debug_data["player"]:
                try:
                    debug_data["poison_gas"] = self.is_there_poison_gas(debug_data["player"][0])
                except Exception:
                    pass

        if movement is not None:
            debug_data["movement"] = [float(movement[0]), float(movement[1])]

        debug_data['gas_boxes'] = self.gas_boxes
        debug_data['gas_state'] = self.gas_state
        debug_data['safety'] = self.safety_telemetry
        debug_data['latency'] = self.latency
        if data and data.get('player'):
            center, radius = self.get_player_hit_circle(data['player'][0])
            debug_data['player_hit_circle'] = [*center, radius]

        self.window_controller.debug_view.publish(frame, debug_data)

    def main(self, frame, brawler, main):
        current_time = time.time()
        frame_time = main.current_frame_time
        if not self.window_controller.frame_is_fresh(frame_time):
            self.window_controller.release_all_inputs()
            return
        state = main.get_latest_state()
        # Reclassify an unknown screen before using positive player evidence.
        if state == 'unknown':
            state = get_state(frame)
        if state not in ('match', 'unknown'):
            self._match_confirmations = 0
            self.window_controller.gameplay_frame_time = None
            self.window_controller.release_all_inputs()
            self.clear_gas_state()
            self.world_state = {'timestamp':frame_time, 'player_present':False, 'state':state}
            return
        if not main.Stage_manager.trio_session_confirmed:
            self.window_controller.release_all_inputs()
            self.safety_telemetry={'override_reason':'TRIO_SESSION_UNCONFIRMED', 'final':(0.,0.)}
            self.world_state={'timestamp':frame_time,'state':state,'player_present':False,
                              'mode_confirmed':False}
            return
        if is_respawning(frame):
            self.window_controller.release_all_inputs()
            self.clear_gas_state()
            self.position_history.clear()
            self._motion_frame=None
            self.world_state={'timestamp':frame_time,'state':'match','player_present':False,
                              'respawn_ui':True,'mode_confirmed':True}
            self.publish_debug_view(frame,None,'match')
            return
        data = self.get_main_data(frame)
        if current_time - self.time_since_walls_checked > self.walls_treshold:
            tile_data = self.get_tile_data(frame, data.get("player"))
            walls, bushes = self.process_tile_data(tile_data)
            self.time_since_walls_checked = current_time
            self.last_walls_data = walls
            data['wall'] = walls
            self.last_bushes_data = bushes
            data['bush'] = bushes
        else:
            data['wall'] = self.last_walls_data
            data['bush'] = self.last_bushes_data

        data = self.validate_game_data(data)
        self.track_no_detections(data)
        if data:
            self.time_since_player_last_found = time.time()
            if state == 'unknown':
                if frame_time != self._confirmation_stamp:
                    self._match_confirmations = self._match_confirmations+1 if self.battle_hud_visible(frame) else 0
                    self._confirmation_stamp = frame_time
                if self._match_confirmations < 3:
                    self.window_controller.release_all_inputs()
                    self.world_state={'timestamp':frame_time,'state':'unknown','player_present':True,
                                      'input_blocked':'MATCH_CONFIRMATION_PENDING'}
                    return
                state = 'match'
                main.set_latest_state('match', frame_time)
                main.Stage_manager.do_state('match')

        if not data:
            self._match_confirmations = 0
            # No player to measure, so the last gas reading would be about a
            # brawler that is no longer on screen.
            self.clear_gas_state()
            self.window_controller.release_all_inputs()
            self.persistent_data['time_since_holding_attack'] = None
            self.position_history.clear()
            self._motion_frame = None
            self.world_state = {'timestamp':frame_time, 'player_present':False, 'state':state}
            self.publish_debug_view(frame, data, state)
            return
        self.time_since_last_proceeding = time.time()
        if current_time - self.time_since_hypercharge_checked > self.hypercharge_treshold:
            self.is_hypercharge_ready = self.check_if_hypercharge_ready(frame)
            self.time_since_hypercharge_checked = current_time
        if current_time - self.time_since_gadget_checked > self.gadget_treshold:
            self.is_gadget_ready = self.check_if_gadget_ready(frame)
            self.time_since_gadget_checked = current_time
        if current_time - self.time_since_super_checked > self.super_treshold:
            self.is_super_ready = self.check_if_super_ready(frame)
            self.time_since_super_checked = current_time
        self.frame = frame
        # Gas is read before the playstyle runs, so the escape it hands over is
        # already known when the playstyle asks what to do.
        player_box = data['player'][0] if data.get('player') else None
        gas_movement = self.avoid_gas(frame, player_box, data.get('wall') or [], current_time)
        if not self.gas_detection_ok or not self.window_controller.begin_gameplay_frame(frame, frame_time):
            self.window_controller.release_all_inputs()
            return
        if self.gas_in_danger and self.persistent_data['time_since_holding_attack'] is not None:
            self.attack(touch_up=True, touch_down=False)
            self.persistent_data['time_since_holding_attack'] = None
        self.update_motion(frame, player_box, current_time)
        movement = self.loop(brawler, data, current_time, gas_movement)
        self.latency.update(entity_ms=self.Detect_main_info.last_inference_ms,
                            gas_ms=self.Detect_gas.last_inference_ms,
                            wall_ms=(self.Detect_centered_tile_detector or self.Detect_tile_detector).last_inference_ms)
        self.world_state = {'timestamp':frame_time, 'detected_time':time.time(),
                            'frame_size':list(frame.shape[:2]), 'player_present':True,
                            'state':state, 'brawler':brawler,
                            'mode_confirmed':True, 'visible_teammates':len(data['teammate']),
                            'teammate_life_known':False,
                            'player':data['player'], 'enemy':data['enemy'],
                            'teammate':data['teammate'], 'wall':data['wall'], 'bush':data['bush'],
                            'gas_boxes':self.gas_boxes, 'gas_coverage':self.gas_coverage,
                            'gas_detections':getattr(self.Detect_gas, 'last_detections', []),
                            'gas_model_confidence_threshold':self.gas_confidence,
                            'ability_vetoes':self.ability_vetoes,
                            'gas_observed_at':self.gas_observed_at, 'movement':self.safety_telemetry}
        self.publish_debug_view(frame, data, state, movement)
        if movement is not None:
            started = time.perf_counter()
            self.do_movement(movement)
            self.latency['input_ms'] = (time.perf_counter()-started)*1000
