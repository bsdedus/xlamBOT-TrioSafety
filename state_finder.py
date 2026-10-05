import os
import sys
import cv2
import time
sys.path.append(os.path.abspath('/'))
from utils import load_toml_as_dict, config_bool, resolve_project_path

last_debug_print_time = 0.0
should_print_debug_info = False

orig_screen_width, orig_screen_height = 1920, 1080

states_path = str(resolve_project_path('images', 'states')) + os.sep

star_drops_path = str(resolve_project_path('images', 'star_drop_types')) + os.sep
images_with_star_drop = []
for file in os.listdir(star_drops_path):
    if "star_drop" in file:
        images_with_star_drop.append(file)

end_results_path = str(resolve_project_path('images', 'end_results')) + os.sep

class _ProfileRegions:
    def __getitem__(self, key):
        return load_toml_as_dict('cfg/lobby_config.toml')['template_matching'][key]
    def get(self, key, default=None):
        return load_toml_as_dict('cfg/lobby_config.toml')['template_matching'].get(key, default)
region_data = _ProfileRegions()


def is_template_in_region(image, template_path, region, threshold=None, return_score=False):
    if threshold is None:
        threshold = load_toml_as_dict("cfg/bot_config.toml").get("state_detection_confidence", 0.75)
    current_height, current_width = image.shape[:2]
    orig_x, orig_y, orig_width, orig_height = region
    width_ratio, height_ratio = current_width / orig_screen_width, current_height / orig_screen_height

    new_x, new_y = int(orig_x * width_ratio), int(orig_y * height_ratio)
    new_width, new_height = int(orig_width * width_ratio), int(orig_height * height_ratio)
    cropped_image = image[new_y:new_y + new_height, new_x:new_x + new_width]
    loaded_template = load_template(template_path, current_width, current_height)
    if loaded_template is None or cropped_image.size == 0:
        return False
    if (
        cropped_image.shape[0] < loaded_template.shape[0]
        or cropped_image.shape[1] < loaded_template.shape[1]
    ):
        return False

    try:
        result = cv2.matchTemplate(cropped_image, loaded_template, cv2.TM_CCOEFF_NORMED)
    except cv2.error as e:
        if should_print_debug_info:
            print(f"Template matching failed for {template_path}: {e}")
        return False

    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    if should_print_debug_info:
        print(f"Template matching for {template_path} in region {region} yielded max_val: {max_val}")
    return max_val if return_score else max_val > threshold


cached_templates = {}
def load_template(image_path, width, height):
    if (image_path, width, height) in cached_templates:
        return cached_templates[(image_path, width, height)]
    import numpy as np
    try:
        image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        image = None
    if image is None:
        print(f"Could not load template: {image_path}")
        return None
    orig_height, orig_width = image.shape[:2]
    current_width_ratio, current_height_ratio = width / orig_screen_width, height / orig_screen_height
    resized_image = cv2.resize(image, (int(orig_width * current_width_ratio), int(orig_height * current_height_ratio)))
    resized_colored_image = cv2.cvtColor(resized_image, cv2.COLOR_BGR2RGB)
    cached_templates[(image_path, width, height)] = resized_colored_image
    return resized_colored_image

SHOWDOWN_PLACE_THRESHOLD = 0.9
showdown_place_templates = {
    0: ["1st.png"],
    1: ["2nd.png"],
    2: ["3rd.png", "3rd_alt.png"],
    3: ["4th.png"]
}

def find_game_result(screenshot):
    # Match only the stable placement caption, independent of animated skins.
    scores={}
    for place, filename in enumerate(('1st_ru_2026.png','2nd_ru_2026.png','3rd_ru_2026.png')):
        scores[place]=is_template_in_region(screenshot,end_results_path+filename,
                                            [20,10,1080,220],return_score=True)
    best=max(scores,key=scores.get)
    if scores[best]>SHOWDOWN_PLACE_THRESHOLD:
        return f'trio_showdown_{best}'
    for place, template_files in showdown_place_templates.items():
        for template_file in template_files:
            if is_template_in_region(
                    screenshot,
                    end_results_path + template_file,
                    region_data['match_result'],
                    threshold=SHOWDOWN_PLACE_THRESHOLD
            ):
                return f"trio_showdown_{place}"
    is_victory = is_template_in_region(screenshot, end_results_path + 'victory.png', region_data['match_result'])
    if is_victory:
        return "victory"

    is_defeat = is_template_in_region(screenshot, end_results_path + 'defeat.png', region_data['match_result'])
    if is_defeat:
        return "defeat"

    is_draw = is_template_in_region(screenshot, end_results_path + 'draw.png', region_data['match_result'])
    if is_draw:
        return "draw"
    return False


