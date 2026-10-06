"""Full per-step upgrade tables for officers, titans, warplanes and antiques.

Each row is one upgrade step: the cost of that step, the running total, and the value reached.
A `Value basis` column says whether a value is published, derived from a published curve, or interpolated.
"""
import json
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "sources" / "aoztips"


def load(name):
    return json.loads((SRC / name).read_text(encoding="utf-8"))


def fmt(e):
    return f'{e["effect"]} {e["value"]:g}{"%" if e["unit"] == "percent" else ""}'


# ---------------------------------------------------------------- officers

def officer_roster():
    off = {o["id"]: o for o in load("officers__e_0.json")["officers"]} if (SRC / "officers__e_0.json").exists() else {}
    rows = []
    for o in load("officer_equipment_map.json"):
        oid = o["officerId"]
        typ, name = oid.split("_", 1)
        for slot, it in enumerate(o["items"], 1):
            rows.append({
                "Officer": off.get(oid, {}).get("name") or name.replace("_", " ").title(), "Type": typ.title(),
                "Equipment slot": slot, "Item": it["name"],
                "Stat 1": it["stat1"], "Stat 2": it["stat2"], "Stat 3 (from level 5)": it["stat3"],
                "Expertise 1 (from level 10)": it["expertise1"], "Expertise 2 (from level 15)": it["expertise2"],
            })
    return rows


def officer_stars():
    st = load("officer_stars.json")
    rows, total = [], 0
    for star in range(1, 7):
        per = st["fragments_per_grade"][str(star)]
        for g in range(1, st["grades_per_star"] + 1):
            total += per
            rows.append({"Step": f"★{star} grade {g}/5", "Star": star, "Grade": g, "Fragments (this step)": per,
                         "Fragments (cumulative)": total,
                         "Value basis": "cost published; star skill values not published"})
    return rows


def officer_skills():
    rows, xp = [], 0
    for r in load("officer_skill_levels.json"):
        xp += r["cost"]
        rows.append({"Skill level (to)": r["level"], "Skill XP (this step)": r["cost"], "Skill XP (cumulative)": xp,
                     "Skill books (this step)": r["books"], "Skill books (cumulative)": r["cumulativeBooks"],
                     "Books refunded on forget": r["forgetCost"], "Books lost on forget": r["booksLost"],
                     "Loss %": r["percentLoss"]})
    return rows


def officer_fortification_breakthrough():
    bt = load("officer_breakthrough.json")
    fort, prev = [], None
    for r in bt["fortification_levels"]:
        fort.append({"Fortification level": r["level"], "XP to reach (cumulative)": r["xp_needed"],
                     "XP (this step)": r["xp_needed"] - prev if prev is not None else 0,
                     "Effect %": r["effect"], "Multiplier": r["multiplier"] or ""})
        prev = r["xp_needed"]
    brk, total = [], 0
    for r in bt["breakthrough_levels"]:
        total += r["fragments"]
        brk.append({"Breakthrough level": r["level"], "Fragments (this step)": r["fragments"],
                    "Fragments (cumulative)": total,
                    "Note": "level 2 is missing from the source" if r["level"] == 3 else ""})
    return fort, brk


def equipment_full():
    """Every item at every level: alloy cost, running total, all stats, rank/power where published."""
    curves = load("equipment_cost_curves.json")["equipment"]
    pct = load("equipment_summary_curves.json")["statPctPerLevel"]  # published curve, levels 0-30
    rows = []
    for it in load("equipment_items.json")["items"]:
        curve = curves["byItem"].get(it["name"]) or curves["byItem"][it["clientName"]]
        cost = curves["curves"][curve]["costPerLevel"]
        known = {lv["level"]: lv for lv in it["levels"]}
        # Every effect line, keyed by name, with its published (level, value) points.
        lines = {}
        for lv in it["levels"]:
            for e in lv["effects"]:
                lines.setdefault(e["effect"], {"unit": e["unit"], "pts": {}})["pts"][lv["level"]] = e["value"]
        unlocks, cum = [], 0
        for L in range(1, it["maxLevel"] + 1):
            cum += cost[L - 1]
            vals, basis = [], set()
            for name, ln in lines.items():
                v, b = value_at(L, ln["pts"], ln["unit"], pct)
                if v is not None:
                    vals.append(f'{name} {v:g}{"%" if ln["unit"] == "percent" else ""}')
                    basis.add(b)
            if L in known:
                unlocks = known[L]["expertiseUnlocks"]
            rows.append({
                "Item": it["name"], "Max level": it["maxLevel"], "Level": L,
                "Alloy (this step)": cost[L - 1], "Alloy (cumulative)": cum,
                "Stats at this level": "; ".join(vals),
                "Rank": known[L]["rank"] if L in known else "", "Power": known[L]["power"] if L in known else "",
                "Expertise unlocked by now": ", ".join(unlocks),
                "Value basis": "published" if basis == {"published"} else ", ".join(sorted(basis)),
            })
    return rows


