import json
import os
import traceback
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
import winreg # For Windows registry access

import glob
# This finds the Steam path for ANY user, not just yours
steam_glob = r"C:\Program Files (x86)\Steam\userdata\*\2868840\remote\profile1\saves\history"
folders = glob.glob(steam_glob)
HISTORY_FOLDER = folders[0] if folders else ""

OUTPUT_PATH    = os.path.join(os.path.expanduser("~"), "Desktop", "spire_metrics.html")

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def clean(s, prefix=""):
    """Strip a game prefix and clean NONE sentinel values."""
    s = (s or "").replace(prefix, "").strip()
    return "" if s.startswith("NONE") or s == "." else s

def fmt_time(seconds):
    if not seconds: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"

def pct(num, den):      return round(num / den * 100) if den else 0
def avg(num, den, d=2): return round(num / den, d)    if den else 0
def li(items):          return "".join(f"<li>{i}</li>" for i in items) if items else "<li>None</li>"

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
        "card_offers": {},    # card_id -> times offered
        "card_picks":  {},    # card_id -> times picked
        "killers":     {},    # encounter_id -> death count
        "asc_wins":    {},    # ascension_level -> wins
        "asc_losses":  {},    # ascension_level -> losses
        "relic_wins":  {},    # relic_id -> {wins, runs}
        "act_variants":{},    # act_variant_name -> appearances
    }

# ─── PARSING ──────────────────────────────────────────────────────────────────
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

