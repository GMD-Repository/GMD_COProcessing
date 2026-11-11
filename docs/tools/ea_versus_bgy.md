# EA vs Barangay Check

**Script:** [`gmd_processing_tools/ea_versus_bgy.py`](../../gmd_processing_tools/ea_versus_bgy.py)
**Menu:** GMD CO Processing › EA Tools › EA vs Barangay Check

Compares boundaries and lists only the **discrepancies** — each one is just the mismatched area, carrying
the attributes of the feature that must be fixed — then snaps the ones you select.

Requires `geopandas`, `pandas`, `pyogrio` and `shapely` in the QGIS Python.

---

## Check modes

| Mode | With a reference layer | Without one |
|------|------------------------|-------------|
| **EA vs BGY** | Each EA is snapped to its barangay (the barangay is fixed) | — (barangay layer required) |
| **BGY vs BGY** | Each barangay is snapped to the reference barangay with the same `geocode` | Barangays are checked against each other |
| **EA vs EA** | Each EA is snapped to the reference EA with the same `ea_geocode` | EAs are checked against each other |

---

## Run it

1. Load the layers — e.g. every city / municipality of a province. To check part of a layer, set a filter
   on it (right-click › Filter).
2. Open **EA vs Barangay Check** and choose the **Check Mode**.
3. Tick the layers to check and (optionally, except for EA vs BGY) the reference layers. **Select All**
   ticks every layer of the list's kind; **Clear** unticks the list. Set **Ignore slivers below**
   (default 5 m²).
4. Press **Scan**. Progress, warnings and the summary appear in the Log.
5. The **Discrepancies** list shows every issue, largest first. Click a row to flash it on the map,
   double-click to zoom. The zoom blinks a **dotted outline with a circle marker** on the discrepancy
   (orange = cut off, red = added, grey = fix by hand) and a **dotted green outline** of the Fix feature as it
   will be after **Snap Selected**. They stay drawn until the next zoom, scan, snap or Clear. A tiny piece
   (e.g. 1 m²) is zoomed to at least ~55 m across so its surroundings show. **Zoom to Selected** does the
   same for several rows. Untick **Blinking dotted outline and snap preview on zoom** (Snap panel) to get
   only the plain red flash.
6. Select the rows to fix (**Switch Fix** changes which feature takes them) and press **Snap Selected**.

### Ticking layers

| Row looks | Meaning |
|-----------|---------|
| Normal | A layer of the list's kind (EA or barangay). **Select All** ticks it. |
| Grey, tickable | Looks like another kind, or is an MBI result layer (`case_uuid` / `bgy_part`). Tick it by hand if you need it; **Select All** leaves it out. |
| Grey, not tickable | No geocode field for this mode, or the layer is ticked in the other list (a layer cannot be both checked and reference). |

If exactly one layer fits a list you have not touched, it is ticked for you, so a single EA / barangay pair
is ready to scan straight away.

## How it works

```mermaid
flowchart LR
    T[Ticked layers to check] --> K[Read each · clean · key<br/>by geocode · merge]
    R[Ticked reference layers] --> K
    K --> D{Reference given?}
    D -->|yes| X[Outside = feature − reference<br/>Short = reference − features<br/>No matching = no geocode]
    D -->|no| Y[Overlap = feature ∩ feature<br/>Gap = enclosed, in none]
    X --> F[Drop slivers · pick<br/>the feature to fix]
    Y --> F
    F --> L[Discrepancies list]
    L --> S[Snap Selected:<br/>cut / add · validate · edit]
```

1. **Read.**
   - Every ticked layer is read (honouring any layer filter) with its geometries cleaned. The checked layers
     are then merged into one set, and so are the reference layers; each feature remembers its own layer.
   - Reference features sharing a key — also across layers — are merged into one (with a warning).
   - A ticked layer that is empty or has no usable geocodes stops the scan, and the Log names it, so a
     result never silently misses a city / municipality.
   - Each feature gets a key: the barangay (first 8 geocode digits) in EA vs BGY and BGY vs BGY, or the
     `ea_geocode` in EA vs EA.
   - Overlays run in the layers' own lat/lon, so shared edges stay exact. Areas are measured in an
     equal-area projection centred on the Philippines.
