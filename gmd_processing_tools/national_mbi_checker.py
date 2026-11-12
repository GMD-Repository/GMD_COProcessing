"""National-Level MBI Checker (Gaps, Overlaps & Disputed Areas) - checks barangay boundaries and building points.

Identifies boundary discrepancies (MBIs) between barangay polygons, at any scale up to the
whole country (merge as many barangay layers as needed):
  - Overlaps: land inside two barangay polygons
  - Gaps: holes enclosed by the barangays but inside none of them
  - Disputed areas: barangay polygons whose 'boundary' field is 'Contested'. Only when
    'Include Disputed Areas' is ticked; unticked, they are dropped before the check runs
  - One row per ordered pair of involved areas (A -> B and B -> A), with the MBI level
    between them (Inter-Region, Inter-Province, Inter-City/Mun, Inter-Barangay, Within-Barangay)
  - Counts building points inside each overlap / gap
  - Optional: exports the merged MBI layer (with mbi_type / mbi_status fields) as a GPKG

How to run:
  - From the GMD CO Processing panel: select the script and click 'Run'.
  - From the QGIS menu: GMD CO Processing > EA Tools > National-Level MBI Checker.
  - Headless: fill in BGY_LAYER_NAMES and BLDG_LAYER_NAMES in CONFIG below.
  - Console / drag-and-drop: drag onto QGIS or run via Python Console editor.

Ported from gaps_overlaps_checker_console.py (Geospatial Management Division).
"""

# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "National-Level MBI Checker"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/mbi_checker.svg"

import math
import os
import re
from datetime import datetime
from itertools import combinations

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMessageLog,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterMultipleLayers,
    QgsProject,
    QgsSpatialIndex,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QApplication
import processing

# ============================ CONFIG ============================
# Layer names exactly as shown in the Layers panel. Leave both empty []
# to choose the layers in the dialog instead.
BGY_LAYER_NAMES = []      # e.g. ['Barangay_R1', 'Barangay_R2']
BLDG_LAYER_NAMES = []     # e.g. ['BldgPts_R1', 'BldgPts_R2']

# Take the disputed (boundary = Contested) polygons into account.
# False: they are removed from the barangay layers before anything is checked -
#        the run behaves as if those polygons had never been digitised.
# True:  they are reported as Disputed Areas, kept out of the gaps and still
#        scanned for overlaps (as in GEMMA's checker).
INCLUDE_DISPUTED = False

# 0 = Overlaps and Gaps, 1 = Overlaps Only, 2 = Gaps Only,
# 3 = Disputed Only (needs INCLUDE_DISPUTED = True)
RUN_MODE = 0

# Export the merged MBI layer as GPKG to EXPORT_FOLDER (only when RUN_MODE = 0)
EXPORT_MBI = False
EXPORT_FOLDER = r"C:\PSA-GIS\2026 1Map\Preliminary Output"

# Style (.qml) applied to the output layers. Set to '' to skip styling.
_LOCAL_STYLE = os.path.join(os.path.dirname(__file__), "styles", "mbi_checker.qml")
STYLE_PATH = (
    _LOCAL_STYLE
    if os.path.exists(_LOCAL_STYLE)
    else r"C:\Users\PSA\Desktop\gmd_pipeline\gmd_pipeline\styles\mbi_checker.qml"
)
# ================================================================

# --- Output schema: one row per ordered pair (this area -> other area) ---
AREA_KEYS = ['geocode', 'region', 'province', 'city_mun', 'barangay']
# (name, refactor type: 10=text, 4=int8, 6=double). No field aliases.
PAIR_FIELDS = (
    [('case_uuid', 10)]
    + [(k, 10) for k in AREA_KEYS]
    + [('mbi_level', 10),
       ('involved_areas', 10),
       ('involved_bgys', 10),
       ('count_involved_areas', 4),
       ('num_bldg_pts', 6),
       ('est_hhcount', 6)]
)

# Household-count field of the building points, summed into est_hhcount
# (first name found, case-insensitive). None found -> est_hhcount stays empty.
HH_FIELD_CANDIDATES = ['est_hhcount', 'hhcount', 'hh_count', 'est_hh', 'household_count',
                       'households', 'no_hh', 'num_hh']
HH_WEIGHT = 'gmd_hh_w'   # internal numeric copy of that field

# 'Analysis to Run' entries. The last one needs 'Include Disputed Areas' ticked;
# QGIS cannot hide an entry of a Processing dropdown, so it says so in its label.
RUN_MODE_LABELS = ['Overlaps and Gaps (plus Disputed Areas when included)',
                   'Overlaps Only',
                   'Gaps Only',
                   'Disputed Only (needs Include Disputed Areas)']
DISPUTED_MODE = 3

# Disputed Areas: barangay polygons whose 'boundary' field holds this value (any case).
# They are reported as their own MBI type and their footprint is kept out of the gaps.
# Written to every exported row: the MBI types this run looked for. It lets a tool
# reading the export (the MBI Barangay Selector) tell 'was not looked for' from 'was
# looked for and not found' - without it, ignored disputed areas read as resolved.
SCOPE_FIELD = 'mbi_scope'

DISPUTED_FIELD = 'boundary'
DISPUTED_VALUE = 'contested'
# A barangay within this distance (metres, EPSG:3857) of a disputed area is involved in it.
DISPUTED_NEAR_M = 0.50

# A gap must share at least this much edge (metres, EPSG:3857) with an area
# for that area to count as involved; corner-only touches are ignored.
SHARED_EDGE_MIN_M = 0.01

WEB_MERCATOR_OP = ('+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad '
                   '+step +proj=webmerc +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84')


def _val(feat, field):
    if field not in feat.fields().names():
        return None
    v = feat[field]
    if v is None or (hasattr(v, 'isNull') and v.isNull()):
        return None
    return v


def area_attrs(feat):
    """Barangay attributes of a boundary polygon."""
    out = {}
    for key in AREA_KEYS:
        v = _val(feat, key)
        out[key] = None if v is None else str(v)
    return out


