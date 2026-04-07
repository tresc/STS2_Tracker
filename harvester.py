import json
import os
import glob
import traceback
from datetime import datetime
from collections import defaultdict, Counter

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

# Finds the Steam history folder for any user automatically
steam_glob = r"C:\Program Files (x86)\Steam\userdata\*\2868840\remote\profile1\saves\history"
folders    = glob.glob(steam_glob)
HISTORY_FOLDER = folders[0] if folders else ""

# Extract the local Steam ID from the folder path so we can highlight
# "your" character in multiplayer run panels.
LOCAL_STEAM_ID = None
if HISTORY_FOLDER:
    parts = HISTORY_FOLDER.replace("\\", "/").split("/")
    try:
        ud_idx = next(i for i, p in enumerate(parts) if p == "userdata")
        LOCAL_STEAM_ID = int(parts[ud_idx + 1])
    except (StopIteration, IndexError, ValueError):
        pass

OUTPUT_PATH = os.path.join(os.path.expanduser("~"), "Desktop", "spire_metrics.html")

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def clean(s, prefix=""):
    s = (s or "").replace(prefix, "").strip()
    return "" if s.startswith("NONE") or s == "." else s

def fmt_time(seconds):
    if seconds is None: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"

def pct(num, den):      return round(num / den * 100) if den else 0
def avg(num, den, d=2): return round(num / den, d)    if den else 0
def li(items):          return "".join(f"<li>{i}</li>" for i in items) if items else "<li>None</li>"
def safe_avg(lst):      return round(sum(lst) / len(lst), 1) if lst else 0

# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────
def new_ledger():
    return {
        "wins": 0, "losses": 0, "abandoned": 0,
        "win_turns": 0, "win_encounters": 0,
        "loss_turns": 0, "loss_encounters": 0, "loss_floors": 0,
        "elites": 0, "campfires": 0,
        "gold_gained": 0, "gold_spent": 0, "gold_stolen": 0,
        "cards_drafted": 0, "cards_removed": 0, "cards_transformed": 0,
        "total_floors": 0, "elite_deaths": 0, "boss_deaths": 0,
        "total_max_hp_gain": 0, "total_run_time": 0,
        "card_offers": {}, "card_picks": {},
        "killers": {},
        "asc_wins": {}, "asc_losses": {},
        "relic_wins": {},
        "act_variants": {},
        # ── v2 fields ──
        "card_skips": {},
        "boss_stats": {},
        "enchantment_counts": {},
        "enchantment_wins": {},
        "win_deck_sizes": [], "loss_deck_sizes": [],
        "win_upgrade_rates": [], "loss_upgrade_rates": [],
        "win_purge_counts": [], "loss_purge_counts": [],
        "campfire_heal_hps": [],
        "act_variant_wins": {},
        "potion_waste_runs": 0,
        "total_cards_skipped": 0,
        "total_gold_at_death": 0,
        "death_floors": [],
    }

def new_mp_ledger():
    return {
        "wins": 0, "losses": 0, "abandoned": 0,
        "total_floors": 0, "total_run_time": 0,
        "total_damage": 0, "total_healed": 0,
        "elites": 0, "campfires": 0,
        "asc_wins": {}, "asc_losses": {},
        "act_variants": {},
        "party_chars": {},
        "char_wins": {},
        "char_runs": {},
        "killers": {},
    }

# ─── SHARED PARSING HELPERS ───────────────────────────────────────────────────
def _parse_deck(deck_list):
    result = []
    for card in deck_list:
        enc = card.get("enchantment") or {}
        result.append({
            "id":          card.get("id", "").replace("CARD.", ""),
            "upgrade":     card.get("current_upgrade_level", 0),
            "enchantment": enc.get("id", "").replace("ENCHANTMENT.", "") if enc else None,
            "floor":       card.get("floor_added_to_deck", 0),
        })
    return sorted(result, key=lambda c: c["floor"])

def _parse_relics(relic_list):
    result = []
    for r in relic_list:
        result.append({
            "id":    r.get("id", "").replace("RELIC.", ""),
            "floor": r.get("floor_added_to_deck", 0),
        })
    return sorted(result, key=lambda r: r["floor"])

# ─── SINGLE-PLAYER PARSER ─────────────────────────────────────────────────────
def parse_run(filepath, data=None):
    if data is None:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

    players = data.get("players", [])
    if len(players) != 1:
        return None

    p0     = players[0]
    kb_enc = clean(data.get("killed_by_encounter", ""), "ENCOUNTER.")
    kb_evt = clean(data.get("killed_by_event",     ""), "EVENT.")

    run = {
        "filepath": filepath, "filename": os.path.basename(filepath),
        "mtime":    os.path.getmtime(filepath),
        "multiplayer": False,
        "win":       data.get("win") is True,
        "abandoned": data.get("was_abandoned", False),
        "ascension": data.get("ascension", 0),
        "seed":      data.get("seed", "N/A"),
        "run_time":  data.get("run_time", 0),
        "game_mode": data.get("game_mode", "standard"),
        "build_id":  data.get("build_id", "N/A"),
        "acts":      [a.replace("ACT.", "") for a in data.get("acts", [])],
        "acts_reached": len(data.get("map_point_history", [])),
        "start_time": data.get("start_time", 0),
        "modifiers": data.get("modifiers", []),
        "kb_enc": kb_enc, "kb_evt": kb_evt,
        "char":         p0.get("character", "UNKNOWN").replace("CHARACTER.", ""),
        "final_deck":   _parse_deck(p0.get("deck", [])),
        "final_relics": _parse_relics(p0.get("relics", [])),
        "end_potions":  [p.get("id","").replace("POTION.","") for p in p0.get("potions", [])],
        "floors": 0, "encounters": 0, "turns": 0,
        "hallway_fights": 0, "elite_fights": 0, "boss_encounters": 0,
        "events": 0, "shops": 0, "treasures": 0, "campfires": 0, "elites": 0,
        "damage": 0, "healed": 0, "elite_dmg": 0, "boss_dmg": 0,
        "gold_gained": 0, "gold_spent": 0, "gold_stolen": 0,
        "max_hp_gain": 0, "max_hp_loss": 0, "start_gold": 0,
        "cards_added": 0, "cards_removed": 0,
        "cards_upgraded": 0, "cards_transformed": 0,
        "smiths": 0, "campfire_heals": 0,
        "potions_gained": 0, "potions_used": 0, "potions_discarded": 0,
        "boss_log": [], "elite_log": [], "upgrade_log": [], "transform_log": [],
        "card_offer_counts": {}, "card_pick_counts": {},
        "hp_timeline": [],
        "gold_timeline": [],
        "act_stats": [],
        "ancient_choice": None,
        "final_gold": 0, "hallway_dmg": 0,
        "died_to_elite": False, "died_to_boss": False, "death_cause": None,
        "boss_details": [],
        "elite_details": [],
        "campfire_heal_hps": [],
        "enchantments": [],
        "cards_skipped": 0,
    }

    floor_num = 0; first_stats = True; last_room = "unknown"

    for act_idx, act_floors in enumerate(data.get("map_point_history", [])):
        act_name = run["acts"][act_idx] if act_idx < len(run["acts"]) else f"ACT_{act_idx+1}"
        act_stat = {"name": act_name, "floors": 0, "encounters": 0, "turns": 0, "damage": 0, "gold": 0}

        for mp_idx, mp in enumerate(act_floors):
            act_stat["floors"] += 1
            floor_num += 1; run["floors"] += 1

            room_type = "unknown"
            is_neow = act_idx == 0 and mp_idx == 0 and any(r.get("model_id") == "EVENT.NEOW" for r in mp.get("rooms", []))
            room_monster_id = None; room_turns = 0

            for room in mp.get("rooms", []):
                room_type = room.get("room_type", "unknown")
                turns     = room.get("turns_taken", 0)
                if room_type in ("monster", "elite", "boss"):
                    run["encounters"] += 1; run["turns"] += turns
                    act_stat["encounters"] += 1; act_stat["turns"] += turns
                    m_id = room.get("monster_ids", ["UNKNOWN"])[0].replace("MONSTER.", "")
                    room_monster_id = m_id; room_turns = turns
                    if   room_type == "boss":  run["boss_encounters"] += 1; run["boss_log"].append(f"{m_id}: {turns}T")
                    elif room_type == "elite": run["elite_fights"]    += 1; run["elite_log"].append(f"{m_id}: {turns}T")
                    else:                      run["hallway_fights"]  += 1
                if   room_type == "elite":     run["elites"]    += 1
                elif room_type == "event":     run["events"]    += 1
                elif room_type == "shop":      run["shops"]     += 1
                elif room_type == "treasure":  run["treasures"] += 1
                elif room_type == "rest_site": run["campfires"] += 1
            last_room = room_type

            for stats in mp.get("player_stats", []):
                g_in = stats.get("gold_gained", 0); g_out = stats.get("gold_spent", 0)
                g_stl = stats.get("gold_stolen", 0); dmg = stats.get("damage_taken", 0)
                heald = stats.get("hp_healed", 0); cur_h = stats.get("current_hp", 0)
                max_h = stats.get("max_hp", 0); hp_g = stats.get("max_hp_gained", 0)
                hp_l  = stats.get("max_hp_lost", 0)
                cur_gold = stats.get("current_gold", 0)

                run["gold_gained"] += g_in; run["gold_spent"] += g_out; run["gold_stolen"] += g_stl
                run["damage"]      += dmg;  run["max_hp_gain"] += hp_g; run["max_hp_loss"]  += hp_l
                act_stat["damage"] += dmg;  act_stat["gold"]   += g_in

                if first_stats:
                    run["start_gold"] = cur_gold; first_stats = False
                if not is_neow:
                    run["healed"] += heald
                if   room_type == "elite": run["elite_dmg"] += dmg
                elif room_type == "boss":  run["boss_dmg"]  += dmg

                run["hp_timeline"].append((floor_num, cur_h, max_h))
                run["gold_timeline"].append((floor_num, cur_gold))

                if room_type == "boss" and room_monster_id:
                    run["boss_details"].append({"id": room_monster_id, "turns": room_turns, "damage": dmg})
                elif room_type == "elite" and room_monster_id:
                    run["elite_details"].append({"id": room_monster_id, "turns": room_turns, "damage": dmg})

                cg = stats.get("cards_gained", [])
                run["cards_added"]   += len(cg)
                run["cards_removed"] += len(stats.get("cards_removed", []))

                upgrades = stats.get("upgraded_cards", [])
                run["cards_upgraded"] += len(upgrades)
                for u in upgrades: run["upgrade_log"].append(u.replace("CARD.", ""))

                for t in stats.get("cards_transformed", []):
                    run["cards_transformed"] += 1
                    old = t.get("original_card", {}).get("id", "UNK").replace("CARD.", "")
                    new = t.get("final_card",    {}).get("id", "UNK").replace("CARD.", "")
                    run["transform_log"].append(f"{old} &rarr; {new}")

                for pc in stats.get("potion_choices", []):
                    if pc.get("was_picked"): run["potions_gained"] += 1
                run["potions_gained"]    += len(stats.get("bought_potions",   []))
                run["potions_used"]      += len(stats.get("potion_used",      []))
                run["potions_discarded"] += len(stats.get("potion_discarded", []))

                if room_type == "rest_site":
                    choices = stats.get("rest_site_choices", [])
                    if "SMITH" in choices: run["smiths"]         += 1
                    if "HEAL"  in choices:
                        run["campfire_heals"] += 1
                        if max_h > 0:
                            pre_heal_hp = cur_h - heald
                            run["campfire_heal_hps"].append(round(max(0, pre_heal_hp) / max_h * 100))

                for choice in stats.get("card_choices", []):
                    cid = choice.get("card", {}).get("id", "").replace("CARD.", "")
                    if not cid: continue
                    run["card_offer_counts"][cid] = run["card_offer_counts"].get(cid, 0) + 1
                    if choice.get("was_picked"):
                        run["card_pick_counts"][cid] = run["card_pick_counts"].get(cid, 0) + 1
                    else:
                        run["cards_skipped"] += 1

                if is_neow:
                    for ac in stats.get("ancient_choice", []):
                        if ac.get("was_chosen"):
                            run["ancient_choice"] = ac.get("TextKey", "UNKNOWN")

        run["act_stats"].append(act_stat)

    run["final_gold"]    = run["gold_timeline"][-1][1] if run["gold_timeline"] else 0
    run["hallway_dmg"]   = run["damage"] - run["elite_dmg"] - run["boss_dmg"]
    run["died_to_elite"] = not run["win"] and last_room == "elite"
    run["died_to_boss"]  = not run["win"] and last_room == "boss"

    if run["win"]:                 run["death_cause"] = None
    elif run["kb_enc"]:            run["death_cause"] = f"Killed by: {run['kb_enc']}"
    elif run["kb_evt"]:            run["death_cause"] = f"Killed by event: {run['kb_evt']}"
    elif run["died_to_elite"]:     run["death_cause"] = "Killed by Elite"
    elif run["died_to_boss"]:      run["death_cause"] = "Killed by Boss"
    else:                          run["death_cause"] = "Cause Unknown"

    run["enchantments"] = [c["enchantment"] for c in run["final_deck"] if c["enchantment"]]
    return run