def value_at(L, pts, unit, pct):
    """Published value if there is one; otherwise derive from the curve the published points follow."""
    first = min(pts)
    if L < first:
        return None, ""
    if L in pts:
        return pts[L], "published"
    # Percent stats: published points are k * statPctPerLevel; use the curve wherever it is defined.
    curve_pts = {lv: v for lv, v in pts.items() if lv < len(pct) and pct[lv]}
    if unit == "percent" and curve_pts and L < len(pct):
        ks = {round(v / pct[lv], 6) for lv, v in curve_pts.items()}
        if len(ks) == 1:
            return round(ks.pop() * pct[L], 2), "derived from published stat curve"
    # Flat stats that are exactly proportional to level (e.g. resistance = 3 x level).
    ratios = {round(v / lv, 6) for lv, v in pts.items()}
    if len(ratios) == 1:
        return round(ratios.pop() * L, 2), "derived (value = constant x level)"
    lo = max(lv for lv in pts if lv < L)
    hi = min((lv for lv in pts if lv > L), default=None)
    if hi is None:
        return None, ""
    v = pts[lo] + (pts[hi] - pts[lo]) * (L - lo) / (hi - lo)
    return round(v, 2), "interpolated between published levels"


def expertise_full():
    ex = load("equipment_cost_curves.json")["expertise"]
    rows = []
    for name, info in sorted(ex["byExpertise"].items()):
        cum = 0
        for L, cost in enumerate(ex["curves"][info["curve"]]["costPerLevel"], 1):
            cum += cost
            rows.append({"Expertise": name, "Resource": info["resource"].replace("Officer Equipment ", ""),
                         "Level (to)": L, "Cost (this step)": cost, "Cost (cumulative)": cum,
                         "Value basis": "cost published; effect values not published"})
    return rows


# ---------------------------------------------------------------- titans

TITANS = {"sourceSpirit": "Source Spirit", "empress": "Empress"}


def titan_levels():
    rows = []
    for r in load("titan_levels_evolution.json")["levels"]:
        rows.append({"Titan level": r["level"], "XP (this step)": r["xp"], "XP (cumulative)": r["cumulativeXp"],
                     "Value basis": "cost published; titan base stats per level not published"})
    return rows


def titan_evolution():
    d = load("titan_levels_evolution.json")
    skills = load("titan_skills.json")
    abil = load("titan_components.json")
    unlock = {}
    for t, lst in list(skills.items()) + list(abil.items()):
        for s in lst:
            key = (s["unlocksAt"]["tier"], s["unlocksAt"]["stage"])
            unlock.setdefault(key, []).append(f'{TITANS[t]}: {s["name"]}')
    rows, cum = [], [0, 0, 0]
    for t in d["evolutionTiers"]:
        for stage in range(0, d["evolutionStagesPerTier"]):
            step = [t["mutantDnaPerStage"], t["storedEnergyJarsPerStage"], t["mutageSourceStonesPerStage"]]
            cum = [a + b for a, b in zip(cum, step)]
            rows.append({"Tier": t["tier"], "Stage": stage,
                         "Mutant DNA (this step)": step[0], "Stored Energy Jars (this step)": step[1],
                         "Mutagen Source Stones (this step)": step[2],
                         "Mutant DNA (cumulative)": cum[0], "Stored Energy Jars (cumulative)": cum[1],
                         "Mutagen Source Stones (cumulative)": cum[2],
                         "Unlocks": "; ".join(unlock.get((t["tier"], stage), []))})
    return rows


