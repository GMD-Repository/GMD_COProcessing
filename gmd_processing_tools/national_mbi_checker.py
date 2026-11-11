"""National-Level MBI Checker (Gaps & Overlaps) - checks barangay boundaries and building points.

Identifies boundary discrepancies (MBIs) between barangay polygons, at any scale up to the
whole country (merge as many barangay layers as needed):
  - Overlaps: land inside two barangay polygons
  - Gaps: holes enclosed by the barangays but inside none of them
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

# 0 = Overlaps and Gaps, 1 = Overlaps Only, 2 = Gaps Only
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
        return ("Finds gaps and overlaps (MBIs) between barangay polygons and counts the "
                "building points inside each one.\n\n"
                "Output: 'Overlaps' and 'Gaps' layers with one row per pair of involved "
                "barangays and the MBI level between them (Inter-Region ... Within-Barangay). "
                "Slivers of 0.1 m² or less with no building are dropped.\n\n"
                "Export (Overlaps and Gaps mode only): one GPKG per province, named "
                "<province code>_<province>_mbi.gpkg (e.g. 027_Ifugao_mbi.gpkg), saved to the "
                "chosen Export folder. Each holds the overlap and gap rows of that province's "
                "barangays (an inter-province MBI is in both provinces' files), plus mbi_type, "
                "mbi_status and mbi_remarks.\n\n"
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
        self.addParameter(QgsProcessingParameterEnum(
            self.RUN_MODE, 'Analysis to Run',
            options=['Overlaps and Gaps', 'Overlaps Only', 'Gaps Only'], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.EXPORT_MBI,
            'Export MBI layers as one GPKG per province, e.g. 027_Ifugao_mbi.gpkg (Overlaps and Gaps only)',
            defaultValue=False))
        self.addParameter(QgsProcessingParameterFile(
            self.EXPORT_DIR, 'Export folder',
            behavior=QgsProcessingParameterFile.Folder,
            defaultValue=EXPORT_FOLDER, optional=True))

    def processAlgorithm(self, parameters, context, feedback: QgsProcessingFeedback):
        bgy_list = self.parameterAsLayerList(parameters, self.INPUT1, context)
        bldg_list = self.parameterAsLayerList(parameters, self.INPUT2, context)
        run_mode = self.parameterAsEnum(parameters, self.RUN_MODE, context)
        export_folder = (self.parameterAsFile(parameters, self.EXPORT_DIR, context)
                         if parameters.get(self.EXPORT_DIR) else '') or EXPORT_FOLDER
        style_path = STYLE_PATH
        export_enabled = self.parameterAsBool(parameters, self.EXPORT_MBI, context)
        self.post_processors = []

        if export_enabled and run_mode != 0:
            feedback.pushInfo("Export is only done in 'Overlaps and Gaps' mode - skipped.")
            export_enabled = False
        if export_enabled and not os.path.exists(export_folder):
            try:
                os.makedirs(export_folder)
                feedback.pushInfo(f"Created directory: {export_folder}")
            except Exception as e:
                feedback.reportError(f"Could not create directory {export_folder}: {e}")
                export_enabled = False

        run_overlaps = run_mode in (0, 1)
        run_gaps = run_mode in (0, 2)

        feedback.pushInfo(f'Processing... Mode: {["Both", "Overlaps Only", "Gaps Only"][run_mode]}')

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

        def build_pair_layer(groups, src_crs):
            """groups: [(geometry in src_crs, piece_attrs, [area_attrs, ...])].
            Writes one row per ordered pair of involved areas (A->B and B->A):
            "this area" and the "other area" the overlap/gap is shared with.
            A piece with a single involved area gets one row with no other area."""
            xform = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem('EPSG:4326'),
                                           context.transformContext())
            out = QgsVectorLayer("Polygon?crs=EPSG:4326", "pairs", "memory")
            qtypes = {10: QVariant.String, 4: QVariant.LongLong, 6: QVariant.Double}
            out.dataProvider().addAttributes([QgsField(n, qtypes[t]) for n, t in PAIR_FIELDS])
            out.updateFields()

            feats = []
            for geom, piece, areas in groups:
                area, bldg = piece['mbi_area'], piece['num_bldg_pts']
                # Drop slivers (<= 0.1 sq.m. with no buildings)
                if area is not None and bldg is not None and 0 <= area <= 0.1 and bldg == 0:
                    continue
                areas = [a for a in areas if a['geocode'] is not None]
                if not areas:
                    continue
                g = QgsGeometry(geom)
                g.transform(xform)
                case = {
                    'case_uuid': piece['gmd_uuid'],
                    'involved_areas': ','.join(a['geocode'] for a in areas),
                    'involved_bgys': ';'.join(f"{a['barangay'] or ''}, {a['city_mun'] or ''}" for a in areas),
                    'count_involved_areas': len(areas),
                    'num_bldg_pts': bldg,
                    'est_hhcount': piece['est_hhcount'],
                }
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

        # --- OVERLAPS ---
        if run_overlaps:
            QgsMessageLog.logMessage("Starting Overlap Analysis...")
            progress_end = 100 if run_mode == 1 else 55
            feedback.setProgress(12)

            singlepart_sf = processing.run("native:multiparttosingleparts", {
                'INPUT': reprojected_bgy, 'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
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

        # --- GAPS ---
        if run_gaps:
            QgsMessageLog.logMessage("Starting Gaps Analysis...")
            feedback.setProgress(10 if run_mode == 2 else 60)

            dissolved_bgy_sf = processing.run("native:dissolve", {
                'FIELD': '', 'INPUT': bgy_layer, 'OUTPUT': 'memory:', 'SEPARATE_DISJOINT': False
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

        # --- EXPORT (Overlaps and Gaps mode) ---
        if export_enabled:
            self._export_merged(overlap_out, gap_out, export_folder, timestamp, context, feedback)

        feedback.setProgress(100)
        return {}

    def _export_merged(self, overlap_layer, gap_layer, export_folder, timestamp, context, feedback):
        """Merge Overlaps + Gaps (with mbi_type / mbi_status fields) and write one GPKG per
        province (<code>_<Province>_mbi.gpkg), then load them."""

        def mbi_extras(mbi_type):
            return [
                ('mbi_type', f"'{mbi_type}'"),
                ('mbi_status',
                 "CASE WHEN \"mbi_level\" IN ('1_Inter-Region', '2_Inter-Province') THEN '2_Pending' END"),
                ('mbi_remarks', 'NULL'),
            ]

        parts = []
        for layer, mbi_type in ((overlap_layer, 'Overlap'), (gap_layer, 'Gap')):
            if layer is None or layer.featureCount() == 0:
                continue
            parts.append(processing.run("native:refactorfields", {
                'INPUT': layer, 'OUTPUT': 'memory:',
                'FIELDS_MAPPING': pair_field_mapping(mbi_extras(mbi_type))
            }, context=context, feedback=feedback)['OUTPUT'])
        if not parts:
            feedback.pushInfo("No gaps or overlaps found - nothing to export.")
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


def run_national_mbi_checker(bgy_layers=None, bldg_layers=None, run_mode=None, export=None, export_dir=None):
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
