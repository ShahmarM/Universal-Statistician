"""Build the Age of Origins upgrade tables (CSV + XLSX) from the source snapshots in ../sources.

Buildings are reconciled from two independent sources:
  * aoztips.com building calculator (current data, includes uranium and levels 39-41, no build times)
  * appmedia.jp Age of Z wiki (2021-22 snapshot, levels up to 38, includes base build times,
    prerequisites and per-level effects; also covers resource/support buildings aoztips lacks)
Every building row carries a Source and a Flag column saying how the numbers were arrived at.
Everything else (officers, equipment, titans, warplanes, antiques, merit) comes from aoztips.com.

Run: python3 age_of_origins/scripts/build_tables.py
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sources"
OUT = ROOT / "output"
RES = ["food", "oil", "steel", "minerals"]

# appmedia (JP) building name -> English in-game name. Names marked * are not confirmed in English sources.
JP_NAMES = {
    "メインホール": "Main Hall", "防御壁": "City Wall", "防衛壁": "City Wall", "防御塔": "Defense Tower",
    "倉庫": "Depot", "情報センター": "Recon Center", "外交センター": "Embassy", "指令センター": "Dispatch Center",
    "病院": "Hospital", "リハビリ拠点": "Rehab Center*", "兵舎": "Camp", "テクノロジーセンター": "Academy",
    "テクノロジー作業場": "Workshop", "農場": "Farm", "石油精製工場": "Oil Refinery", "鉄鉱山": "Steel Mill",
    "レアアース鉱山": "Mineral Mine", "訓練場": "Training Grounds", "車両工場": "Garage", "機械工場": "Factory",
    "住居": "House", "後方支援センター": "Hospital",
}
# aoztips splits the Recon Center at level 31; everything else uses the in-game name already.
AOZ_NAMES = {"Recon": "Recon Center"}

EFFECT_LABELS = {
    "採集速度": "Gathering speed", "負傷兵最大ユニット数": "Wounded capacity (units)", "ストレージ量": "Storage",
    "生産量": "Production / hour", "最大住民数": "Max residents", "レアアース保護": "Minerals protected",
    "石油保護": "Oil protected", "鉄鉱保護": "Steel protected", "食糧保護": "Food protected",
    "同盟メンバー最大駐屯数": "Max alliance reinforcement (units)", "救援回数": "Alliance helps",
    "兵士ユニット集結上限": "Rally capacity (units)", "治療速度": "Healing speed", "兵士募集加速": "Training speed",
    "最大募集数": "Max units per training batch", "最大部隊兵士ユニット": "Max units per march (fleet size)",
    "ダメージ出力": "Damage", "攻撃範囲": "Attack range", "攻撃間隔": "Attack interval", "都市防衛力": "City defense",
}
RECON_TEXT = {
    "あなたに向かってくる部隊の指揮官名を表示します。": "Shows the commander name of incoming marches",
    "あなたに向かってくる部隊の目的地を表示します。": "Shows the destination of incoming marches",
    "相手城内の具体的な資源量と未収穫量を偵察することができます。": "Scout: exact resources and uncollected resources in target city",
    "だれがその部隊を所有しているか世界地図上で確認することができます。": "Shows on the world map who owns a march",
    "あなたに向かってくる部隊の到着時間を表示します。": "Shows arrival time of incoming marches",
    "あなたに向かってくる部隊がどの座標から来ているかを表示します。": "Shows origin coordinates of incoming marches",
    "あなたに向かってくる部隊のおよそのユニット数を表示します。": "Shows approximate unit count of incoming marches",
    "相手の守備部隊のおよそのユニット数を偵察できます。": "Scout: approximate garrison unit count",
    "あなたに向かってくる部隊の指揮官の所属都市レベルを表示します。": "Shows city level of incoming commander",
    "相手防御塔レベルを偵察できます。": "Scout: defense tower levels",
    "あなたに向かってくる兵種とおよその数を教えてく表示します。": "Shows troop types and approximate numbers of incoming marches",
    "相手の守備部隊の兵種とおよその数を偵察できます。": "Scout: garrison troop types and approximate numbers",
    "相手支援部隊のおよそのユニット数を偵察できます。": "Scout: approximate reinforcement unit count",
    "あなたに向かってくる全兵種と具体的な規模を表示します。": "Shows all troop types and exact sizes of incoming marches",
    "相手支援部隊の兵種と都市レベルを偵察できます。": "Scout: reinforcement troop types and city level",
    "相手の防御壁の耐久性最大値と現在地を偵察できます。": "Scout: city wall max and current durability",
    "相手支援部隊の兵種とおよその規模を偵察できます。": "Scout: reinforcement troop types and approximate size",
    "敵方のタイタン情報を偵察できます": "Scout: enemy Titan info",
    "相手守衛部隊の兵種と具体的な規模を偵察できます。": "Scout: garrison troop types and exact size",
    "偵察行軍速度＋10％": "Scout march speed +10%",
    "相手支援部隊の全兵種と具体的な規模を偵察できます。": "Scout: all reinforcement troop types and exact size",
    "あなたに向かてくる部隊のテクノロジーセンターレベルを表示します。": "Shows Academy level of incoming commander",
    "相手の装置情報を偵察することができます。": "Scout: target's device (gear) info",
    "相手のテクノロジーセンターレベルを偵察できます。": "Scout: target's Academy level",
    "あなたに向かってくる部隊の指揮官スキルを表示します。": "Shows commander skills of incoming marches",
    "相手の副官情報を偵察することができます。": "Scout: target's officers",
    "相手のキャラテクノロジーを偵察できます": "Scout: target's commander tech",
    "ターゲットの生物化学技術レベルを偵察できます。": "Scout: target's biochemical tech level",
    "偵察速度15％アップ": "Scout speed +15%",
    "相手の副官スキル情報を偵察できます。": "Scout: target's officer skills",
    "偵察速度25％アップ": "Scout speed +25%",
    "ターゲットの拡張機器効果を偵察できます。": "Scout: target's expansion-gear effects",
}
# Buildings that legitimately share a cost table (troop-training buildings).
SAME_COST = {"Camp": ("Factory",), "Factory": ("Camp",)}
TIER_WORDS = {"初級": "Basic ", "精鋭": "Elite ", "中級": "Intermediate "}
OTHER_ITEMS = {"砲塔パーツ": "Turret Parts"}


def load(path):
    return json.loads((SRC / path).read_text(encoding="utf-8"))


def fmt_time(sec):
    if sec is None:
        return ""
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d}d {h:02}:{m:02}:{s:02}" if d else f"{h:02}:{m:02}:{s:02}"


def translate_effect(text):
    m = re.match(r"(.+?)：(.+)", text)
    if m:
        return f"{EFFECT_LABELS.get(m.group(1), m.group(1))}: {m.group(2)}"
    if text in RECON_TEXT:
        return RECON_TEXT[text]
    if text == "-":
        return ""
    for jp, en in TIER_WORDS.items():
        if text.startswith(jp):
            return f"Unlocks {en.strip()} unit ({text})"
    return f"Unlocks unit ({text})"  # higher-tier troop names: official English names not verified


def translate_req(req):
    m = re.match(r"(.+?)Lv(\d+)", req)
    return f"{JP_NAMES.get(m.group(1), m.group(1))} {m.group(2)}" if m else req


def core(res):
    return {k: res.get(k, 0) or 0 for k in RES}


def pick(field, level, a_val, j_val, agreed, base):
    """Resolve a genuine conflict: keep the candidate whose ratio to the level's food/oil best matches neighbours."""
    def ratio_at(lv):
        r = agreed.get(lv)
        base = r and (r["food"] or r["oil"])
        return r[field] / base if base and r[field] else None

    def nearest(step):
        for d in range(1, 6):
            x = ratio_at(level + step * d)
            if x:
                return x
    neigh = [x for x in (nearest(-1), nearest(1)) if x]
    if neigh and base and field not in ("food", "oil"):
        target = sum(neigh) / len(neigh)
        return min((a_val, "aoztips"), (j_val, "appmedia"), key=lambda c: abs(c[0] / base - target))
    # Single-resource line: prefer the value closer to the geometric mean of its neighbours.
    nb = [agreed[lv][field] for lv in (level - 1, level + 1) if lv in agreed and agreed[lv][field]]
    if len(nb) == 2:
        gm = (nb[0] * nb[1]) ** 0.5
        return min((a_val, "aoztips"), (j_val, "appmedia"), key=lambda c: abs(c[0] - gm))
    return (a_val, "aoztips")


