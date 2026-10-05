"""Frame-coordinate gas observations and the final movement safety policy.

This module has no device or input access, so saved observations can be replayed.
Gas/wall vetoes are hard constraints; centre and combat preferences only break ties.
"""
from dataclasses import asdict, dataclass
import math

import cv2
import numpy as np

ANGLES = tuple(math.radians(a) for a in range(0, 360, 45))


def gas_boxes_mask(image, boxes, top=0.21, bottom=1.0):
    height, width = image.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    kept = []
    for box in boxes or []:
        if len(box) < 4 or not all(math.isfinite(float(v)) for v in box[:4]):
            continue
        x1, y1, x2, y2 = map(float, box[:4])
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if not top * height <= (y1 + y2) / 2 <= bottom * height:
            continue
        x1, x2 = int(max(0, min(width, x1))), int(max(0, min(width, x2)))
        y1, y2 = int(max(top*height, min(height, y1))), int(min(bottom*height, max(0, min(height, y2))))
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 255
            kept.append([x1, y1, x2, y2])
    return kept, mask


class GasMemory:
    """Short, non-renewing TTL for missed clouds; errors never mean clear arena."""
    def __init__(self, ttl=0.6):
        if not math.isfinite(ttl) or ttl<=0:
            raise ValueError('Gas memory TTL must be positive and finite')
        self.ttl = ttl
        self.observations = []

    def update(self, image, boxes, now, top, bottom):
        kept, current = gas_boxes_mask(image, boxes, top, bottom)
        self.observations = [(t, m) for t, m in self.observations
                             if now - t < self.ttl and m.shape == current.shape]
        if kept:
            self.observations.append((now, current))
        mask = current.copy()
        for stamp, old in self.observations:
            weight = max(0.0, 1.0 - (now - stamp) / self.ttl)
            np.maximum(mask, (old * weight).astype(np.uint8), out=mask)
        return kept, mask


@dataclass(frozen=True)
class GasRisk:
    immediate: float
    near: float
    far: float
    risk: float
    blocked: bool
    reason: str
    endpoint: tuple


def patch_share(mask, x, y, radius):
    height, width = mask.shape[:2]
    x1, x2 = int(max(0, x-radius)), int(min(width, x+radius))
    y1, y2 = int(max(0, y-radius)), int(min(height, y+radius))
    if x2 <= x1 or y2 <= y1:
        return 1.0  # unobserved space is not evidence of safety
    patch = mask[y1:y2, x1:x2]
    return float(np.mean(patch, dtype=np.float64) / 255.0)


def corridor_share(mask, x, y, player_width, player_height, dx, dy, reach, start=0.0):
    """Maximum body-footprint coverage along a corridor, including its endpoints."""
    if mask is None or reach <= 0:
        return 0.0
    radius = max(player_width, player_height) / 2
    distance = max(player_width, player_height) * reach
    # Overlapping footprints cannot jump over a thin gas strip.
    steps = max(6, int(math.ceil(distance * (1-start) / max(radius*.5, 1))))
    return max(patch_share(mask, x + dx*distance*t, y + dy*distance*t, radius)
               for t in np.linspace(start, 1.0, steps+1))


def evaluate_gas_risk(mask, center, radius, movement, reach=3.0, lookahead=4.0, sensitivity=.05):
    x, y = center
    length = math.hypot(*movement)
    immediate = patch_share(mask, x, y, radius) if mask is not None else 0.0
    if length < 1e-9:
        return GasRisk(immediate, immediate, immediate, immediate, False, 'HOLD', center)
    dx, dy = movement[0]/length, movement[1]/length
    near = corridor_share(mask, x, y, radius*2, radius*2, dx, dy, reach)
    far = corridor_share(mask, x, y, radius*2, radius*2, dx, dy, lookahead,
                         reach/lookahead) if lookahead > reach else near
    risk = max(near, far)
    endpoint = (x + dx*radius*2*reach, y + dy*radius*2*reach)
    blocked = risk > max(sensitivity, immediate + sensitivity)
    return GasRisk(immediate, near, far, risk, blocked,
                   'GAS_CORRIDOR' if blocked else 'CLEAR', endpoint)