def titan_skills():
    rows = []
    for t, lst in load("titan_skills.json").items():
        for s in lst:
            fl = sk = 0
            for i in range(len(s["fluidPerLevel"])):
                fl += s["fluidPerLevel"][i]
                sk += s["skeletonPerLevel"][i]
                rows.append({"Titan": TITANS[t], "Skill": s["name"],
                             "Unlocks at": f'{s["unlocksAt"]["tier"]} stage {s["unlocksAt"]["stage"]}', "Level": i + 1,
                             "Value": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in s["stats"]),
                             "Fluid (this step)": s["fluidPerLevel"][i], "Fluid (cumulative)": fl,
                             "Skeleton (this step)": s["skeletonPerLevel"][i], "Skeleton (cumulative)": sk})
    return rows


def titan_equipment():
    abil_rows, comp_rows = [], []
    for t, lst in load("titan_components.json").items():
        for s in lst:
            ch = co = 0
            for i, chips in enumerate(s["chipsPerLevel"]):
                if chips is None:
                    continue
                ch += chips
                co += s["componentsPerLevel"][i]
                abil_rows.append({"Titan": TITANS[t], "Equipment / awakening skill": s["name"],
                                  "Unlocks at": f'{s["unlocksAt"]["tier"]} stage {s["unlocksAt"]["stage"]}',
                                  "Level": i + 1,
                                  "Value": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in s["mainStats"]),
                                  "Total component levels required": s["componentsPerLevel"][i],
                                  "Chips (this step)": chips, "Chips (cumulative)": ch})
            for c in s["components"]:
                al = 0
                for i, alloy in enumerate(c["alloyPerLevel"]):
                    al += alloy
                    comp_rows.append({"Titan": TITANS[t], "Equipment": s["name"], "Component": c["name"],
                                      "Level": i + 1,
                                      "Value": "; ".join(f'{st["stat"]} {st["valuesPerLevel"][i]}' for st in c["stats"]),
                                      "Alloy (this step)": alloy, "Alloy (cumulative)": al})
    return abil_rows, comp_rows


# ---------------------------------------------------------------- warplanes

def warplane_levels():
    return [{"Warplane level": r["level"], "XP (this step)": r["xp"], "XP (cumulative)": r["cumXp"]}
            for r in load("warplane_xp_levels.json")["levels"]]


def warplane_evolution():
    unlock = {}
    for w in load("warplane_weapons.json"):
        unlock.setdefault(w["evolution_needed"], []).append(f'Weapon: {w["name"]}')
    for p in load("warplane_skill_summary.json"):
        unlock.setdefault(p["evolution_needed"], []).append(f'Part: {p["name"]}')
    rows, al, bp = [], 0, 0
    for t in load("warplane_evolution.json")["tiers"]:
        for stage in range(0, t["stages"]):
            # Stage 0 is the tier's entry; each further stage costs the per-stage amount.
            step_al = t["alloyPerStage"]
            step_bp = t["blueprintPerStage"]
            al += step_al
            bp += step_bp
            rows.append({"Tier": t["name"], "Stage": stage, "Alloy (this step)": step_al,
                         "Blueprints (this step)": step_bp, "Alloy (cumulative)": al, "Blueprints (cumulative)": bp,
                         "Unlocks": "; ".join(unlock.get(f'{t["name"]} Tier {stage}', []))})
    return rows


def warplane_parts():
    comps = load("warplane_part_components.json")
    rows = []
    for p in load("warplane_levels.json"):
        c = comps.get(p["name"], [])
        cum = 0
        for r in p["levels"]:
            if r["level"] == 0:
                continue
            step = c[r["level"]] if r["level"] < len(c) else None
            cum += step or 0
            rows.append({"Part": p["name"], "Level": r["level"], "HP": r["hp"], "Attack": r["attack"],
                         "Defense": r["defense"],
                         "Components (this step)": "" if step is None else step,
                         "Components (cumulative, levels 1-30)": cum if r["level"] < len(c) else "",
                         "Value basis": "published" if step is not None else "stats published; component cost above level 30 not published"})
    return rows


