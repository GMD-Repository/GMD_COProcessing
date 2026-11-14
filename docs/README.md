# GMD CO Processing — Tool Documentation

One page per tool in [`gmd_processing_tools/`](../gmd_processing_tools/). Every tool opens from the
**GMD CO Processing** menu (**EA Tools** submenu) or from the plugin panel (pick it, then **Run**).
**MBI Coverage Selector** and **National-Level MBI Checker** are listed at the top level of the menu.
Each tool window also has a **Help and Information** tab with the same guidance (the two MBI checkers open
as Processing dialogs, with their help in the dialog's side panel).

Every page has a **How it works** section — a flowchart plus step-by-step explanation of what the tool does
to your data (the rules, thresholds and matching keys it uses).

| Tool | Script | What it does |
|------|--------|--------------|
| [EA MBI Checker](tools/EA_topochecker.md) | `deprecated/EA_topochecker.py` | **Deprecated (hidden from menu).** Finds gaps and overlaps between EA polygons and counts the buildings inside each |
| [National-Level MBI Checker](tools/national_mbi_checker.md) | `national_mbi_checker.py` | Finds gaps, overlaps and disputed areas between barangay polygons (up to national scale), with the MBI level of each pair |
| [EA vs Barangay Check](tools/ea_versus_bgy.md) | `ea_versus_bgy.py` | Compares EAs with their barangay (or barangays / EAs with each other) and snaps the mismatches |
| [MBI Coverage Selector](tools/mbi_coverage_selector.md) | `mbi_coverage_selector.py` | Counter-checks the remaining MBIs, chooses the covering barangay, then the covering EA, and writes the coverage status |
| [EARF Post-Processing](tools/earf_post_processing.md) | `earf_post_processing.py` | Matches POPCEN, Preliminary EARF and EA GeoPackages on geocode and exports the final deliverables |

## Recommended order for MBIs

Both steps are tabs of one window, the **MBI Coverage Selector**.

```
Barangay Selector tab                        EA Selector tab
─────────────────────                        ───────────────
Remaining MBIs ──► Load MBI Cases            ppp_mbi layer ──► Load MBI Cases
                   (counter-check)                             ► tick covering EA(s)
                   ► tick covering barangay                    ► Apply (writes assigned_ea,
                   ► Apply (writes assigned_bgy)                        coverage_status)
                   ► "Choose EAs in the EA Selector Tab →" ──► ► Export GPKG + CSV
```

1. The **Barangay Selector** tab decides *which barangay* covers each MBI (`assigned_bgy`).
2. The **EA Selector** tab decides *which EA of that barangay* covers it and writes the final
   `coverage_status` (1_Covered / 2_Not Covered / 3_Partially Covered).

## Geocodes

The tools use the PSA 14-digit geocode:

| Part | Digits | Example |
|------|--------|---------|
| Barangay | first 8 (province 3 + city/municipality 2 + barangay 3) | `02709007` |
| EA number | last 6 (`ean`) | `001000` |
| Barangay written as a geocode | barangay + `000000` | `02709007000000` |
| EA geocode | barangay + EA number | `02709007001000` |

A barangay and its EAs are matched on the first 8 digits. An EA numbered `000000` has the same
geocode as its barangay.

## Reviews

| Review | Topic |
|--------|-------|
| [Sliver rule in the MBI checks](reviews/sliver_rule_comparison.md) | When a tiny gap / overlap is dropped (0.1 m² and no building) |

## Adding or changing a tool

See [`gmd_processing_tools/README.md`](../gmd_processing_tools/README.md) for how scripts are listed in the
menu. When a tool changes, update its page here **and** its in-window Help tab.