def parse_run(filepath):
    """Parse a single .run file into a structured dict."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    players = data.get("players", [])
    
    # Ignore multiplayer runs completely
    if len(players) > 1:
        return None
        
    p0      = players[0] if players else {}

    # Cause of death — use top-level fields first, fall back to room type
    kb_enc = clean(data.get("killed_by_encounter", ""), "ENCOUNTER.")
    kb_evt = clean(data.get("killed_by_event",     ""), "EVENT.")

    run = {
        "filepath":   filepath,
        "filename":   os.path.basename(filepath),
        "mtime":      os.path.getmtime(filepath),
        # Metadata
        "win":        data.get("win") is True,
        "abandoned":  data.get("was_abandoned", False),
        "ascension":  data.get("ascension", 0),
        "seed":       data.get("seed", "N/A"),
        "run_time":   data.get("run_time", 0),
        "game_mode":  data.get("game_mode", "standard"),
        "build_id":   data.get("build_id", "N/A"),
        "acts":       [a.replace("ACT.", "") for a in data.get("acts", [])],
        "kb_enc":     kb_enc,
        "kb_evt":     kb_evt,
        # Player
        "char":         p0.get("character", "UNKNOWN").replace("CHARACTER.", ""),
        "final_deck":   _parse_deck(p0.get("deck", [])),
        "final_relics": _parse_relics(p0.get("relics", [])),
        # Floor counters
        "floors": 0, "encounters": 0, "turns": 0,
        "hallway_fights": 0, "elite_fights": 0, "boss_encounters": 0,
        "events": 0, "shops": 0, "treasures": 0, "campfires": 0,
        "elites": 0,
        # Health/Gold
        "damage": 0, "healed": 0, "elite_dmg": 0, "boss_dmg": 0,
        "gold_gained": 0, "gold_spent": 0, "gold_stolen": 0,
        "max_hp_gain": 0, "max_hp_loss": 0, "start_gold": 0,
        # Deck ops
        "cards_added": 0, "cards_removed": 0,
        "cards_upgraded": 0, "cards_transformed": 0,
        "smiths": 0, "campfire_heals": 0,
        # Potions
        "potions_gained": 0, "potions_used": 0, "potions_discarded": 0,
        # Logs
        "boss_log": [], "elite_log": [], "upgrade_log": [], "transform_log": [],
        # Pick rates
        "card_offer_counts": {}, "card_pick_counts": {},
        # HP over time
        "hp_timeline": [],   # [(floor_num, cur_hp, max_hp), ...]
        # Per-act breakdown
        "act_stats": [],
        # Neow/ancient gift
        "ancient_choice": None,
        # Derived (filled after loop)
        "final_gold": 0, "hallway_dmg": 0,
        "died_to_elite": False, "died_to_boss": False, "death_cause": None,
    }

    floor_num   = 0
    first_stats = True
    last_room   = "unknown"

    for act_idx, act_floors in enumerate(data.get("map_point_history", [])):
        act_name = run["acts"][act_idx] if act_idx < len(run["acts"]) else f"ACT_{act_idx + 1}"
        act_stat = {"name": act_name, "floors": 0, "encounters": 0,
                    "turns": 0, "damage": 0, "gold": 0}

        for mp_idx, mp in enumerate(act_floors):
            act_stat["floors"] += 1
            floor_num          += 1
            run["floors"]      += 1

            # Determine room type and whether this is the Neow opening
            room_type = "unknown"
            is_neow = (mp_idx == 0 and
                       any(r.get("model_id") == "EVENT.NEOW" for r in mp.get("rooms", [])))

            for room in mp.get("rooms", []):
                room_type = room.get("room_type", "unknown")
                turns     = room.get("turns_taken", 0)

                if room_type in ("monster", "elite", "boss"):
                    run["encounters"]      += 1
                    run["turns"]           += turns
                    act_stat["encounters"] += 1
                    act_stat["turns"]      += turns
                    m_id = room.get("monster_ids", ["UNKNOWN"])[0].replace("MONSTER.", "")
                    if   room_type == "boss":  run["boss_encounters"] += 1; run["boss_log"].append(f"{m_id}: {turns}T")
                    elif room_type == "elite": run["elite_fights"]    += 1; run["elite_log"].append(f"{m_id}: {turns}T")
                    else:                      run["hallway_fights"]  += 1

                if   room_type == "elite":     run["elites"]    += 1
                elif room_type == "event":     run["events"]    += 1
                elif room_type == "shop":      run["shops"]     += 1
                elif room_type == "treasure":  run["treasures"] += 1
                elif room_type == "rest_site": run["campfires"] += 1

            last_room = room_type  # track after rooms loop, not inside it

            for stats in mp.get("player_stats", []):
                g_in  = stats.get("gold_gained", 0)
                g_out = stats.get("gold_spent",  0)
                g_stl = stats.get("gold_stolen", 0)
                dmg   = stats.get("damage_taken", 0)
                heald = stats.get("hp_healed",    0)
                cur_h = stats.get("current_hp",   0)
                max_h = stats.get("max_hp",       0)
                hp_g  = stats.get("max_hp_gained",0)
                hp_l  = stats.get("max_hp_lost",  0)

                run["gold_gained"]    += g_in
                run["gold_spent"]     += g_out
                run["gold_stolen"]    += g_stl
                run["damage"]         += dmg
                run["max_hp_gain"]    += hp_g
                run["max_hp_loss"]    += hp_l
                act_stat["damage"]    += dmg
                act_stat["gold"]      += g_in

                if first_stats:
                    run["start_gold"] = stats.get("current_gold", 0)
                    first_stats = False

                # Exclude Neow's full-heal from the healed total
                if not is_neow:
                    run["healed"] += heald

                if   room_type == "elite": run["elite_dmg"] += dmg
                elif room_type == "boss":  run["boss_dmg"]  += dmg

                run["hp_timeline"].append((floor_num, cur_h, max_h))

                # Cards
                cg = stats.get("cards_gained", [])
                run["cards_added"]   += len(cg)
                run["cards_removed"] += len(stats.get("cards_removed", []))

                upgrades = stats.get("upgraded_cards", [])
                run["cards_upgraded"] += len(upgrades)
                for u in upgrades:
                    run["upgrade_log"].append(u.replace("CARD.", ""))

                for t in stats.get("cards_transformed", []):
                    run["cards_transformed"] += 1
                    old = t.get("original_card", {}).get("id", "UNK").replace("CARD.", "")
                    new = t.get("final_card",    {}).get("id", "UNK").replace("CARD.", "")
                    run["transform_log"].append(f"{old} &rarr; {new}")

                # Potions
                for pc in stats.get("potion_choices", []):
                    if pc.get("was_picked"): run["potions_gained"] += 1
                run["potions_gained"]    += len(stats.get("bought_potions",    []))
                run["potions_used"]      += len(stats.get("potion_used",       []))
                run["potions_discarded"] += len(stats.get("potion_discarded",  []))

                # Campfire choices
                if room_type == "rest_site":
                    choices = stats.get("rest_site_choices", [])
                    if "SMITH" in choices: run["smiths"]         += 1
                    if "HEAL"  in choices: run["campfire_heals"] += 1

                # Card pick rates (all offer contexts: reward, shop, event)
                for choice in stats.get("card_choices", []):
                    cid = choice.get("card", {}).get("id", "").replace("CARD.", "")
                    if not cid: continue
                    run["card_offer_counts"][cid] = run["card_offer_counts"].get(cid, 0) + 1
                    if choice.get("was_picked"):
                        run["card_pick_counts"][cid] = run["card_pick_counts"].get(cid, 0) + 1

                # Ancient / Neow starting gift
                if is_neow:
                    for ac in stats.get("ancient_choice", []):
                        if ac.get("was_chosen"):
                            run["ancient_choice"] = ac.get("TextKey", "UNKNOWN")

        run["act_stats"].append(act_stat)

    # ── Derived fields ──
    run["final_gold"]    = run["start_gold"] + run["gold_gained"] - run["gold_spent"]
    run["hallway_dmg"]   = run["damage"] - run["elite_dmg"] - run["boss_dmg"]
    run["died_to_elite"] = not run["win"] and last_room == "elite"
    run["died_to_boss"]  = not run["win"] and last_room == "boss"

    if run["win"]:
        run["death_cause"] = None
    elif run["kb_enc"]:
        run["death_cause"] = f"Killed by: {run['kb_enc']}"
    elif run["kb_evt"]:
        run["death_cause"] = f"Killed by event: {run['kb_evt']}"
    elif run["died_to_elite"]:
        run["death_cause"] = "Killed by Elite"
    elif run["died_to_boss"]:
        run["death_cause"] = "Killed by Boss"
    else:
        run["death_cause"] = "Cause Unknown"

    return run


# ─── AGGREGATION ──────────────────────────────────────────────────────────────
def aggregate(all_runs):
    """Combine all parsed runs into per-character + OVERALL career ledgers."""
    ledgers = {"OVERALL": new_ledger()}

    for run in all_runs:
        char = run["char"]
        if char not in ledgers:
            ledgers[char] = new_ledger()

        for key in ("OVERALL", char):
            l = ledgers[key]

            if run["abandoned"] or run["encounters"] == 0:
                l["abandoned"] += 1
                continue

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

            # Killers (from parsed encounter name)
            if not run["win"] and run["kb_enc"]:
                l["killers"][run["kb_enc"]] = l["killers"].get(run["kb_enc"], 0) + 1

            asc = run["ascension"]
            if run["win"]:
                l["wins"]            += 1
                l["win_turns"]       += run["turns"]
                l["win_encounters"]  += run["encounters"]
                l["asc_wins"][asc]   = l["asc_wins"].get(asc, 0) + 1
            else:
                l["losses"]           += 1
                l["loss_turns"]       += run["turns"]
                l["loss_encounters"]  += run["encounters"]
                l["loss_floors"]      += run["floors"]
                l["asc_losses"][asc]  = l["asc_losses"].get(asc, 0) + 1

            for rel in run["final_relics"]:
                rid = rel["id"]
                if rid not in l["relic_wins"]:
                    l["relic_wins"][rid] = {"wins": 0, "runs": 0}
                l["relic_wins"][rid]["runs"] += 1
                if run["win"]: l["relic_wins"][rid]["wins"] += 1

            for act_name in run["acts"]:
                l["act_variants"][act_name] = l["act_variants"].get(act_name, 0) + 1

    return ledgers


# ─── HTML / CSS ───────────────────────────────────────────────────────────────
CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
    font-family: 'Courier New', monospace;
    background: #0d0d1a;
    color: #c8c8d8;
    padding: 24px;
    margin: 0;
    font-size: 13px;
}
h1 {
    border-bottom: 3px solid #c83232;
    color: #fff;
    padding-bottom: 10px;
    letter-spacing: 3px;
    font-size: 1.3em;
}
h2 {
    margin: 0 0 0 0;
    padding: 9px 14px;
    color: #fff;
    font-size: 0.95em;
    letter-spacing: 1px;
}
h3 {
    color: #888;
    font-size: 0.78em;
    margin: 0 0 8px 0;
    text-transform: uppercase;
    letter-spacing: 2px;
    border-bottom: 1px solid #222;
    padding-bottom: 4px;
}
.card {
    background: #13132a;
    border: 1px solid #2a2a4a;
    margin-bottom: 20px;
    overflow: hidden;
}
.hdr-red   { background: #6b0000; }
.hdr-blue  { background: #002855; }
.hdr-green { background: #004422; }
.hdr-dark  { background: #111118; border-bottom: 1px solid #2a2a4a; }
.card-body { padding: 14px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.panel {
    flex: 1;
    min-width: 220px;
    background: #0a0a1e;
    border: 1px solid #222;
    padding: 12px;
}
table.dt { width: 100%; border-collapse: collapse; font-size: 0.82em; }
.dt td, .dt th { padding: 4px 8px; border: 1px solid #1e1e3a; }
.dt tr:nth-child(even) td { background: #0f0f24; }
.dt .th { background: #1a1a3a; color: #7a7ab8; text-align: center;
          font-size: 0.75em; letter-spacing: 1px; }
.dt .hl td { background: #0d2640 !important; font-weight: bold; color: #fff; }
.win-b  { background: #0a3d0a; color: #5dff5d; padding: 1px 8px;
          border: 1px solid #5dff5d; }
.loss-b { background: #3d0a0a; color: #ff5d5d; padding: 1px 8px;
          border: 1px solid #ff5d5d; }
.asc-b  { background: #2a2a00; color: #ffee88; padding: 1px 8px;
          border: 1px solid #ffee88; margin-left: 8px; }
.death-banner {
    background: #3d0a0a;
    border-left: 4px solid #c83232;
    padding: 8px 12px;
    margin-bottom: 12px;
    color: #ff8888;
    font-weight: bold;
}
.meta-bar {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    padding: 8px 14px;
    background: #0a0a18;
    border-bottom: 1px solid #1e1e3a;
    font-size: 0.8em;
}
.meta-item { color: #666; }
.meta-item b { color: #aaa; }
details summary {
    cursor: pointer;
    color: #555;
    padding: 5px 0;
    font-size: 0.8em;
    user-select: none;
}
details summary:hover { color: #aaa; }
details[open] summary { color: #aaa; }
ul { margin: 4px 0; padding-left: 16px; font-size: 0.8em; }
.sparkline-wrap { margin-bottom: 12px; }
.sparkline-label { font-size: 0.72em; color: #444; margin-bottom: 3px; padding: 0 2px; }
.deck-grid { display: flex; flex-wrap: wrap; gap: 4px; }
.chip {
    background: #0d0d22;
    border: 1px solid #2a2a50;
    padding: 2px 6px;
    font-size: 0.76em;
    white-space: nowrap;
}
.chip.up  { border-color: #aaaa00; color: #ffff88; }
.chip.enc { border-color: #4488cc; color: #99ccff; }
.rchip {
    background: #1a1400;
    border: 1px solid #554400;
    padding: 2px 6px;
    font-size: 0.76em;
    display: inline-block;
    margin: 2px 2px 2px 0;
}
.bar-bg { background: #111; height: 6px; width: 100%; }
.bar-fg { background: #c83232; height: 6px; }
table.hist { width: 100%; border-collapse: collapse; font-size: 0.8em; }
.hist th { background: #1a1a3a; padding: 5px 8px; text-align: left;
           border: 1px solid #2a2a4a; color: #888; }
.hist td { padding: 4px 8px; border: 1px solid #1a1a2a; }
.hist .win-row  { background: #071a07; }
.hist .loss-row { background: #1a0707; }
"""