def warplane_skills():
    names = {p["id"]: p["name"] for p in load("warplane_levels.json")}
    meta = {s["name"]: (p["evolution_needed"], s["unlocksAt"], s["maxBoost"])
            for p in load("warplane_skill_summary.json") for s in p["skills"]}
    rows = []
    for p in load("warplane_skills.json"):
        for s in p["skills"]:
            ev, at, mx = meta.get(s["name"], ("", "", ""))
            ch = co = 0
            for i in range(1, len(s["statValues"])):
                ch += s["chipsPerLevel"][i]
                co += s["coresPerLevel"][i]
                rows.append({"Part": names.get(p["id"], p["id"]), "Part unlocks at": ev, "Skill": s["name"],
                             "Skill unlocks at part level": at, "Stat": s["stat"], "Level": i,
                             "Value": s["statValues"][i], "Max": mx,
                             "Chips (this step)": s["chipsPerLevel"][i], "Chips (cumulative)": ch,
                             "Cores (this step)": s["coresPerLevel"][i], "Cores (cumulative)": co})
    return rows


def warplane_weapons():
    rows = []
    for w in load("warplane_weapons.json"):
        comps = w["components"]
        offset = comps[0] not in (0, None)  # most lists are padded with a level-0 entry; a few are not
        if offset:
            comps = [0] + comps
        cum = 0
        for i in range(1, len(comps)):
            step = comps[i]
            # Once a step is unpublished the running total is unknown from there on.
            cum = cum + step if (step is not None and cum is not None) else None
            rows.append({"Weapon": w["name"], "Unlocks at evolution": w["evolution_needed"], "Level": i,
                         "Components (this step)": "" if step is None else step,
                         "Components (cumulative)": "" if cum is None else cum,
                         "Effect (description)": w["buff"], "Community priority": w["priority"],
                         "Value basis": ("cost published; per-level effect values not published" if step is not None
                                         else "cost of this step not published")
                         + ("; source list has no level-0 entry, first value assumed to be level 1" if offset else "")})
    return rows


# ---------------------------------------------------------------- antiques

def antiques_full():
    seq = load("antique_star_costs.json")
    meta = {a["id"]: a for a in load("antiques_list.json")}
    rows = []
    for a in load("antique_effects.json")["antiques"]:
        m = meta.get(a["id"], {})
        eff = {s["star"]: s for s in a["stars"]}
        sid = seq["byAntique"].get(a["id"])
        cum = 0
        for step in seq["sequences"][sid]["levels"]:
            cum += step["fragments"]
            star = float(step["level"])
            whole = star.is_integer() and int(star) in eff
            rows.append({
                "Antique": a["name"], "Rarity": m.get("type", ""), "Set": m.get("set") or "", "Max star": a["maxStar"],
                "Star step (to)": step["level"], "Fragments (this step)": step["fragments"],
                "Fragments (cumulative)": cum,
                "Power": eff[int(star)]["power"] if whole else "",
                "Effects at this star (total)": "; ".join(fmt(e) for e in eff[int(star)]["effects"]) if whole else "",
                "Value basis": "published" if whole else "cost published; effect values above ★6 not published"
                if star > 6 else "cost published; effect only published at ★0/3/6",
            })
    return rows


def antique_sets():
    rows = []
    for s in load("antique_set_effects.json")["sets"]:
        for lv in s["levels"]:
            rows.append({"Set": s["name"], "Members": ", ".join(s["members"]),
                         "Total stars across set": lv["starTotal"],
                         "Set effects": "; ".join(f'{e["effect"] or "(unnamed)"} {e["value"]:g}'
                                                  f'{"%" if e["unit"] == "percent" else ""}' for e in lv["effects"])})
    return rows


# ---------------------------------------------------------------- totals

