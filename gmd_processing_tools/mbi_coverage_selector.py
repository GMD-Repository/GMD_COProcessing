r"""MBI Coverage Selector — choose the barangay, then the EA, that covers each MBI.

One window with three tabs:

  • Barangay Selector     — counter-check the MBI layer against the remaining MBIs, then
                            tick the barangay that covers each gap / overlap / disputed area
                            and write it as 'assigned_bgy' (tab code: _mbi_bgy_selector.py).
  • EA Selector           — list each MBI under its covering barangay and choose which EA of
                            that barangay covers it; writes 'assigned_ea' and
                            'coverage_status' (tab code: _mbi_ea_selector.py).
  • Help and Information  — how both tabs work, step by step.

Workflow:
  Step 1  Barangay Selector: pick the Remaining MBI layer, the MBI Layer, the Building Points
          and the barangay layers, then click Load MBI Cases (the MBI layer is counter-checked
          against the remaining MBIs and a 'ppp_mbi' layer is added).
  Step 2  Tick the covering barangay of each case, then Apply Coverage ('assigned_bgy').
  Step 3  Click "Choose EAs in the EA Selector Tab →": the window switches to the EA Selector
          tab with the same MBI layer (and building points) already picked.
  Step 4  EA Selector: tick the EA layers of the covering barangays and click Load MBI Cases.
  Step 5  Tick each case row (its EA(s) cover the MBI), then Apply Coverage
          ('assigned_ea', 'coverage_status') and Export GPKG + CSV.

Closing or minimizing the window keeps the work of both tabs: open the tool again to resume
(until QGIS is closed). Nothing is saved to disk until Export, and Apply Coverage writes into
the MBI layer as ordinary, undoable QGIS edits.
"""
# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "MBI Coverage Selector"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/mbi_coverage_selector.svg"

import importlib.util
import os
import sys

DIALOG_NAME = "MBICoverageSelectorDialog"
DIALOG_TITLE = "MBI Coverage Selector"
TAB_BGY = "Barangay Selector"
TAB_EA = "EA Selector"
TAB_HELP = "Help and Information"
HELP_SUBTITLE = "How the Barangay Selector and the EA Selector work, step by step."

# The two tabs live in modules whose names start with "_", so the plugin menu does not list them.
BGY_MODULE = ("_mbi_bgy_selector.py", "gmd_mbi_bgy_selector")
EA_MODULE = ("_mbi_ea_selector.py", "gmd_mbi_ea_selector")
TAB_ICONS = {TAB_BGY: "icons/mbi_bgy_selector.svg", TAB_EA: "icons/mbi_ea_selector.svg"}

HELP_DARK_SWAPS = [("#111827", "#ECEFF1"), ("#1f2937", "#ECEFF1"), ("#374151", "#B0BEC5"),
                   ("#4b5563", "#B0BEC5"), ("#f9fafb", "#1E2327"), ("#e5e7eb", "#455A64")]

_H3 = 'style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;"'
_TABLE = 'width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;"'
_TH = 'width="110" style="font-weight:bold; vertical-align:top;"'
_TD = 'style="font-weight:bold; vertical-align:top;"'
_NOTE = ('style="background:#f9fafb; border:1px solid #e5e7eb; border-left:4px solid {c}; '
         'padding:10px 14px; margin:12px 0 16px 0;"')
_H2 = ('style="margin:22px 0 4px 0; color:#111827; font-size:16px; border-bottom:2px solid #374151; '
       'padding-bottom:4px;"')