class MovementArbiter:
    def choose(self, desired, *, mask, center, radius, walls_block, frame_size,
               reach=3., lookahead=4., sensitivity=.05, escape=False,
               enemies=(), teammates=(), centre_bias=.06, tile=54., fallback_magnitude=1.,
               observed_y=None):
        desired = desired or (0., 0.)
        length = math.hypot(*desired)
        desired_unit = (desired[0]/length, desired[1]/length) if length else (0.,0.)
        width, height = frame_size
        to_centre = (width/2-center[0], height/2-center[1])
        centre_length = math.hypot(*to_centre) or 1.

        def assess(move):
            gas = evaluate_gas_risk(mask, center, radius, move, reach, lookahead, sensitivity)
            blocked = bool(walls_block(move, tile))
            # A first step into unobserved space is disallowed. The camera can
            # scroll, but only a subsequent fresh observation can prove it clear.
            norm = math.hypot(*move) or 1.
            px, py = center[0]+move[0]/norm*tile, center[1]+move[1]/norm*tile
            boundary = not (radius <= px <= width-radius and radius <= py <= height-radius)
            # Cropped-out HUD space has no gas observations. It must not win
            # escape scoring just because the mask is zero outside its ROI.
            y_min,y_max=observed_y if observed_y is not None else (0.,height)
            observation_boundary=not (y_min+radius <= py <= y_max-radius)
            boundary=boundary or observation_boundary
            blocked = blocked or boundary
            alignment = move[0]*desired_unit[0] + move[1]*desired_unit[1]
            inward = (move[0]*to_centre[0]+move[1]*to_centre[1])/centre_length
            pressure = sum(max(0., 1.-math.hypot(px-ex, py-ey)/(tile*6)) for ex, ey in enemies)
            support = max((max(0., 1.-math.hypot(px-tx, py-ty)/(tile*6)) for tx, ty in teammates), default=0.)
            space = sum(not walls_block(move, tile*n) for n in (1,2,3)) / 3.
            # During escape terminal coverage is the primary cost: shared gas
            # at t=0 must not make all exits look identical.
            terminal = patch_share(mask, *gas.endpoint, radius) if mask is not None else 0.
            terminal_observed=y_min+radius<=gas.endpoint[1]<=y_max-radius
            if not terminal_observed:
                terminal=1.
            score = (terminal*10 + gas.near*2 + gas.far) if escape else gas.risk*10
            score += pressure*.25 - support*.05 - space*.05 - centre_bias*inward - alignment*.1
            return {**asdict(gas), 'movement':move, 'wall_collision':blocked,
                    'observation_boundary':observation_boundary,'terminal_observed':terminal_observed,
                    'enemy_risk':pressure, 'teammate_value':support, 'escape_space':space,
                    'alignment':alignment, 'terminal':terminal, 'score':score}

        options = [assess((round(math.cos(a), 12), round(math.sin(a), 12))) for a in ANGLES]
        requested = assess(desired_unit) if length else None
        walkable = [o for o in options if not o['wall_collision']]
        safe = [o for o in walkable if not o['blocked']]
        if not escape and not length:
            return (0.,0.), 'HOLD', options, requested
        if not escape and requested and not requested['wall_collision'] and not requested['blocked']:
            return desired, 'NONE', options, requested
        pool = safe or (walkable if escape else [])
        if not pool:
            return (0.,0.), 'NO_WALKABLE_EXIT' if not walkable else 'NO_SAFE_EXIT', options, requested
        chosen = min(pool, key=lambda o: o['score'])
        reason = 'GAS_ESCAPE' if escape else 'GAS_PREVENTION' if requested and requested['blocked'] else 'WALL_PREVENTION'
        # Options are unit directions; the controller expects joystick pixels.
        # Preserve the candidate's amplitude when replacing its direction.
        magnitude=length if length>1e-9 else fallback_magnitude
        return tuple(v*magnitude for v in chosen['movement']), reason, options, requested