# ─── RENDERING HELPERS ────────────────────────────────────────────────────────
def sparkline_svg(hp_timeline, width=700, height=72):
    if len(hp_timeline) < 2: return ""
    floors = [t[0] for t in hp_timeline]
    hps    = [t[1] for t in hp_timeline]
    mhps   = [t[2] for t in hp_timeline]
    fmin, fmax = min(floors), max(floors)
    hmax       = max(mhps) if mhps else 1
    pad        = 2

    def sx(f): return pad + (f - fmin) / max(fmax - fmin, 1) * (width - 2 * pad)
    def sy(h): return height - pad - h / hmax * (height - 2 * pad)

    mhp_pts  = " ".join(f"{sx(f):.1f},{sy(h):.1f}" for f, _, h in hp_timeline)
    hp_pts   = " ".join(f"{sx(f):.1f},{sy(h):.1f}" for f, h, _ in hp_timeline)
    area_pts = f"{sx(floors[0]):.1f},{height} " + hp_pts + f" {sx(floors[-1]):.1f},{height}"

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" style="display:block;width:100%;background:#07071a;">'
        f'<polyline points="{mhp_pts}" fill="none" stroke="#2a2a5a" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<polygon  points="{area_pts}" fill="rgba(200,50,50,0.2)"/>'
        f'<polyline points="{hp_pts}"  fill="none" stroke="#c83232" stroke-width="2"/>'
        f'</svg>'
    )