# ─── MULTIPLAYER PARSER ───────────────────────────────────────────────────────
def parse_run_mp(filepath, data=None):
    if data is None:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

    players = data.get("players", [])
    if len(players) < 2:
        return None

    id_to_char  = {p.get("id"): p.get("character", "UNKNOWN").replace("CHARACTER.", "") for p in players}
    local_id    = LOCAL_STEAM_ID
    player_ids  = [p.get("id") for p in players]

    kb_enc = clean(data.get("killed_by_encounter", ""), "ENCOUNTER.")
    kb_evt = clean(data.get("killed_by_event",     ""), "EVENT.")

    def new_player_stat(pid):
        char = id_to_char.get(pid, "UNKNOWN")
        p_obj = next((p for p in players if p.get("id") == pid), {})
        return {
            "player_id": pid, "char": char, "is_local": pid == local_id,
            "final_deck":   _parse_deck(p_obj.get("deck", [])),
            "final_relics": _parse_relics(p_obj.get("relics", [])),
            "end_potions":  [p.get("id","").replace("POTION.","") for p in p_obj.get("potions", [])],
            "damage": 0, "healed": 0, "elite_dmg": 0, "boss_dmg": 0, "hallway_dmg": 0,
            "gold_gained": 0, "gold_spent": 0, "gold_stolen": 0, "final_gold": 0,
            "max_hp_gain": 0, "max_hp_loss": 0, "start_gold": 0,
            "cards_added": 0, "cards_removed": 0, "cards_upgraded": 0, "cards_transformed": 0,
            "smiths": 0, "campfire_heals": 0,
            "potions_gained": 0, "potions_used": 0, "potions_discarded": 0,
            "hp_timeline": [], "upgrade_log": [], "transform_log": [],
            "card_offer_counts": {}, "card_pick_counts": {},
            "ancient_choice": None, "_first_stats": True,
        }

    pstats = {pid: new_player_stat(pid) for pid in player_ids}

    run = {
        "filepath": filepath, "filename": os.path.basename(filepath),
        "mtime": os.path.getmtime(filepath), "multiplayer": True,
        "win": data.get("win") is True, "abandoned": data.get("was_abandoned", False),
        "ascension": data.get("ascension", 0), "seed": data.get("seed", "N/A"),
        "run_time": data.get("run_time", 0), "game_mode": data.get("game_mode", "standard"),
        "build_id": data.get("build_id", "N/A"),
        "acts": [a.replace("ACT.", "") for a in data.get("acts", [])],
        "start_time": data.get("start_time", 0),
        "kb_enc": kb_enc, "kb_evt": kb_evt,
        "player_count": len(players),
        "party_chars": [id_to_char[pid] for pid in player_ids],
        "local_char": id_to_char.get(local_id, None),
        "floors": 0, "encounters": 0, "turns": 0,
        "hallway_fights": 0, "elite_fights": 0, "boss_encounters": 0,
        "events": 0, "shops": 0, "treasures": 0, "campfires": 0, "elites": 0,
        "boss_log": [], "elite_log": [], "act_stats": [], "death_cause": None,
        "players": pstats, "player_ids": player_ids,
    }

    floor_num = 0; last_room = "unknown"

    for act_idx, act_floors in enumerate(data.get("map_point_history", [])):
        act_name = run["acts"][act_idx] if act_idx < len(run["acts"]) else f"ACT_{act_idx+1}"
        act_stat = {"name": act_name, "floors": 0, "encounters": 0, "turns": 0}

        for mp_idx, mp in enumerate(act_floors):
            act_stat["floors"] += 1; floor_num += 1; run["floors"] += 1
            room_type = "unknown"
            is_neow = act_idx == 0 and mp_idx == 0 and any(r.get("model_id") == "EVENT.NEOW" for r in mp.get("rooms", []))

            for room in mp.get("rooms", []):
                room_type = room.get("room_type", "unknown")
                turns = room.get("turns_taken", 0)
                if room_type in ("monster", "elite", "boss"):
                    run["encounters"] += 1; run["turns"] += turns
                    act_stat["encounters"] += 1; act_stat["turns"] += turns
                    m_id = room.get("monster_ids", ["UNKNOWN"])[0].replace("MONSTER.", "")
                    if   room_type == "boss":  run["boss_encounters"] += 1; run["boss_log"].append(f"{m_id}: {turns}T")
                    elif room_type == "elite": run["elite_fights"] += 1; run["elite_log"].append(f"{m_id}: {turns}T")
                    else:                      run["hallway_fights"] += 1
                if   room_type == "elite":     run["elites"] += 1
                elif room_type == "event":     run["events"] += 1
                elif room_type == "shop":      run["shops"] += 1
                elif room_type == "treasure":  run["treasures"] += 1
                elif room_type == "rest_site": run["campfires"] += 1
            last_room = room_type

            for stats in mp.get("player_stats", []):
                pid = stats.get("player_id")
                if pid not in pstats: continue
                ps = pstats[pid]
                g_in = stats.get("gold_gained", 0); g_out = stats.get("gold_spent", 0)
                g_stl = stats.get("gold_stolen", 0); dmg = stats.get("damage_taken", 0)
                heald = stats.get("hp_healed", 0); cur_h = stats.get("current_hp", 0)
                max_h = stats.get("max_hp", 0); hp_g = stats.get("max_hp_gained", 0)
                hp_l = stats.get("max_hp_lost", 0)

                ps["gold_gained"] += g_in; ps["gold_spent"] += g_out; ps["gold_stolen"] += g_stl
                ps["damage"] += dmg; ps["max_hp_gain"] += hp_g; ps["max_hp_loss"] += hp_l
                if ps["_first_stats"]:
                    ps["start_gold"] = stats.get("current_gold", 0); ps["_first_stats"] = False
                if not is_neow: ps["healed"] += heald
                if   room_type == "elite": ps["elite_dmg"] += dmg
                elif room_type == "boss":  ps["boss_dmg"]  += dmg
                ps["hp_timeline"].append((floor_num, cur_h, max_h))
                ps["cards_added"] += len(stats.get("cards_gained", []))
                ps["cards_removed"] += len(stats.get("cards_removed", []))
                for u in stats.get("upgraded_cards", []):
                    ps["cards_upgraded"] += 1; ps["upgrade_log"].append(u.replace("CARD.", ""))
                for t in stats.get("cards_transformed", []):
                    ps["cards_transformed"] += 1
                    old = t.get("original_card", {}).get("id", "UNK").replace("CARD.", "")
                    new = t.get("final_card", {}).get("id", "UNK").replace("CARD.", "")
                    ps["transform_log"].append(f"{old} &rarr; {new}")
                for pc in stats.get("potion_choices", []):
                    if pc.get("was_picked"): ps["potions_gained"] += 1
                ps["potions_gained"] += len(stats.get("bought_potions", []))
                ps["potions_used"] += len(stats.get("potion_used", []))
                ps["potions_discarded"] += len(stats.get("potion_discarded", []))
                if room_type == "rest_site":
                    choices = stats.get("rest_site_choices", [])
                    if "SMITH" in choices: ps["smiths"] += 1
                    if "HEAL"  in choices: ps["campfire_heals"] += 1
                for choice in stats.get("card_choices", []):
                    cid = choice.get("card", {}).get("id", "").replace("CARD.", "")
                    if not cid: continue
                    ps["card_offer_counts"][cid] = ps["card_offer_counts"].get(cid, 0) + 1
                    if choice.get("was_picked"):
                        ps["card_pick_counts"][cid] = ps["card_pick_counts"].get(cid, 0) + 1
                if is_neow:
                    for ac in stats.get("ancient_choice", []):
                        if ac.get("was_chosen"):
                            ps["ancient_choice"] = ac.get("TextKey", "UNKNOWN")

        run["act_stats"].append(act_stat)

    for ps in pstats.values():
        ps["hallway_dmg"] = ps["damage"] - ps["elite_dmg"] - ps["boss_dmg"]
        ps["final_gold"] = ps["start_gold"] + ps["gold_gained"] - ps["gold_spent"]
        ps.pop("_first_stats", None)

    run["died_to_elite"] = not run["win"] and last_room == "elite"
    run["died_to_boss"]  = not run["win"] and last_room == "boss"
    if run["win"]:             run["death_cause"] = None
    elif run["kb_enc"]:        run["death_cause"] = f"Killed by: {run['kb_enc']}"
    elif run["kb_evt"]:        run["death_cause"] = f"Killed by event: {run['kb_evt']}"
    elif run["died_to_elite"]: run["death_cause"] = "Killed by Elite"
    elif run["died_to_boss"]:  run["death_cause"] = "Killed by Boss"
    else:                      run["death_cause"] = "Cause Unknown"
    return run