def get_in_game_state(image):
    global last_debug_print_time, should_print_debug_info
    state_finder_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('state_finder_debug'), False)
    current_time = time.time()
    should_print_debug_info = state_finder_debug and (current_time - last_debug_print_time >= 1.0)
    if should_print_debug_info:
        last_debug_print_time = current_time

    try:
        # First, because the dialog covers the lobby and the lobby template still
        # matches through it. Checked later, the lobby would win every time and
        # the dialog would never be seen.
        if is_in_connection_lost(image): return "connection_lost"
        if is_template_in_region(image,states_path+'team_panel_ru.png',[930,0,840,130],threshold=.9):
            return 'team_panel'
        if should_print_debug_info: print("Checking for match result...")
        game_result = is_in_end_of_a_match(image)
        if game_result: return f"end_{game_result}"
        if is_in_showdown_match(image): return 'match'
        if should_print_debug_info: print("Checking for lobby...")
        if is_in_lobby(image): return "lobby"
        if should_print_debug_info: print("Checking for match making...")
        if is_in_match_making(image): return "match_making"
        if should_print_debug_info: print("Checking for brawler selection...")
        if is_in_brawler_selection(image): return "brawler_selection"
        if should_print_debug_info: print("Checking for shop")
        if is_in_shop(image): return "shop"
        if should_print_debug_info: print("Checking for offer popup...")
        if is_in_offer_popup(image): return "popup"
        if should_print_debug_info: print("Checking for brawl pass or star road (shop state)...")
        if is_in_brawl_pass(image) or is_in_star_road(image): return "shop"
        if should_print_debug_info: print("Checking for prestige milestone...")
        if is_in_prestige_milestone(image): return "prestige_milestone"
        if should_print_debug_info: print("Checking for star drop...")
        star_drop_type = is_in_star_drop(image)
        if star_drop_type:
            return f"star_drop_{star_drop_type}"
        if should_print_debug_info: print("Checking for trophy reward...")
        if is_in_trophy_reward(image):
            return "trophy_reward"

        return "unknown"
    finally:
        should_print_debug_info = False


def is_in_shop(image) -> bool:
    return is_template_in_region(image, states_path + 'powerpoint.png', region_data["powerpoint"])


def is_respawning(image) -> bool:
    return is_template_in_region(image, states_path+'respawn_ru.png',
                                 [570,330,780,180], threshold=.88)


def is_in_showdown_match(image) -> bool:
    """Positive HUD evidence, including spectator frames with no attack button.

    Only the constant label is matched; team count, map and player coordinates
    are excluded. Require bright HUD text too: a modal can retain a dimmed HUD.
    Mode authorization remains the separate Trio lobby confirmation.
    """
    import numpy as np
    height,width=image.shape[:2]
    crop=image[int(height*.01):int(height*.12), int(width*.01):int(width*.30)]
    if not crop.size or np.mean(np.all(crop>205,axis=2))<.06:
        return False
    return is_template_in_region(image,states_path+'teams_remaining_ru.png',
                                 [20,10,556,120],threshold=.88)


def is_in_connection_lost(image) -> bool:
    """The private server drops the connection and puts a dialog up.

    Scored 0.99 on three emulators showing the dialog and 0.23-0.43 on ordinary
    lobby and match screens, so the shared 0.75 threshold separates them with
    room to spare.
    """
    region = region_data.get("connection_lost_dialog")
    if not region:
        return False
    return (is_template_in_region(image, states_path + 'connection_lost_dialog.png', region)
            or is_template_in_region(image, states_path + 'idle_disconnect_ru.png', region, threshold=.9)
            or is_template_in_region(image, states_path + 'login_failure_ru.png', region, threshold=.9))


def is_in_brawler_selection(image) -> bool:
    return is_template_in_region(image, states_path + 'brawler_menu_heart.png', region_data["brawler_menu_heart"]) or is_template_in_region(image, states_path + 'brawler_menu_search.png', region_data["brawler_menu_search"])


def is_in_offer_popup(image) -> bool:
    return is_template_in_region(image, states_path + 'close_popup.png', region_data["close_popup"])


def is_in_lobby(image) -> bool:
    return is_template_in_region(image, states_path + 'lobby_menu.png', region_data["lobby_menu"])


def selected_showdown_mode(image):
    """Confirm the selected lobby icon, independently of language/menu tiles."""
    if not is_in_lobby(image):
        return None
    height, width = image.shape[:2]
    crop = image[int(height*.82):, int(width*.36):int(width*.75)]
    scores = {}
    for mode in ('solo_showdown', 'duo_showdown', 'trio_showdown'):
        template = load_template(str(resolve_project_path('images', 'gamemodes', mode+'_selected.png')), width, height)
        if template is None or any(crop.shape[i] < template.shape[i] for i in (0, 1)):
            continue
        scores[mode] = cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1]
    ordered = sorted(scores, key=scores.get, reverse=True)
    if not ordered or scores[ordered[0]] < .85:
        return None
    if len(ordered)>1 and scores[ordered[0]]-scores[ordered[1]] < .03:
        return None
    return ordered[0]


def is_in_end_of_a_match(image):
    return find_game_result(image)


def is_in_trophy_reward(image):
    return is_template_in_region(image, states_path + 'trophies_screen.png', region_data["trophies_screen"])


def is_in_brawl_pass(image):
    return is_template_in_region(image, states_path + 'brawl_pass_house.png', region_data['brawl_pass_house'])


def is_in_star_road(image):
    return is_template_in_region(image, states_path + "go_back_arrow.png", region_data['go_back_arrow'])


def is_in_match_making(image):
    return is_template_in_region(image, states_path + "exit_match_making.png", region_data['exit_match_making'])


def is_in_prestige_milestone(image):
    return is_template_in_region(image, states_path + "prestige_continue.png", region_data['prestige_continue'])


def is_in_star_drop(image):
    for image_filename in images_with_star_drop:
        if is_template_in_region(image, star_drops_path + image_filename, region_data['star_drop']):
            if "angelic" in image_filename.lower(): return "angelic"
            if "demonic" in image_filename.lower(): return "demonic"
            if "starr_nova" in image_filename.lower(): return "starr_nova"
            return "regular"
    return False


def is_underdog(image):
    return is_template_in_region(image, end_results_path + "underdog.png", region_data['underdog'])


def get_state(screenshot):
    state = get_in_game_state(screenshot)
    if config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('state_finder_debug'), False):
        os.makedirs('debug_frames', exist_ok=True)
        cv2.imwrite(f"./debug_frames/state_screenshot_{state}_{time.time_ns()}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
    return state