def deck_html(final_deck):
    chips = []
    for c in final_deck:
        label = c["id"]
        if c["upgrade"]:     label += "+" * min(c["upgrade"], 3)
        if c["enchantment"]: label += f" [{c['enchantment']}]"
        cls   = "chip enc" if c["enchantment"] else ("chip up" if c["upgrade"] else "chip")
        floor = c["floor"]
        chips.append(f'<span class="{cls}" title="Floor {floor}">{label}</span>')
    return '<div class="deck-grid">' + "".join(chips) + "</div>"

def relics_html(final_relics):
    parts = []
    for r in final_relics:
        rid, fl = r["id"], r["floor"]
        parts.append(f'<span class="rchip" title="Floor {fl}">{rid}</span>')
    return "".join(parts) or "<em>None</em>"

def card_pick_table(offers, picks, min_offers=1, sort_by="offers"):
    """Table of card pick rates. sort_by: 'offers' or 'picks'"""
    rows = []
    key_fn = (lambda x: -x[1]) if sort_by == "offers" else (lambda x: -x[2])
    data = sorted(
        [(cid, offers[cid], picks.get(cid, 0)) for cid in offers if offers[cid] >= min_offers],
        key=key_fn
    )
    for cid, offered, picked in data:
        rate = pct(picked, offered)
        rows.append(
            f"<tr><td>{cid}</td>"
            f"<td style='text-align:center'>{offered}</td>"
            f"<td style='text-align:center'>{picked}</td>"
            f"<td style='text-align:center'>{rate}%</td>"
            f"<td style='min-width:60px'><div class='bar-bg'><div class='bar-fg' style='width:{rate}%'></div></div></td>"
            f"</tr>"
        )
    if not rows:
        return "<em>No card choice data.</em>"
    return (
        "<table class='dt'>"
        "<tr><th class='th'>Card</th><th class='th'>Offered</th>"
        "<th class='th'>Picked</th><th class='th'>Rate</th><th class='th'></th></tr>"
        + "".join(rows) + "</table>"
    )

