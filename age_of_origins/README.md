# Age of Origins — upgrade tables

Cost and effect tables for the upgrade systems in *Age of Origins* (Camel Games; *Age of Z Origins* in Japan).

**Main file:** [`output/age_of_origins_upgrades.xlsx`](output/age_of_origins_upgrades.xlsx) has 26 sheets. Every sheet has a filter on its header row. The same tables are in [`output/csv/`](output/csv/), one file per sheet.

## What's covered

| Area | Sheets | Coverage |
|---|---|---|
| Buildings | Buildings, Building totals, Building conflicts | 21 buildings, 695 level rows. Food/Oil/Steel/Minerals/Uranium/Leader Badges/other items, base build time, prerequisites, effect at each level |
| Officers | Officer skill levels, Officer stars, Officer fortification, Officer breakthrough | Skill XP and books (levels 2–20), star fragments (★1–★6 × 5 grades), fortification XP and effect (1–30), breakthrough fragments |
| Officer equipment | Officer equipment levels, Officer equipment items, Equipment expertise, Equipment stat curve | Alloy cost curves (levels 1–40), 48 items with effects/power every 5 levels, 18 expertises (levels 1–20, Essence Stone or Core) |
| Titans | Titan levels, Titan evolution, Titan skills, Titan abilities, Titan ability components | Levels 1–45 XP, 4 evolution tiers, skill fluid/skeleton cost, ability chip/component cost, component alloy cost (1–30) |
| Warplanes | Warplane levels, Warplane evolution, Warplane parts, Warplane skills, Warplane weapons | XP levels 1–55, 8 evolution tiers (alloy/blueprints), 6 parts × 55 levels of stats, skills × 30 levels (chips/cores), 24 weapons |
| Antiques | Antique star costs, Antique effects, Antique sets | Fragment cost sequences (max ★15/18/21), 52 antiques' effects by star, 6 set bonuses |
| Merit | Merit ranks | 9 ranks: Attack %, HP %, fleet size |
| **Not covered** | Gaps – not covered | See [Gaps](#gaps) |

## Sources

| Source | What it gave | Date |
|---|---|---|
| [aoztips.com](https://aoztips.com/calculators/) calculators. The JSON is embedded in the site's JS bundles; several datasets say they come from the game client config (`buff_values.json`, nation 933, app 1.3.844). | Building costs to level 41 including uranium. All officer, equipment, titan, warplane, antique and merit data. | 2026-09 |
| [appmedia.jp Age of Z wiki](https://appmedia.jp/ageofzorigins/27075133), 30 facility pages | Building costs to level 38, **base build times**, prerequisites, per-level effects. The only source found for Farm, Oil Refinery, Steel Mill, Mineral Mine, Training Grounds, House, Rehab Center and Defense Tower. | 2021-11 to 2022-01 |

`sources/` holds the extracted snapshots. `scripts/` holds the extractors (`extract.py`, `literals.mjs`, `appmedia_parse.py`) and `build_tables.py`, which rebuilds everything in `output/`:

```bash
python3 age_of_origins/scripts/build_tables.py   # needs openpyxl
```

## How the building numbers were reconciled

The two building sources were compared level by level, resource by resource. Each row's `Source` and `Flag` columns say how its numbers were arrived at:

- **`verified: 2 sources agree`**: 362 of 695 rows. Both sources give identical food/oil/steel/minerals.
- **`aoztips row offset corrected`**: aoztips lists Workshop (all levels) and Garage (levels 2–11) one level too high. Its level N+1 matches appmedia's level N exactly. Uranium starting at level 31, as on every other building, confirms the appmedia alignment, so these rows were shifted back. Because of the shift, Workshop level 41 is unknown.
- **`conflict`**: 38 field-level disagreements, all listed on the *Building conflicts* sheet. The value kept is the one whose steel- or minerals-to-food/oil ratio, or whose growth curve, best fits the agreed neighbouring levels. Examples:
  - Main Hall 31 steel: kept 38,273,410. aoztips' 28,273,520 breaks the ~0.28 ratio.
  - Garage 25 steel: aoztips copied the oil value.
  - Embassy 36–38: appmedia's rows are verbatim copies of its Garage rows, so aoztips was kept.
  - Most other conflicts are one-digit typos or rounding.
- **`single source`**: either levels 39–41 (aoztips only) or the eight buildings only appmedia covers. These are unconfirmed, and the appmedia-only buildings date from 2021–22.

## Caveats

- **Build times** are base times before construction-speed bonuses, from the 2021–22 appmedia data, and only up to level 38. Patches may have changed them.
- **Uranium** comes from aoztips only. appmedia-only rows show 0 uranium even where the current game may charge some.
- **Resource buildings, Training Grounds, House and Rehab Center** stop at level 30, and Defense Tower at 10, because that was the maximum in 2021. Higher levels may exist now.
- **English names:** *Rehab Center* (リハビリ拠点, a wounded-capacity building) is a translation, not a confirmed English name. Higher-tier troop unlocks in the effect column keep their Japanese names.
- **Core Mining Station** is a single odd row in aoztips (level 30 only) and is flagged unverified.
- **Equipment item effects** are sampled at levels 1, 5, 10, 15 and so on, as published. Per-level alloy cost is in *Officer equipment levels*.
- **Officer stars:** fragments per grade (★1 = 5, ★2 = 4, ★3 = 12, ★4 = 24, ★5 = 48, ★6 = 58) are from aoztips and match appmedia's star-up totals.
- **Officer skill XP** matches across both sources on all 19 levels.

## Gaps

These systems have no per-level numbers in any source this environment could reach:

1. **Academy research.** This means every branch: Development, Military, Resource, Defense and the rest. It's the largest gap. aozcodes.com publishes research data but sits behind a Cloudflare bot check that blocks automated access. web.archive.org is also unreachable from here. tofumocha.com/aoo-upgrade returned 503.
2. **Troop tiers:** training cost, stats and upkeep per unit tier.
3. **Biochemical Lab, Elite Equipment Facility, alliance tech, VIP levels, commander skills, battle machines, mega devices.**
4. **Current build times** and costs above the 2021 caps for resource/support buildings.
