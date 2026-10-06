# Age of Origins — upgrade tables

Cost and effect tables for the upgrade systems in *Age of Origins* (Camel Games; *Age of Z Origins* in Japan).

There are two workbooks. Every sheet has a filter on its header row, and the same tables are in [`output/csv/`](output/csv/), one file per sheet.

- [`output/age_of_origins_officers_titans_warplanes_antiques.xlsx`](output/age_of_origins_officers_titans_warplanes_antiques.xlsx): officers, titans, warplanes and antiques (21 sheets)
- [`output/age_of_origins_buildings.xlsx`](output/age_of_origins_buildings.xlsx): buildings and merit ranks (5 sheets)

## Officers, titans, warplanes, antiques

Every sheet has **one row per upgrade step**. Each row gives the cost of that step, the running total from the start, and the value reached at that level. A `Value basis` column says whether the value is published, derived from a published curve, or interpolated.

| Sheet | Rows | Values | Costs |
|---|---|---|---|
| **Totals to max** | 227 | — | Total cost to max for every officer stat, equipment item, expertise, titan skill and component, warplane part, skill and weapon, and antique |
| **Coverage** | 17 | What's in, and what's still missing, for each area | |
| Officer roster & equipment | 48 | Each officer's 2 equipment items, their 3 stats and 2 expertises | — |
| Officer stars | 30 | — (star skill values not published) | Fragments per grade, ★1–★6 |
| Officer skill levels | 19 | — (skill values per officer not published) | Skill XP and books, levels 2–20 |
| Officer fortification | 30 | Effect % | XP |
| Officer breakthrough | 17 | — | Fragments (level 2 missing from source) |
| Officer equipment (all levels) | 1,780 | All stats at every level 1–40 for 48 items; rank and power at published levels | Alloy per level |
| Equipment expertise | 360 | — (not published) | Essence Stone / Core, levels 1–20 |
| Titan levels / evolution | 45 / 40 | Skill and equipment unlock points | XP; Mutant DNA, Energy Jars, Source Stones per stage |
| Titan skills | 480 | 24 skills × 20 levels | Fluid, skeleton |
| Titan equipment awakening / components | 156 / 720 | 6 equipment × 26 levels; 24 components × 30 levels | Chips; alloy |
| Warplane levels / evolution | 55 / 80 | Weapon and part unlock points | XP; alloy and blueprints per stage |
| Warplane parts | 330 | HP, Attack, Defense, 6 parts × 55 levels | Components (levels 1–30) |
| Warplane skills | 540 | 18 skills × 30 levels | Chips, cores |
| Warplane weapons | 520 | Description only (not published per level) | Components per level, 24 weapons |
| Antiques (all star steps) | 3,880 | Effects and power at ★0, ★3 and ★6 | Fragments for every step (including sub-steps like 3.1, 3.2) to ★15/18/21, 52 antiques |
| Antique sets | 48 | Set bonus by total stars | — |

**How missing equipment values were filled in.** The source publishes item stats only at levels 1, 5, 10, 15, 20, 25, 30 and 40.
- **Percentage stats:** at every published point they equal the game's published stat curve exactly, so levels 1–30 come from that curve.
- **Flat stats** (Misfire/Disruptor Resistance) follow 3 per level, so levels 1–30 use that.
- **Levels 31–39** have no published values. They are interpolated between 30 and 40 and flagged as such.

**Cross-checks and assumptions:**
- **Officer skill XP:** matches appmedia.jp on all 19 levels.
- **Officer stars:** fragments per grade (★1 = 5, ★2 = 4, ★3 = 12, ★4 = 24, ★5 = 48, ★6 = 58) match appmedia's star-up totals (20 / 60 / 120 / 240).
- **Three warplane weapons** (Superior Force Field, EMP Armor, Armor Piercing Laser) have no level-0 entry in the source, so their first value is taken as level 1. Those rows are flagged.
- **Evolution steps:** titan and warplane evolution are charged per stage, 10 stages per tier, matching the source's per-tier totals.