def build_buildings():
    aoz = defaultdict(dict)
    for r in load("aoztips/buildings.json"):
        aoz[AOZ_NAMES.get(r["name"], r["name"])][r["level"]] = r
    jp = {}
    jp_all = {k: v for k, v in load("appmedia/facilities.json").items() if v["costs"]}
    for jname, rec in jp_all.items():
        if rec["costs"]:
            jp[JP_NAMES[jname]] = (jname, rec)

    rows, conflicts = [], []
    for name in sorted(set(aoz) | set(jp)):
        a = aoz.get(name, {})
        jname, jrec = jp.get(name, (None, None))
        jc = {int(k): v for k, v in (jrec or {}).get("costs", {}).items()}
        je = {int(k): v for k, v in (jrec or {}).get("effects", {}).items()}

        def a_core(lv):
            r = a.get(lv)
            return core(r["resources"]) if r else None

        # Detect rows aoztips lists one level too high (its level L+1 equals appmedia's level L).
        shifted = {lv for lv in jc if a_core(lv + 1) and any(a_core(lv + 1).values())
                   and a_core(lv + 1) == core(jc[lv]["resources"]) and a_core(lv) != core(jc[lv]["resources"])}
        comparable = [lv for lv in jc if a_core(lv) is not None]
        whole_shift = bool(comparable) and len(shifted) >= 0.8 * len(comparable)

        # appmedia rows that are verbatim copies of another building's row at the same level (copy-paste errors).
        copied = {lv for lv in jc if any(core(jc[lv]["resources"]).values()) and any(
            o != jname and str(lv) in r["costs"] and core(r["costs"][str(lv)]["resources"]) == core(jc[lv]["resources"])
            and JP_NAMES[o] not in SAME_COST.get(name, ()) for o, r in jp_all.items())}
        # Levels agreed by both sources feed the ratio checks used to settle conflicts.
        agreed = {lv: core(jc[lv]["resources"]) for lv in jc
                  if a_core(lv + 1 if (whole_shift or lv in shifted) else lv) == core(jc[lv]["resources"])}
        levels = sorted(set(jc) | ({lv - 1 for lv in a} if whole_shift else set(a)))
        for lv in levels:
            if lv < 1:
                continue
            a_lv = lv + 1 if (whole_shift or lv in shifted) else lv
            ar = a.get(a_lv)
            j = jc.get(lv)
            a_res = core(ar["resources"]) if ar else None
            a_blank = a_res is not None and not any(a_res.values()) and j and any(core(j["resources"]).values())
            flag, source = [], []
            if j and a_res and not a_blank:
                jres = core(j["resources"])
                if a_lv != lv:
                    flag.append(f"aoztips row offset corrected (its level {a_lv} = this level)")
                final = dict(jres)
                prev = jc.get(lv - 1)
                if a_lv == lv and a_res != jres and prev and a_res == core(prev["resources"]):
                    # aoztips repeats the previous level's cost here (tail end of an offset run).
                    flag.append(f"aoztips row duplicates level {lv - 1}; appmedia used")
                    a_res = jres
                for f in RES:
                    if a_res[f] != jres[f]:
                        if lv in copied:
                            val, who = a_res[f], "aoztips"
                        else:
                            base = (a_res["food"] or a_res["oil"]) if a_res["food"] == jres["food"] and a_res["oil"] == jres["oil"] else None
                            val, who = pick(f, lv, a_res[f], jres[f], agreed, base)
                        final[f] = val
                        conflicts.append([name, lv, f, a_res[f], jres[f], val, who])
                        flag.append(f"{f} conflict: aoztips {a_res[f]:,} vs appmedia {jres[f]:,}; kept {who}"
                                    + (" (appmedia row copies another building)" if lv in copied else ""))
                source.append("aoztips+appmedia")
                if not flag:
                    flag.append("verified: 2 sources agree")
            elif j:
                final = core(j["resources"])
                source.append("appmedia")
                flag.append("single source (appmedia 2021-22)" + ("; aoztips row blank" if a_blank else ""))
            elif ar:
                final = a_res
                source.append("aoztips")
                flag.append("single source (aoztips 2026)")
            else:
                continue
            uranium = (ar or {}).get("resources", {}).get("uranium", 0) or (j or {}).get("resources", {}).get("uranium", 0) or 0
            badges = (ar or {}).get("resources", {}).get("leader_badges", 0) or (j or {}).get("resources", {}).get("leader_badges", 0) or 0
            other = []
            for o in (j or {}).get("resources", {}).get("_other", []):
                k, _, v = o.partition(" / ")
                other.append(f"{OTHER_ITEMS.get(k, k)} x{v}")
            if j:
                req = "; ".join(translate_req(x) for x in j["requires"])
            elif ar:
                req = "; ".join(x for x in ar["requirements"].values() if x)
            else:
                req = ""
            effects = "; ".join(e for e in (translate_effect(x) for x in je.get(lv, [])) if e)
            if name.endswith("*"):
                flag.append("English building name not confirmed")
            if name == "Core Mining Station":
                flag.append("unverified: single odd row in aoztips")
            rows.append({
                "Building": name.rstrip("*"), "JP name": jname or "", "Level": lv,
                "Food": final["food"], "Oil": final["oil"], "Steel": final["steel"], "Minerals": final["minerals"],
                "Uranium": uranium, "Leader Badges": badges, "Other items": ", ".join(other),
                "Base build time": fmt_time(j["time_seconds"]) if j else "",
                "Build time (hours)": round(j["time_seconds"] / 3600, 2) if j and j["time_seconds"] is not None else "",
                "Prerequisites": req, "Effect at this level": effects,
                "Source": "+".join(source), "Flag": " | ".join(flag),
            })
    return rows, conflicts