def asc_table_html(asc_wins, asc_losses):
    all_ascs = sorted(set(list(asc_wins.keys()) + list(asc_losses.keys())))
    if not all_ascs: return "<em>No data yet.</em>"
    rows = ["<tr><th class='th'>Asc</th><th class='th'>W</th><th class='th'>L</th><th class='th'>Win%</th></tr>"]
    for asc in all_ascs:
        w = asc_wins.get(asc, 0); lo = asc_losses.get(asc, 0)
        rows.append(f"<tr><td>A{asc}</td><td>{w}</td><td>{lo}</td><td>{pct(w, w+lo)}%</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def killers_table_html(killers):
    if not killers: return "<em>No deaths logged yet.</em>"
    rows = ["<tr><th class='th'>Encounter</th><th class='th'>Deaths</th></tr>"]
    for k, cnt in sorted(killers.items(), key=lambda x: -x[1])[:10]:
        rows.append(f"<tr><td>{k}</td><td>{cnt}</td></tr>")
    return f"<table class='dt'>{''.join(rows)}</table>"

def top_picks_html(card_offers, card_picks, top_n=10):
    if not card_offers: return "<em>No data yet.</em>"
    data = sorted(
        [(cid, card_offers.get(cid, 0), card_picks.get(cid, 0)) for cid in card_offers],
        key=lambda x: -x[2]
    )[:top_n]
    rows = ["<tr><th class='th'>Card</th><th class='th'>Offered</th><th class='th'>Picked</th><th class='th'>Rate</th></tr>"]
    for cid, offered, picked in data:
        rows.append(f"<tr><td>{cid}</td><td>{offered}</td><td>{picked}</td><td>{pct(picked, offered)}%</td></tr>")
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


# ─── SECTION RENDERERS ────────────────────────────────────────────────────────
def render_latest_run(r):
    result_badge = f'<span class="win-b">WIN</span>' if r["win"] else f'<span class="loss-b">LOSS</span>'
    asc_badge    = f'<span class="asc-b">A{r["ascension"]}</span>'

    meta = (
        f'<div class="meta-bar">'
        f'<span class="meta-item"><b>Seed:</b> {r["seed"]}</span>'
        f'<span class="meta-item"><b>Time:</b> {fmt_time(r["run_time"])}</span>'
        f'<span class="meta-item"><b>Mode:</b> {r["game_mode"].upper()}</span>'
        f'<span class="meta-item"><b>Acts:</b> {" &rarr; ".join(r["acts"]) or "N/A"}</span>'
        f'<span class="meta-item"><b>Build:</b> {r["build_id"]}</span>'
        f'</div>'
    )

    death_banner = ""
    if not r["win"] and r["death_cause"]:
        death_banner = f'<div class="death-banner">&#9888; {r["death_cause"]}</div>'

    total_combats = r["hallway_fights"] + r["elite_fights"] + r["boss_encounters"]
    avg_ttk = avg(r["turns"], total_combats)
    potion_util = pct(r["potions_used"] + r["potions_discarded"], r["potions_gained"])
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
        f"<tr class='hl'><td>Avg. TTK</td><td>{avg_ttk} Turns</td></tr>"
    )

    health_rows = (
        f"<tr><td>Total Damage Taken</td><td>{r['damage']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Hallway</td><td>{r['hallway_dmg']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Elite</td><td>{r['elite_dmg']}</td></tr>"
        f"<tr><td>&nbsp; &#8627; Boss</td><td>{r['boss_dmg']}</td></tr>"
        f"<tr><td>HP Restored</td><td>{r['healed']}</td></tr>"
        f"<tr><td>Max HP &#177;</td><td>+{r['max_hp_gain']} / -{r['max_hp_loss']}</td></tr>"
        f"<tr><td>Gold Gained / Spent</td><td>{r['gold_gained']} / {r['gold_spent']}</td></tr>"
        f"<tr><td>Gold Stolen by Enemies</td><td>{r['gold_stolen']}</td></tr>"
        f"<tr class='hl'><td>Final Gold</td><td>{r['final_gold']}</td></tr>"
    )

    asset_rows = (
        f"<tr><td>Ancient Gift (Neow)</td><td>{ancient}</td></tr>"
        f"<tr><td>Cards Drafted / Purged</td><td>+{r['cards_added']} / -{r['cards_removed']}</td></tr>"
        f"<tr><td>Cards Upgraded</td><td>{r['cards_upgraded']}</td></tr>"
        f"<tr><td>Cards Transformed</td><td>{r['cards_transformed']}</td></tr>"
        f"<tr><td>Campfire Smiths / Heals</td><td>{r['smiths']} / {r['campfire_heals']}</td></tr>"
        f"<tr><td>Potions Gained</td><td>{r['potions_gained']}</td></tr>"
        f"<tr><td>Potions Used / Discarded</td><td>{r['potions_used']} / {r['potions_discarded']}</td></tr>"
        f"<tr class='hl'><td>Potion Utilization</td><td>{potion_util}%</td></tr>"
    )

    act_rows = "".join(
        f"<tr><td>{a['name']}</td><td>{a['floors']}</td><td>{a['encounters']}</td>"
        f"<td>{a['turns']}</td><td>{a['damage']}</td><td>{a['gold']}</td></tr>"
        for a in r["act_stats"]
    )
    act_table = (
        "<table class='dt'>"
        "<tr><th class='th'>Act</th><th class='th'>Floors</th><th class='th'>Combats</th>"
        "<th class='th'>Turns</th><th class='th'>Damage</th><th class='th'>Gold</th></tr>"
        + act_rows + "</table>"
    )

    return f"""
<div class="card">
  <h2 class="hdr-{'green' if r['win'] else 'red'}">[1] LATEST RUN AUTOPSY &mdash; {r['char']} {result_badge}{asc_badge}</h2>
  {meta}
  <div class="card-body">
    {death_banner}
    <div class="row">
      <div class="panel"><h3>Pathing &amp; Velocity</h3><table class='dt'>{pathing_rows}</table></div>
      <div class="panel"><h3>Health &amp; Capital</h3><table class='dt'>{health_rows}</table></div>
      <div class="panel"><h3>Asset Lifecycle</h3><table class='dt'>{asset_rows}</table></div>
    </div>
    <div class="sparkline-wrap">
      <div class="sparkline-label">HP TIMELINE &mdash; red = current HP &nbsp;|&nbsp; dashed = max HP pool &nbsp;|&nbsp; each point = one floor</div>
      {sparkline_svg(r['hp_timeline'])}
    </div>
    <div class="row">
      <div class="panel" style="flex:1;min-width:240px;"><h3>Per-Act Breakdown</h3>{act_table}</div>
      <div class="panel" style="flex:2;"><h3>Card Choices This Run</h3>{card_pick_table(r['card_offer_counts'], r['card_pick_counts'])}</div>
    </div>
    <div class="row">
      <div class="panel" style="flex:2;"><h3>Final Deck &mdash; {len(r['final_deck'])} cards &nbsp; (gold = upgraded &nbsp;|&nbsp; blue = enchanted)</h3>{deck_html(r['final_deck'])}</div>
      <div class="panel" style="flex:1;min-width:200px;"><h3>Final Relics &mdash; {len(r['final_relics'])}</h3>{relics_html(r['final_relics'])}</div>
    </div>
    <details><summary>&#9658; Combat Logs</summary>
      <div class="row" style="margin-top:8px;">
        <div class="panel"><b style="font-size:.8em;">Bosses:</b><ul>{li(r['boss_log'])}</ul></div>
        <div class="panel"><b style="font-size:.8em;">Elites:</b><ul>{li(r['elite_log'])}</ul></div>
      </div>
    </details>
    <details><summary>&#9658; Deck Modification Logs</summary>
      <div class="row" style="margin-top:8px;">
        <div class="panel"><b style="font-size:.8em;">Upgraded:</b><ul>{li(r['upgrade_log'])}</ul></div>
        <div class="panel"><b style="font-size:.8em;">Transformed:</b><ul>{li(r['transform_log'])}</ul></div>
      </div>
    </details>
  </div>
</div>"""