# ─── AGGREGATION ──────────────────────────────────────────────────────────────
def aggregate(all_runs):
    ledgers = {"OVERALL": new_ledger()}
    for run in all_runs:
        char = run["char"]
        if char not in ledgers: ledgers[char] = new_ledger()
        for key in ("OVERALL", char):
            l = ledgers[key]
            if run["abandoned"] or run["encounters"] == 0:
                l["abandoned"] += 1; continue
            l["total_floors"]      += run["floors"]
            l["elites"]            += run["elites"]
            l["campfires"]         += run["campfires"]
            l["gold_gained"]       += run["gold_gained"]
            l["gold_spent"]        += run["gold_spent"]
            l["gold_stolen"]       += run["gold_stolen"]
            l["cards_drafted"]     += run["cards_added"]
            l["cards_removed"]     += run["cards_removed"]
            l["cards_transformed"] += run["cards_transformed"]
            l["total_max_hp_gain"] += run["max_hp_gain"]
            l["total_run_time"]    += run["run_time"]
            if run["died_to_elite"]: l["elite_deaths"] += 1
            if run["died_to_boss"]:  l["boss_deaths"]  += 1
            for cid, cnt in run["card_offer_counts"].items():
                l["card_offers"][cid] = l["card_offers"].get(cid, 0) + cnt
            for cid, cnt in run["card_pick_counts"].items():
                l["card_picks"][cid] = l["card_picks"].get(cid, 0) + cnt
            if not run["win"] and run["kb_enc"]:
                l["killers"][run["kb_enc"]] = l["killers"].get(run["kb_enc"], 0) + 1
            asc = run["ascension"]
            if run["win"]:
                l["wins"] += 1; l["win_turns"] += run["turns"]; l["win_encounters"] += run["encounters"]
                l["asc_wins"][asc] = l["asc_wins"].get(asc, 0) + 1
            else:
                l["losses"] += 1; l["loss_turns"] += run["turns"]; l["loss_encounters"] += run["encounters"]
                l["loss_floors"] += run["floors"]; l["asc_losses"][asc] = l["asc_losses"].get(asc, 0) + 1
            for rel in run["final_relics"]:
                rid = rel["id"]
                if rid not in l["relic_wins"]: l["relic_wins"][rid] = {"wins": 0, "runs": 0}
                l["relic_wins"][rid]["runs"] += 1
                if run["win"]: l["relic_wins"][rid]["wins"] += 1
            acts_reached = run["acts"][:run.get("acts_reached", len(run["acts"]))]
            for act_name in acts_reached:
                l["act_variants"][act_name] = l["act_variants"].get(act_name, 0) + 1

            # ── v2 aggregation ──
            for cid, offered in run["card_offer_counts"].items():
                picked = run["card_pick_counts"].get(cid, 0)
                skipped = offered - picked
                if skipped > 0:
                    l["card_skips"][cid] = l["card_skips"].get(cid, 0) + skipped
                    l["total_cards_skipped"] += skipped

            for bd in run.get("boss_details", []):
                bid = bd["id"]
                if bid not in l["boss_stats"]:
                    l["boss_stats"][bid] = {"turns": [], "damage": [], "deaths": 0, "fights": 0}
                l["boss_stats"][bid]["turns"].append(bd["turns"])
                l["boss_stats"][bid]["damage"].append(bd["damage"])
                l["boss_stats"][bid]["fights"] += 1
            if not run["win"] and run["died_to_boss"] and run.get("boss_details"):
                last_boss = run["boss_details"][-1]["id"]
                if last_boss in l["boss_stats"]:
                    l["boss_stats"][last_boss]["deaths"] += 1

            deck_size = len(run["final_deck"])
            upgrade_rate = pct(run["cards_upgraded"], deck_size) if deck_size else 0
            if run["win"]:
                l["win_deck_sizes"].append(deck_size)
                l["win_upgrade_rates"].append(upgrade_rate)
                l["win_purge_counts"].append(run["cards_removed"])
            else:
                l["loss_deck_sizes"].append(deck_size)
                l["loss_upgrade_rates"].append(upgrade_rate)
                l["loss_purge_counts"].append(run["cards_removed"])

            l["campfire_heal_hps"].extend(run.get("campfire_heal_hps", []))

            for act_name in acts_reached:
                if act_name not in l["act_variant_wins"]:
                    l["act_variant_wins"][act_name] = {"wins": 0, "runs": 0}
                l["act_variant_wins"][act_name]["runs"] += 1
                if run["win"]: l["act_variant_wins"][act_name]["wins"] += 1

            for enc in run.get("enchantments", []):
                l["enchantment_counts"][enc] = l["enchantment_counts"].get(enc, 0) + 1
            for enc in set(run.get("enchantments", [])):
                if enc not in l["enchantment_wins"]:
                    l["enchantment_wins"][enc] = {"wins": 0, "runs": 0}
                l["enchantment_wins"][enc]["runs"] += 1
                if run["win"]: l["enchantment_wins"][enc]["wins"] += 1

            if not run["win"] and run.get("end_potions"):
                l["potion_waste_runs"] += 1
            if not run["win"]:
                l["total_gold_at_death"] += run["final_gold"]
                l["death_floors"].append(run["floors"])

    return ledgers


def aggregate_mp(mp_runs):
    ledger = new_mp_ledger()
    for run in mp_runs:
        if run["abandoned"] or run["encounters"] == 0:
            ledger["abandoned"] += 1; continue
        ledger["total_floors"] += run["floors"]
        ledger["total_run_time"] += run["run_time"]
        ledger["elites"] += run["elites"]; ledger["campfires"] += run["campfires"]
        for ps in run["players"].values():
            ledger["total_damage"] += ps["damage"]; ledger["total_healed"] += ps["healed"]
        party_key = " / ".join(sorted(run["party_chars"]))
        if party_key not in ledger["party_chars"]:
            ledger["party_chars"][party_key] = {"wins": 0, "runs": 0}
        ledger["party_chars"][party_key]["runs"] += 1
        for ch in run["party_chars"]:
            ledger["char_runs"][ch] = ledger["char_runs"].get(ch, 0) + 1
        if not run["win"] and run["kb_enc"]:
            ledger["killers"][run["kb_enc"]] = ledger["killers"].get(run["kb_enc"], 0) + 1
        asc = run["ascension"]
        if run["win"]:
            ledger["wins"] += 1; ledger["asc_wins"][asc] = ledger["asc_wins"].get(asc, 0) + 1
            ledger["party_chars"][party_key]["wins"] += 1
            for ch in run["party_chars"]:
                ledger["char_wins"][ch] = ledger["char_wins"].get(ch, 0) + 1
        else:
            ledger["losses"] += 1; ledger["asc_losses"][asc] = ledger["asc_losses"].get(asc, 0) + 1
        for act_name in run["acts"]:
            ledger["act_variants"][act_name] = ledger["act_variants"].get(act_name, 0) + 1
    return ledger


# ─── RECORDS & STREAKS ───────────────────────────────────────────────────────
def compute_records(all_solo):
    valid = [r for r in all_solo if not r["abandoned"] and r["encounters"] > 0]
    if not valid: return {}
    wins = [r for r in valid if r["win"]]
    losses = [r for r in valid if not r["win"]]
    records = {}
    if wins:
        records["fastest_win"]       = min(wins, key=lambda r: r["run_time"])
        records["least_damage_win"]  = min(wins, key=lambda r: r["damage"])
        records["smallest_deck_win"] = min(wins, key=lambda r: len(r["final_deck"]))
        records["fewest_turns_win"]  = min(wins, key=lambda r: r["turns"])
        records["most_elites_win"]   = max(wins, key=lambda r: r["elites"])
        records["highest_asc_win"]   = max(wins, key=lambda r: r["ascension"])
    records["most_damage_run"]  = max(valid, key=lambda r: r["damage"])
    records["most_gold_run"]    = max(valid, key=lambda r: r["gold_gained"])
    records["longest_run"]      = max(valid, key=lambda r: r["floors"])

    sorted_runs = sorted(valid, key=lambda r: r.get("start_time", 0) or r["mtime"])
    cur_w = 0; cur_l = 0; max_w = 0; max_l = 0
    for r in sorted_runs:
        if r["win"]: cur_w += 1; cur_l = 0; max_w = max(max_w, cur_w)
        else:        cur_l += 1; cur_w = 0; max_l = max(max_l, cur_l)
    records["current_win_streak"]  = cur_w
    records["current_loss_streak"] = cur_l
    records["longest_win_streak"]  = max_w
    records["longest_loss_streak"] = max_l
    return records