def resolve_field(layer, name):
    """The layer's field called 'name', whatever its letter case - or None."""
    return next((n for n in layer.fields().names() if n.lower() == name.lower()), None)


def webmerc_metre(geom):
    """EPSG:3857 map units in one real metre at this geometry's latitude.
    Web Mercator stretches distances by 1 / cos(latitude)."""
    try:
        pt = geom.centroid().asPoint()
        lat = math.atan(math.sinh(pt.y() / 6378137.0))
        return 1.0 / max(math.cos(lat), 1e-6)
    except Exception:
        return 1.0


def is_disputed(feat, field=DISPUTED_FIELD):
    """True when the polygon's DISPUTED_FIELD marks it as a disputed (contested) area."""
    v = _val(feat, field)
    return v is not None and str(v).strip().lower() == DISPUTED_VALUE


def not_disputed_expression(field=DISPUTED_FIELD):
    """QGIS expression selecting the polygons that are NOT disputed."""
    return ("coalesce(lower(trim(to_string(\"%s\"))), '') <> '%s'"
            % (field, DISPUTED_VALUE))


def disputed_expression(field=DISPUTED_FIELD):
    """QGIS expression selecting the disputed polygons."""
    return ("coalesce(lower(trim(to_string(\"%s\"))), '') = '%s'"
            % (field, DISPUTED_VALUE))


def piece_attrs(feat):
    return {k: _val(feat, k) for k in ('gmd_uuid', 'mbi_area', 'num_bldg_pts', 'est_hhcount')}


def find_hh_fields(layer):
    """Household-count fields present in the building points, in HH_FIELD_CANDIDATES order."""
    names = {n.lower(): n for n in layer.fields().names()}
    return [names[c] for c in HH_FIELD_CANDIDATES if c in names]


def pair_level(a, b):
    """MBI level between this area (a) and the other area (b)."""
    if b is not None:
        for key, level in (('region', '1_Inter-Region'), ('province', '2_Inter-Province'),
                           ('city_mun', '3_Inter-City/Municipality'), ('barangay', '4_Inter-Barangay')):
            if a[key] != b[key]:
                return level
    return '5_Within-Barangay'


def group_level(areas):
    """MBI level across every involved area (used where there is no A -> B pair)."""
    for key, level in (('region', '1_Inter-Region'), ('province', '2_Inter-Province'),
                       ('city_mun', '3_Inter-City/Municipality'), ('barangay', '4_Inter-Barangay')):
        if len({a[key] for a in areas}) > 1:
            return level
    return '5_Within-Barangay'


def unique_areas(areas):
    """Drop repeats, keeping the order. Multipart barangays come back as several
    polygons; they are one involved area, not several."""
    out, seen = [], set()
    for a in areas:
        key = tuple(a[k] for k in AREA_KEYS)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def province_file_base(code, name):
    """'027', 'Ifugao' -> '027_Ifugao_mbi' (spaces and symbols in the name become '_')."""
    clean = re.sub(r"[^\w]+", "_", str(name or "").strip()).strip("_")
    return f"{code}_{clean}_mbi" if clean else f"{code}_mbi"


def pair_field_mapping(extra=()):
    """native:refactorfields mapping: PAIR_FIELDS + extra (name, expression) text fields."""
    m = [{'alias': '', 'comment': '', 'expression': f'"{name}"', 'length': 0, 'name': name,
          'precision': 0, 'sub_type': 0, 'type': ftype, 'type_name': ''}
         for name, ftype in PAIR_FIELDS]
    m += [{'alias': '', 'comment': '', 'expression': expr, 'length': 0, 'name': name,
           'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'}
          for name, expr in extra]
    return m


class ConsoleFeedback(QgsProcessingFeedback):
    """Prints processing messages and progress to the console / panel log."""

    def __init__(self):
        super().__init__()
        self._last = -10
        self.progressChanged.connect(self._on_progress)

    def _on_progress(self, value):
        if int(value) >= self._last + 10:
            self._last = int(value)
            print(f"  ... {int(value)}%")

    def pushInfo(self, info):
        print(info)

    def pushWarning(self, warning):
        print(f"WARNING: {warning}")

    def reportError(self, error, fatalError=False):
        print(f"ERROR: {error}")

    def pushCommandInfo(self, info):
        pass

    def pushDebugInfo(self, info):
        pass

    def pushConsoleInfo(self, info):
        pass


class MBIStylePostProcessor(QgsProcessingLayerPostProcessorInterface):
    # Used when run through the dialog. In headless mode, styling is applied
    # by load_results() below instead.
    def __init__(self, style_path):
        super().__init__()
        self.style_path = style_path

    def postProcessLayer(self, layer, context, feedback):
        if layer and self.style_path and os.path.exists(self.style_path):
            layer.loadNamedStyle(self.style_path)
            layer.triggerRepaint()