def totals(eq, ex, t_sk, t_ab, t_comp, w_parts, w_sk, w_wp, ant, stars, skills, brk, t_lv, t_evo, w_lv, w_evo):
    rows = []

    def last(rs, key, *cols):
        seen = {}
        for r in rs:
            seen[key(r)] = r
        for k, r in seen.items():
            yield k, {c: r[c] for c in cols}

    def add(system, item, costs):
        rows.append({"System": system, "Upgrade": item,
                     "Total cost to max": "; ".join(f"{k.split(' (')[0]} {v:,}" for k, v in costs.items() if v != "")})

    add("Officer", "Stars ★0 → ★6 (per officer)", {"Fragments": stars[-1]["Fragments (cumulative)"]})
    add("Officer", "Skill level 1 → 20 (per skill)", {"Skill XP": skills[-1]["Skill XP (cumulative)"],
                                                      "Skill books": skills[-1]["Skill books (cumulative)"]})
    add("Officer", f"Breakthrough to level {brk[-1]['Breakthrough level']} (level 2 missing)",
        {"Fragments": brk[-1]["Fragments (cumulative)"]})
    for k, v in last(eq, lambda r: r["Item"], "Max level", "Alloy (cumulative)"):
        add("Officer equipment", f'{k} (to {v["Max level"]})', {"Alloy": v["Alloy (cumulative)"]})
    for k, v in last(ex, lambda r: r["Expertise"], "Resource", "Cost (cumulative)"):
        add("Equipment expertise", f"{k} (to 20)", {v["Resource"]: v["Cost (cumulative)"]})
    add("Titan", "Level 1 → 45 (per titan)", {"XP": t_lv[-1]["XP (cumulative)"]})
    add("Titan", "Evolution Green → Orange, all stages (per titan)", {
        "Mutant DNA": t_evo[-1]["Mutant DNA (cumulative)"],
        "Stored Energy Jars": t_evo[-1]["Stored Energy Jars (cumulative)"],
        "Mutagen Source Stones": t_evo[-1]["Mutagen Source Stones (cumulative)"]})
    for k, v in last(t_sk, lambda r: (r["Titan"], r["Skill"]), "Fluid (cumulative)", "Skeleton (cumulative)"):
        add("Titan skill", f"{k[0]}: {k[1]} (to 20)", {"Fluid": v["Fluid (cumulative)"], "Skeleton": v["Skeleton (cumulative)"]})
    for k, v in last(t_ab, lambda r: (r["Titan"], r["Equipment / awakening skill"]), "Level", "Chips (cumulative)"):
        add("Titan equipment", f"{k[0]}: {k[1]} awakening (to {v['Level']})", {"Chips": v["Chips (cumulative)"]})
    for k, v in last(t_comp, lambda r: (r["Titan"], r["Equipment"], r["Component"]), "Alloy (cumulative)"):
        add("Titan equipment", f"{k[0]}: {k[1]} / {k[2]} (to 30)", {"Alloy": v["Alloy (cumulative)"]})
    add("Warplane", f"Plane level 1 → {w_lv[-1]['Warplane level']}", {"XP": w_lv[-1]["XP (cumulative)"]})
    add("Warplane", "Evolution Enforcer → Immortal (all stages)", {"Alloy": w_evo[-1]["Alloy (cumulative)"],
                                                                    "Blueprints": w_evo[-1]["Blueprints (cumulative)"]})
    for k, v in last([r for r in w_parts if r["Components (cumulative, levels 1-30)"] != ""], lambda r: r["Part"],
                     "Level", "Components (cumulative, levels 1-30)"):
        add("Warplane part", f'{k} (to {v["Level"]})', {"Components": v["Components (cumulative, levels 1-30)"]})
    for k, v in last(w_sk, lambda r: (r["Part"], r["Skill"]), "Level", "Chips (cumulative)", "Cores (cumulative)"):
        add("Warplane skill", f"{k[0]}: {k[1]} (to {v['Level']})", {"Chips": v["Chips (cumulative)"], "Cores": v["Cores (cumulative)"]})
    for k, v in last(w_wp, lambda r: r["Weapon"], "Level", "Components (cumulative)"):
        add("Warplane weapon", f'{k} (to {v["Level"]})', {"Components": v["Components (cumulative)"]})
    for k, v in last(ant, lambda r: r["Antique"], "Max star", "Fragments (cumulative)"):
        add("Antique", f'{k} (to ★{v["Max star"]})', {"Fragments": v["Fragments (cumulative)"]})
    return rows