def building_totals(rows):
    out = {}
    for r in rows:
        t = out.setdefault(r["Building"], {"Building": r["Building"], "Levels covered": "", "Max level": 0,
                                           "Food": 0, "Oil": 0, "Steel": 0, "Minerals": 0, "Uranium": 0,
                                           "Leader Badges": 0, "Base build time, levels <=38 (days)": 0.0, "_min": 999})
        for k in ("Food", "Oil", "Steel", "Minerals", "Uranium", "Leader Badges"):
            t[k] += r[k]
        if r["Build time (hours)"] != "":
            t["Base build time, levels <=38 (days)"] += r["Build time (hours)"] / 24
        t["Max level"] = max(t["Max level"], r["Level"])
        t["_min"] = min(t["_min"], r["Level"])
    for t in out.values():
        t["Levels covered"] = f'{t.pop("_min")}-{t["Max level"]}'
        t["Base build time, levels <=38 (days)"] = round(t["Base build time, levels <=38 (days)"], 1)
    return list(out.values())


def officer_tables():
    skills = [{"Skill level (to)": r["level"], "Skill XP needed": r["cost"], "Skill books": r["books"],
               "Cumulative books": r["cumulativeBooks"], "Books refunded on forget": r["forgetCost"],
               "Books lost on forget": r["booksLost"], "Loss %": r["percentLoss"]}
              for r in load("aoztips/officer_skill_levels.json")]
    st = load("aoztips/officer_stars.json")
    stars, total = [], 0
    for star in range(1, 7):
        per = st["fragments_per_grade"][str(star)]
        for g in range(1, st["grades_per_star"] + 1):
            total += per
            stars.append({"Star": star, "Grade": f"{g}/5", "Fragments for this grade": per, "Cumulative fragments": total})
    bt = load("aoztips/officer_breakthrough.json")
    fort = [{"Fortification level": r["level"], "XP needed": r["xp_needed"], "Effect %": r["effect"],
             "Multiplier": r["multiplier"] or ""} for r in bt["fortification_levels"]]
    brk = [{"Breakthrough level": r["level"], "Fragments": r["fragments"]} for r in bt["breakthrough_levels"]]
    merit = [{"Merit rank": r["rank"], "Attack %": r["attack"], "HP %": r["hp"], "Fleet (units)": r["fleet"]}
             for r in load("aoztips/merit_ranks.json")]
    return skills, stars, fort, brk, merit


