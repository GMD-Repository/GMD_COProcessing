# Review: Sliver rule in the MBI checks

**Status:** For review · **Decision:** the National-Level MBI Checker rule is the correct one

## Comparison, as reviewed

![Sliver rule comparison: MBI Barangay Selector vs National-Level MBI Checker](images/sliver_rule_comparison.png)

| | MBI Barangay Selector (Run Check & Counter-Check) | National-Level MBI Checker |
|---|---|---|
| Cut-off | `CHECKER_MIN_M2 = 0.10` m² ([mbi_bgy_selector.py](../../gmd_processing_tools/mbi_bgy_selector.py)) | `0 <= area <= 0.1` m² |
| Rule | Drops every finding of 0.10 m² or less, even with buildings inside | Drops it only if it is 0.1 m² or less **and** has 0 buildings |
| When it drops | Each overlap or gap piece as it is found | After the pieces are measured and their buildings counted |
| How area is measured | Planar in EPSG:3857 | QGIS `$area`, which uses the project's ellipsoid |

## Rule now used by all three checkers

> An MBI of **0.1 m² or less with no building point inside** is dropped as a sliver.
> A tiny MBI that holds a building is **kept**.

| Checker | Script | Rule |
|---------|--------|------|
| National-Level MBI Checker | [`national_mbi_checker.py`](../../gmd_processing_tools/national_mbi_checker.py) | Already correct: `0 <= area <= 0.1 and bldg == 0` |
| EA MBI Checker | [`EA_topochecker.py`](../../gmd_processing_tools/EA_topochecker.py) | Already correct: same condition |
| MBI Barangay Selector (built-in checker) | [`mbi_bgy_selector.py`](../../gmd_processing_tools/mbi_bgy_selector.py) | **Changed**: `is_sliver()` = area ≤ `CHECKER_MIN_M2` (0.10 m²) **and** no building point inside. Used for overlap pieces and gaps. Area measured as `$area` (below). |

Without a building layer, every count is 0, so all findings of 0.10 m² or less are still dropped.

## Test

Two barangays in EPSG:3857 overlapping by a 0.2 m × 0.25 m strip (0.05 m²), run through the
Barangay Selector's built-in checker in Overlaps Only mode:

| Building point inside the overlap | Overlaps reported |
|-----------------------------------|-------------------|
| No | 0 (dropped as a sliver) |
| Yes | 1 (`num_bldg_pts` = 1) |

## How the area is measured

**Decision:** match the National-Level and EA MBI Checkers.

All three now measure the sliver area the way QGIS `$area` does: on the **project's ellipsoid**
(Project Properties › General › Ellipsoid), or **planar in EPSG:3857** when the project's ellipsoid is
*None*. In the Barangay Selector this is `mbi_area_m2()` (a `QgsDistanceArea` on EPSG:3857 with the
project's ellipsoid), used only by `is_sliver()`. Other area checks in the Barangay Selector are unchanged:
the 0.01 m² share needed to count a barangay as involved, and the counter-check match.

The same test passes with the project ellipsoid set to *None* and to WGS 84 (`EPSG:7030`): with no building
the overlap is dropped (0 reported), and with a building inside it is kept (1 reported).