COVERAGE = [
    ("Officer stars", "Fragments per grade ★1–★6", "What each star adds to the officer's skills"),
    ("Officer skills", "XP/books per level 2–20 (cross-checked with appmedia.jp, 19/19 match)", "Skill effect values per officer and level"),
    ("Officer fortification", "XP and effect % for levels 1–30", "—"),
    ("Officer breakthrough", "Fragments for levels 1 and 3–18", "Level 2 cost; effect values"),
    ("Officer equipment", "Alloy cost every level; stats published every 5 levels; levels 1–30 filled from the published stat curve (exact match at every published point)", "Levels 31–39 are interpolated (only 30 and 40 published); rank/power only at published levels"),
    ("Equipment expertise", "Essence Stone/Core cost levels 1–20", "Expertise effect values"),
    ("Titan levels", "XP levels 1–45", "Titan base stats per level"),
    ("Titan evolution", "DNA/Energy Jars/Source Stones per stage, all 4 tiers; skill unlock points", "Stat gain per evolution stage"),
    ("Titan skills", "Value and fluid/skeleton cost, every level 1–20, all 24 skills", "—"),
    ("Titan equipment", "Awakening skill value + chip cost per level; component value + alloy cost levels 1–30", "Awakening levels 27–30"),
    ("Warplane levels", "XP levels 1–55", "Plane stats per level (part stats are covered)"),
    ("Warplane evolution", "Alloy/blueprints per stage, 8 tiers; weapon and part unlock points", "Stat gain per evolution stage"),
    ("Warplane parts", "HP/Attack/Defense levels 1–55; components levels 1–30", "Component cost above level 30"),
    ("Warplane skills", "Value + chips/cores every level 1–30, all 18 skills", "—"),
    ("Warplane weapons", "Component cost per level, 24 weapons", "Per-level effect values (only descriptions published)"),
    ("Antiques", "Fragment cost every star step for all 52 antiques; effects at ★0/★3/★6", "Effects above ★6"),
    ("Antique sets", "Set bonuses by total stars, 6 sets", "—"),
]


def coverage():
    return [{"Area": a, "Published values & costs included": h, "Missing (not in any reachable source)": m}
            for a, h, m in COVERAGE]


def build():
    eq, ex = equipment_full(), expertise_full()
    fort, brk = officer_fortification_breakthrough()
    stars, skills = officer_stars(), officer_skills()
    t_lv, t_evo, t_sk = titan_levels(), titan_evolution(), titan_skills()
    t_ab, t_comp = titan_equipment()
    w_lv, w_evo, w_parts, w_sk, w_wp = warplane_levels(), warplane_evolution(), warplane_parts(), warplane_skills(), warplane_weapons()
    ant = antiques_full()
    return [
        ("Totals to max", totals(eq, ex, t_sk, t_ab, t_comp, w_parts, w_sk, w_wp, ant, stars, skills, brk, t_lv, t_evo, w_lv, w_evo)),
        ("Coverage", coverage()),
        ("Officer roster & equipment", officer_roster()), ("Officer stars", stars), ("Officer skill levels", skills),
        ("Officer fortification", fort), ("Officer breakthrough", brk),
        ("Officer equipment (all levels)", eq), ("Equipment expertise", ex),
        ("Titan levels", t_lv), ("Titan evolution", t_evo), ("Titan skills", t_sk),
        ("Titan equipment awakening", t_ab), ("Titan equipment components", t_comp),
        ("Warplane levels", w_lv), ("Warplane evolution", w_evo), ("Warplane parts", w_parts),
        ("Warplane skills", w_sk), ("Warplane weapons", w_wp),
        ("Antiques (all star steps)", ant), ("Antique sets", antique_sets()),
    ]