def render_career(name, s, section_num=""):
    v    = s["wins"] + s["losses"]
    wr   = pct(s["wins"], v)
    wttk = avg(s["win_turns"],  s["win_encounters"])
    lttk = avg(s["loss_turns"], s["loss_encounters"])
    risk = avg(s["elites"],     s["campfires"])
    g_fl = avg(s["gold_gained"],  s["total_floors"])
    purge= pct(s["cards_removed"], s["cards_drafted"])
    e_l  = pct(s["elite_deaths"], s["losses"])
    b_l  = pct(s["boss_deaths"],  s["losses"])
    hpsc = avg(s["total_max_hp_gain"], v, 1)
    avg_rt = fmt_time(avg(s["total_run_time"], v, 0))

    is_overall = (name == "OVERALL")
    title      = "GLOBAL CAREER LEDGER" if is_overall else f"CHARACTER AUDIT: {name}"
    hdr_cls    = "hdr-dark" if is_overall else "hdr-blue"

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
        f"<tr><td>Gold Stolen (career total)</td><td>{s['gold_stolen']}</td></tr>"
        f"</table>"
    )

    return f"""
<div class="card">
  <h2 class="{hdr_cls}">{section_num} {title}</h2>
  <div class="card-body">
    <div class="row">
      <div class="panel" style="flex:2">{primary}</div>
      <div class="panel" style="flex:1;min-width:180px;">
        <h3>Win Rate by Ascension</h3>{asc_table_html(s['asc_wins'], s['asc_losses'])}
        <br><h3>Act Variant Exposure</h3>{act_var_html(s['act_variants'])}
      </div>
    </div>
    <div class="row">
      <div class="panel" style="flex:1;min-width:200px;"><h3>Top Killers</h3>{killers_table_html(s['killers'])}</div>
      <div class="panel" style="flex:2;"><h3>Most Picked Cards</h3>{top_picks_html(s['card_offers'], s['card_picks'])}</div>
      <div class="panel" style="flex:1;min-width:200px;"><h3>Relic Win Rates</h3>{relic_wr_html(s['relic_wins'])}</div>
    </div>
  </div>
</div>"""