HELP_HTML = f"""
<div style="font-family:'Segoe UI',Arial,sans-serif; color:#1f2937; font-size:12px; line-height:1.5;">
  <div style="border-bottom:2px solid #374151; padding-bottom:8px; margin-bottom:14px;">
    <h2 style="margin:0 0 4px 0; color:#111827; font-size:17px;">MBI Coverage Selector</h2>
    <div style="color:#4b5563;">Decide who covers each MBI (gap, overlap or disputed area) in two steps: first the
    <b>barangay</b> (<i>Barangay Selector</i> tab), then the <b>EA of that barangay</b> (<i>EA Selector</i> tab).
    Both tabs live in this one window and keep their work when you switch between them.</div>
  </div>

  <h3 {_H3}>The Two Steps</h3>
  <table {_TABLE}>
    <tr><td {_TH}>1. Barangay Selector</td>
        <td>Counter-checks the <b>MBI Layer</b> against the <b>remaining MBIs</b> (still / newly / no longer
        detected), lists every MBI with its involved barangays, and you tick the <b>covering barangay</b>.
        Writes <i>assigned_bgy</i>. See <a href="#bgy">Barangay Selector</a>.</td></tr>
    <tr><td {_TD}>2. EA Selector</td>
        <td>Lists every MBI under the covering barangay chosen in step 1 and offers only <b>that barangay's EAs</b>;
        you tick the case row and its EA(s) cover the MBI. Writes <i>assigned_ea</i> and <i>coverage_status</i>.
        See <a href="#ea">EA Selector</a>.</td></tr>
    <tr><td {_TD}>From 1 to 2</td>
        <td>In the Barangay Selector click <b>Choose EAs in the EA Selector Tab →</b>. It applies any pending choices,
        switches to the EA Selector tab and picks the same MBI layer and building points there; ticked barangay layers
        that carry <i>ea_geocode</i> are pre-ticked as EA layers. It refuses when the <i>Coverage field</i> is not
        <i>assigned_bgy</i>, or when no MBI is covered or partly covered yet. Tick the EA layers, then click
        <b>Load MBI Cases</b>.</td></tr>
  </table>

  <div {_NOTE.format(c="#4b5563")}>
    <b style="color:#111827;">Nothing Reaches Disk Without Your Say-So:</b>
    Ticking only changes the viewer. <i>Apply Coverage</i> writes into the MBI layer inside an ordinary QGIS edit
    session — keep it with Toggle Editing's <b>Save Edits</b>, or undo with <b>Edit &gt; Undo</b> /
    <b>Discard Edits</b>. The remaining MBI layer, the barangay layers and the EA layers are never edited. Files are
    only created by <i>Export GPKG + CSV</i>.
  </div>

  <div {_NOTE.format(c="#2e7d32")}>
    <b style="color:#111827;">Closed It by Accident?</b>
    <b>Minimize</b> or <b>Close</b> only put the window away: open <i>MBI Coverage Selector</i> again and both tabs
    come back as you left them — loaded cases, ticks and log (until QGIS is closed); the layer lists are refreshed.
    Switching tab clears the other tab's map highlights.
  </div>

  <!-- ═══════════════════════════ BARANGAY SELECTOR ═══════════════════════════ -->
  <a name="bgy"></a><h2 {_H2}>Barangay Selector</h2>

  <h3 {_H3}>Quick Start</h3>
  <ol style="margin-top:4px;">
    <li><b>Pick the Remaining MBI Layer</b> (top left) — the MBIs the PSOs declared pending. Not loaded yet? Click
        the folder button (<b>Browse GPKG…</b>) and open the MBI Validator's <i>ref_mbi_reviewed-….gpkg</i>: its
        <i>remaining_cases</i> layer is added and picked for you.</li>
    <li><b>Pick the MBI Layer</b> — the MBIs to counter-check and cover (e.g. a National-Level MBI Checker result;
        it needs <i>case_uuid</i>).</li>
    <li>Pick the <b>Building Points</b> layer (and its <i>Geocode field</i>) so each case and barangay shows its
        building count.</li>
    <li><b>Tick your Barangay Layers</b> — every layer the involved barangays come from.</li>
    <li>Click <b style="color:#2e7d32;">Load MBI Cases</b> (green). Every MBI of the MBI Layer is compared with the
        remaining MBIs, Disputed ones included; a <i>ppp_mbi</i> layer with the result is added and its cases are
        listed. <b>Cancel</b> stops a running load; <b>Clear</b> empties the viewer and the log.</li>
    <li>Read the <b>Detection</b> column: <b>Still detected</b>, <b>Newly detected</b> (the whole row is
        <b style="color:#558b2f;">light green</b>), <b>No longer detected</b> (grey, nothing to cover) or
        <b>Not checked</b> (blue-grey cell: the MBI Layer has no MBI of its type — still to be covered).</li>
    <li>In the list, <b>tick the Role / Status box of the barangay that covers</b> each MBI. An overlap that runs
        across three or more barangays can be <b>shared out</b>: tick more than one barangay and each keeps only its
        own part (see <i>Shared Overlaps</i> below).</li>
    <li>Click <b>Apply Coverage</b> to write your choices into the <i>ppp_mbi</i> layer (<i>assigned_bgy</i>).
        No coverage status is written here — the EA Selector writes it.</li>
    <li>Optional: <b>Join MBI to Barangays</b> and <b>Export GPKG + CSV</b>.</li>
    <li><b>Next:</b> click <b>Choose EAs in the EA Selector Tab →</b>.</li>
  </ol>

  <h3 {_H3}>The Counter-Check (on Load MBI Cases)</h3>
  <table {_TABLE}>
    <tr><td {_TH}>When it runs</td>
        <td>When a <b>Remaining MBI Layer</b> is picked and the MBI Layer is not counter-checked yet (it has no
        <i>detection</i> field). With <i>Only selected MBI features</i> ticked, only the selected MBIs are compared.
        Without a remaining layer — or when you pick a <i>ppp_mbi</i> layer made earlier — the MBI Layer is loaded as
        it is.</td></tr>
    <tr><td {_TD}>Still detected</td>
        <td>An MBI that <b>shares area</b> with a remaining MBI of the same type (gap with gap, overlap with overlap,
        disputed with disputed) — the discrepancy declared pending is still there. A remaining <b>Disputed</b> MBI is
        still detected by any MBI sharing its area. Hover the Detection cell for the remaining MBI's
        <i>case_uuid</i>, its declared status and remarks.</td></tr>
    <tr><td {_TD}>Newly detected<br><span style="background:#dcedc8;">&nbsp;light green&nbsp;</span></td>
        <td>An MBI that matches <b>no</b> remaining MBI. The case row and its barangay rows are light green and listed
        first.</td></tr>
    <tr><td {_TD}>No longer detected<br><span style="background:#e0e0e0;">&nbsp;grey&nbsp;</span></td>
        <td>A remaining MBI that no MBI of the MBI Layer matches. Listed for your information only — there is nothing
        to cover, so it cannot be ticked and Apply Coverage leaves it untouched.</td></tr>
    <tr><td {_TD}>Not checked<br><span style="background:#cfd8dc;">&nbsp;blue-grey&nbsp;</span></td>
        <td>A remaining MBI of a type the MBI Layer does not contain (e.g. remaining overlaps when the MBI Layer holds
        gaps only). It was never counter-checked, so it is <b>still an open case</b>: it is listed with its involved
        barangays and you tick the covering barangay as usual.</td></tr>
    <tr><td {_TD}>Matching rule</td>
        <td>Same as GEMMA's MBI Validator: MBIs only match when they overlap in <b>area</b>; touching along an edge does
        not count. One MBI overlapping several remaining MBIs lists them all in <i>ref_case_uuid</i>.</td></tr>
    <tr><td {_TD}>ppp_mbi layer</td>
        <td>A temporary layer named <b>ppp_mbi</b> (<i>ppp</i> = province code of the barangay layers, e.g.
        <i>027_mbi</i>): <b>one row per involved barangay</b>, all sharing the MBI's <i>case_uuid</i> and geometry, plus
        <b>detection</b>, <b>ref_case_uuid</b>, <b>ref_mbi_type</b>, <b>ref_status</b> and <b>ref_remarks</b>. It
        replaces the MBI Layer in the panel; coloured by detection on the map. Right-click &gt; <b>Make Permanent</b>
        to keep it.</td></tr>
  </table>

  <h3 {_H3}>Inputs (left panel)</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Remaining MBI Layer</td>
        <td>The MBIs declared pending: the <i>remaining_cases</i> (or <i>pending_cases</i>) layer of the MBI
        Validator's GeoPackage, or a <i>ref_mbi_cases</i> layer — picked automatically when loaded. Its
        <i>case_uuid</i>, <i>mbi_type</i>, status (<i>mbi_status</i> / <i>ref_status</i>) and remarks
        (<i>pso_remarks</i> / <i>ref_remarks</i>) are read. Leave it empty to load the MBI Layer without a
        counter-check.</td></tr>
    <tr><td {_TD}>MBI Layer</td>
        <td>The MBIs to cover (needs <i>case_uuid</i>, plus <i>geocode</i> / <i>involved_areas</i>). Tick <i>Only
        selected MBI features</i> to load part of it, and <i>Resume saved coverage choices</i> to pre-tick barangays
        already applied to it.</td></tr>
    <tr><td {_TD}>Building Points</td>
        <td><b>Case row:</b> building points inside the MBI. <b>Barangay rows:</b> the points whose <i>Geocode
        field</i> (auto: bgy_geocode, geocode, then ea_geocode) is the barangay's geocode <b>or starts with it</b>.
        Optional.</td></tr>
    <tr><td {_TD}>Barangay Layers</td>
        <td>Click every barangay layer the involved barangays come from, or use <b>Select All</b> / <b>Clear</b>
        (matched on <i>geocode</i> /
        <i>bgy_geocode</i>; names from <i>barangay</i> / <i>bgy_name</i> / <i>name</i>). Involved barangays none of
        the layers contain are shown in red. The barangay shapes are what <b>shared overlaps</b> are clipped
        to.</td></tr>
  </table>

  <h3 {_H3}>Reading the Case List</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Case row (bold)</td>
        <td>One MBI: its <b>Detection</b>, MBI level, barangays and cities / municipalities involved and the <b>Bldg
        Pts</b> inside it. Status is <b>Pending — tick the covering barangay below</b> (peach) until a barangay is
        chosen, then <b>✔ Done — covered by &lt;barangay&gt;</b> (green), or <b>Partly covered (x%)</b> (cream) when
        the barangays ticked in a shared overlap do not fill the whole MBI yet.</td></tr>
    <tr><td {_TD}>Barangay rows (below)</td>
        <td>Each involved barangay with its own <b>Bldg Pts</b>. <b>Tick the Role / Status box of the covering
        barangay.</b> A gap, an MBI between <b>two</b> barangays, or an overlap that cannot be shared out takes one
        barangay — ticking one unticks the other. Unticking every barangay makes the case Pending again.</td></tr>
    <tr><td {_TD}>Moving around</td>
        <td>Click a row to select it on the map, double-click to zoom (always the whole MBI), right-click for more
        (set / add / remove a covering barangay, mark pending, copy case IDs). Use the search box and the
        <i>Show</i> filter to find cases; click a column header to sort.</td></tr>
  </table>

  <h3 {_H3}>Choices and Shared Overlaps</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Overlap</td>
        <td>Ticked barangay: <b>✔ Covers — keeps the overlap</b>. Others: <b>✖ Gives up the overlap — cut out of
        it</b>.</td></tr>
    <tr><td {_TD}>Gap</td>
        <td>Ticked barangay: <b>✔ Covers — its boundary is extended over the gap</b>. Others: <b>Not extended —
        stays as it is</b>.</td></tr>
    <tr><td {_TD}>Shared overlap</td>
        <td>An overlap across <b>three or more</b> barangays whose layers are all ticked: tick several and each
        <b>✔ Covers its part (x% of the MBI)</b> — the MBI clipped to it. Where two ticked barangays contain the same
        piece, the one <b>ticked first</b> keeps it. A leftover of at most 0.5 m² or 0.1% of the MBI (whichever is
        larger) counts as fully covered. Others: <b>✖ Gives up</b> what the ticked ones keep.</td></tr>
    <tr><td {_TD}>On the map</td>
        <td>Select a barangay row to see its part: <b style="color:#2e7d32;">green</b> = what it keeps,
        <b style="color:#1565c0;">dashed blue</b> = what it would keep if ticked. Select the case row to see every
        kept part and, in <b style="color:#e65100;">orange</b>, what no ticked barangay keeps yet.</td></tr>
  </table>

  <h3 {_H3}>Apply, Join and Export</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Apply Coverage</td>
        <td>Writes the <b>Coverage field</b> (default <i>assigned_bgy</i>): the covering barangay geocode on every row
        of the case, comma-separated for a shared overlap. Keep the name <i>assigned_bgy</i> — the EA Selector reads
        it. No-longer-detected MBIs are left untouched.</td></tr>
    <tr><td {_TD}>Join MBI to Barangays</td>
        <td>Adds a <i>Barangays + MBI</i> layer: the ticked barangay layers plus one feature per kept MBI part, with
        the covering barangay's attributes, <b>bgy_part</b> = <i>MBI</i> / <b>is_mbi</b> = <i>1</i>, drawn
        <b style="color:#c51162;">hatched magenta</b>. Kept parts are cut out of the barangays that released
        them. Enabled only when barangay layers are ticked.</td></tr>
    <tr><td {_TD}>Export GPKG + CSV</td>
        <td><i>ppp_mbi_&lt;date&gt;_&lt;time&gt;.gpkg</i> (the MBI layer, plus <i>mbi_bgy_joined</i> when barangay
        layers are ticked and kept parts exist) and a CSV
        decision log, in the <i>Export folder</i> (asked when empty). Pending choices are applied first.</td></tr>
  </table>

  <h3 {_H3}>If Something Looks Wrong</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Load MBI Cases is greyed out</td>
        <td>Pick the MBI Layer — it needs a <i>case_uuid</i> field.</td></tr>
    <tr><td {_TD}>No Detection column</td>
        <td>No Remaining MBI Layer was picked, so nothing was counter-checked. Pick it (or Browse GPKG…) and load
        again.</td></tr>
    <tr><td {_TD}>Everything is Newly detected</td>
        <td>No MBI shares area with a remaining MBI: check the remaining layer is of the same province /
        municipality and that its <i>mbi_type</i> values say Gap / Overlap / Disputed.</td></tr>
    <tr><td {_TD}>Barangay in red / "not found"</td>
        <td>The barangay's layer is not ticked, or its geocode differs from the MBI layer's. Tick the layer, then
        load again.</td></tr>
    <tr><td {_TD}>Can only tick one barangay in an overlap</td>
        <td>Only an overlap across three or more barangays, with <b>all</b> their layers ticked, can be shared
        out.</td></tr>
    <tr><td {_TD}>Made a mistake</td>
        <td>Before Apply: <b>Reset Choices</b> or untick. After Apply: <b>Edit &gt; Undo</b> or <b>Discard Edits</b>
        on the MBI layer (before saving).</td></tr>
  </table>

  <!-- ═══════════════════════════ EA SELECTOR ═══════════════════════════ -->
  <a name="ea"></a><h2 {_H2}>EA Selector</h2>

  <h3 {_H3}>Quick Start</h3>
  <ol style="margin-top:4px;">
    <li><b>Pick the MBI Results Layer</b> — the <i>ppp_mbi</i> layer from the Barangay Selector tab, after you
        applied the covering barangays there (<b>Choose EAs in the EA Selector Tab →</b> picks it for you). No check
        is run here: the cases are only loaded.</li>
    <li><b>Tick your EA Layers</b> — the layers of the covering barangays' EAs.</li>
    <li>Optional: pick <b>Building Points</b> so each case and EA shows its building count.</li>
    <li>Click <b style="color:#2e7d32;">Load MBI Cases</b> (green). <b>Cancel</b> stops a running load;
        <b>Clear</b> empties the viewer and the log.</li>
    <li><b>Tick the Role / Status box on the case row</b> — the EA(s) listed under it cover the MBI. An overlap
        across several EAs is <b>shared out</b> between them. To pick one EA yourself, <b>right-click its
        row</b>.</li>
    <li>Click <b>Apply Coverage</b>, then <b>Export GPKG + CSV</b>.</li>
  </ol>

  <h3 {_H3}>Inputs (left panel)</h3>
  <table {_TABLE}>
    <tr><td {_TH}>MBI Results Layer</td>
        <td>The <i>ppp_mbi</i> layer of the Barangay Selector (needs <i>case_uuid</i>). Tick <i>Only selected MBI
        features</i> to load part of it and <i>Resume saved coverage choices</i> to pre-tick EAs already stored in
        it. A layer with <i>assigned_bgy</i> automatically lists each MBI under its covering barangay and offers only
        that barangay's EAs. <b>EAs within … m of a gap</b> (shown only for a layer with <i>assigned_bgy</i>): how
        close an EA must be to a gap to be offered (default 5 m); the same distance is used for an overlap that no
        EA holds more than 0.5 m² of.</td></tr>
    <tr><td {_TD}>EA Layers</td>
        <td>Click every EA layer the covering barangays' EAs come from, or use <b>Select All</b> / <b>Clear</b>
        (matched on <i>ea_geocode</i>). EAs none of
        the layers contain are shown in red. The EA shapes are what shared overlaps are clipped to.</td></tr>
    <tr><td {_TD}>Building Points</td>
        <td>Optional. <b>Case row:</b> building points inside the MBI. <b>EA rows:</b> the points whose <i>Geocode
        field</i> (auto: ea_geocode, then geocode) matches the EA.</td></tr>
    <tr><td {_TD}>Barangay name fallback</td>
        <td>A row with no barangay name gets it by its geocode from the other MBI rows, else from the ticked EA layers
        (<i>barangay</i> / <i>bgy_name</i> / <i>brgy_name</i> / <i>barangay_name</i>).</td></tr>
  </table>

  <h3 {_H3}>Which EAs Are Offered</h3>
  <table {_TABLE}>
    <tr><td {_TH}>One case per barangay</td>
        <td>Each MBI is listed under its covering barangay. An overlap shared out between barangays is listed
        <b>once per barangay</b>, each holding only its part (e.g. <i>its part (60%)</i>). MBIs still pending in the
        Barangay Selector and <i>No longer detected</i> MBIs are skipped; the log says how many.</td></tr>
    <tr><td {_TD}>The barangay's EAs</td>
        <td>With 14-digit geocodes the <b>first 8 digits</b> are the barangay: <i>02709007000000</i> gets the EAs
        <i>02709007001000</i>, <i>02709007002000</i>, … <b>Overlap / disputed area</b>: the EAs holding part of it.
        <b>Gap</b>: the EAs bordering it or within the gap distance; one EA absorbs it. <b>None that close</b>: the
        3 nearest EAs, with their distance.</td></tr>
  </table>

  <h3 {_H3}>Reading the Case List</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Case row (bold)</td>
        <td><b>Carries the tickbox</b> — tick it and the EA(s) below cover the MBI. Status is <b>☐ Tick to cover it
        with the EAs below</b> (peach), then <b>✔ Done — covered by &lt;EA&gt;</b> (green), or <b>Partly covered
        (x%)</b> (cream) when the EAs of a shared overlap do not fill the MBI yet.</td></tr>
    <tr><td {_TD}>EA rows (below)</td>
        <td>Each EA with its barangay and <b>Bldg Pts</b> — <b>information only</b>. In a gap, or an overlap that
        cannot be shared out, one EA covers the case: the one holding most of the MBI (for a gap, the one with most
        tagged buildings, else the nearest). <b>Right-click an EA row</b> to override, or to add / remove one EA of a
        shared overlap.</td></tr>
    <tr><td {_TD}>Shared overlap</td>
        <td>An overlap held by <b>two or more</b> EAs (more than 0.5 m² each) can be shared out. Each covering EA
        <b>✔ Covers its part</b>; where two contain the same piece, the EA with the largest part keeps it. Right-click
        <b>Add</b> puts the EA at the end of the order. A leftover of at most 0.5 m² or 0.1% of the MBI (whichever is
        larger) counts as fully covered. Others <b>✖ Give up</b> the parts kept. On the map: <b style="color:#2e7d32;">green</b> = kept,
        <b style="color:#1565c0;">dashed blue</b> = would keep, <b style="color:#e65100;">orange</b> = kept by no
        EA yet.</td></tr>
  </table>

  <h3 {_H3}>Apply Coverage — Fields Written</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Coverage field<br>(default <i>assigned_ea</i>)</td>
        <td>Code(s) of the covering EA(s), on the covering barangay's row(s). Empty while Pending.</td></tr>
    <tr><td {_TD}>Status field<br>(default <i>coverage_status</i>)</td>
        <td><b>1_Covered</b>, <b>2_Not Covered</b> (Pending, or a barangay not chosen) or <b>3_Partially
        Covered</b> (part of a shared overlap has no covering EA yet). Only the EA Selector writes it.
        <i>assigned_bgy</i> is never changed.</td></tr>
    <tr><td {_TD}>ppp_mbi_ea_clipped</td>
        <td>Apply also adds a temporary layer with the same fields, split into <b>one row per ticked EA</b> (the MBI
        clipped to that EA, <i>1_Covered</i>), plus the not-covered rows. Each Apply replaces it.</td></tr>
    <tr><td {_TD}>Export GPKG + CSV</td>
        <td><i>mbi_ea_coverage_&lt;date&gt;_&lt;time&gt;.gpkg</i> (<i>mbi_ea_coverage</i> + <i>mbi_ea_coverage_parts</i>)
        and a CSV decision log. Pending choices are applied first.</td></tr>
  </table>

  <h3 {_H3}>If Something Looks Wrong</h3>
  <table {_TABLE}>
    <tr><td {_TH}>Load MBI Cases is greyed out</td>
        <td>Pick the <i>ppp_mbi</i> layer (it needs <i>case_uuid</i>) and tick at least one EA layer with an
        <i>ea_geocode</i> field.</td></tr>
    <tr><td {_TD}>Fewer cases than MBIs</td>
        <td>MBIs still pending in the Barangay Selector (no <i>assigned_bgy</i>) are skipped — assign them there,
        Apply, then load again.</td></tr>
    <tr><td {_TD}>"No EA found"</td>
        <td>No EA of the covering barangay is in the ticked EA layers. Tick that barangay's EA layer, then load
        again.</td></tr>
    <tr><td {_TD}>Only one EA covers an overlap</td>
        <td>The log lists these cases with the reason — usually no other EA holds more than 0.5 m² of the MBI. Check
        the EA layer lines up with the MBI on the map.</td></tr>
    <tr><td {_TD}>Field name turns red</td>
        <td>Use letters, digits and underscores only; the Coverage field, Status field and <i>ea_role</i> must all
        differ, and <i>assigned_bgy</i> cannot be used.</td></tr>
    <tr><td {_TD}>Made a mistake</td>
        <td>Before Apply: <b>Reset Choices</b> or untick the case. After Apply: <b>Edit &gt; Undo</b> or
        <b>Discard Edits</b> on the MBI layer.</td></tr>
  </table>
</div>
"""