2. **Compare with a reference.** Features are matched to the reference feature with the same key.
   - **Outside:** feature − its reference. Each piece names the reference area it extends into.
   - **Short of:** reference − the union of its features. The gap is linked to the features within 1 m of
     it. It is given to the one with the longest shared border, but only if that feature has at least 30%
     of the bordering length; otherwise it is marked *Fix manually*.
   - **No matching:** features whose key is not in the reference; the whole feature is listed.
3. **Self check (no reference).**
   - **Overlap:** the shared area of two features, cut from the larger one.
   - **Gap:** land enclosed by the features but in none, added to the feature with the longest border along
     it.
4. **Filter.** Pieces smaller than *Ignore slivers below* are dropped, and the rest are sorted by area.
5. **Snap.** For each selected row the fix feature's new geometry is built:
   - a cut (difference) or an add (union);
   - then holes and specks below the sliver size are removed.

   The result is checked (valid, not empty, expected area, fits the layer, feature unchanged since the
   scan) and written to the feature's own layer, as one undo step per layer. A row that fails the checks is marked *Failed* and the feature is
   left alone.

## Discrepancy types

| Type | Meaning | Snap does |
|------|---------|-----------|
| **Outside …** | Part of a feature outside its reference | Cuts it off the feature |
| **Short of …** | Reference land the feature does not reach (all bordering features listed) | Adds it to the feature with the longest shared border |
| **No matching …** | The feature's geocode is not in the reference layer | Nothing — fix by hand |
| **Overlap** | Self check: land in two features | Cuts it from the larger one (Switch Fix: the other) |
| **Gap** | Self check: land enclosed by the features but in none | Adds it to the feature with the longest border (Switch Fix: the next one) |

Holes and parts smaller than the sliver setting that an edit leaves behind are removed.

---

## Safety checks and saving

Before a feature changes, the new geometry must be valid, not empty, have the expected area, stay valid in
the layer's CRS, fit the layer (a single-part layer cannot take several parts), and the feature must not
have been edited since the scan. Otherwise the row is marked **Failed** with the reason, and the feature is
untouched.

Only geometry changes — attributes such as household counts stay as they are. Each snap edits the
feature's own layer, and the changed layers are left in edit mode (the Log names them):

- **Keep:** Save Layer Edits, or **Layer › Current Edits › Save for All Layers** for several layers.
- **Revert:** Toggle Editing › Discard, or **Edit › Undo** (one undo step per layer per Snap Selected).

The reference layers are never edited. No layer is added to the project; each scan removes any
"Discrepancies …" scratch layers left by older versions.

## Fields it looks for

Barangay layers: `geocode`/`bgy_geocode`, `region`, `province`, `city_mun`, `barangay`.
EA layers: `bgy_geocode`/`BGY_GEO`, `ea_geocode`/`geocode`, `name`/`ean`, plus the admin fields.
A barangay is the first 8 digits of the 14-digit geocode.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **Scan** is disabled | Tick at least one layer to check (and, for EA vs BGY, at least one barangay layer). The hint under each list says what is missing. |
| A layer is greyed out | It has no geocode field for this mode, or it is ticked in the other list. Hover it for the reason. |
| **Select All** skipped a layer | It does not look like the list's kind (or is an MBI layer). Tick it by hand. |
| "No usable geocodes" / "has no features" | That ticked layer stopped the scan. Untick it or fix its geocodes. |
| Big **Gap** in a self check | Land really outside the ticked layers (e.g. a neighbouring town not loaded). Tick that layer too, or ignore the row. |