def equipment_tables():
    curves = load("aoztips/equipment_cost_curves.json")
    eq = curves["equipment"]
    level_rows = []
    for cname, c in eq["curves"].items():
        cum = 0
        for i, cost in enumerate(c["costPerLevel"], 1):
            cum += cost
            level_rows.append({"Cost curve": cname, "Level (to)": i, "Alloy": cost, "Cumulative alloy": cum})
    curve_of = eq["byItem"]
    item_rows = []
    for it in load("aoztips/equipment_items.json")["items"]:
        for lv in it["levels"]:
            item_rows.append({
                "Item": it["name"], "Max level": it["maxLevel"], "Cost curve": curve_of.get(it["name"], ""),
                "Level": lv["level"], "Rank": lv["rank"], "Power": lv["power"],
                "Effects (total at level)": "; ".join(
                    f'{e["effect"]} {e["value"]}{"%" if e["unit"] == "percent" else ""}' for e in lv["effects"]),
                "Cost at this level": ", ".join(f'{c["item"]} x{c["count"]}' for c in lv["cost"]),
                "Expertise unlocked": ", ".join(lv["expertiseUnlocks"]),
            })
    ex = curves["expertise"]
    exp_rows = []
    for name, info in sorted(ex["byExpertise"].items()):
        cum = 0
        for i, cost in enumerate(ex["curves"][info["curve"]]["costPerLevel"], 1):
            cum += cost
            exp_rows.append({"Expertise": name, "Resource": info["resource"], "Level (to)": i,
                             "Cost": cost, "Cumulative": cum})
    summ = load("aoztips/equipment_summary_curves.json")
    stat_rows = [{"Level": i, "Main stat %": summ["statPctPerLevel"][i],
                  "Expertise 1 %": summ["expertise1PctPerLevel"][i] if i < len(summ["expertise1PctPerLevel"]) else ""}
                 for i in range(1, len(summ["statPctPerLevel"]))]
    return level_rows, item_rows, exp_rows, stat_rows