# ─── FUN STATS ────────────────────────────────────────────────────────────────
def compute_fun_stats(all_solo, all_mp, solo_ledgers):
    valid = [r for r in all_solo if not r["abandoned"] and r["encounters"] > 0]
    fun = {}
    if not valid: return fun

    total_time = sum(r["run_time"] for r in valid)
    fun["total_time_played"] = fmt_time(total_time)
    fun["total_runs"] = len(valid)
    fun["total_floors_climbed"] = sum(r["floors"] for r in valid)
    fun["total_damage_taken"] = sum(r["damage"] for r in valid)
    fun["total_gold_earned"] = sum(r["gold_gained"] for r in valid)
    fun["total_cards_seen"] = sum(sum(r["card_offer_counts"].values()) for r in valid)
    fun["total_potions_chugged"] = sum(r["potions_used"] for r in valid)
    fun["total_potions_wasted"] = sum(r["potions_discarded"] for r in valid)
    fun["total_cards_skipped"] = sum(r.get("cards_skipped", 0) for r in valid)
    fun["total_encounters"] = sum(r["encounters"] for r in valid)
    fun["avg_run_time"] = fmt_time(total_time // len(valid))

    char_counts = Counter(r["char"] for r in valid)
    if char_counts:
        fav = char_counts.most_common(1)[0]
        fun["favorite_character"] = f"{fav[0]} ({fav[1]} runs)"

    wins = [r for r in valid if r["win"]]
    losses = [r for r in valid if not r["win"]]

    if wins:
        luckiest = max(wins, key=lambda r: r["damage"])
        fun["luckiest_win"] = f"{luckiest['char']} A{luckiest['ascension']} — {luckiest['damage']} dmg taken, still won"
    if losses:
        unluckiest = max(losses, key=lambda r: r["floors"])
        fun["unluckiest_loss"] = f"{unluckiest['char']} A{unluckiest['ascension']} — died floor {unluckiest['floors']}"
        fun["gold_hoarded_at_death"] = sum(r["final_gold"] for r in losses)
        potion_waste = sum(1 for r in losses if r.get("end_potions"))
        fun["died_with_potions"] = f"{potion_waste}/{len(losses)} losses"
        death_floors = [r["floors"] for r in losses]
        if death_floors:
            fc = Counter(death_floors).most_common(1)[0]
            fun["most_common_death_floor"] = f"Floor {fc[0]} ({fc[1]} deaths)"

    bad_heals = 0; total_heals = 0
    for r in valid:
        for hp_pct in r.get("campfire_heal_hps", []):
            total_heals += 1
            if hp_pct > 70: bad_heals += 1
    if total_heals:
        fun["bad_campfire_heals"] = f"{bad_heals}/{total_heals} heals at &gt;70% HP"

    longest_fight = None
    for r in valid:
        for bd in r.get("boss_details", []) + r.get("elite_details", []):
            if longest_fight is None or bd["turns"] > longest_fight["turns"]:
                longest_fight = {"id": bd["id"], "turns": bd["turns"], "char": r["char"]}
    if longest_fight:
        fun["longest_fight"] = f"{longest_fight['id']} ({longest_fight['turns']}T) as {longest_fight['char']}"

    valid_mp = [r for r in all_mp if not r["abandoned"] and r["encounters"] > 0]
    if valid_mp:
        fun["coop_runs"] = len(valid_mp)
        fun["coop_time"] = fmt_time(sum(r["run_time"] for r in valid_mp))

    return fun


# ─── HTML / CSS ───────────────────────────────────────────────────────────────
CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
    font-family: 'Courier New', monospace;
    background: #0d0d1a; color: #c8c8d8;
    padding: 24px; margin: 0; font-size: 13px;
}
h1 { border-bottom: 3px solid #c83232; color: #fff; padding-bottom: 10px; letter-spacing: 3px; font-size: 1.3em; }
h2 { margin: 0; padding: 9px 14px; color: #fff; font-size: 0.95em; letter-spacing: 1px; }
h3 { color: #888; font-size: 0.78em; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 2px; border-bottom: 1px solid #222; padding-bottom: 4px; }
.card { background: #13132a; border: 1px solid #2a2a4a; margin-bottom: 20px; overflow: hidden; }
.hdr-red    { background: #6b0000; }
.hdr-blue   { background: #002855; }
.hdr-green  { background: #004422; }
.hdr-dark   { background: #111118; border-bottom: 1px solid #2a2a4a; }
.hdr-coop   { background: #1a0a3a; border-bottom: 1px solid #4a2a7a; }
.hdr-coop-win  { background: #0a2a1a; }
.hdr-coop-loss { background: #2a0a0a; }
.hdr-fun    { background: linear-gradient(90deg, #1a0a3a 0%, #3a0a1a 50%, #0a1a3a 100%); }
.hdr-records { background: #2a1a00; border-bottom: 1px solid #554400; }
.card-body { padding: 14px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.panel { flex: 1; min-width: 220px; background: #0a0a1e; border: 1px solid #222; padding: 12px; }
.panel.coop-panel { border-color: #2a1a4a; background: #0a0718; }
.panel.local-player { border-color: #7a5a00; background: #0f0c00; }
.player-tag { display: inline-block; font-size: 0.72em; letter-spacing: 1px; padding: 1px 7px; margin-bottom: 6px; text-transform: uppercase; }
.player-tag.local { background: #2a1a00; color: #ffbb44; border: 1px solid #7a5a00; }
.player-tag.remote { background: #101028; color: #8888bb; border: 1px solid #2a2a5a; }
table.dt { width: 100%; border-collapse: collapse; font-size: 0.82em; }
.dt td, .dt th { padding: 4px 8px; border: 1px solid #1e1e3a; }
.dt tr:nth-child(even) td { background: #0f0f24; }
.dt .th { background: #1a1a3a; color: #7a7ab8; text-align: center; font-size: 0.75em; letter-spacing: 1px; }
.dt .hl td { background: #0d2640 !important; font-weight: bold; color: #fff; }
.win-b  { background: #0a3d0a; color: #5dff5d; padding: 1px 8px; border: 1px solid #5dff5d; }
.loss-b { background: #3d0a0a; color: #ff5d5d; padding: 1px 8px; border: 1px solid #ff5d5d; }
.asc-b  { background: #2a2a00; color: #ffee88; padding: 1px 8px; border: 1px solid #ffee88; margin-left: 8px; }
.mp-b   { background: #1a0a3a; color: #bb88ff; padding: 1px 8px; border: 1px solid #7a4ab8; margin-left: 8px; }
.death-banner { background: #3d0a0a; border-left: 4px solid #c83232; padding: 8px 12px; margin-bottom: 12px; color: #ff8888; font-weight: bold; }
.meta-bar { display: flex; gap: 20px; flex-wrap: wrap; padding: 8px 14px; background: #0a0a18; border-bottom: 1px solid #1e1e3a; font-size: 0.8em; }
.meta-item { color: #666; } .meta-item b { color: #aaa; }
details summary { cursor: pointer; color: #555; padding: 5px 0; font-size: 0.8em; user-select: none; }
details summary:hover { color: #aaa; } details[open] summary { color: #aaa; }
ul { margin: 4px 0; padding-left: 16px; font-size: 0.8em; }
.sparkline-wrap { margin-bottom: 6px; }
.sparkline-label { font-size: 0.72em; color: #444; margin-bottom: 3px; padding: 0 2px; }
.deck-grid { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { background: #0d0d22; border: 1px solid #2a2a50; padding: 2px 6px; font-size: 0.76em; white-space: nowrap; }
.chip.up  { border-color: #aaaa00; color: #ffff88; }
.chip.enc { border-color: #4488cc; color: #99ccff; }
.rchip { background: #1a1400; border: 1px solid #554400; padding: 2px 6px; font-size: 0.76em; display: inline-block; margin: 2px 2px 2px 0; }
.bar-bg { background: #111; height: 6px; width: 100%; }
.bar-fg { background: #c83232; height: 6px; }
table.hist { width: 100%; border-collapse: collapse; font-size: 0.8em; }
.hist th { background: #1a1a3a; padding: 5px 8px; text-align: left; border: 1px solid #2a2a4a; color: #888; cursor: pointer; user-select: none; }
.hist th:hover { color: #ccf; background: #22224a; }
.hist th::after { content: ' \\21C5'; font-size: 0.7em; opacity: 0.4; }
.hist td { padding: 4px 8px; border: 1px solid #1a1a2a; }
.hist .win-row  { background: #071a07; } .hist .loss-row { background: #1a0707; }
.hist .mp-win-row  { background: #07071a; } .hist .mp-loss-row { background: #120718; }
.fun-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.fun-stat { background: #0a0a1e; border: 1px solid #2a2a4a; padding: 10px 14px; }
.fun-stat .fun-label { font-size: 0.7em; color: #556; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 4px; }
.fun-stat .fun-value { font-size: 1.1em; color: #ddd; font-weight: bold; }
.fun-stat .fun-value.gold { color: #ffdd44; } .fun-stat .fun-value.red { color: #ff6666; }
.fun-stat .fun-value.green { color: #66ff66; } .fun-stat .fun-value.purple { color: #bb88ff; }
.tab-bar { display: flex; gap: 0; border-bottom: 2px solid #2a2a4a; margin-bottom: 16px; }
.tab-btn { padding: 8px 20px; background: #0a0a18; border: 1px solid #2a2a4a; border-bottom: none; color: #556; cursor: pointer; font-family: inherit; font-size: 0.82em; letter-spacing: 1px; text-transform: uppercase; }
.tab-btn:hover { color: #aaa; background: #13132a; }
.tab-btn.active { color: #fff; background: #13132a; border-color: #4a4a7a; }
.tab-content { display: none; } .tab-content.active { display: block; }
.warn-box { background:#2a2a00; border-left:4px solid #aaaa00; padding:6px 12px; margin-bottom:10px; color:#ffee88; font-size:0.85em; }
"""

# ─── RENDERING HELPERS ────────────────────────────────────────────────────────
def sparkline_svg(hp_timeline, width=700, height=72, color="#c83232", show_zones=False):
    if len(hp_timeline) < 2: return ""
    floors = [t[0] for t in hp_timeline]; hps = [t[1] for t in hp_timeline]; mhps = [t[2] for t in hp_timeline]
    fmin, fmax = min(floors), max(floors); hmax = max(mhps) if mhps else 1; pad = 2
    def sx(f): return pad + (f - fmin) / max(fmax - fmin, 1) * (width - 2 * pad)
    def sy(h): return height - pad - h / hmax * (height - 2 * pad)

    zones = ""
    if show_zones and hmax > 0:
        y60 = sy(hmax * 0.6); y30 = sy(hmax * 0.3); ytop = sy(hmax); ybot = height
        zones = (
            f'<rect x="0" y="{ytop:.1f}" width="{width}" height="{y60-ytop:.1f}" fill="rgba(0,100,0,0.08)"/>'
            f'<rect x="0" y="{y60:.1f}" width="{width}" height="{y30-y60:.1f}" fill="rgba(100,100,0,0.08)"/>'
            f'<rect x="0" y="{y30:.1f}" width="{width}" height="{ybot-y30:.1f}" fill="rgba(100,0,0,0.12)"/>'
            f'<line x1="0" y1="{y60:.1f}" x2="{width}" y2="{y60:.1f}" stroke="#1a3a1a" stroke-width="0.5" stroke-dasharray="3,5"/>'
            f'<line x1="0" y1="{y30:.1f}" x2="{width}" y2="{y30:.1f}" stroke="#3a1a1a" stroke-width="0.5" stroke-dasharray="3,5"/>'
        )

    mhp_pts  = " ".join(f"{sx(f):.1f},{sy(h):.1f}" for f, _, h in hp_timeline)
    hp_pts   = " ".join(f"{sx(f):.1f},{sy(h):.1f}" for f, h, _ in hp_timeline)
    area_pts = f"{sx(floors[0]):.1f},{height} " + hp_pts + f" {sx(floors[-1]):.1f},{height}"
    r, g, b  = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)

    segments = ""
    if show_zones:
        for i in range(len(hp_timeline) - 1):
            f1, h1, m1 = hp_timeline[i]; f2, h2, m2 = hp_timeline[i+1]
            hp_pct = h1 / m1 * 100 if m1 else 100
            seg_color = "#22aa22" if hp_pct > 60 else ("#aaaa22" if hp_pct > 30 else "#cc3333")
            segments += f'<line x1="{sx(f1):.1f}" y1="{sy(h1):.1f}" x2="{sx(f2):.1f}" y2="{sy(h2):.1f}" stroke="{seg_color}" stroke-width="2"/>'
    else:
        segments = f'<polyline points="{hp_pts}" fill="none" stroke="{color}" stroke-width="2"/>'

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" style="display:block;width:100%;background:#07071a;">'
        f'{zones}'
        f'<polyline points="{mhp_pts}" fill="none" stroke="#2a2a5a" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<polygon  points="{area_pts}" fill="rgba({r},{g},{b},0.15)"/>'
        f'{segments}</svg>'
    )

def gold_sparkline_svg(gold_timeline, width=700, height=50):
    if len(gold_timeline) < 2: return ""
    floors = [t[0] for t in gold_timeline]; golds = [t[1] for t in gold_timeline]
    fmin, fmax = min(floors), max(floors); gmax = max(golds) if golds else 1; pad = 2
    def sx(f): return pad + (f - fmin) / max(fmax - fmin, 1) * (width - 2 * pad)
    def sy(g): return height - pad - g / max(gmax, 1) * (height - 2 * pad)
    pts = " ".join(f"{sx(f):.1f},{sy(g):.1f}" for f, g in gold_timeline)
    area = f"{sx(floors[0]):.1f},{height} " + pts + f" {sx(floors[-1]):.1f},{height}"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" style="display:block;width:100%;background:#07071a;">'
        f'<polygon points="{area}" fill="rgba(255,200,0,0.12)"/>'
        f'<polyline points="{pts}" fill="none" stroke="#ddaa00" stroke-width="1.5"/></svg>'
    )

PLAYER_COLORS = ["#c83232", "#3278c8", "#32c878", "#c878c8"]

def deck_html(final_deck):
    chips = []
    for c in final_deck:
        label = c["id"]
        if c["upgrade"]:     label += "+" * min(c["upgrade"], 3)
        if c["enchantment"]: label += f" [{c['enchantment']}]"
        cls = "chip enc" if c["enchantment"] else ("chip up" if c["upgrade"] else "chip")
        chips.append(f'<span class="{cls}" title="Floor {c["floor"]}">{label}</span>')
    return '<div class="deck-grid">' + "".join(chips) + "</div>"

def relics_html(final_relics):
    return "".join(f'<span class="rchip" title="Floor {r["floor"]}">{r["id"]}</span>' for r in final_relics) or "<em>None</em>"

def card_pick_table(offers, picks):
    data = sorted([(cid, offers[cid], picks.get(cid, 0)) for cid in offers], key=lambda x: -x[1])
    if not data: return "<em>No card choice data.</em>"
    rows = ["<tr><th class='th'>Card</th><th class='th'>Offered</th><th class='th'>Picked</th><th class='th'>Rate</th><th class='th'></th></tr>"]
    for cid, offered, picked in data:
        rate = pct(picked, offered)
        rows.append(f"<tr><td>{cid}</td><td style='text-align:center'>{offered}</td><td style='text-align:center'>{picked}</td><td style='text-align:center'>{rate}%</td><td><div class='bar-bg'><div class='bar-fg' style='width:{rate}%'></div></div></td></tr>")
    return "<table class='dt'>" + "".join(rows) + "</table>"

def asc_table_html(asc_wins, asc_losses):
    all_ascs = sorted(set(list(asc_wins.keys()) + list(asc_losses.keys())))
    if not all_ascs: return "<em>No data yet.</em>"
    rows = ["<tr><th class='th'>Asc</th><th class='th'>W</th><th class='th'>L</th><th class='th'>Win%</th></tr>"]
    for a in all_ascs:
        w = asc_wins.get(a, 0); lo = asc_losses.get(a, 0)
        rows.append(f"<tr><td>A{a}</td><td>{w}</td><td>{lo}</td><td>{pct(w, w+lo)}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def killers_table_html(killers):
    if not killers: return "<em>No deaths logged yet.</em>"
    rows = ["<tr><th class='th'>Encounter</th><th class='th'>Deaths</th></tr>"]
    for k, cnt in sorted(killers.items(), key=lambda x: -x[1])[:10]:
        rows.append(f"<tr><td>{k}</td><td>{cnt}</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def top_picks_html(card_offers, card_picks, top_n=10):
    if not card_offers: return "<em>No data yet.</em>"
    data = sorted([(cid, card_offers.get(cid,0), card_picks.get(cid,0)) for cid in card_offers], key=lambda x: -x[2])[:top_n]
    rows = ["<tr><th class='th'>Card</th><th class='th'>Offered</th><th class='th'>Picked</th><th class='th'>Rate</th></tr>"]
    for cid, offered, picked in data:
        rows.append(f"<tr><td>{cid}</td><td>{offered}</td><td>{picked}</td><td>{pct(picked, offered)}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def top_skips_html(card_skips, card_offers, top_n=10):
    if not card_skips: return "<em>No data yet.</em>"
    data = sorted(card_skips.items(), key=lambda x: -x[1])[:top_n]
    rows = ["<tr><th class='th'>Card</th><th class='th'>Skipped</th><th class='th'>Offered</th><th class='th'>Skip%</th></tr>"]
    for cid, skipped in data:
        offered = card_offers.get(cid, skipped)
        rows.append(f"<tr><td>{cid}</td><td>{skipped}</td><td>{offered}</td><td>{pct(skipped, offered)}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def relic_wr_html(relic_wins):
    data = [(rid, d["wins"], d["runs"]) for rid, d in relic_wins.items() if d["runs"] >= 2]
    if not data: return "<em>Need 2+ runs per relic.</em>"
    rows = ["<tr><th class='th'>Relic</th><th class='th'>W/Runs</th><th class='th'>Win%</th></tr>"]
    for rid, w, r in sorted(data, key=lambda x: -pct(x[1], x[2]))[:12]:
        rows.append(f"<tr><td>{rid}</td><td>{w}/{r}</td><td>{pct(w, r)}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def act_var_html(act_variants):
    if not act_variants: return "<em>No data.</em>"
    rows = ["<tr><th class='th'>Act Variant</th><th class='th'>Times Seen</th></tr>"]
    for act, cnt in sorted(act_variants.items(), key=lambda x: -x[1]):
        rows.append(f"<tr><td>{act}</td><td>{cnt}</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def act_var_wr_html(avw):
    if not avw: return "<em>No data.</em>"
    rows = ["<tr><th class='th'>Act Variant</th><th class='th'>W/Runs</th><th class='th'>Win%</th></tr>"]
    for act, d in sorted(avw.items(), key=lambda x: -pct(x[1]["wins"], x[1]["runs"])):
        rows.append(f"<tr><td>{act}</td><td>{d['wins']}/{d['runs']}</td><td>{pct(d['wins'], d['runs'])}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def boss_difficulty_html(boss_stats):
    if not boss_stats: return "<em>No boss data yet.</em>"
    data = []
    for bid, bs in boss_stats.items():
        at = avg(sum(bs["turns"]), len(bs["turns"]), 1)
        ad = avg(sum(bs["damage"]), len(bs["damage"]), 0)
        data.append((bid, bs["fights"], at, ad, bs["deaths"], pct(bs["deaths"], bs["fights"])))
    data.sort(key=lambda x: -x[3])
    rows = ["<tr><th class='th'>Boss</th><th class='th'>Fights</th><th class='th'>Avg T</th><th class='th'>Avg Dmg</th><th class='th'>Deaths</th><th class='th'>Death%</th></tr>"]
    for bid, fights, at, ad, deaths, dr in data:
        rows.append(f"<tr><td>{bid}</td><td>{fights}</td><td>{at}</td><td>{ad}</td><td>{deaths}</td><td>{dr}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def enchantment_html(ec, ew):
    if not ec: return "<em>No enchantments found.</em>"
    rows = ["<tr><th class='th'>Enchantment</th><th class='th'>Times</th><th class='th'>Win%</th></tr>"]
    for enc, cnt in sorted(ec.items(), key=lambda x: -x[1]):
        d = ew.get(enc, {"wins": 0, "runs": 0})
        rows.append(f"<tr><td>{enc}</td><td>{cnt}</td><td>{pct(d['wins'], d['runs'])}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def win_fingerprint_html(s):
    rows = ["<tr><th class='th'>Metric</th><th class='th'>Wins</th><th class='th'>Losses</th></tr>"]
    rows.append(f"<tr><td>Avg Deck Size</td><td>{safe_avg(s['win_deck_sizes'])}</td><td>{safe_avg(s['loss_deck_sizes'])}</td></tr>")
    rows.append(f"<tr><td>Avg Upgrade Rate</td><td>{safe_avg(s['win_upgrade_rates'])}%</td><td>{safe_avg(s['loss_upgrade_rates'])}%</td></tr>")
    rows.append(f"<tr><td>Avg Cards Purged</td><td>{safe_avg(s['win_purge_counts'])}</td><td>{safe_avg(s['loss_purge_counts'])}</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"


# ─── SOLO RENDERERS ──────────────────────────────────────────────────────────
def render_latest_run(r):
    result_badge = '<span class="win-b">WIN</span>' if r["win"] else '<span class="loss-b">LOSS</span>'
    asc_badge = f'<span class="asc-b">A{r["ascension"]}</span>'
    meta = (f'<div class="meta-bar">'
        f'<span class="meta-item"><b>Seed:</b> {r["seed"]}</span>'
        f'<span class="meta-item"><b>Time:</b> {fmt_time(r["run_time"])}</span>'
        f'<span class="meta-item"><b>Mode:</b> {r["game_mode"].upper()}</span>'
        f'<span class="meta-item"><b>Acts:</b> {" &rarr; ".join(r["acts"]) or "N/A"}</span>'
        f'<span class="meta-item"><b>Build:</b> {r["build_id"]}</span></div>')

    death_banner = f'<div class="death-banner">&#9888; {r["death_cause"]}</div>' if not r["win"] and r["death_cause"] else ""
    potion_warning = ""
    if not r["win"] and r.get("end_potions"):
        potion_warning = f'<div class="warn-box">&#9888; Died with unused potions: {", ".join(r["end_potions"])}</div>'

    total_combats = r["hallway_fights"] + r["elite_fights"] + r["boss_encounters"]
    avg_ttk = avg(r["turns"], total_combats)
    potion_util = pct(r["potions_used"], r["potions_gained"])
    ancient = r["ancient_choice"] or "N/A"

    pathing_rows = (
        f"<tr><td>Hallway Fights</td><td>{r['hallway_fights']}</td></tr>"
        f"<tr><td>Elite Encounters</td><td>{r['elite_fights']}</td></tr>"
        f"<tr><td>Boss Encounters</td><td>{r['boss_encounters']}</td></tr>"
        f"<tr><td>Events</td><td>{r['events']}</td></tr>"
        f"<tr><td>Shops</td><td>{r['shops']}</td></tr>"
        f"<tr><td>Treasures</td><td>{r['treasures']}</td></tr>"
        f"<tr><td>Rest Sites</td><td>{r['campfires']}</td></tr>"
        f"<tr><td>Total Floors</td><td>{r['floors']}</td></tr>"
        f"<tr class='hl'><td>Avg. TTK</td><td>{avg_ttk} Turns</td></tr>")

    health_rows = (
        f"<tr><td>Total Damage Taken</td><td>{r['damage']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Hallway</td><td>{r['hallway_dmg']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Elite</td><td>{r['elite_dmg']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Boss</td><td>{r['boss_dmg']}</td></tr>"
        f"<tr><td>HP Restored</td><td>{r['healed']}</td></tr>"
        f"<tr><td>Max HP &#177;</td><td>+{r['max_hp_gain']} / -{r['max_hp_loss']}</td></tr>"
        f"<tr><td>Gold Gained / Spent</td><td>{r['gold_gained']} / {r['gold_spent']}</td></tr>"
        f"<tr><td>Gold Stolen</td><td>{r['gold_stolen']}</td></tr>"
        f"<tr class='hl'><td>Final Gold</td><td>{r['final_gold']}</td></tr>")

    asset_rows = (
        f"<tr><td>Ancient Gift (Neow)</td><td>{ancient}</td></tr>"
        f"<tr><td>Cards Drafted / Purged</td><td>+{r['cards_added']} / -{r['cards_removed']}</td></tr>"
        f"<tr><td>Cards Upgraded</td><td>{r['cards_upgraded']}</td></tr>"
        f"<tr><td>Cards Transformed</td><td>{r['cards_transformed']}</td></tr>"
        f"<tr><td>Campfire Smiths / Heals</td><td>{r['smiths']} / {r['campfire_heals']}</td></tr>"
        f"<tr><td>Potions Gained</td><td>{r['potions_gained']}</td></tr>"
        f"<tr><td>Potions Used / Discarded</td><td>{r['potions_used']} / {r['potions_discarded']}</td></tr>"
        f"<tr class='hl'><td>Potion Utilization</td><td>{potion_util}%</td></tr>")

    enc_list = r.get("enchantments", [])
    enc_summary = ""
    if enc_list:
        ec = Counter(enc_list)
        enc_summary = '<div style="margin-top:8px;"><b style="font-size:0.78em;color:#4488cc;">Enchantments:</b> ' + " ".join(f'<span class="chip enc">{e} x{c}</span>' for e, c in ec.most_common()) + "</div>"

    act_rows = "".join(f"<tr><td>{a['name']}</td><td>{a['floors']}</td><td>{a['encounters']}</td><td>{a['turns']}</td><td>{a['damage']}</td><td>{a['gold']}</td></tr>" for a in r["act_stats"])
    act_table = "<table class='dt'><tr><th class='th'>Act</th><th class='th'>Floors</th><th class='th'>Combats</th><th class='th'>Turns</th><th class='th'>Damage</th><th class='th'>Gold</th></tr>" + act_rows + "</table>"

    campfire_note = ""
    bad_heals = [h for h in r.get("campfire_heal_hps", []) if h > 70]
    if bad_heals:
        campfire_note = f'<div style="margin-top:6px;font-size:0.78em;color:#aaaa44;">&#9888; {len(bad_heals)} heal(s) at &gt;70% HP</div>'

    hdr_cls = "hdr-green" if r["win"] else "hdr-red"
    return f"""
<div class="card">
  <h2 class="{hdr_cls}">[SP-1] LATEST SOLO RUN AUTOPSY &mdash; {r['char']} {result_badge}{asc_badge}</h2>
  {meta}
  <div class="card-body">
    {death_banner}{potion_warning}
    <div class="row">
      <div class="panel"><h3>Pathing &amp; Velocity</h3><table class='dt'>{pathing_rows}</table></div>
      <div class="panel"><h3>Health &amp; Capital</h3><table class='dt'>{health_rows}</table></div>
      <div class="panel"><h3>Asset Lifecycle</h3><table class='dt'>{asset_rows}</table>{campfire_note}</div>
    </div>
    <div class="sparkline-wrap">
      <div class="sparkline-label">HP TIMELINE &mdash; green &gt;60% | yellow 30-60% | red &lt;30% | dashed = max HP</div>
      {sparkline_svg(r['hp_timeline'], show_zones=True)}
    </div>
    <div class="sparkline-wrap">
      <div class="sparkline-label">GOLD ECONOMY</div>
      {gold_sparkline_svg(r.get('gold_timeline', []))}
    </div>
    <div class="row">
      <div class="panel" style="flex:1;min-width:240px;"><h3>Per-Act Breakdown</h3>{act_table}</div>
      <div class="panel" style="flex:2;"><h3>Card Choices This Run</h3>{card_pick_table(r['card_offer_counts'], r['card_pick_counts'])}</div>
    </div>
    <div class="row">
      <div class="panel" style="flex:2;"><h3>Final Deck &mdash; {len(r['final_deck'])} cards</h3>{deck_html(r['final_deck'])}{enc_summary}</div>
      <div class="panel" style="flex:1;min-width:200px;"><h3>Final Relics &mdash; {len(r['final_relics'])}</h3>{relics_html(r['final_relics'])}</div>
    </div>
    <details><summary>&#9658; Combat Logs</summary><div class="row" style="margin-top:8px;"><div class="panel"><b style="font-size:.8em;">Bosses:</b><ul>{li(r['boss_log'])}</ul></div><div class="panel"><b style="font-size:.8em;">Elites:</b><ul>{li(r['elite_log'])}</ul></div></div></details>
    <details><summary>&#9658; Deck Modification Logs</summary><div class="row" style="margin-top:8px;"><div class="panel"><b style="font-size:.8em;">Upgraded:</b><ul>{li(r['upgrade_log'])}</ul></div><div class="panel"><b style="font-size:.8em;">Transformed:</b><ul>{li(r['transform_log'])}</ul></div></div></details>
  </div>
</div>"""


def render_career(name, s, section_num=""):
    v = s["wins"] + s["losses"]; wr = pct(s["wins"], v)
    wttk = avg(s["win_turns"], s["win_encounters"]); lttk = avg(s["loss_turns"], s["loss_encounters"])
    risk = avg(s["elites"], s["campfires"]); g_fl = avg(s["gold_gained"], s["total_floors"])
    purge = pct(s["cards_removed"], s["cards_drafted"])
    e_l = pct(s["elite_deaths"], s["losses"]); b_l = pct(s["boss_deaths"], s["losses"])
    hpsc = avg(s["total_max_hp_gain"], v, 1); avg_rt = fmt_time(avg(s["total_run_time"], v, 0))
    is_overall = (name == "OVERALL")
    title = "GLOBAL CAREER LEDGER" if is_overall else f"CHARACTER AUDIT: {name}"
    hdr_cls = "hdr-dark" if is_overall else "hdr-blue"
    primary = (
        f"<table class='dt'>"
        f"<tr><th class='th' colspan='2'>Primary Statistics</th></tr>"
        f"<tr><td>Valid Runs (Abandoned: {s['abandoned']})</td><td>{v}</td></tr>"
        f"<tr class='hl'><td>Win Rate</td><td>{wr}%</td></tr>"
        f"<tr><td>Win TTK / Loss TTK</td><td>{wttk}T / {lttk}T</td></tr>"
        f"<tr><td>Risk Ratio (Elites / Campfires)</td><td>{risk}</td></tr>"
        f"<tr><th class='th' colspan='2'>Advanced Metrics</th></tr>"
        f"<tr><td>Gold per Floor</td><td>{g_fl}</td></tr>"
        f"<tr><td>Deck Purge Rate</td><td>{purge}%</td></tr>"
        f"<tr><td>Elite Lethality / Boss Lethality</td><td>{e_l}% / {b_l}%</td></tr>"
        f"<tr><td>Avg Max HP Scaling</td><td>+{hpsc} HP/run</td></tr>"
        f"<tr><td>Avg Run Duration</td><td>{avg_rt}</td></tr>"
        f"<tr><td>Gold Stolen (career)</td><td>{s['gold_stolen']}</td></tr>"
        f"<tr><td>Potion Waste Deaths</td><td>{s['potion_waste_runs']}</td></tr>"
        f"</table>")
    return f"""
<div class="card">
  <h2 class="{hdr_cls}">{section_num} {title}</h2>
  <div class="card-body">
    <div class="row">
      <div class="panel" style="flex:2">{primary}</div>
      <div class="panel" style="flex:1;min-width:180px;">
        <h3>Win Rate by Ascension</h3>{asc_table_html(s['asc_wins'], s['asc_losses'])}
        <br><h3>Act Variant Win Rates</h3>{act_var_wr_html(s['act_variant_wins'])}
      </div>
    </div>
    <div class="row">
      <div class="panel" style="flex:1;"><h3>Top Killers</h3>{killers_table_html(s['killers'])}</div>
      <div class="panel" style="flex:2;"><h3>Most Picked Cards</h3>{top_picks_html(s['card_offers'], s['card_picks'])}</div>
      <div class="panel" style="flex:1;"><h3>Relic Win Rates</h3>{relic_wr_html(s['relic_wins'])}</div>
    </div>
    <div class="row">
      <div class="panel" style="flex:1;"><h3>Most Skipped Cards</h3>{top_skips_html(s['card_skips'], s['card_offers'])}</div>
      <div class="panel" style="flex:1;"><h3>Boss Difficulty</h3>{boss_difficulty_html(s['boss_stats'])}</div>
      <div class="panel" style="flex:1;">
        <h3>Win Condition Fingerprint</h3>{win_fingerprint_html(s)}
        <br><h3>Enchantment Tracker</h3>{enchantment_html(s['enchantment_counts'], s['enchantment_wins'])}
      </div>
    </div>
  </div>
</div>"""


def render_records(records):
    if not records:
        return '<div class="card"><h2 class="hdr-records">[SP-R] RECORDS &amp; STREAKS</h2><div class="card-body"><em>No data yet.</em></div></div>'

    def rf(r, stat): return f"{r['char']} A{r['ascension']} — {stat} ({r['seed']})"

    rows = ""
    for label, key, fmt_fn in [
        ("&#9889; Fastest Win", "fastest_win", lambda r: fmt_time(r["run_time"])),
        ("&#128737; Least Damage Win", "least_damage_win", lambda r: f"{r['damage']} dmg"),
        ("&#9889; Fewest Turns Win", "fewest_turns_win", lambda r: f"{r['turns']}T"),
        ("&#127183; Smallest Deck Win", "smallest_deck_win", lambda r: f"{len(r['final_deck'])} cards"),
        ("&#9876; Most Elites (Win)", "most_elites_win", lambda r: f"{r['elites']} elites"),
        ("&#11014; Highest Asc Win", "highest_asc_win", lambda r: f"A{r['ascension']}"),
        ("&#128165; Most Damage Taken", "most_damage_run", lambda r: f"{r['damage']} dmg"),
        ("&#128176; Most Gold Earned", "most_gold_run", lambda r: f"{r['gold_gained']}g"),
    ]:
        r = records.get(key)
        if r: rows += f"<tr><td>{label}</td><td>{rf(r, fmt_fn(r))}</td></tr>"

    streak_rows = (
        f"<tr><td>Current Win Streak</td><td>{records.get('current_win_streak', 0)}</td></tr>"
        f"<tr><td>Longest Win Streak</td><td>{records.get('longest_win_streak', 0)}</td></tr>"
        f"<tr><td>Current Loss Streak</td><td>{records.get('current_loss_streak', 0)}</td></tr>"
        f"<tr><td>Longest Loss Streak</td><td>{records.get('longest_loss_streak', 0)}</td></tr>")

    return f"""
<div class="card">
  <h2 class="hdr-records">[SP-R] RECORDS &amp; STREAKS</h2>
  <div class="card-body"><div class="row">
    <div class="panel" style="flex:2;"><h3>Personal Bests</h3><table class="dt">{rows}</table></div>
    <div class="panel" style="flex:1;min-width:200px;"><h3>Streak Tracking</h3><table class="dt">{streak_rows}</table></div>
  </div></div>
</div>"""


def render_fun_stats(fun):
    if not fun: return ""
    items = []
    def add(label, key, cls=""):
        if key in fun:
            items.append(f'<div class="fun-stat"><span class="fun-label">{label}</span><span class="fun-value {cls}">{fun[key]}</span></div>')
    add("Total Time in the Spire", "total_time_played", "purple")
    add("Average Run Time", "avg_run_time")
    add("Total Runs", "total_runs")
    add("Total Floors Climbed", "total_floors_climbed")
    add("Total Encounters", "total_encounters")
    add("Total Damage Absorbed", "total_damage_taken", "red")
    add("Total Gold Earned", "total_gold_earned", "gold")
    add("Total Cards Offered", "total_cards_seen")
    add("Total Cards Skipped", "total_cards_skipped")
    add("Potions Chugged", "total_potions_chugged")
    add("Potions Wasted", "total_potions_wasted", "red")
    add("Favorite Character", "favorite_character", "green")
    add("Died With Potions", "died_with_potions", "red")
    add("Gold Hoarded at Death", "gold_hoarded_at_death", "gold")
    add("Bad Campfire Heals", "bad_campfire_heals")
    add("Luckiest Win", "luckiest_win", "green")
    add("Unluckiest Loss", "unluckiest_loss", "red")
    add("Longest Fight", "longest_fight")
    add("Most Common Death Floor", "most_common_death_floor", "red")
    add("Co-op Runs", "coop_runs", "purple")
    add("Co-op Time", "coop_time", "purple")
    return f"""
<div class="card">
  <h2 class="hdr-fun">[FUN] &#127922; FUN STATS &amp; CURIOSITIES</h2>
  <div class="card-body"><div class="fun-grid">{''.join(items)}</div></div>
</div>"""


def render_history(all_runs):
    sorted_runs = sorted(all_runs, key=lambda r: -r["mtime"])
    rows = []
    for i, r in enumerate(sorted_runs):
        dt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        res = "WIN" if r["win"] else "LOSS"; cls = "win-row" if r["win"] else "loss-row"
        acts = " &rarr; ".join(r["acts"]) or "N/A"
        dc = r.get("death_cause") or "&mdash;"
        rows.append(f'<tr class="{cls}"><td>{i+1}</td><td>{dt}</td><td>{r["char"]}</td><td>A{r["ascension"]}</td><td>{res}</td><td>{r["floors"]}</td><td>{len(r["final_deck"])}</td><td>{acts}</td><td data-sort="{r["run_time"]}">{fmt_time(r["run_time"])}</td><td>{dc}</td><td>{r["seed"]}</td></tr>')
    return f"""
<div class="card">
  <h2 class="hdr-dark">[SP-3] SOLO RUN HISTORY &mdash; {len(all_runs)} runs</h2>
  <div class="card-body"><table class="hist" id="solo-hist">
    <tr><th onclick="sortTable('solo-hist',0)">#</th><th onclick="sortTable('solo-hist',1)">Date</th><th onclick="sortTable('solo-hist',2)">Char</th><th onclick="sortTable('solo-hist',3)">Asc</th><th onclick="sortTable('solo-hist',4)">Result</th><th onclick="sortTable('solo-hist',5)">Floors</th><th onclick="sortTable('solo-hist',6)">Deck</th><th onclick="sortTable('solo-hist',7)">Acts</th><th onclick="sortTable('solo-hist',8)">Time</th><th onclick="sortTable('solo-hist',9)">Death</th><th onclick="sortTable('solo-hist',10)">Seed</th></tr>
    {''.join(rows)}
  </table></div>
</div>"""


# ─── MULTIPLAYER RENDERERS ────────────────────────────────────────────────────
def render_latest_mp_run(r):
    result_badge = '<span class="win-b">WIN</span>' if r["win"] else '<span class="loss-b">LOSS</span>'
    asc_badge = f'<span class="asc-b">A{r["ascension"]}</span>'
    mp_badge = f'<span class="mp-b">CO-OP {r["player_count"]}P</span>'
    party_str = " / ".join(r["party_chars"])
    meta = (f'<div class="meta-bar"><span class="meta-item"><b>Party:</b> {party_str}</span><span class="meta-item"><b>Seed:</b> {r["seed"]}</span><span class="meta-item"><b>Time:</b> {fmt_time(r["run_time"])}</span><span class="meta-item"><b>Acts:</b> {" &rarr; ".join(r["acts"]) or "N/A"}</span><span class="meta-item"><b>Build:</b> {r["build_id"]}</span></div>')
    death_banner = f'<div class="death-banner">&#9888; {r["death_cause"]}</div>' if not r["win"] and r["death_cause"] else ""

    total_combats = r["hallway_fights"] + r["elite_fights"] + r["boss_encounters"]
    avg_ttk = avg(r["turns"], total_combats)
    shared_rows = (f"<tr><td>Hallway / Elite / Boss</td><td>{r['hallway_fights']} / {r['elite_fights']} / {r['boss_encounters']}</td></tr>"
        f"<tr><td>Events / Shops / Treasures</td><td>{r['events']} / {r['shops']} / {r['treasures']}</td></tr>"
        f"<tr><td>Rest Sites / Total Floors</td><td>{r['campfires']} / {r['floors']}</td></tr>"
        f"<tr class='hl'><td>Avg. TTK</td><td>{avg_ttk} Turns</td></tr>")

    act_rows = "".join(f"<tr><td>{a['name']}</td><td>{a['floors']}</td><td>{a['encounters']}</td><td>{a['turns']}</td></tr>" for a in r["act_stats"])
    act_table = "<table class='dt'><tr><th class='th'>Act</th><th class='th'>Fl</th><th class='th'>Cmb</th><th class='th'>T</th></tr>" + act_rows + "</table>"

    # Damage share bar
    total_party_dmg = sum(r["players"][pid]["damage"] for pid in r["player_ids"])
    dmg_bar = ""
    if total_party_dmg > 0:
        bars = "".join(f'<div style="width:{r["players"][pid]["damage"]/total_party_dmg*100:.1f}%;background:{PLAYER_COLORS[i%len(PLAYER_COLORS)]};height:16px;" title="{r["players"][pid]["char"]}: {r["players"][pid]["damage"]}"></div>' for i, pid in enumerate(r["player_ids"]))
        legend = " &bull; ".join(f'<span style="color:{PLAYER_COLORS[i%len(PLAYER_COLORS)]}">{r["players"][pid]["char"]}: {r["players"][pid]["damage"]}</span>' for i, pid in enumerate(r["player_ids"]))
        dmg_bar = f'<div style="margin:8px 0;"><div class="sparkline-label">DAMAGE TAKEN SHARE</div><div style="display:flex;width:100%;background:#111;border:1px solid #222;">{bars}</div><div style="font-size:0.7em;color:#555;margin-top:3px;">{legend}</div></div>'

    player_panels = []
    for i, pid in enumerate(r["player_ids"]):
        ps = r["players"][pid]; color = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        panel_cls = "panel local-player" if ps["is_local"] else "panel coop-panel"
        tag_cls = "player-tag local" if ps["is_local"] else "player-tag remote"
        tag_label = "&#9733; YOU" if ps["is_local"] else "teammate"
        potion_util = pct(ps["potions_used"], ps["potions_gained"])
        stat_rows = (
            f"<tr><td>Damage</td><td>{ps['damage']} (H:{ps['hallway_dmg']} E:{ps['elite_dmg']} B:{ps['boss_dmg']})</td></tr>"
            f"<tr><td>HP Restored / Max HP &#177;</td><td>{ps['healed']} / +{ps['max_hp_gain']} -{ps['max_hp_loss']}</td></tr>"
            f"<tr><td>Gold G/S/Stolen</td><td>{ps['gold_gained']}/{ps['gold_spent']}/{ps['gold_stolen']}</td></tr>"
            f"<tr class='hl'><td>Final Gold</td><td>{ps['final_gold']}</td></tr>"
            f"<tr><td>Cards +/-/U/T</td><td>+{ps['cards_added']}/-{ps['cards_removed']}/{ps['cards_upgraded']}/{ps['cards_transformed']}</td></tr>"
            f"<tr><td>Smiths / Heals</td><td>{ps['smiths']}/{ps['campfire_heals']}</td></tr>"
            f"<tr><td>Potions G/U/D</td><td>{ps['potions_gained']}/{ps['potions_used']}/{ps['potions_discarded']} ({potion_util}%)</td></tr>")
        spark = sparkline_svg(ps["hp_timeline"], height=55, color=color) if ps["hp_timeline"] else ""
        spark_block = f'<div class="sparkline-wrap"><div class="sparkline-label">HP</div>{spark}</div>' if spark else ""
        player_panels.append(
            f'<div class="{panel_cls}" style="flex:1;min-width:220px;"><span class="{tag_cls}">{tag_label}</span>'
            f'<h3 style="color:{color};">{ps["char"]}</h3>{spark_block}'
            f'<table class="dt">{stat_rows}</table>'
            f'<br><details><summary>&#9658; Deck ({len(ps["final_deck"])})</summary><div style="margin-top:6px;">{deck_html(ps["final_deck"])}</div></details>'
            f'<details><summary>&#9658; Relics ({len(ps["final_relics"])})</summary><div style="margin-top:4px;">{relics_html(ps["final_relics"])}</div></details>'
            f'<details><summary>&#9658; Card Choices</summary><div style="margin-top:4px;">{card_pick_table(ps["card_offer_counts"], ps["card_pick_counts"])}</div></details></div>')

    hdr_cls = "hdr-coop-win" if r["win"] else "hdr-coop-loss"
    return f"""
<div class="card">
  <h2 class="{hdr_cls}">[MP-1] LATEST CO-OP &mdash; {party_str} {result_badge}{asc_badge}{mp_badge}</h2>
  {meta}
  <div class="card-body">{death_banner}{dmg_bar}
    <div class="row">
      <div class="panel" style="flex:1;min-width:200px;"><h3>Shared Stats</h3><table class="dt">{shared_rows}</table><br><h3>Per-Act</h3>{act_table}<br><details><summary>&#9658; Boss Log</summary><ul>{li(r['boss_log'])}</ul></details><details><summary>&#9658; Elite Log</summary><ul>{li(r['elite_log'])}</ul></details></div>
      {''.join(player_panels)}
    </div>
  </div>
</div>"""


def render_mp_career(ledger):
    v = ledger["wins"] + ledger["losses"]; wr = pct(ledger["wins"], v)
    avg_rt = fmt_time(avg(ledger["total_run_time"], v, 0))
    primary = (f"<table class='dt'><tr><th class='th' colspan='2'>Co-op Career</th></tr>"
        f"<tr><td>Valid Runs (Aband: {ledger['abandoned']})</td><td>{v}</td></tr>"
        f"<tr class='hl'><td>Win Rate</td><td>{wr}%</td></tr>"
        f"<tr><td>Avg Duration</td><td>{avg_rt}</td></tr>"
        f"<tr><td>Avg Party Damage</td><td>{avg(ledger['total_damage'], v, 0)}</td></tr>"
        f"<tr><td>Avg Party Healing</td><td>{avg(ledger['total_healed'], v, 0)}</td></tr></table>")
    party_rows = ["<tr><th class='th'>Party</th><th class='th'>W/R</th><th class='th'>Win%</th></tr>"]
    for party, d in sorted(ledger["party_chars"].items(), key=lambda x: -pct(x[1]["wins"], x[1]["runs"])):
        party_rows.append(f"<tr><td>{party}</td><td>{d['wins']}/{d['runs']}</td><td>{pct(d['wins'], d['runs'])}%</td></tr>")
    char_rows = ["<tr><th class='th'>Char</th><th class='th'>Runs</th><th class='th'>Wins</th><th class='th'>Win%</th></tr>"]
    for ch in sorted(ledger["char_runs"].keys()):
        runs = ledger["char_runs"][ch]; wins = ledger["char_wins"].get(ch, 0)
        char_rows.append(f"<tr><td>{ch}</td><td>{runs}</td><td>{wins}</td><td>{pct(wins, runs)}%</td></tr>")
    return f"""
<div class="card"><h2 class="hdr-coop">[MP-2] CO-OP CAREER LEDGER</h2><div class="card-body">
  <div class="row"><div class="panel" style="flex:1;">{primary}</div>
    <div class="panel" style="flex:1;"><h3>Ascension Win Rate</h3>{asc_table_html(ledger['asc_wins'], ledger['asc_losses'])}<br><h3>Act Variants</h3>{act_var_html(ledger['act_variants'])}</div>
    <div class="panel" style="flex:1;"><h3>Top Killers</h3>{killers_table_html(ledger['killers'])}</div></div>
  <div class="row"><div class="panel" style="flex:1;"><h3>Party Win Rates</h3><table class='dt'>{''.join(party_rows)}</table></div>
    <div class="panel" style="flex:1;"><h3>Character Win Rates</h3><table class='dt'>{''.join(char_rows)}</table></div></div>
</div></div>"""


def render_mp_history(mp_runs):
    sorted_runs = sorted(mp_runs, key=lambda r: -r["mtime"])
    rows = []
    for i, r in enumerate(sorted_runs):
        dt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        res = "WIN" if r["win"] else "LOSS"; cls = "mp-win-row" if r["win"] else "mp-loss-row"
        party = " / ".join(r["party_chars"])
        if r.get("local_char"): party = party.replace(r["local_char"], f"<b>{r['local_char']}*</b>")
        dc = r.get("death_cause") or "&mdash;"
        rows.append(f'<tr class="{cls}"><td>{i+1}</td><td>{dt}</td><td>{party}</td><td>A{r["ascension"]}</td><td>{res}</td><td>{r["floors"]}</td><td>{" &rarr; ".join(r["acts"]) or "N/A"}</td><td data-sort="{r["run_time"]}">{fmt_time(r["run_time"])}</td><td>{dc}</td><td>{r["seed"]}</td></tr>')
    return f"""
<div class="card"><h2 class="hdr-coop">[MP-3] CO-OP HISTORY &mdash; {len(mp_runs)} runs <small style="font-weight:normal;color:#886;">(*=you)</small></h2>
  <div class="card-body"><table class="hist" id="mp-hist">
    <tr><th onclick="sortTable('mp-hist',0)">#</th><th onclick="sortTable('mp-hist',1)">Date</th><th onclick="sortTable('mp-hist',2)">Party</th><th onclick="sortTable('mp-hist',3)">Asc</th><th onclick="sortTable('mp-hist',4)">Result</th><th onclick="sortTable('mp-hist',5)">Floors</th><th onclick="sortTable('mp-hist',6)">Acts</th><th onclick="sortTable('mp-hist',7)">Time</th><th onclick="sortTable('mp-hist',8)">Death</th><th onclick="sortTable('mp-hist',9)">Seed</th></tr>
    {''.join(rows)}</table></div></div>"""


JS = """
<script>
var sortDirs = {};
function sortTable(id, col) {
  var t = document.getElementById(id); if (!t) return;
  var rows = Array.from(t.querySelectorAll('tr')).slice(1);
  var k = id+'_'+col; var asc = sortDirs[k] = !sortDirs[k];
  rows.sort(function(a,b) {
    var ac = a.cells[col], bc = b.cells[col];
    if (!ac || !bc) return 0;
    var at = ac.getAttribute('data-sort') || ac.textContent || '';
    var bt = bc.getAttribute('data-sort') || bc.textContent || '';
    var an = parseFloat(at.replace(/[^0-9.-]/g,'')), bn = parseFloat(bt.replace(/[^0-9.-]/g,''));
    if (!isNaN(an) && !isNaN(bn)) return asc ? an-bn : bn-an;
    return asc ? at.localeCompare(bt) : bt.localeCompare(at);
  });
  var tb = t.querySelector('tbody') || t;
  rows.forEach(function(r){tb.appendChild(r);});
}
function switchTab(id) {
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active');});
  document.querySelector('[data-tab="'+id+'"]').classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
}
</script>
"""

# ─── PAGE BUILDER ─────────────────────────────────────────────────────────────
def build_page(latest_solo, solo_ledgers, all_solo, latest_mp, mp_ledger, all_mp):
    records = compute_records(all_solo)
    fun = compute_fun_stats(all_solo, all_mp, solo_ledgers)

    sp = (render_latest_run(latest_solo)
        + "".join(render_career(name, solo_ledgers[name], "[SP-2]" if name == "OVERALL" else f"[SP-2.{i}]")
            for i, name in enumerate(sorted(solo_ledgers.keys(), key=lambda x: (x != "OVERALL", x))))
        + render_records(records)
        + render_history(all_solo))

    if all_mp:
        mp = render_latest_mp_run(latest_mp) + render_mp_career(mp_ledger) + render_mp_history(all_mp)
    else:
        mp = '<div class="card"><div class="card-body"><em>No multiplayer runs found yet.</em></div></div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spire-Metrics Terminal v1.3</title><style>{CSS}</style></head><body>
<h1>&#9760; SPIRE-METRICS TERMINAL <span style="font-size:0.55em;color:#555;">v1.3</span></h1>
<div class="tab-bar">
  <button class="tab-btn active" data-tab="solo" onclick="switchTab('solo')">&#9632; Solo</button>
  <button class="tab-btn" data-tab="coop" onclick="switchTab('coop')">&#9670; Co-op</button>
  <button class="tab-btn" data-tab="fun" onclick="switchTab('fun')">&#127922; Fun Stats</button>
</div>
<div id="tab-solo" class="tab-content active">{sp}</div>
<div id="tab-coop" class="tab-content">{mp}</div>
<div id="tab-fun" class="tab-content">{render_fun_stats(fun)}</div>
{JS}</body></html>"""


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        all_files = [os.path.join(HISTORY_FOLDER, f) for f in os.listdir(HISTORY_FOLDER) if f.endswith(".run")]
        if not all_files:
            raise FileNotFoundError("No .run files found. Check HISTORY_FOLDER path.")
        print(f"Parsing {len(all_files)} run file(s)...")
        all_solo = []; all_mp = []
        for path in all_files:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pc = len(data.get("players", []))
            if pc == 1:
                result = parse_run(path, data)
                if result: all_solo.append(result)
            elif pc > 1:
                result = parse_run_mp(path, data)
                if result: all_mp.append(result)
        if not all_solo:
            raise ValueError("No valid solo .run files found.")
        solo_ledgers = aggregate(all_solo)
        latest_solo = max(all_solo, key=lambda r: r["mtime"])
        mp_ledger = aggregate_mp(all_mp) if all_mp else new_mp_ledger()
        latest_mp = max(all_mp, key=lambda r: r["mtime"]) if all_mp else None
        html = build_page(latest_solo, solo_ledgers, all_solo, latest_mp, mp_ledger, all_mp)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"SUCCESS: {len(all_solo)} solo, {len(all_mp)} co-op runs processed.")
        print(f"Dashboard written to {OUTPUT_PATH}")
    except Exception:
        traceback.print_exc()
    input("\nPress Enter to exit...")