def _tools_dir():
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))
    from qgis.core import QgsApplication
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins",
                        "GMD_COProcessing", "gmd_processing_tools")


def _load_module(file_name, module_name):
    """Load one tab's module (_mbi_bgy_selector.py / _mbi_ea_selector.py) from this folder."""
    path = os.path.join(_tools_dir(), file_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{DIALOG_TITLE}: missing {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_dialog_class():
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QIcon

    bgy_mod = _load_module(*BGY_MODULE)
    ea_mod = _load_module(*EA_MODULE)
    BgyPanel = bgy_mod.make_panel_class()
    EAPanel = ea_mod.make_panel_class()
    themes = bgy_mod.THEMES

    def icon(rel):
        path = os.path.join(_tools_dir(), rel)
        return QIcon(path) if os.path.exists(path) else QIcon()

    class CoverageSelectorDialog(QtWidgets.QDialog):
        def __init__(self, iface, parent=None):
            super().__init__(parent)
            self.setObjectName(DIALOG_NAME)
            self.setWindowTitle(DIALOG_TITLE)
            self.setWindowIcon(icon(TOOL_ICON))
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
            # Not deleted on close: closing only hides the window, so reopening the tool
            # resumes the work of both tabs (see show_dialog).
            self.setAttribute(Qt.WA_DeleteOnClose, False)
            self.setMinimumSize(900, 600)
            self.resize(1150, 780)
            self.iface = iface
            self.is_dark = self.palette().color(self.palette().Window).lightness() < 128

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(8)
            self.title_label = QtWidgets.QLabel(DIALOG_TITLE)
            root.addWidget(self.title_label)
            self.sub_label = QtWidgets.QLabel()
            self.sub_label.setWordWrap(True)
            root.addWidget(self.sub_label)
            self.sep_line = QtWidgets.QFrame()
            self.sep_line.setFrameShape(QtWidgets.QFrame.HLine)
            root.addWidget(self.sep_line)

            self.bgy_panel = BgyPanel(iface)
            self.ea_panel = EAPanel(iface)
            self.bgy_panel.ea_panel_hook = self.show_ea_tab
            self.panels = (self.bgy_panel, self.ea_panel)
            self.subtitles = {self.bgy_panel: bgy_mod.DIALOG_SUBTITLE, self.ea_panel: ea_mod.DIALOG_SUBTITLE}

            self.tabs = QtWidgets.QTabWidget()
            self.tabs.addTab(self.bgy_panel, icon(TAB_ICONS[TAB_BGY]), TAB_BGY)
            self.tabs.addTab(self.ea_panel, icon(TAB_ICONS[TAB_EA]), TAB_EA)
            help_tab = QtWidgets.QWidget()
            hl = QtWidgets.QVBoxLayout(help_tab)
            hl.setContentsMargins(8, 8, 8, 8)
            self.help_text = QtWidgets.QTextBrowser()
            self.help_text.setOpenExternalLinks(True)
            hl.addWidget(self.help_text)
            self.tabs.addTab(help_tab, TAB_HELP)
            self.tabs.currentChanged.connect(self._on_tab)
            root.addWidget(self.tabs, 1)

            close_row = QtWidgets.QHBoxLayout()
            close_row.addStretch()
            min_btn = QtWidgets.QPushButton("Minimize")
            min_btn.setMinimumWidth(100)
            min_btn.setToolTip("Minimize this window; the work in both tabs stays as it is.")
            min_btn.clicked.connect(self.showMinimized)
            close_row.addWidget(min_btn)
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.setMinimumWidth(100)
            close_btn.setToolTip("Hide this window. Open the tool again to resume where you left off\n"
                                 "(loaded cases and ticks of both tabs are kept until QGIS is closed).")
            close_btn.clicked.connect(self.close)
            close_row.addWidget(close_btn)
            root.addLayout(close_row)

            self._on_tab(self.tabs.currentIndex())
            self.apply_theme()

        # ──────────────── tabs ────────────────
        def _on_tab(self, index):
            page = self.tabs.widget(index)
            self.sub_label.setText(self.subtitles.get(page, HELP_SUBTITLE))
            # Map highlights belong to the tab in view: drop the other tab's.
            for panel in self.panels:
                if panel is not page:
                    panel.clear_bands()

        def show_ea_tab(self):
            """Used by the Barangay Selector's 'Choose EAs in the EA Selector Tab →'."""
            self.tabs.setCurrentWidget(self.ea_panel)
            return self.ea_panel

        # ──────────────── theme ────────────────
        def apply_theme(self):
            t = themes[self.is_dark]
            self.title_label.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {t['title']}; padding: 2px 0;")
            self.sub_label.setStyleSheet(f"color: {t['sub']}; font-size: 11px; margin-bottom: 4px;")
            self.sep_line.setStyleSheet(f"color: {t['line']};")
            self.tabs.setStyleSheet(t["tabs"])
            html = HELP_HTML
            if self.is_dark:
                for a, b in HELP_DARK_SWAPS:
                    html = html.replace(a, b)
            self.help_text.setStyleSheet(t["help"])
            self.help_text.setHtml(html)

        def showEvent(self, event):
            super().showEvent(event)
            dark = self.palette().color(self.palette().Window).lightness() < 128
            if dark != self.is_dark:
                self.is_dark = dark
                self.apply_theme()

        # ──────────────── hide / resume ────────────────
        def _hidden(self):
            for panel in self.panels:
                panel.on_window_hidden()

        def done(self, result):
            # Close / Esc only hides the window (work is resumed on reopen).
            self._hidden()
            super().done(result)

        def closeEvent(self, event):
            self._hidden()
            super().closeEvent(event)

        def resume(self):
            """Bring a hidden / minimized window back with the work of both tabs intact."""
            for panel in self.panels:
                panel.resume()
            self.showNormal()
            self.raise_()
            self.activateWindow()

    return CoverageSelectorDialog


# ─────────────────── entry point ───────────────────

def show_dialog():
    """Open the MBI Coverage Selector window (non-modal, so the map stays usable)."""
    from qgis.PyQt.QtWidgets import QDialog

    qgis_iface = globals().get("iface")
    if qgis_iface is None:
        try:
            from qgis.utils import iface as qgis_iface
        except ImportError:
            qgis_iface = None
    main = qgis_iface.mainWindow() if qgis_iface is not None else None

    if main is not None:
        # Resume a window that was closed (hidden) or minimized, keeping its work.
        for old in main.findChildren(QDialog, DIALOG_NAME):
            if hasattr(old, "resume"):
                old.resume()
                return old
            old.close()
            old.deleteLater()

    dialog = make_dialog_class()(qgis_iface, main)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


if "qgis" in sys.modules and not os.environ.get("MBI_COVERAGE_SELECTOR_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > MBI Coverage Selector.")