class NationalMBIChecker(QgsProcessingAlgorithm):

    INPUT1 = 'INPUT1'
    INPUT2 = 'INPUT2'
    INCLUDE_DISPUTED = 'INCLUDE_DISPUTED'
    RUN_MODE = 'RUN_MODE'
    EXPORT_MBI = 'EXPORT_MBI'
    EXPORT_DIR = 'EXPORT_DIR'

    def __init__(self):
        super().__init__()
        self.post_processors = []

    def name(self):
        return 'national_mbi_checker'

    def displayName(self):
        return 'National-Level MBI Checker'

    def shortHelpString(self):
        return ("Finds gaps, overlaps and disputed areas (MBIs) between barangay polygons "
                "and counts the "
                "building points inside each one.\n\n"
                "Include Disputed Areas (barangay polygons whose 'boundary' field is "
                "'Contested'):\n"
                "  - unticked (default): those polygons are removed before anything is "
                "checked, so the run behaves as if they had never been digitised - no "
                "Disputed Areas layer, and the ground they cover can come out as a gap.\n"
                "  - ticked: they are reported whole, one row each, with every barangay "
                "within 0.50 m of them listed as involved; their footprint is kept out of "
                "the gaps and, as in GEMMA's checker, they are still scanned for overlaps. "
                "'Disputed Only' needs this ticked.\n\n"
                "Output: 'Overlaps', 'Gaps' and (when included) 'Disputed Areas' layers with "
                "one row per pair of involved "
                "barangays and the MBI level between them (Inter-Region ... Within-Barangay). "
                "Slivers of 0.1 m² or less with no building are dropped.\n\n"
                "Export (first mode only): one GPKG per province, named "
                "<province code>_<province>_mbi.gpkg (e.g. 027_Ifugao_mbi.gpkg), saved to the "
                "chosen Export folder. Each holds the overlap, gap and disputed rows of that "
                "province's barangays (an inter-province MBI is in both provinces' files), "
                "plus mbi_type (Overlap / Gap / Disputed), mbi_status (2_Pending on every "
                "disputed row), mbi_remarks and mbi_scope (the MBI types this run looked for, "
                "so a later counter-check can tell 'not looked for' from 'not found').\n\n"
                "est_hhcount = sum of the household count of the building points inside the MBI "
                "(per point: est_hhcount, or hhcount when est_hhcount is empty; also "
                "hh_count / household_count ... if those are the only ones).")

    def createInstance(self):
        return NationalMBIChecker()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT1, 'Select Barangay Polygon Layer(s)', QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT2, 'Select Building Point Layer(s)', QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_DISPUTED,
            "Include Disputed Areas (barangay polygons whose 'boundary' is Contested). "
            "Unticked: they are ignored, as if those polygons were not there",
            defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.RUN_MODE, 'Analysis to Run',
            options=RUN_MODE_LABELS, defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.EXPORT_MBI,
            'Export MBI layers as one GPKG per province, e.g. 027_Ifugao_mbi.gpkg '
            '(Overlaps, Gaps and Disputed only)',
            defaultValue=False))
        self.addParameter(QgsProcessingParameterFile(
            self.EXPORT_DIR, 'Export folder',
            behavior=QgsProcessingParameterFile.Folder,
            defaultValue=EXPORT_FOLDER, optional=True))

    def checkParameterValues(self, parameters, context):
        # 'Disputed Only' is meaningless while the disputed polygons are being ignored.
        if (self.parameterAsEnum(parameters, self.RUN_MODE, context) == DISPUTED_MODE
                and not self.parameterAsBool(parameters, self.INCLUDE_DISPUTED, context)):
            return False, ("'%s' needs 'Include Disputed Areas' ticked - without it the "
                           "contested polygons are ignored and there is nothing to report."
                           % RUN_MODE_LABELS[DISPUTED_MODE])
        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback: QgsProcessingFeedback):
        bgy_list = self.parameterAsLayerList(parameters, self.INPUT1, context)
        bldg_list = self.parameterAsLayerList(parameters, self.INPUT2, context)
        run_mode = self.parameterAsEnum(parameters, self.RUN_MODE, context)
        include_disputed = self.parameterAsBool(parameters, self.INCLUDE_DISPUTED, context)
        export_folder = (self.parameterAsFile(parameters, self.EXPORT_DIR, context)
                         if parameters.get(self.EXPORT_DIR) else '') or EXPORT_FOLDER
        style_path = STYLE_PATH
        export_enabled = self.parameterAsBool(parameters, self.EXPORT_MBI, context)
        self.post_processors = []

        if export_enabled and run_mode != 0:
            feedback.pushInfo("Export is only done in '%s' mode - skipped."
                              % RUN_MODE_LABELS[0])
            export_enabled = False
        if export_enabled and not os.path.exists(export_folder):
            try:
                os.makedirs(export_folder)
                feedback.pushInfo(f"Created directory: {export_folder}")
            except Exception as e:
                feedback.reportError(f"Could not create directory {export_folder}: {e}")
                export_enabled = False

        if run_mode == DISPUTED_MODE and not include_disputed:
            raise QgsProcessingException(
                "'%s' needs 'Include Disputed Areas' ticked." % RUN_MODE_LABELS[DISPUTED_MODE])

        run_overlaps = run_mode in (0, 1)
        run_gaps = run_mode in (0, 2)
        run_disputed = include_disputed and run_mode in (0, DISPUTED_MODE)

        feedback.pushInfo('Processing... Mode: ' + RUN_MODE_LABELS[run_mode]
                          + (' (Disputed Areas included)' if include_disputed
                             else ' (Disputed Areas ignored)'))

        if not bgy_list:
            raise QgsProcessingException("Barangay Polygon Layer(s) must be selected.")
        if not bldg_list:
            raise QgsProcessingException("Building Point Layer(s) must be selected.")

        feedback.pushInfo("Merging Selected Layers...")
        feedback.setProgress(2)

        def refactor_layer(layer):
            field_mapping = [{'expression': f'"{f.name()}"', 'length': 0,   # no length limit
                              'name': f.name(), 'type': f.type()} for f in layer.fields()]
            return processing.run("native:refactorfields", {
                'INPUT': layer,
                'FIELDS_MAPPING': field_mapping,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']

        bgy_refactored = [refactor_layer(l) for l in bgy_list]
        bldg_refactored = [refactor_layer(l) for l in bldg_list]

        bgy_layer = processing.run("native:mergevectorlayers", {
            'LAYERS': bgy_refactored,
            'CRS': bgy_refactored[0].crs(),
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        bldg_layer = processing.run("native:mergevectorlayers", {
            'LAYERS': bldg_refactored,
            'CRS': bldg_refactored[0].crs(),
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setProgress(5)

        def to_3857_fixed(layer):
            reproj = processing.run("native:reprojectlayer", {
                'CONVERT_CURVED_GEOMETRIES': False,
                'INPUT': layer,
                'OPERATION': WEB_MERCATOR_OP,
                'OUTPUT': 'memory:',
                'TARGET_CRS': QgsCoordinateReferenceSystem('EPSG:3857')
            }, context=context, feedback=feedback)['OUTPUT']
            return processing.run("native:fixgeometries", {
                'INPUT': reproj, 'METHOD': 1, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']

        reprojected_bgy = to_3857_fixed(bgy_layer)
        if feedback.isCanceled():
            return {}
        feedback.setProgress(10)

        reprojected_bldg = to_3857_fixed(bldg_layer)
        if feedback.isCanceled():
            return {}

        hh_fields = find_hh_fields(reprojected_bldg)
        hh_field = hh_fields[0] if hh_fields else None
        if hh_fields:
            # Per point: the first non-empty of the found fields (est_hhcount, then hhcount, ...)
            feedback.pushInfo("est_hhcount = sum, over the building points in each MBI, of "
                              + " (else ".join(f"'{f}'" for f in hh_fields)
                              + ")" * (len(hh_fields) - 1) + ".")
            formula = "coalesce(" + ", ".join(f'to_real("{f}")' for f in hh_fields) + ", 0)"
            reprojected_bldg = processing.run("native:fieldcalculator", {
                'INPUT': reprojected_bldg, 'FIELD_NAME': HH_WEIGHT, 'FIELD_TYPE': 0,
                'FIELD_LENGTH': 0, 'FIELD_PRECISION': 0,
                'FORMULA': formula, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
        else:
            feedback.pushWarning("No household-count field in the building points ("
                                 + ", ".join(HH_FIELD_CANDIDATES) + ") - est_hhcount is left empty.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        def export_layer(target, layer_name, set_style=True):
            if target is None:
                feedback.reportError(f"Skipping {layer_name}: Layer target is None.")
                return
            layer_details = QgsProcessingContext.LayerDetails(layer_name, context.project(), "OUTPUT")
            if set_style:
                processor = MBIStylePostProcessor(style_path)
                self.post_processors.append(processor)
                layer_details.setPostProcessor(processor)

            if isinstance(target, str):
                context.addLayerToLoadOnCompletion(target, layer_details)
                feedback.pushInfo(f"Saved layer queued: {target}")
            elif isinstance(target, QgsVectorLayer):
                target.setName(layer_name)
                context.temporaryLayerStore().addMapLayer(target)
                context.addLayerToLoadOnCompletion(target.id(), layer_details)
                feedback.pushInfo(f"Scratch layer queued: {layer_name}")
            else:
                feedback.reportError(f"Unexpected type for {layer_name}: {type(target)}")

        def build_pair_layer(groups, src_crs, single_row=False):
            """groups: [(geometry in src_crs, piece_attrs, [area_attrs, ...])].
            Writes one row per ordered pair of involved areas (A->B and B->A):
            "this area" and the "other area" the overlap/gap is shared with.
            A piece with a single involved area gets one row with no other area.
            single_row=True (disputed areas): one row per piece instead, carrying the
            first involved area (the disputed polygon itself) and the level across all
            of them - the whole polygon is the case, so pair rows would only repeat it."""
            xform = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem('EPSG:4326'),
                                           context.transformContext())
            out = QgsVectorLayer("Polygon?crs=EPSG:4326", "pairs", "memory")
            qtypes = {10: QVariant.String, 4: QVariant.LongLong, 6: QVariant.Double}
            out.dataProvider().addAttributes([QgsField(n, qtypes[t]) for n, t in PAIR_FIELDS])
            out.updateFields()

            feats = []
            for geom, piece, areas in groups:
                area, bldg = piece['mbi_area'], piece['num_bldg_pts']
                # Drop slivers (<= 0.1 sq.m. with no buildings). A disputed area is a
                # whole barangay polygon declared contested, so it is never a sliver.
                if (not single_row and area is not None and bldg is not None
                        and 0 <= area <= 0.1 and bldg == 0):
                    continue
                if single_row:
                    # areas[0] is the disputed polygon itself - it stays the row's own
                    # area even when it carries no geocode of its own.
                    areas = unique_areas(areas)
                else:
                    areas = unique_areas([a for a in areas if a['geocode'] is not None])
                if not areas:
                    continue
                g = QgsGeometry(geom)
                g.transform(xform)
                case = {
                    'case_uuid': piece['gmd_uuid'],
                    'involved_areas': ','.join(a['geocode'] or '' for a in areas),
                    'involved_bgys': ';'.join(f"{a['barangay'] or ''}, {a['city_mun'] or ''}" for a in areas),
                    'count_involved_areas': len(areas),
                    'num_bldg_pts': bldg,
                    'est_hhcount': piece['est_hhcount'],
                }
                if single_row:
                    row = dict(case, **areas[0])
                    row['mbi_level'] = group_level(areas)
                    f = QgsFeature(out.fields())
                    f.setGeometry(g)
                    f.setAttributes([row[n] for n, _ in PAIR_FIELDS])
                    feats.append(f)
                    continue
                pairs = []
                for a, b in combinations(areas, 2):
                    pairs += [(a, b), (b, a)]
                if not pairs:
                    pairs = [(areas[0], None)]
                for a, b in pairs:
                    row = dict(case, **a)
                    row['mbi_level'] = pair_level(a, b)
                    f = QgsFeature(out.fields())
                    f.setGeometry(g)
                    f.setAttributes([row[n] for n, _ in PAIR_FIELDS])
                    feats.append(f)
            out.dataProvider().addFeatures(feats)
            out.updateExtents()
            return out

        def with_uuid_and_size(layer, keep_ids):
            """gmd_uuid + mbi_area, then the min. bounding box width / height of each piece."""
            ids = ([{'alias': '', 'comment': '', 'expression': f'"{n}"', 'length': 0, 'name': n,
                     'precision': 0, 'sub_type': 0, 'type': 2, 'type_name': 'integer'}
                    for n in ('ID1', 'ID2')] if keep_ids else [])
            uuid_area = ids + [
                {'alias': '', 'comment': '', 'expression': 'substr($uuid, 2,36)', 'length': 0, 'name': 'gmd_uuid',
                 'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'},
                {'alias': '', 'comment': '', 'expression': ' round($area , 2)', 'length': 0, 'name': 'mbi_area',
                 'precision': 0, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'}]
            with_uuid = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': uuid_area, 'INPUT': layer, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            bounds = processing.run("qgis:minimumboundinggeometry", {
                'FIELD': 'gmd_uuid', 'INPUT': with_uuid, 'OUTPUT': 'memory:', 'TYPE': 1
            }, context=context, feedback=feedback)['OUTPUT']
            joined = processing.run("native:joinattributestable", {
                'DISCARD_NONMATCHING': False, 'FIELD': 'gmd_uuid',
                'FIELDS_TO_COPY': ['gmd_uuid', 'width', 'height'], 'FIELD_2': 'gmd_uuid',
                'INPUT': with_uuid, 'INPUT_2': bounds, 'METHOD': 1, 'OUTPUT': 'memory:', 'PREFIX': ''
            }, context=context, feedback=feedback)['OUTPUT']
            final = ids + [
                {'alias': '', 'comment': '', 'expression': '"gmd_uuid"', 'length': 0, 'name': 'gmd_uuid',
                 'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'},
                {'alias': '', 'comment': '', 'expression': '"mbi_area"', 'length': 0, 'name': 'mbi_area',
                 'precision': 0, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                {'alias': '', 'comment': '', 'expression': '"width"', 'length': 20, 'name': 'mbi_width',
                 'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                {'alias': '', 'comment': '', 'expression': '"height"', 'length': 20, 'name': 'mbi_height',
                 'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'}]
            refactored = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': final, 'INPUT': joined, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            counted = processing.run("native:countpointsinpolygon", {
                'CLASSFIELD': '', 'FIELD': 'num_bldg_pts', 'OUTPUT': 'memory:',
                'POINTS': reprojected_bldg, 'POLYGONS': refactored, 'WEIGHT': ''
            }, context=context, feedback=feedback)['OUTPUT']
            if not hh_field:
                return counted
            # Same points, weighted by their household count -> est_hhcount
            return processing.run("native:countpointsinpolygon", {
                'CLASSFIELD': '', 'FIELD': 'est_hhcount', 'OUTPUT': 'memory:',
                'POINTS': reprojected_bldg, 'POLYGONS': counted, 'WEIGHT': HH_WEIGHT
            }, context=context, feedback=feedback)['OUTPUT']

        overlap_out = None
        gap_out = None
        disputed_out = None
        bgy_parts = None             # reprojected_bgy exploded into single parts

        def barangay_parts():
            """The barangay polygons as single parts - built once, reused by the
            overlap and the disputed pass (exploding them twice is expensive)."""
            nonlocal bgy_parts
            if bgy_parts is None:
                bgy_parts = processing.run("native:multiparttosingleparts", {
                    'INPUT': reprojected_bgy, 'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
            return bgy_parts

        # --- Disputed areas (boundary = Contested) ---
        boundary_field = resolve_field(bgy_layer, DISPUTED_FIELD)
        has_boundary = boundary_field is not None
        disputed_src = None          # input-CRS copy of the disputed polygons
        disputed_count = 0
        if not has_boundary:
            if include_disputed:
                feedback.pushWarning(
                    f"No '{DISPUTED_FIELD}' field in the barangay layer(s) - disputed areas "
                    "are not detected and nothing is kept out of the gaps.")
            else:
                feedback.pushInfo(f"No '{DISPUTED_FIELD}' field in the barangay layer(s) - "
                                  "no contested polygon to ignore.")
        else:
            disputed_src = processing.run("native:extractbyexpression", {
                'INPUT': bgy_layer, 'EXPRESSION': disputed_expression(boundary_field),
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            disputed_count = disputed_src.featureCount()
            feedback.pushInfo(f"{disputed_count:,} disputed ({boundary_field} = "
                              f"{DISPUTED_VALUE.title()}) polygon(s) found.")

            if not include_disputed and disputed_count:
                # 'Include Disputed Areas' is off: take the contested polygons out of
                # both working layers, so everything below runs as if they were never
                # digitised (the ground they cover can therefore come out as a gap).
                keep_expr = not_disputed_expression(boundary_field)
                bgy_layer = processing.run("native:extractbyexpression", {
                    'INPUT': bgy_layer, 'EXPRESSION': keep_expr, 'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
                reprojected_bgy = processing.run("native:extractbyexpression", {
                    'INPUT': reprojected_bgy, 'EXPRESSION': keep_expr, 'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
                feedback.pushInfo(f"'Include Disputed Areas' is off - {disputed_count:,} "
                                  "contested polygon(s) removed from the check.")
                disputed_src = None
                disputed_count = 0
                if not bgy_layer.featureCount():
                    raise QgsProcessingException(
                        "Every barangay polygon is Contested, so nothing is left to check. "
                        "Tick 'Include Disputed Areas' to report them.")

        # --- OVERLAPS ---
        if run_overlaps:
            QgsMessageLog.logMessage("Starting Overlap Analysis...")
            progress_end = 100 if run_mode == 1 else 55
            feedback.setProgress(12)

            singlepart_sf = barangay_parts()
            if feedback.isCanceled():
                return {}
            feedback.setProgress(20)

            # Reconstructed LF Tools' Overlapping Polygon: intersect every pair of
            # single-part polygons whose bounding boxes meet.
            fields = QgsFields()
            fields.append(QgsField('ID1', QVariant.Int))
            fields.append(QgsField('ID2', QVariant.Int))

            index = QgsSpatialIndex(singlepart_sf.getFeatures())
            overlap_features = []
            seen_pairs = set()
            total = singlepart_sf.featureCount()
            step = (progress_end - 20) / total if total else 0

            for current, feat1 in enumerate(singlepart_sf.getFeatures()):
                if feedback.isCanceled():
                    return {}
                feedback.setProgress(int(20 + current * step))
                id1 = feat1.id()
                geom1 = feat1.geometry()
                for feat2 in singlepart_sf.getFeatures(QgsFeatureRequest(index.intersects(geom1.boundingBox()))):
                    id2 = feat2.id()
                    if id1 == id2 or (id2, id1) in seen_pairs:
                        continue
                    geom2 = feat2.geometry()
                    if not geom1.intersects(geom2):
                        continue
                    inter = geom1.intersection(geom2)
                    if inter.isEmpty() or inter.wkbType() == QgsWkbTypes.NoGeometry:
                        continue
                    seen_pairs.add((id1, id2))
                    for item in inter.asGeometryCollection():
                        if item.type() != QgsWkbTypes.PolygonGeometry:
                            continue
                        polys = ([QgsGeometry.fromPolygonXY(p) for p in item.asMultiPolygon()]
                                 if item.isMultipart() else [item])
                        for poly in polys:
                            feature = QgsFeature(fields)
                            feature.setGeometry(poly)
                            feature.setAttributes([id1, id2])
                            overlap_features.append(feature)

            if not overlap_features:
                feedback.pushInfo("Overlap check finished: no overlapping areas found.")
            else:
                overlapping_sf = QgsVectorLayer("Polygon?crs=EPSG:3857", "temporary_overlaps", "memory")
                overlapping_sf.dataProvider().addAttributes(fields)
                overlapping_sf.updateFields()
                overlapping_sf.dataProvider().addFeatures(overlap_features)

                count_bldg_sf = with_uuid_and_size(overlapping_sf, keep_ids=True)
                if feedback.isCanceled():
                    return {}

                # Each overlap piece is the intersection of exactly two polygons
                # (ID1, ID2), so take those two areas directly - no spatial join.
                area_by_fid = {f.id(): area_attrs(f) for f in singlepart_sf.getFeatures()}
                overlap_groups = [
                    (f.geometry(), piece_attrs(f), [area_by_fid[f['ID1']], area_by_fid[f['ID2']]])
                    for f in count_bldg_sf.getFeatures()
                ]

                feedback.pushInfo("Writing Overlap Layer")
                overlap_out = build_pair_layer(overlap_groups, count_bldg_sf.crs())
                export_layer(overlap_out, 'Overlaps')
            feedback.setProgress(progress_end)

        # --- DISPUTED AREAS ---
        if run_disputed and has_boundary and disputed_count:
            QgsMessageLog.logMessage("Starting Disputed Areas Analysis...")
            feedback.setProgress(10 if run_mode == 3 else 56)

            disputed_parts = barangay_parts()
            if feedback.isCanceled():
                return {}

            d_fields = QgsFields()
            d_fields.append(QgsField('ID1', QVariant.Int))
            d_fields.append(QgsField('ID2', QVariant.Int))

            d_geom_by_fid, d_area_by_fid, d_fids = {}, {}, []
            d_index = QgsSpatialIndex()
            for f in disputed_parts.getFeatures():
                if not f.hasGeometry() or f.geometry().isEmpty():
                    continue
                d_geom_by_fid[f.id()] = QgsGeometry(f.geometry())
                d_area_by_fid[f.id()] = area_attrs(f)
                d_index.addFeature(f)
                if is_disputed(f, boundary_field):
                    d_fids.append(f.id())

            if not d_fids:
                feedback.pushInfo("Disputed check finished: no disputed polygon with geometry.")
            else:
                disputed_sf = QgsVectorLayer("Polygon?crs=EPSG:3857", "temporary_disputed", "memory")
                disputed_sf.dataProvider().addAttributes(d_fields)
                disputed_sf.updateFields()
                d_feats = []
                for fid in d_fids:
                    f = QgsFeature(d_fields)
                    f.setGeometry(d_geom_by_fid[fid])
                    f.setAttributes([fid, fid])
                    d_feats.append(f)
                disputed_sf.dataProvider().addFeatures(d_feats)
                disputed_sf.updateExtents()

                count_bldg_disputed_sf = with_uuid_and_size(disputed_sf, keep_ids=True)
                if feedback.isCanceled():
                    return {}
                feedback.setProgress(60 if run_mode == 3 else 58)

                # Involved areas: the disputed polygon itself plus every barangay polygon
                # within DISPUTED_NEAR_M of it, so the barangays laying claim are listed.
                disputed_groups = []
                for f in count_bldg_disputed_sf.getFeatures():
                    if feedback.isCanceled():
                        return {}
                    fid = f['ID1']
                    geom = f.geometry()
                    # EPSG:3857 stretches distances away from the equator, so the
                    # buffer is widened to stay DISPUTED_NEAR_M real metres.
                    search = geom.buffer(DISPUTED_NEAR_M * webmerc_metre(geom), 5)
                    areas, seen = [], set()
                    if fid in d_area_by_fid:
                        areas.append(d_area_by_fid[fid])
                        seen.add(fid)
                    for other in d_index.intersects(search.boundingBox()):
                        if other in seen or other not in d_geom_by_fid:
                            continue
                        if not search.intersects(d_geom_by_fid[other]):
                            continue
                        seen.add(other)
                        areas.append(d_area_by_fid[other])
                    disputed_groups.append((geom, piece_attrs(f), areas))

                feedback.pushInfo("Writing Disputed Areas Layer")
                disputed_out = build_pair_layer(disputed_groups, count_bldg_disputed_sf.crs(),
                                                single_row=True)
                export_layer(disputed_out, 'Disputed Areas')
            feedback.setProgress(95 if run_mode == 3 else 59)

        # --- GAPS ---
        if run_gaps:
            QgsMessageLog.logMessage("Starting Gaps Analysis...")
            feedback.setProgress(10 if run_mode == 2 else 60)

            # Disputed polygons are left out of the dissolve and their footprint is
            # subtracted below, so a disputed area is never reported as a gap as well.
            gap_src = bgy_layer
            if has_boundary and disputed_count:
                gap_src = processing.run("native:extractbyexpression", {
                    'INPUT': bgy_layer, 'EXPRESSION': not_disputed_expression(boundary_field),
                    'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
                feedback.pushInfo(f"{disputed_count:,} disputed polygon(s) kept out of the gaps.")

            dissolved_bgy_sf = processing.run("native:dissolve", {
                'FIELD': '', 'INPUT': gap_src, 'OUTPUT': 'memory:', 'SEPARATE_DISJOINT': False
            }, context=context, feedback=feedback)['OUTPUT']
            if feedback.isCanceled():
                return {}

            delete_holes_sf = processing.run("native:deleteholes", {
                'INPUT': dissolved_bgy_sf, 'MIN_AREA': 0, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            if feedback.isCanceled():
                return {}

            dissolved_bgy_cleaned_sf = processing.run("native:dissolve", {
                'FIELD': '', 'INPUT': delete_holes_sf, 'OUTPUT': 'memory:', 'SEPARATE_DISJOINT': False
            }, context=context, feedback=feedback)['OUTPUT']
            if feedback.isCanceled():
                return {}
            feedback.setProgress(70 if run_mode == 0 else 30)

            difference_bgy_sf = processing.run("native:difference", {
                'INPUT': dissolved_bgy_cleaned_sf, 'OVERLAY': dissolved_bgy_sf, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            if feedback.isCanceled():
                return {}

            if has_boundary and disputed_count:
                difference_bgy_sf = processing.run("native:difference", {
                    'INPUT': difference_bgy_sf, 'OVERLAY': disputed_src, 'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']
                if feedback.isCanceled():
                    return {}

            multi_to_single_sf = processing.run("native:multiparttosingleparts", {
                'INPUT': difference_bgy_sf, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            if feedback.isCanceled():
                return {}
            feedback.setProgress(80 if run_mode == 0 else 55)

            count_bldg_gap_sf = with_uuid_and_size(multi_to_single_sf, keep_ids=False)
            if feedback.isCanceled():
                return {}
            feedback.setProgress(90 if run_mode == 0 else 80)

            # Areas each gap borders. The gaps were cut from bgy_layer (the
            # input CRS, not EPSG:3857), so match against bgy_layer in that same
            # CRS. A shared edge is required: areas that only touch the gap at a
            # corner are not candidates to absorb it.
            gap_crs = count_bldg_gap_sf.crs()
            to_metres = QgsCoordinateTransform(gap_crs, QgsCoordinateReferenceSystem('EPSG:3857'),
                                               context.transformContext())
            gap_bgy = processing.run("native:fixgeometries", {
                'INPUT': bgy_layer, 'METHOD': 1, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            if gap_bgy.crs() != gap_crs:
                gap_bgy = processing.run("native:reprojectlayer", {
                    'INPUT': gap_bgy, 'TARGET_CRS': gap_crs, 'OUTPUT': 'memory:'
                }, context=context, feedback=feedback)['OUTPUT']

            bgy_by_fid = {f.id(): f for f in gap_bgy.getFeatures()}
            bgy_index = QgsSpatialIndex(gap_bgy.getFeatures())
            gap_groups = []
            for gap in count_bldg_gap_sf.getFeatures():
                if feedback.isCanceled():
                    return {}
                gap_geom = gap.geometry()
                if gap_geom is None or gap_geom.isEmpty():
                    continue
                touching, sharing_edge = [], []
                for fid in bgy_index.intersects(gap_geom.boundingBox()):
                    bgy_geom = bgy_by_fid[fid].geometry()
                    if not gap_geom.intersects(bgy_geom):
                        continue
                    attrs = area_attrs(bgy_by_fid[fid])
                    touching.append(attrs)
                    shared = gap_geom.intersection(bgy_geom)
                    shared.transform(to_metres)
                    if shared.length() > SHARED_EDGE_MIN_M:
                        sharing_edge.append(attrs)
                # Fall back to every touching area if rounding left no clean shared edge.
                gap_groups.append((gap_geom, piece_attrs(gap), sharing_edge or touching))

            feedback.pushInfo("Writing Gaps Layer")
            gap_out = build_pair_layer(gap_groups, gap_crs)
            export_layer(gap_out, 'Gaps')
            feedback.setProgress(96)

        # --- EXPORT (first mode only) ---
        if export_enabled:
            scope = ','.join(['Gap', 'Overlap'] + (['Disputed'] if include_disputed else []))
            self._export_merged(overlap_out, gap_out, disputed_out, export_folder, timestamp,
                                context, feedback, scope)

        feedback.setProgress(100)
        return {}

    def _export_merged(self, overlap_layer, gap_layer, disputed_layer, export_folder, timestamp,
                       context, feedback, scope='Gap,Overlap'):
        """Merge Overlaps + Gaps + Disputed (with mbi_type / mbi_status fields) and write one GPKG per
        province (<code>_<Province>_mbi.gpkg), then load them."""

        def mbi_extras(mbi_type):
            # A Disputed area is pending by definition; a gap / overlap only when the
            # barangays involved are in different regions or provinces.
            status = ("'2_Pending'" if mbi_type == 'Disputed' else
                      "CASE WHEN \"mbi_level\" IN ('1_Inter-Region', '2_Inter-Province') "
                      "THEN '2_Pending' END")
            return [
                ('mbi_type', f"'{mbi_type}'"),
                ('mbi_status', status),
                ('mbi_remarks', 'NULL'),
                # What this run looked for - so a reader can tell an MBI type that was
                # never checked from one that was checked and not found.
                (SCOPE_FIELD, f"'{scope}'"),
            ]

        parts = []
        for layer, mbi_type in ((overlap_layer, 'Overlap'), (gap_layer, 'Gap'),
                                (disputed_layer, 'Disputed')):
            if layer is None or layer.featureCount() == 0:
                continue
            parts.append(processing.run("native:refactorfields", {
                'INPUT': layer, 'OUTPUT': 'memory:',
                'FIELDS_MAPPING': pair_field_mapping(mbi_extras(mbi_type))
            }, context=context, feedback=feedback)['OUTPUT'])
        if not parts:
            feedback.pushInfo("No gaps, overlaps or disputed areas found - nothing to export.")
            return

        merged = processing.run("native:mergevectorlayers", {
            'CRS': QgsCoordinateReferenceSystem('EPSG:4326'), 'LAYERS': parts, 'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        # Province of each row = first 3 digits of its barangay geocode; the name
        # comes from the 'province' field (first non-empty value seen).
        provinces = {}
        for f in merged.getFeatures():
            code = str(f['geocode'] or '')[:3]
            if not code:
                continue
            name = _val(f, 'province')
            if code not in provinces or (not provinces[code] and name):
                provinces[code] = name
        if not provinces:
            feedback.pushInfo("No row has a geocode - nothing to export.")
            return

        keep = pair_field_mapping([(n, f'"{n}"') for n, _ in mbi_extras('')])
        for code in sorted(provinces):
            if feedback.isCanceled():
                return
            base = province_file_base(code, provinces[code])
            file_path = os.path.join(export_folder, f"{base}.gpkg")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    feedback.pushInfo(f"Existing file {file_path} replaced.")
                except Exception as e:
                    feedback.reportError(f"Could not replace {file_path} (open in QGIS?): {e}")
                    continue
            subset = processing.run("native:extractbyexpression", {
                'INPUT': merged, 'EXPRESSION': f"left(to_string(\"geocode\"), 3) = '{code}'",
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            # Also drops the 'layer'/'path' fields added by the merge.
            processing.run("native:refactorfields", {
                'INPUT': subset, 'OUTPUT': file_path, 'FIELDS_MAPPING': keep
            }, context=context, feedback=feedback)

            layer_details = QgsProcessingContext.LayerDetails(base, context.project(), "OUTPUT")
            processor = MBIStylePostProcessor(STYLE_PATH)
            self.post_processors.append(processor)
            layer_details.setPostProcessor(processor)
            context.addLayerToLoadOnCompletion(file_path, layer_details)
            feedback.pushInfo(f"Exported {subset.featureCount()} rows: {file_path}")


# ======================== HEADLESS / DIALOG RUNNER ========================

def _resolve_layers(names, geom_type, label):
    project = QgsProject.instance()
    layers = []
    for n in names:
        found = project.mapLayersByName(n)
        if not found:
            raise Exception(f"{label}: layer '{n}' not found in the project. "
                            f"Check the spelling in CONFIG (it must match the Layers panel).")
        layers.append(found[0])
    for l in layers:
        if not isinstance(l, QgsVectorLayer) or l.geometryType() != geom_type:
            raise Exception(f"{label}: layer '{l.name()}' has the wrong geometry type.")
    return layers


def load_results(context):
    """Add the layers queued with addLayerToLoadOnCompletion() to the project.
    (The Processing dialog normally does this; in headless mode we do it here.)"""
    project = QgsProject.instance()
    store = context.temporaryLayerStore()
    for ref, details in context.layersToLoadOnCompletion().items():
        layer = store.mapLayer(ref)
        if layer is not None:
            layer = store.takeMapLayer(layer)
        elif os.path.exists(ref):
            layer = QgsVectorLayer(ref, details.name, 'ogr')
        if layer is None or not layer.isValid():
            print(f"WARNING: could not load result '{details.name}' ({ref})")
            continue
        layer.setName(details.name)
        if STYLE_PATH and os.path.exists(STYLE_PATH):
            layer.loadNamedStyle(STYLE_PATH)
        project.addMapLayer(layer)
        layer.triggerRepaint()
        print(f"Loaded layer: {details.name} ({layer.featureCount()} features)")


def run_national_mbi_checker(bgy_layers=None, bldg_layers=None, run_mode=None, export=None,
                            export_dir=None, include_disputed=None):
    """Run without a dialog (layer objects, or the CONFIG layer names when not given)."""
    if bgy_layers is None:
        bgy_layers = _resolve_layers(BGY_LAYER_NAMES, QgsWkbTypes.PolygonGeometry, 'Barangay polygon')
    if bldg_layers is None:
        bldg_layers = _resolve_layers(BLDG_LAYER_NAMES, QgsWkbTypes.PointGeometry, 'Building point')
    print("Barangay layers: " + ", ".join(l.name() for l in bgy_layers))
    print("Building layers: " + ", ".join(l.name() for l in bldg_layers))
    if STYLE_PATH and not os.path.exists(STYLE_PATH):
        print(f"WARNING: style file not found, outputs will not be styled: {STYLE_PATH}")

    alg = NationalMBIChecker()
    alg.initAlgorithm()

    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    feedback = ConsoleFeedback()
    context.setFeedback(feedback)

    params = {
        'INPUT1': bgy_layers,
        'INPUT2': bldg_layers,
        'INCLUDE_DISPUTED': (INCLUDE_DISPUTED if include_disputed is None
                             else include_disputed),
        'RUN_MODE': RUN_MODE if run_mode is None else run_mode,
        'EXPORT_MBI': EXPORT_MBI if export is None else export,
        'EXPORT_DIR': export_dir or EXPORT_FOLDER,
    }

    start = datetime.now()
    try:
        alg.processAlgorithm(params, context, feedback)
    except QgsProcessingException as e:
        print(f"ERROR: {e}")
        return
    load_results(context)
    print(f"National-Level MBI Checker finished in {datetime.now() - start}.")


def open_dialog():
    """Open the National-Level MBI Checker dialog."""
    try:
        from qgis.utils import iface
        if iface is None or iface.mainWindow() is None:
            print("The National-Level MBI Checker dialog requires the QGIS graphical interface.")
            return
    except (ImportError, AttributeError):
        print("The National-Level MBI Checker dialog requires the QGIS graphical interface.")
        return

    # Restore the wait cursor set by the GMD CO Processing panel
    try:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
    except Exception:
        pass

    alg = NationalMBIChecker()
    provider = QgsApplication.processingRegistry().providerById("script")
    if provider is not None:
        alg.setProvider(provider)
    alg.initAlgorithm()
    processing.execAlgorithmDialog(alg)


# When the file is dragged onto QGIS / run via "Run Script", QGIS itself execs
# this file and then opens the dialog for the NationalMBIChecker class it finds.
# That runner execs without __name__, so we must not open a second dialog.
_RUN_BY_QGIS_SCRIPT_RUNNER = "__name__" not in globals()

if __name__ == "__main__" or _RUN_BY_QGIS_SCRIPT_RUNNER:
    if BGY_LAYER_NAMES and BLDG_LAYER_NAMES:
        run_national_mbi_checker()           # headless, using the CONFIG layer names
        if _RUN_BY_QGIS_SCRIPT_RUNNER:
            del NationalMBIChecker           # stop QGIS from also opening the dialog
    elif not _RUN_BY_QGIS_SCRIPT_RUNNER:
        open_dialog()                        # pick layers in the dialog