def titan_tables():
    lv = load("aoztips/titan_levels_evolution.json")
    levels = [{"Titan level": r["level"], "XP": r["xp"], "Cumulative XP": r["cumulativeXp"]} for r in lv["levels"]]
    evo = [{"Tier": t["tier"], "Stages": lv["evolutionStagesPerTier"],
            "Mutant DNA / stage": t["mutantDnaPerStage"], "Stored Energy Jars / stage": t["storedEnergyJarsPerStage"],
            "Mutagen Source Stones / stage": t["mutageSourceStonesPerStage"],
            "Mutant DNA / tier": t["mutantDnaPerTier"], "Stored Energy Jars / tier": t["storedEnergyJarsPerTier"],
            "Mutagen Source Stones / tier": t["mutageSourceStonesPerTier"]} for t in lv["evolutionTiers"]]
    skills = []
    for titan, lst in load("aoztips/titan_skills.json").items():
        for s in lst:
            for i in range(len(s["fluidPerLevel"])):
                skills.append({"Titan": titan, "Skill": s["name"], "Level": i + 1,
                               "Stats": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in s["stats"]),
                               "Fluid": s["fluidPerLevel"][i], "Skeleton": s["skeletonPerLevel"][i],
                               "Unlocks at": f'{s["unlocksAt"]["tier"]} stage {s["unlocksAt"]["stage"]}'})
    abilities, comps = [], []
    for titan, lst in load("aoztips/titan_components.json").items():
        for s in lst:
            for i, chips in enumerate(s["chipsPerLevel"]):
                if chips is None:
                    continue
                abilities.append({"Titan": titan, "Ability": s["name"], "Level": i + 1,
                                  "Stats": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in s["mainStats"]),
                                  "Components needed": s["componentsPerLevel"][i], "Chips": chips,
                                  "Unlocks at": f'{s["unlocksAt"]["tier"]} stage {s["unlocksAt"]["stage"]}'})
            for c in s["components"]:
                for i, alloy in enumerate(c["alloyPerLevel"]):
                    comps.append({"Titan": titan, "Ability": s["name"], "Component": c["name"], "Level": i + 1,
                                  "Stats": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in c["stats"]),
                                  "Alloy": alloy})
    return levels, evo, skills, abilities, comps


