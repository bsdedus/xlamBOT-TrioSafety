"""Exact navigation methods from v0.8.15, commit 9540ce82cef79c83a7711e6445e440df31542fa3.
Component comparison only: old playstyle/input/temporal state are not simulated.
"""
import math
import cv2
import numpy as np
from utils import clamp, count_mask_pixels, JOYSTICK_RADIUS
PLAYER_HIT_CIRCLE_RADIUS=53
GAS_ESCAPE_ANGLES=tuple(math.radians(a) for a in range(0,360,45))
GAS_CORRIDOR_SAMPLES=5
GAS_FAR_FIELD_WEIGHT=.5
brawl_stars_width,brawl_stars_height=1920,1080

class BaselineNavigation:

    @staticmethod
    def get_entity_pos(entity):
        return ((entity[0] + entity[2]) / 2, (entity[1] + entity[3]) / 2)

    @staticmethod
    def movement_to_vector(movement):
        if not isinstance(movement, (tuple, list)) or len(movement) != 2:
            return None
        x, y = movement
        if x is None or y is None:
            return None
        try:
            return (float(x), float(y))
        except (TypeError, ValueError):
            return None

    def get_player_hit_circle(self, player_box):
        radius = PLAYER_HIT_CIRCLE_RADIUS * (self.window_controller.scale_factor or 1)
        if player_box and len(player_box) >= 4:
            x1, y1, x2, y2 = player_box[:4]
            return (((x1 + x2) / 2, y2 - radius), radius)
        return (None, radius)

    def get_actual_player_box(self, player_box):
        center, radius = self.get_player_hit_circle(player_box)
        if center is None:
            return None
        return [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]

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
        min_x, max_x = (min(p1_t[0], p2_t[0]), max(p1_t[0], p2_t[0]))
        min_y, max_y = (min(p1_t[1], p2_t[1]), max(p1_t[1], p2_t[1]))
        radius = int(math.ceil(radius))
        for wall in walls:
            x1, y1, x2, y2 = wall[:4]
            wall_rect = (x1, y1, x2, y2)
            expanded_x1 = int(x1 - radius)
            expanded_y1 = int(y1 - radius)
            expanded_x2 = int(x2 + radius)
            expanded_y2 = int(y2 + radius)
            if max_x < expanded_x1 or min_x > expanded_x2 or max_y < expanded_y1 or (min_y > expanded_y2):
                continue
            rect = (expanded_x1, expanded_y1, max(1, expanded_x2 - expanded_x1), max(1, expanded_y2 - expanded_y1))
            if cv2.clipLine(rect, p1_t, p2_t)[0]:
                radius_sq = radius * radius
                start_distance_sq = Play.point_rect_distance_sq(p1, wall_rect)
                end_distance_sq = Play.point_rect_distance_sq(p2, wall_rect)
                if start_distance_sq <= radius_sq and end_distance_sq > start_distance_sq:
                    continue
                return True
        return False

    @staticmethod
    def build_gas_mask(image, boxes):
        """Flatten gas boxes into one 0/255 mask the size of the frame."""
        height, width = image.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        for box in boxes or []:
            x1, y1, x2, y2 = [int(value) for value in box[:4]]
            x1, x2 = sorted((max(0, x1), min(width, x2)))
            y1, y2 = sorted((max(0, y1), min(height, y2)))
            if x2 <= x1 or y2 <= y1:
                continue
            mask[y1:y2, x1:x2] = 255
        return mask

    def map_centre(self, image=None):
        """Middle of the arena, in the coordinates the boxes are in."""
        if image is not None and getattr(image, 'ndim', 0) >= 2:
            height, width = image.shape[:2]
            return (width / 2, height / 2)
        width = self.window_controller.width or brawl_stars_width
        height = self.window_controller.height or brawl_stars_height
        return (width / 2, height / 2)

    def gas_direction_share(self, mask, x, y, player_width, player_height, direction_x, direction_y, reach, start=0.0):
        """Gas share inside the corridor the player would walk down.

        The corridor starts at the edge of the body and runs `reach` player
        widths out, sampled in a few patches so a narrow gap between two clouds
        still shows up. `start` is the share of it to leave out, which is how
        the far end of the corridor is measured on its own. Samples outside the
        frame are dropped and the rest is averaged, so a direction that runs off
        the side of the screen is not punished for the part that was never on it.
        """
        if mask is None or reach <= 0:
            return 0.0
        height, width = mask.shape[:2]
        body = max(player_width, player_height) / 2
        start_x = x + direction_x * body
        start_y = y + direction_y * body
        reach_x = player_width * reach
        reach_y = player_height * reach
        patch = body * 0.6
        gas_pixels = 0
        measured = 0
        for step in range(GAS_CORRIDOR_SAMPLES):
            along = start + (step + 0.5) / GAS_CORRIDOR_SAMPLES * (1.0 - start)
            sample_x = start_x + direction_x * reach_x * along
            sample_y = start_y + direction_y * reach_y * along
            x1 = clamp(sample_x - patch / 2, 0, width)
            x2 = clamp(sample_x + patch / 2, 0, width)
            y1 = clamp(sample_y - patch / 2, 0, height)
            y2 = clamp(sample_y + patch / 2, 0, height)
            if x1 >= x2 or y1 >= y2:
                continue
            measured += (x2 - x1) * (y2 - y1)
            gas_pixels += count_mask_pixels(mask, x1, y1, x2, y2)
        if measured <= 0:
            return 0.0
        return gas_pixels / measured

    def _clearest_escape(self, mask, player_box, walls=None):
        """The least gassy of the eight directions around the player.

        Every direction is scored by the gas share of the corridor it walks
        down, minus a bonus for pointing back at the middle of the map: showdown
        gas does more damage the further out the player is, so a clean step
        inwards beats an equally clean step sideways. Shares under
        `gas_sensitivity` count as clean, which lets that bonus decide between
        two sides instead of a few stray pixels. A side the player cannot walk
        into because of a wall is pushed to the back of the queue.

        Returns a unit (x, y) vector in screen coordinates, or None when there is
        nothing to measure.
        """
        if mask is None or not player_box or len(player_box) < 4:
            return None
        box = self.get_actual_player_box(player_box) or player_box
        player_width = max(box[2] - box[0], 1)
        player_height = max(box[3] - box[1], 1)
        centre_x, centre_y = self.get_entity_pos(box)
        centre = self.map_centre()
        to_centre_x = centre[0] - centre_x
        to_centre_y = centre[1] - centre_y
        distance_to_centre = math.hypot(to_centre_x, to_centre_y)
        if distance_to_centre > 0:
            to_centre_x /= distance_to_centre
            to_centre_y /= distance_to_centre
        else:
            to_centre_x, to_centre_y = (0.0, 0.0)
        best_direction = None
        best_score = None
        far_start = self.gas_reach / self.gas_lookahead if self.gas_lookahead > 0 else 0.0
        for angle in GAS_ESCAPE_ANGLES:
            direction_x = math.cos(angle)
            direction_y = math.sin(angle)
            share = self.gas_direction_share(mask, centre_x, centre_y, player_width, player_height, direction_x, direction_y, self.gas_reach)
            if self.gas_lookahead > self.gas_reach:
                share += GAS_FAR_FIELD_WEIGHT * self.gas_direction_share(mask, centre_x, centre_y, player_width, player_height, direction_x, direction_y, self.gas_lookahead, far_start)
            if share < self.gas_sensitivity:
                share = 0.0
            inward = max(0.0, direction_x * to_centre_x + direction_y * to_centre_y)
            score = share - self.gas_centre_bias * inward
            if walls and self.is_path_blocked(player_box, (direction_x, direction_y), walls):
                score += 1.0
            if best_score is None or score < best_score:
                best_score = score
                best_direction = (direction_x, direction_y)
        return best_direction

    def is_path_blocked(self, player_box, move_direction, walls, distance=None):
        if distance is None:
            distance = self.TILE_SIZE * self.window_controller.scale_factor
        movement = self.movement_to_vector(move_direction)
        if movement is None:
            return False
        magnitude = math.hypot(movement[0], movement[1])
        if magnitude < 1:
            return False
        dx = movement[0] / magnitude * distance
        dy = movement[1] / magnitude * distance
        hit_circle_center, hit_circle_radius = self.get_player_hit_circle(player_box)
        if hit_circle_center is None:
            return False
        new_pos = (hit_circle_center[0] + dx, hit_circle_center[1] + dy)
        return self.walls_block_swept_circle(hit_circle_center, new_pos, hit_circle_radius, walls)

    def clamp_movement(self, movement):
        x, y = movement
        length = math.hypot(x, y)
        if length <= 0:
            return (0.0, 0.0)
        scale = JOYSTICK_RADIUS / length
        target_x = x * scale * self.window_controller.width_ratio
        target_y = y * scale * self.window_controller.height_ratio
        return (target_x, target_y)
Play=BaselineNavigation