**What's still not published anywhere I could reach:**
- Antique effects above ★6
- Officer skill and star values per officer
- Expertise effect values
- Titan and warplane stat gains per evolution stage
- Titan base stats per level
- Per-level warplane weapon effects
- A few missing cost steps: breakthrough level 2, some weapon levels, and titan awakening levels 27–30

The *Coverage* sheet lists each gap. Screenshots of the in-game detail screens can fill them.

## Sources

| Source | What it gave | Date |
|---|---|---|
| [aoztips.com](https://aoztips.com/calculators/) calculators. The JSON is embedded in the site's JS bundles; several datasets say they come from the game client config (`buff_values.json`, nation 933, app 1.3.844). | All officer, equipment, titan, warplane, antique and merit data. Building costs to level 41 including uranium. | 2026-09 |
| [appmedia.jp Age of Z wiki](https://appmedia.jp/ageofzorigins/27075133), 30 facility pages plus officer star and skill pages | Cross-checks for officer stars and skills. Building costs to level 38, **base build times**, prerequisites, per-level effects. The only source found for Farm, Oil Refinery, Steel Mill, Mineral Mine, Training Grounds, House, Rehab Center and Defense Tower. | 2021-11 to 2022-01 |

`sources/` holds the extracted snapshots. `scripts/` holds:
- the extractors: `extract.py`, `literals.mjs` and `appmedia_parse.py`
- `build_tables.py` and `progression.py`, which rebuild everything in `output/`

```bash
python3 age_of_origins/scripts/build_tables.py   # needs openpyxl
```

## Buildings

`age_of_origins_buildings.xlsx` has 21 buildings and 695 level rows: costs, base build time, prerequisites and effects. It also has building totals, a sheet of source conflicts, merit ranks and a gaps list.

### How the building numbers were reconciled

The two building sources were compared level by level, resource by resource. Each row's `Source` and `Flag` columns say how its numbers were arrived at:

- **`verified: 2 sources agree`**: 362 of 695 rows. Both sources give identical food/oil/steel/minerals.
- **`aoztips row offset corrected`**: aoztips lists Workshop (all levels) and Garage (levels 2–11) one level too high. Its level N+1 matches appmedia's level N exactly. Uranium starting at level 31, as on every other building, confirms the appmedia alignment, so these rows were shifted back. Because of the shift, Workshop level 41 is unknown.
- **`conflict`**: 38 field-level disagreements, all listed on the *Building conflicts* sheet. The value kept is the one whose steel- or minerals-to-food/oil ratio, or whose growth curve, best fits the agreed neighbouring levels. Examples:
  - Main Hall 31 steel: kept 38,273,410. aoztips' 28,273,520 breaks the ~0.28 ratio.
  - Garage 25 steel: aoztips copied the oil value.
  - Embassy 36–38: appmedia's rows are verbatim copies of its Garage rows, so aoztips was kept.
  - Most other conflicts are one-digit typos or rounding.
- **`single source`**: either levels 39–41 (aoztips only) or the eight buildings only appmedia covers. These are unconfirmed, and the appmedia-only buildings date from 2021–22.

### Building caveats

- **Build times** are base times before construction-speed bonuses, from the 2021–22 appmedia data, and only up to level 38. Patches may have changed them.
- **Uranium** comes from aoztips only. appmedia-only rows show 0 uranium even where the current game may charge some.
- **Level caps:** Resource buildings, Training Grounds, House and Rehab Center stop at level 30, and Defense Tower at 10, because those were the 2021 maximums. Higher levels may exist now.
- **English names:** *Rehab Center* (リハビリ拠点, a wounded-capacity building) is a translation, not a confirmed English name. Higher-tier troop unlocks in the effect column keep their Japanese names.
- **Core Mining Station** is a single odd row in aoztips (level 30 only) and is flagged unverified.

## Other gaps

1. **Academy research.** This means every branch. aozcodes.com publishes research data but blocks automated access with a Cloudflare bot check. web.archive.org is also unreachable from here.
2. **Troop tiers:** training cost, stats and upkeep.
3. **Biochemical Lab, Elite Equipment Facility, alliance tech, VIP levels, commander skills, battle machines, mega devices.**