def render_history(all_runs):
    sorted_runs = sorted(all_runs, key=lambda r: -r["mtime"])
    rows = []
    for i, r in enumerate(sorted_runs):
        dt   = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        res  = "WIN" if r["win"] else "LOSS"
        cls  = "win-row" if r["win"] else "loss-row"
        acts = " &rarr; ".join(r["acts"]) if r["acts"] else "N/A"
        dc   = r.get("death_cause") or "&mdash;"
        rows.append(
            f'<tr class="{cls}">'
            f"<td>{i+1}</td><td>{dt}</td><td>{r['char']}</td>"
            f"<td>A{r['ascension']}</td><td>{res}</td>"
            f"<td>{r['floors']}</td><td>{acts}</td>"
            f"<td>{fmt_time(r['run_time'])}</td>"
            f"<td>{dc}</td><td>{r['seed']}</td>"
            f"</tr>"
        )
    return f"""
<div class="card">
  <h2 class="hdr-dark">[3] RUN HISTORY &mdash; {len(all_runs)} total runs</h2>
  <div class="card-body">
    <table class="hist">
      <tr>
        <th>#</th><th>Date</th><th>Char</th><th>Asc</th><th>Result</th>
        <th>Floors</th><th>Acts</th><th>Time</th><th>Death Cause</th><th>Seed</th>
      </tr>
      {''.join(rows)}
    </table>
  </div>
</div>"""