def warplane_tables():
    xp = [{"Warplane level": r["level"], "XP": r["xp"], "Cumulative XP": r["cumXp"]}
          for r in load("aoztips/warplane_xp_levels.json")["levels"]]
    evo = []
    for t in load("aoztips/warplane_evolution.json")["tiers"]:
        evo.append({"Tier": t["name"], "Stages": t["stages"], "Alloy / stage": t["alloyPerStage"],
                    "Blueprints / stage": t["blueprintPerStage"],
                    "Alloy / tier": t["stages"] * t["alloyPerStage"], "Blueprints / tier": t["stages"] * t["blueprintPerStage"]})
    names = {p["id"]: p["name"] for p in load("aoztips/warplane_levels.json")}
    parts = []
    for p in load("aoztips/warplane_levels.json"):
        comp = load("aoztips/warplane_part_components.json").get(p["name"], [])
        for r in p["levels"]:
            if r["level"] == 0:
                continue
            parts.append({"Part": p["name"], "Level": r["level"], "HP": r["hp"], "Attack": r["attack"],
                          "Defense": r["defense"],
                          "Components to reach level": comp[r["level"]] if r["level"] < len(comp) else ""})
    unlock = {(s["name"]): (p["evolution_needed"], s["unlocksAt"], s["maxBoost"])
              for p in load("aoztips/warplane_skill_summary.json") for s in p["skills"]}
    skills = []
    for p in load("aoztips/warplane_skills.json"):
        for s in p["skills"]:
            ev, at, mx = unlock.get(s["name"], ("", "", ""))
            for i, v in enumerate(s["statValues"]):
                if i == 0:
                    continue
                skills.append({"Part": names.get(p["id"], p["id"]), "Skill": s["name"], "Stat": s["stat"], "Level": i,
                               "Value": v, "Chips": s["chipsPerLevel"][i], "Cores": s["coresPerLevel"][i],
                               "Part evolution needed": ev, "Unlocks at part level": at, "Max boost": mx})
    weapons = []
    for w in load("aoztips/warplane_weapons.json"):
        for i, c in enumerate(w["components"]):
            if i == 0:
                continue
            weapons.append({"Weapon": w["name"], "Level": i, "Components": c, "Buff": w["buff"],
                            "Evolution needed": w["evolution_needed"], "Community priority": w["priority"]})
    return xp, evo, parts, skills, weapons


def antique_tables():
    seq = load("aoztips/antique_star_costs.json")
    meta = {a["id"]: a for a in load("aoztips/antiques_list.json")}
    costs = []
    for sid, s in seq["sequences"].items():
        cum = 0
        for r in s["levels"]:
            cum += r["fragments"]
            costs.append({"Sequence": sid, "Max star": s["maxStar"], "Star step": r["level"],
                          "Fragments": r["fragments"], "Cumulative fragments": cum})
    effects = []
    for a in load("aoztips/antique_effects.json")["antiques"]:
        m = meta.get(a["id"], {})
        for s in a["stars"]:
            effects.append({"Antique": a["name"], "Rarity": m.get("type", ""), "Set": m.get("set") or "",
                            "Max star": a["maxStar"], "Cost sequence": seq["byAntique"].get(a["id"], ""),
                            "Star": s["star"], "Power": s["power"],
                            "Effects (cumulative)": "; ".join(
                                f'{e["effect"]} {e["value"]}{"%" if e["unit"] == "percent" else ""}' for e in s["effects"])})
    sets = []
    for s in load("aoztips/antique_set_effects.json")["sets"]:
        for lv in s["levels"]:
            sets.append({"Set": s["name"], "Members": ", ".join(s["members"]), "Total stars across set": lv["starTotal"],
                         "Set effects": "; ".join(
                             f'{e["effect"] or "(unnamed)"} {e["value"]}{"%" if e["unit"] == "percent" else ""}'
                             for e in lv["effects"])})
    return costs, effects, sets


