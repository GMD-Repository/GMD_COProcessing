# National-Level MBI Checker

**Script:** [`gmd_processing_tools/national_mbi_checker.py`](../../gmd_processing_tools/national_mbi_checker.py)
**Menu:** GMD CO Processing › EA Tools › National-Level MBI Checker

Finds the **MBIs** (gaps and overlaps) between **barangay** polygons, at any scale up to the whole country,
and counts the building points inside each one. It is the barangay-level counterpart of the
[EA MBI Checker](EA_topochecker.md). The [MBI Barangay Selector](mbi_bgy_selector.md) later chooses the
covering barangay of the MBIs that remain after review.

It opens as a standard QGIS Processing dialog; the right-hand panel of the dialog holds its help.

---

## Quick start

1. **Select Barangay Polygon Layer(s)** — tick every barangay layer to check (they are merged first).
2. **Select Building Point Layer(s)** — tick the building point layers.
3. **Analysis to Run** — **Overlaps and Gaps**, **Overlaps Only** or **Gaps Only**.
4. Optional: tick **Export MBI layers as one GPKG per province, e.g. 027_Ifugao_mbi.gpkg (Overlaps and Gaps only)**
   and pick the **Export folder** (default `C:\PSA-GIS\2026 1Map\Preliminary Output`).
5. Click **Run**. The **Overlaps** and **Gaps** layers (and each exported province) are added to the project,
   styled with `mbi_checker.qml`.

> The input layers are never edited. Without export, the results are temporary layers — right-click ›
> **Make Permanent** to keep them.

---

## How it works

```mermaid
flowchart LR
    B[Barangay layers] --> M[Merge · EPSG:3857<br/>fix geometries]
    P[Building points] --> M2[Merge · EPSG:3857]
    M --> O[Overlaps:<br/>intersect every pair<br/>of barangay parts]
    M --> G[Gaps:<br/>dissolve · fill holes ·<br/>difference]
    O --> C[gmd_uuid · area · size<br/>building count]
    G --> C
    M2 --> C
    C --> R[One row per pair<br/>of barangays + MBI level]
    R --> L[Overlaps / Gaps layers]
    R --> X[Export one GPKG per province<br/>027_Ifugao_mbi.gpkg]
```

1. **Prepare.** The selected layers are merged (one barangay layer, one building layer), reprojected to
   EPSG:3857 and repaired (`native:fixgeometries`).
2. **Detect overlaps.** The barangays are split into single parts. Every pair of parts whose bounding boxes
   meet is intersected; each polygon piece of the intersection is one overlap, tagged with the two parts
   it comes from.
3. **Detect gaps.** The barangays are dissolved into one shape, its holes are filled, and the original
   dissolved shape is subtracted. What remains, split into single parts, are the gaps.
4. **Measure.** Each overlap / gap gets a `case_uuid`, its area (`mbi_area`, rounded to 2 decimals), the
   width and height of its minimum bounding box, and the number of building points inside it
   (`num_bldg_pts`) and the sum of their household counts (`est_hhcount`).
5. **Find the involved barangays.**
   - **Overlap:** the two barangays it was cut from.
   - **Gap:** every barangay sharing more than **0.01 m** of edge with it (measured in EPSG:3857).
     Corner-only touches do not count — unless no barangay shares a clean edge, then all touching ones count.
   - Barangays without a `geocode` are left out.
6. **Drop slivers.** An MBI of **0.1 m² or less with no building** is dropped.
7. **Write pairs.** Each MBI is written once per **ordered pair** of involved barangays (A → B and B → A),
   in EPSG:4326, with the MBI level between the two (below). An MBI with a single involved barangay gets
   one row, level `5_Within-Barangay`.
8. **Export (optional, Overlaps and Gaps mode).** Overlaps and Gaps are merged, with `mbi_type`,
   `mbi_status` and `mbi_remarks` added, then split by **province**: the first 3 digits of
   each row's `geocode`. Each province is written to its own GPKG, named after the code and the `province`
   field, and loaded into the project. A row belongs to its own barangay's province, so an inter-province
   MBI is in both provinces' files.

---

## MBI level

Taken from the first attribute that differs between the two barangays of the row:

| Differs | `mbi_level` |
|---------|-------------|
| `region` | `1_Inter-Region` |
| `province` | `2_Inter-Province` |
| `city_mun` | `3_Inter-City/Municipality` |
| `barangay` | `4_Inter-Barangay` |
| nothing (or a single barangay) | `5_Within-Barangay` |

The barangay layers need the fields `geocode`, `region`, `province`, `city_mun` and `barangay`.

For `est_hhcount`, each building point gives its **`est_hhcount`**, or its **`hhcount`** when `est_hhcount`
is empty; these are summed over the points inside the MBI. If the points have neither field, these are
used instead, in this order: `hh_count`, `est_hh`, `household_count`, `households`, `no_hh`, `num_hh`
(any letter case). A point with no value in any of them counts as 0.

---

## What gets written

**Overlaps** / **Gaps** layers (one row per ordered pair of involved barangays):

| Field | Contents |
|-------|----------|
| `case_uuid` | ID of the MBI — the same on all its rows |
| `geocode`, `region`, `province`, `city_mun`, `barangay` | This row's barangay |
| `mbi_level` | Level between this barangay and the other one of the pair |
| `involved_areas` | Geocodes of every involved barangay, comma-separated |
| `involved_bgys` | `barangay, city_mun` of every involved barangay, `;`-separated |
| `count_involved_areas` | Number of involved barangays |
| `num_bldg_pts` | Building points inside the MBI |
| `est_hhcount` | Sum of the household count of those building points (empty when the points have no household field) |

**Export** — one `<province code>_<Province>_mbi.gpkg` per province in the **Export folder**
(e.g. `027_Ifugao_mbi.gpkg`; spaces and symbols in the name become `_`, so `Nueva Vizcaya` →
`050_Nueva_Vizcaya_mbi.gpkg`). An existing file with the same name is replaced. Same fields, plus:

| Field | Contents |
|-------|----------|
| `mbi_type` | `Overlap` or `Gap` |
| `mbi_remarks` | Empty, for the reviewer |
| `mbi_status` | `2_Pending` for `1_Inter-Region` and `2_Inter-Province` rows, otherwise empty |

---

## Running without the dialog

Fill in `BGY_LAYER_NAMES` and `BLDG_LAYER_NAMES` (layer names as in the Layers panel), and optionally
`RUN_MODE` (0 / 1 / 2), `EXPORT_MBI` and `EXPORT_FOLDER`, in the CONFIG block at the top of the script, then
run it. Progress prints to the panel log / Python console.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No layer added for overlaps | No overlaps were found — the log says *no overlapping areas found*. |
| `mbi_level` is always `5_Within-Barangay` | The barangay layers lack `region` / `province` / `city_mun` / `barangay`, or they are empty. |
| An MBI is missing | It was 0.1 m² or less with no building (dropped as a sliver), or its barangays have no `geocode`. |
| Export ticked but no GPKG | Export runs only with **Overlaps and Gaps**; the folder must be writable. |
| *Could not replace … (open in QGIS?)* | That province's GPKG is open in QGIS — remove the layer and run again. |
| `est_hhcount` empty | The building points have none of the household fields listed under MBI level — the log warns about it. |
| File named `027_mbi.gpkg` | The barangays of that province have an empty `province` field. |
| Layers not styled | `gmd_processing_tools/styles/mbi_checker.qml` is missing. |
| Slow on a whole region | Every pair of touching barangay parts is intersected — run per region, or give the building layers a spatial index. |