def build_page(latest_run, ledgers, all_runs):
    sr_html      = render_latest_run(latest_run)
    history_html = render_history(all_runs)

    career_sections = []
    for i, name in enumerate(sorted(ledgers.keys(), key=lambda x: (x != "OVERALL", x))):
        section_label = "[2]" if name == "OVERALL" else f"[2.{i}]"
        career_sections.append(render_career(name, ledgers[name], section_label))
    career_html = "\n".join(career_sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spire-Metrics Terminal</title>
<style>{CSS}</style>
</head>
<body>
<h1>&#9760; SPIRE-METRICS TERMINAL</h1>
{sr_html}
{career_html}
{history_html}
</body>
</html>"""


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        all_files = [
            os.path.join(HISTORY_FOLDER, f)
            for f in os.listdir(HISTORY_FOLDER)
            if f.endswith(".run")
        ]
        if not all_files:
            raise FileNotFoundError("No .run files found in history folder. Check HISTORY_FOLDER path.")

        print(f"Parsing {len(all_files)} run file(s)...")
        
        # 1. Parse everything first, filtering out multiplayer runs (which return None)
        parsed_files = [parse_run(p) for p in all_files]
        all_runs = [r for r in parsed_files if r is not None]

        if not all_runs:
            raise ValueError("No valid single-player .run files found to process.")

        # 2. Build the career aggregations
        ledgers = aggregate(all_runs)
        
        # 3. Find the newest valid single-player run by checking the mtime directly
        latest_run = max(all_runs, key=lambda r: r["mtime"])

        # 4. Generate the dashboard
        html = build_page(latest_run, ledgers, all_runs)

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"SUCCESS: Dashboard written to {OUTPUT_PATH}")

    except Exception:
        traceback.print_exc()

    input("\nPress Enter to exit...")