GAPS = [
    ("Academy research (all branches: Development, Military, Resource, Defense, etc.)",
     "No accessible public source publishes per-level research costs/values. aozcodes.com has research pages but is behind a Cloudflare bot check this environment cannot pass."),
    ("Troop tiers (training cost, stats, upkeep per T1-T12 unit)", "Not found in an accessible structured source."),
    ("Resource buildings (Farm, Oil Refinery, Steel Mill, Mineral Mine), Training Grounds, House, Rehab Center, Defense Tower",
     "Only the 2021-22 appmedia.jp snapshot (max level 30 / 10). Levels added since then are missing; costs may have been rebalanced."),
    ("Base build times", "From appmedia.jp (2021-22), and only up to level 38. Times are before construction-speed buffs. aoztips publishes no times."),
    ("Uranium costs", "Only from aoztips; appmedia predates uranium. Levels 31+ in appmedia-only rows show 0 uranium, which may be wrong."),
    ("Biochemical Lab, Elite Equipment Facility, Alliance tech, VIP, Commander skills, Battle machines, Mega devices",
     "No per-level numeric source found."),
]


def write(sheets):
    OUT.mkdir(exist_ok=True)
    (OUT / "csv").mkdir(exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="305496")
    for title, rows in sheets:
        if not rows:
            continue
        cols = list(rows[0].keys())
        with open(OUT / "csv" / f"{re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        ws = wb.create_sheet(title[:31])
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = head, fill
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in rows:
            ws.append([r[c] for c in cols])
        for i, c in enumerate(cols, 1):
            width = max([len(str(c))] + [len(str(r[c])) for r in rows[:400]])
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 70)
            if all(isinstance(r[c], int) for r in rows if r[c] != ""):
                for cell in ws[get_column_letter(i)][1:]:
                    cell.number_format = "#,##0"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.save(OUT / "age_of_origins_upgrades.xlsx")


def main():
    b_rows, conflicts = build_buildings()
    conf_rows = [{"Building": c[0], "Level": c[1], "Resource": c[2], "aoztips value": c[3], "appmedia value": c[4],
                  "Value kept": c[5], "Kept from": c[6]} for c in conflicts]
    skills, stars, fort, brk, merit = officer_tables()
    eq_levels, eq_items, eq_exp, eq_stats = equipment_tables()
    t_lv, t_evo, t_sk, t_ab, t_comp = titan_tables()
    w_xp, w_evo, w_parts, w_sk, w_wp = warplane_tables()
    a_cost, a_eff, a_sets = antique_tables()
    gaps = [{"Upgrade system": g, "Status / reason": r} for g, r in GAPS]
    sheets = [
        ("Buildings", b_rows), ("Building totals", building_totals(b_rows)), ("Building conflicts", conf_rows),
        ("Officer skill levels", skills), ("Officer stars", stars), ("Officer fortification", fort),
        ("Officer breakthrough", brk), ("Officer equipment levels", eq_levels), ("Officer equipment items", eq_items),
        ("Equipment expertise", eq_exp), ("Equipment stat curve", eq_stats), ("Titan levels", t_lv),
        ("Titan evolution", t_evo), ("Titan skills", t_sk), ("Titan abilities", t_ab), ("Titan ability components", t_comp),
        ("Warplane levels", w_xp), ("Warplane evolution", w_evo), ("Warplane parts", w_parts),
        ("Warplane skills", w_sk), ("Warplane weapons", w_wp), ("Antique star costs", a_cost),
        ("Antique effects", a_eff), ("Antique sets", a_sets), ("Merit ranks", merit), ("Gaps - not covered", gaps),
    ]
    write(sheets)
    for t, r in sheets:
        print(f"{t:30} {len(r):6} rows")


if __name__ == "__main__":
    main()
