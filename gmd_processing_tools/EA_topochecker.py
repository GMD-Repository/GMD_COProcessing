"""MBI Checker (Gaps & Overlaps) - checks Barangay boundaries and building points.

Identifies boundary discrepancies between Barangay polygons:
  - Overlaps: overlapping boundary areas between adjacent barangays
  - Gaps: missing areas/slivers between adjacent barangays
  - Each contiguous overlap / gap is ONE case (case_uuid), even when it runs
    across more than two EAs: involved_areas / involved_eas / involved_bgys /
    count_involved_areas then list every EA involved (used by the MBI EA Selector)
  - Assigns MBI levels (Inter-Region, Inter-Province, Inter-City/Mun, Inter-Barangay, Within-Barangay)
  - Counts building points inside each discrepancy piece

How to run:
  - From the GMD CO Processing panel: select the script and click 'Run'.
  - From the QGIS menu: GMD CO Processing > MBI Tools > MBI Checker (Gaps & Overlaps).
  - Headless: fill in BGY_LAYER_NAMES and BLDG_LAYER_NAMES in CONFIG below.
  - Console / drag-and-drop: drag onto QGIS or run via Python Console editor.
"""

# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "EA Topology Checker"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/mbi_checker.svg"

import os
import uuid
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

# Export merged MBI layer as GPKG to C:\PSA-GIS\2026 1Map\Preliminary Output
# (only applies when RUN_MODE = 0)
EXPORT_MBI = False

# Style (.qml) applied to the output layers. Set to '' to skip styling.
_LOCAL_STYLE = os.path.join(os.path.dirname(__file__), "styles", "mbi_checker.qml")
STYLE_PATH = (
    _LOCAL_STYLE
    if os.path.exists(_LOCAL_STYLE)
    else r"C:\Users\PSA\Desktop\gmd_pipeline\gmd_pipeline\styles\mbi_checker.qml"
)
# ================================================================

# --- Output schema: one row per ordered pair (this area -> other area) ---
AREA_KEYS = ['geocode', 'ea_geocode', 'ea_name', 'region', 'province', 'city_mun', 'barangay']
# (name, refactor type: 10=text, 4=int8, 6=double). No field aliases.
PAIR_FIELDS = (
    [('case_uuid', 10)]
    + [(k, 10) for k in AREA_KEYS]
    + [('mbi_level', 10),
       ('involved_areas', 10),
       ('involved_bgys', 10),
       ('involved_eas', 10),
       ('count_involved_areas', 4),
       ('num_bldg_pts', 6)]
)

# A gap must share at least this much edge (metres, EPSG:3857) with an area
# for that area to count as involved; corner-only touches are ignored.
SHARED_EDGE_MIN_M = 0.01


def _val(feat, field):
    if field not in feat.fields().names():
        return None
    v = feat[field]
    if v is None or (hasattr(v, 'isNull') and v.isNull()):
        return None
    return v


def area_attrs(feat):
    """Barangay/EA attributes of a boundary polygon (the EA name is in 'name')."""
    out = {}
    for key in AREA_KEYS:
        v = _val(feat, 'name' if key == 'ea_name' else key)
        out[key] = None if v is None else str(v)
    return out


def piece_attrs(feat):
    return {k: _val(feat, k) for k in ('gmd_uuid', 'mbi_area', 'num_bldg_pts')}


def area_key(a):
    """Identity of an area (a multipart EA's parts share it)."""
    return tuple(a[k] for k in AREA_KEYS)


def unique_areas(areas):
    """Drop repeated areas (e.g. two parts of one multipart EA), sorted by geocode."""
    seen = {}
    for a in areas:
        seen.setdefault(area_key(a), a)
    return sorted(seen.values(), key=lambda a: (a['geocode'] or '', a['ea_geocode'] or '', a['ea_name'] or ''))


def merge_overlap_pieces(pieces, feedback=None):
    """Group pairwise overlap pieces into one MBI case per contiguous overlap region.

    pieces: [(QgsGeometry, fid1, fid2)] - one piece per intersection of two areas.
    An overlap that runs across 3+ areas (e.g. A∩B touching A∩C, or a triple
    overlap A∩B∩C) comes out of the pairwise check as several pieces; here they are
    unioned so the whole region becomes ONE case listing every involved area.
    Pieces that only touch at a corner stay separate cases.

    Returns [{'geom': QgsGeometry, 'fids': set, 'links': set((fid1, fid2))}]."""
    if not pieces:
        return []

    def per_piece():
        return [{'geom': g, 'fids': {a, b}, 'links': {(a, b)}} for g, a, b in pieces]

    union = QgsGeometry.unaryUnion([g for g, _, _ in pieces])
    if union is None or union.isNull() or union.isEmpty():
        union = QgsGeometry.unaryUnion([g.makeValid() for g, _, _ in pieces])
    if union is None or union.isNull() or union.isEmpty():
        if feedback:
            feedback.pushWarning("Could not merge overlap pieces; each overlap pair is kept as its own case.")
        return per_piece()

    parts = [QgsGeometry(p) for p in (union.asGeometryCollection() if union.isMultipart() else [union])
             if p.type() == QgsWkbTypes.PolygonGeometry and not p.isEmpty()]
    if not parts:
        return per_piece()

    index = QgsSpatialIndex()
    for i, part in enumerate(parts):
        f = QgsFeature(i)
        f.setGeometry(part)
        index.addFeature(f)

    cases = [{'geom': part, 'fids': set(), 'links': set()} for part in parts]
    for g, a, b in pieces:
        pt = g.pointOnSurface()
        candidates = index.intersects(g.boundingBox())
        best = next((i for i in candidates if parts[i].intersects(pt)), None)
        if best is None and candidates:
            best = max(candidates, key=lambda i: parts[i].intersection(g).area())
        if best is None:
            nearest = index.nearestNeighbor(pt.asPoint(), 1)
            best = nearest[0] if nearest else None
        if best is None:
            continue
        cases[best]['fids'].update((a, b))
        cases[best]['links'].add((a, b))
    return [c for c in cases if c['fids']]


def pair_level(a, b):
    """MBI level between this area (a) and the other area (b)."""
    if b is not None:
        for key, level in (('region', '1_Inter-Region'), ('province', '2_Inter-Province'),
                           ('city_mun', '3_Inter-City/Municipality'), ('barangay', '4_Inter-Barangay')):
            if a[key] != b[key]:
                return level
    return '5_Within-Barangay'


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
    """Prints processing messages and progress to the Python console."""

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


class GapsOverlaps(QgsProcessingAlgorithm):

    INPUT1 = 'INPUT1'
    INPUT2 = 'INPUT2'
    RUN_MODE = 'RUN_MODE'
    EXPORT_MBI = 'EXPORT_MBI'

    def __init__(self):
        super().__init__()
        self.post_processors = []

    def name(self):
        return 'gaps_overlaps_checker'

    def displayName(self):
        return 'MBI Checker'

    def createInstance(self):
        return GapsOverlaps()

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
            'Export MBI layers as GPKG to C:\\PSA-GIS\\2026 1Map\\Preliminary Output',
            defaultValue=False))

    def processAlgorithm(self, parameters, context, feedback: QgsProcessingFeedback):

        bgy_list = self.parameterAsLayerList(parameters, self.INPUT1, context)
        bldg_list = self.parameterAsLayerList(parameters, self.INPUT2, context)
        run_mode = self.parameterAsEnum(parameters, self.RUN_MODE, context)  # Get the selected mode
        export_folder = r"C:\PSA-GIS\2026 1Map\Preliminary Output"
        style_path = STYLE_PATH
        export_enabled = self.parameterAsBool(parameters, self.EXPORT_MBI, context)
        self.post_processors = []

        # Create directory if it doesn't exist and export is enabled
        if export_enabled:
            if not os.path.exists(export_folder):
                try:
                    os.makedirs(export_folder)
                    feedback.pushInfo(f"Created directory: {export_folder}")
                except Exception as e:
                    feedback.reportError(f"Could not create directory {export_folder}: {str(e)}")
                    export_enabled = False  # Disable export if folder creation fails

        run_overlaps = (run_mode == 0 or run_mode == 1)
        run_gaps = (run_mode == 0 or run_mode == 2)

        feedback.pushInfo(f'Processing... Mode: {["Both", "Overlaps Only", "Gaps Only"][run_mode]}')

        # Check for empty lists (which means no layers were selected)
        if not bgy_list:
            raise QgsProcessingException("Barangay Polygon Layer(s) must be selected.")
        if not bldg_list:
            raise QgsProcessingException("Building Point Layer(s) must be selected.")

        feedback.pushInfo("Merging Selected Layers...")
        feedback.setProgress(2)

        def refactor_layer(layer):
            field_mapping = []
            for f in layer.fields():
                field_mapping.append({
                    'expression': f'"{f.name()}"',
                    'length': 0,            # no limit
                    'name': f.name(),
                    'type': f.type()
                })
            return processing.run("native:refactorfields", {
                'INPUT': layer,
                'FIELDS_MAPPING': field_mapping,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']

        # Refactor all layers before merging
        bgy_refactored = [refactor_layer(l) for l in bgy_list]
        bldg_refactored = [refactor_layer(l) for l in bldg_list]

        # Merge for Barangay Polygons
        # Use the first layer's CRS for the output of merge
        bgy_layer = processing.run("native:mergevectorlayers", {
            'LAYERS': bgy_refactored,
            'CRS': bgy_refactored[0].crs(),
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        # Perform Merge for Building Points
        bldg_layer = processing.run("native:mergevectorlayers", {
            'LAYERS': bldg_refactored,
            'CRS': bldg_refactored[0].crs(),
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setProgress(5)

        reprojected_bgy_res = processing.run("native:reprojectlayer", {
            'CONVERT_CURVED_GEOMETRIES': False,
            'INPUT': bgy_layer,
            'OPERATION': '+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad +step +proj=webmerc +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84',
            'OUTPUT': 'memory:',
            'TARGET_CRS': QgsCoordinateReferenceSystem('EPSG:3857')
        }, context=context, feedback=feedback)

        reprojected_bgy_res_fix = processing.run("native:fixgeometries", {
            'INPUT': reprojected_bgy_res['OUTPUT'],
            'METHOD': 1,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)

        if feedback.isCanceled():
            return {}
        reprojected_bgy = reprojected_bgy_res_fix['OUTPUT']

        feedback.setProgress(10)

        reprojected_bldg_res = processing.run("native:reprojectlayer", {
            'CONVERT_CURVED_GEOMETRIES': False,
            'INPUT': bldg_layer,
            'OPERATION': '+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad +step +proj=webmerc +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84',
            'OUTPUT': 'memory:',
            'TARGET_CRS': QgsCoordinateReferenceSystem('EPSG:3857')
        }, context=context, feedback=feedback)

        reprojected_bldg_res_fix = processing.run("native:fixgeometries", {
            'INPUT': reprojected_bldg_res['OUTPUT'],
            'METHOD': 1,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)

        if feedback.isCanceled():
            return {}
        reprojected_bldg = reprojected_bldg_res_fix['OUTPUT']

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        def export_layer(target, layer_name=f"Gaps and Overlap ({timestamp})", set_style=True):
            if target is None:
                feedback.reportError(f"Skipping {layer_name}: Layer target is None.")
                return

            layer_details = QgsProcessingContext.LayerDetails(
                layer_name,
                context.project(),
                "OUTPUT"
            )

            # Apply style if True
            if set_style:
                processor = MBIStylePostProcessor(style_path)
                self.post_processors.append(processor)
                layer_details.setPostProcessor(processor)

            if isinstance(target, str):
                # Scenario A: Target is a file path (GPKG/SHP)
                context.addLayerToLoadOnCompletion(target, layer_details)
                feedback.pushInfo(f"Saved layer queued: {target}")

            elif isinstance(target, QgsVectorLayer):
                # Scenario B: Target is an in-memory/scratch layer object
                target.setName(layer_name)
                # Register the object so the project can 'see' it after the script ends
                context.temporaryLayerStore().addMapLayer(target)
                # For memory layers, we pass the layer ID to the context
                context.addLayerToLoadOnCompletion(target.id(), layer_details)
                feedback.pushInfo(f"Scratch layer queued: {layer_name}")

            else:
                feedback.reportError(f"Unexpected type for {layer_name}: {type(target)}")

        def build_pair_layer(groups, src_crs):
            """groups: [(geometry in src_crs, piece_attrs, [area_attrs, ...], links)].
            One MBI case per group (any number of involved areas), written as one
            row per ordered pair of areas (A->B and B->A): "this area" and the
            "other area" the overlap/gap is shared with.
            links: [(area_attrs, area_attrs)] pairs that really touch (overlaps:
            the pairs that intersect), or None to pair every involved area (gaps:
            all of them border the same hole). An area with no pair gets one row
            with no other area."""
            xform = QgsCoordinateTransform(src_crs,
                                           QgsCoordinateReferenceSystem('EPSG:4326'),
                                           context.transformContext())
            out = QgsVectorLayer("Polygon?crs=EPSG:4326", "pairs", "memory")
            qtypes = {10: QVariant.String, 4: QVariant.LongLong, 6: QVariant.Double}
            out.dataProvider().addAttributes([QgsField(n, qtypes[t]) for n, t in PAIR_FIELDS])
            out.updateFields()

            feats = []
            for geom, piece, areas, links in groups:
                area, bldg = piece['mbi_area'], piece['num_bldg_pts']
                # Drop slivers (<= 0.1 sq.m. with no buildings)
                if area is not None and bldg is not None and 0 <= area <= 0.1 and bldg == 0:
                    continue
                areas = unique_areas(a for a in areas if a['geocode'] is not None)
                if not areas:
                    continue
                g = QgsGeometry(geom)
                g.transform(xform)
                case = {
                    'case_uuid': piece['gmd_uuid'],
                    'involved_areas': ','.join(a['geocode'] for a in areas),
                    'involved_bgys': ';'.join(f"{a['barangay'] or ''}, {a['city_mun'] or ''}" for a in areas),
                    'involved_eas': ','.join(a['ea_name'] for a in areas if a['ea_name']),
                    'count_involved_areas': len(areas),
                    'num_bldg_pts': bldg,
                }
                if links is None:
                    links = combinations(areas, 2)
                by_key = {area_key(a): a for a in areas}
                pair_keys = set()
                for a, b in links:
                    ka, kb = area_key(a), area_key(b)
                    if ka != kb and ka in by_key and kb in by_key:
                        pair_keys.update(((ka, kb), (kb, ka)))
                pairs = [(a, b) for a in areas for b in areas
                         if (area_key(a), area_key(b)) in pair_keys]
                paired = {ka for ka, _ in pair_keys}
                # e.g. two parts of one EA overlapping each other inside a larger case
                pairs += [(a, None) for a in areas if area_key(a) not in paired]
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

        summary_final_status_filtered = None
        gap_summary_final_status_filtered = None

        # --- Start ng OVERLAP checks---
        if run_overlaps:
            QgsMessageLog.logMessage("Starting Overlap Analysis...")

            progress_start = 10
            progress_end = 100 if run_mode == 1 else 55
            progress_range = progress_end - progress_start

            def update_overlap_progress(step):
                original_range = 55 - 10  # 45 total points for old overlap section
                new_progress = progress_start + (step - 10) * progress_range / original_range
                feedback.setProgress(int(new_progress))

            update_overlap_progress(12)

            singlepart_res = processing.run("native:multiparttosingleparts", {
                'INPUT': reprojected_bgy,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            singlepart_sf = singlepart_res['OUTPUT']

            update_overlap_progress(20)

            # Reconstructed LF Tools' Overlapping Polygon: pairwise pieces first,
            # merged afterwards into one case per contiguous overlap region.
            Fields = QgsFields()
            Fields.append(QgsField('case_idx', QVariant.Int))

            index = QgsSpatialIndex(singlepart_sf.getFeatures())

            overlap_pieces = []      # (geometry, ID1, ID2)
            overlap_cases = []       # merge_overlap_pieces() result, indexed by case_idx
            overlapping_sf = QgsVectorLayer("Polygon?crs=EPSG:3857", "temporary_overlaps", "memory")
            overlapping_sf.dataProvider().addAttributes(Fields)
            overlapping_sf.updateFields()
            sobreposicoes = set()

            total_features = singlepart_sf.featureCount()
            if total_features == 0:
                feedback.pushInfo("No features in single-part layer to check for overlaps. Skipping Overlap Analysis.")
                if run_mode == 0:
                    feedback.setProgress(55)
                elif run_mode == 1:
                    feedback.setProgress(100)
                run_overlaps = False

            if run_overlaps:
                total_progress_steps = total_features
                progress_step_size = (progress_end - 20) / total_progress_steps if total_progress_steps else 0

                for current, feat1 in enumerate(singlepart_sf.getFeatures()):
                    ID1 = feat1.id()
                    geom1 = feat1.geometry()
                    bbox1 = geom1.boundingBox()
                    feat_ids = index.intersects(bbox1)

                    current_progress = 20 + (current * progress_step_size)
                    feedback.setProgress(int(current_progress))

                    for feat2 in singlepart_sf.getFeatures(QgsFeatureRequest(feat_ids)):
                        ID2 = feat2.id()
                        if ID1 != ID2 and (ID2, ID1) not in sobreposicoes:
                            geom2 = feat2.geometry()
                            if geom1.intersects(geom2):
                                inter = geom1.intersection(geom2)

                                if not inter.isEmpty() and inter.wkbType() != QgsWkbTypes.NoGeometry:
                                    sobreposicoes.add((ID1, ID2))

                                    for item in inter.asGeometryCollection():
                                        if item.type() == 2:  # polygon
                                            if item.isMultipart():
                                                for coord in item.asMultiPolygon():
                                                    overlap_pieces.append(
                                                        (QgsGeometry.fromPolygonXY(coord), ID1, ID2))
                                            else:
                                                overlap_pieces.append((QgsGeometry(item), ID1, ID2))
                    if feedback.isCanceled():
                        break

                if feedback.isCanceled():
                    return {}

                # One case per contiguous overlap region, however many areas it
                # crosses (A∩B + A∩C + B∩C ... -> one case with A, B, C).
                feedback.pushInfo(f"Merging {len(overlap_pieces):,} pairwise overlap piece(s) into cases...")
                overlap_cases = merge_overlap_pieces(overlap_pieces, feedback)
                multi = sum(1 for c in overlap_cases if len(c['fids']) > 2)
                feedback.pushInfo(f"  {len(overlap_cases):,} overlap case(s), "
                                  f"{multi:,} involving more than two polygons")

                overlap_features_to_add = []
                for case_idx, case in enumerate(overlap_cases):
                    feature = QgsFeature(Fields)
                    feature.setGeometry(case['geom'])
                    feature.setAttributes([case_idx])
                    overlap_features_to_add.append(feature)
                if overlap_features_to_add:
                    overlapping_sf.dataProvider().addFeatures(overlap_features_to_add)

                if overlapping_sf.featureCount() == 0:
                    feedback.pushInfo(
                        "Overlap check finished but no overlapping features were found. Skipping subsequent overlap steps.")
                    if run_mode == 0:
                        feedback.setProgress(55)
                    elif run_mode == 1:
                        feedback.setProgress(100)
                    run_overlaps = False

            if feedback.isCanceled():
                return {}

            update_overlap_progress(25)

            overlapping_sf_with_uuid_res = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': [
                    {'alias': '', 'comment': '', 'expression': '"case_idx"', 'length': 0, 'name': 'case_idx',
                     'precision': 0, 'sub_type': 0, 'type': 2, 'type_name': 'integer'},
                    {'alias': '', 'comment': '', 'expression': 'substr($uuid, 2,36)', 'length': 0, 'name': 'gmd_uuid',
                     'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'},
                    {'alias': '', 'comment': '', 'expression': ' round($area , 2)', 'length': 0, 'name': 'mbi_area',
                     'precision': 0, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'}],
                'INPUT': overlapping_sf,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            overlapping_sf_with_uuid = overlapping_sf_with_uuid_res['OUTPUT']

            update_overlap_progress(28)

            min_bounding_res = processing.run("qgis:minimumboundinggeometry", {
                'FIELD': 'gmd_uuid',
                'INPUT': overlapping_sf_with_uuid,
                'OUTPUT': 'memory:',
                'TYPE': 1
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            min_bounding_sf = min_bounding_res['OUTPUT']

            update_overlap_progress(30)

            overlap_joined_res = processing.run("native:joinattributestable", {
                'DISCARD_NONMATCHING': False,
                'FIELD': 'gmd_uuid',
                'FIELDS_TO_COPY': ['gmd_uuid', 'width', 'height'],
                'FIELD_2': 'gmd_uuid',
                'INPUT': overlapping_sf_with_uuid,
                'INPUT_2': min_bounding_sf,
                'METHOD': 1,
                'OUTPUT': 'memory:',
                'PREFIX': ''
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            overlap_joined = overlap_joined_res['OUTPUT']

            update_overlap_progress(32)

            overlap_joined_refactored_res = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': [
                    {'alias': '', 'comment': '', 'expression': '"case_idx"', 'length': 0, 'name': 'case_idx',
                     'precision': 0, 'sub_type': 0, 'type': 2, 'type_name': 'integer'},
                    {'alias': '', 'comment': '', 'expression': '"gmd_uuid"', 'length': 0, 'name': 'gmd_uuid',
                     'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'},
                    {'alias': '', 'comment': '', 'expression': '"mbi_area"', 'length': 0, 'name': 'mbi_area',
                     'precision': 0, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                    {'alias': '', 'comment': '', 'expression': '"width"', 'length': 20, 'name': 'mbi_width',
                     'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                    {'alias': '', 'comment': '', 'expression': '"height"', 'length': 20, 'name': 'mbi_height',
                     'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'}],
                'INPUT': overlap_joined,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            overlap_joined_refactored = overlap_joined_refactored_res['OUTPUT']

            update_overlap_progress(38)

            count_bldg = processing.run("native:countpointsinpolygon", {
                'CLASSFIELD': '',
                'FIELD': 'num_bldg_pts',
                'OUTPUT': 'memory:',
                'POINTS': reprojected_bldg,
                'POLYGONS': overlap_joined_refactored,
                'WEIGHT': ''
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            count_bldg_sf = count_bldg['OUTPUT']

            update_overlap_progress(42)

            # Each case knows the polygons whose pairwise overlaps formed it, so
            # take those areas (and the pairs that really overlap) directly - no
            # spatial join.
            area_by_fid = {f.id(): area_attrs(f) for f in singlepart_sf.getFeatures()}
            overlap_groups = []
            for f in count_bldg_sf.getFeatures():
                case = overlap_cases[f['case_idx']]
                overlap_groups.append((
                    f.geometry(), piece_attrs(f),
                    [area_by_fid[i] for i in sorted(case['fids'])],
                    [(area_by_fid[a], area_by_fid[b]) for a, b in sorted(case['links'])],
                ))

            update_overlap_progress(48)

            feedback.pushInfo("Writing Overlap Layer")
            summary_final_status_filtered = build_pair_layer(overlap_groups, count_bldg_sf.crs())

            feedback.pushInfo("Exporting Overlap Layer")
            export_layer(summary_final_status_filtered, r'Overlaps')

            # Final progress update for the Overlaps block
            feedback.setProgress(progress_end)

        # --- GAPS ANALYSIS BLOCK ---
        if run_gaps:
            QgsMessageLog.logMessage("Starting Gaps Analysis...")

            progress_start = 10 if run_mode == 2 else 55
            progress_end = 100
            progress_range = progress_end - progress_start

            def update_gaps_progress(step):
                original_range = 96 - 55  # 41 total points for old gaps section up to the end
                if step < 55:
                    return
                new_progress = progress_start + (step - 55) * progress_range / original_range
                feedback.setProgress(int(new_progress))

            update_gaps_progress(60)

            dissolved_bgy = processing.run("native:dissolve", {
                'FIELD': '',
                'INPUT': bgy_layer,
                'OUTPUT': 'memory:',
                'SEPARATE_DISJOINT': False
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            dissolved_bgy_sf = dissolved_bgy['OUTPUT']

            update_gaps_progress(65)

            delete_holes = processing.run("native:deleteholes", {
                'INPUT': dissolved_bgy_sf,
                'MIN_AREA': 0,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            delete_holes_sf = delete_holes['OUTPUT']

            update_gaps_progress(70)

            dissolved_bgy_cleaned = processing.run("native:dissolve", {
                'FIELD': '',
                'INPUT': delete_holes_sf,
                'OUTPUT': 'memory:',
                'SEPARATE_DISJOINT': False
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            dissolved_bgy_cleaned_sf = dissolved_bgy_cleaned['OUTPUT']

            update_gaps_progress(72)

            difference_bgy = processing.run("native:difference", {
                'GRID_SIZE': 'None',
                'INPUT': dissolved_bgy_cleaned_sf,
                'OVERLAY': dissolved_bgy_sf,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            difference_bgy_sf = difference_bgy['OUTPUT']

            update_gaps_progress(75)

            multi_to_single = processing.run("native:multiparttosingleparts", {
                'INPUT': difference_bgy_sf,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            multi_to_single_sf = multi_to_single['OUTPUT']

            update_gaps_progress(78)

            multi_to_single_sf_uuid_res = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': [
                    {'alias': '', 'comment': '', 'expression': 'substr($uuid, 2,36)', 'length': 0, 'name': 'gmd_uuid',
                     'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'}],
                'INPUT': multi_to_single_sf,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            multi_to_single_sf_uuid = multi_to_single_sf_uuid_res['OUTPUT']

            update_gaps_progress(80)

            min_bounding_gap = processing.run("qgis:minimumboundinggeometry", {
                'FIELD': 'gmd_uuid',
                'INPUT': multi_to_single_sf_uuid,
                'OUTPUT': 'memory:',
                'TYPE': 1
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            min_bounding_gap_sf = min_bounding_gap['OUTPUT']

            update_gaps_progress(82)

            gap_joined_res = processing.run("native:joinattributestable", {
                'DISCARD_NONMATCHING': False,
                'FIELD': 'gmd_uuid',
                'FIELDS_TO_COPY': ['gmd_uuid', 'width', 'height'],
                'FIELD_2': 'gmd_uuid',
                'INPUT': multi_to_single_sf_uuid,
                'INPUT_2': min_bounding_gap_sf,
                'METHOD': 1,
                'OUTPUT': 'memory:',
                'PREFIX': ''
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            gap_joined = gap_joined_res['OUTPUT']

            update_gaps_progress(88)

            gap_joined_refactored_res = processing.run("native:refactorfields", {
                'FIELDS_MAPPING': [
                    {'alias': '', 'comment': '', 'expression': '"gmd_uuid"', 'length': 0, 'name': 'gmd_uuid',
                     'precision': 0, 'sub_type': 0, 'type': 10, 'type_name': 'text'},
                    {'alias': '', 'comment': '', 'expression': ' round($area , 2)', 'length': 0, 'name': 'mbi_area',
                     'precision': 0, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                    {'alias': '', 'comment': '', 'expression': '"width"', 'length': 20, 'name': 'mbi_width',
                     'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'},
                    {'alias': '', 'comment': '', 'expression': '"height"', 'length': 20, 'name': 'mbi_height',
                     'precision': 6, 'sub_type': 0, 'type': 6, 'type_name': 'double precision'}],
                'INPUT': gap_joined,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            gap_joined_refactored = gap_joined_refactored_res['OUTPUT']

            update_gaps_progress(92)

            count_bldg_gap = processing.run("native:countpointsinpolygon", {
                'CLASSFIELD': '',
                'FIELD': 'num_bldg_pts',
                'OUTPUT': 'memory:',
                'POINTS': reprojected_bldg,
                'POLYGONS': gap_joined_refactored,
                'WEIGHT': ''
            }, context=context, feedback=feedback)

            if feedback.isCanceled():
                return {}
            count_bldg_gap_sf = count_bldg_gap['OUTPUT']

            update_gaps_progress(96)

            # Areas each gap borders. The gaps were cut from bgy_layer (the
            # input CRS, not EPSG:3857), so match against bgy_layer in that same
            # CRS. A shared edge is required: areas that only touch the gap at a
            # corner are not candidates to absorb it.
            gap_crs = count_bldg_gap_sf.crs()
            to_metres = QgsCoordinateTransform(gap_crs, QgsCoordinateReferenceSystem('EPSG:3857'),
                                               context.transformContext())
            gap_bgy = processing.run("native:fixgeometries", {
                'INPUT': bgy_layer,
                'METHOD': 1,
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            if gap_bgy.crs() != gap_crs:
                gap_bgy = processing.run("native:reprojectlayer", {
                    'INPUT': gap_bgy,
                    'TARGET_CRS': gap_crs,
                    'OUTPUT': 'memory:'
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
                gap_groups.append((gap_geom, piece_attrs(gap), sharing_edge or touching, None))

            update_gaps_progress(98)

            feedback.pushInfo("Writing Gaps Layer")
            gap_summary_final_status_filtered = build_pair_layer(gap_groups, gap_crs)

            export_layer(gap_summary_final_status_filtered, r'Gaps')

            export_file_path = os.path.join(export_folder, f"gaps_overlap_{timestamp}.gpkg")

            def export_layer_final(overlap_layer, gap_layer, file_path):
                if export_enabled and overlap_layer is not None and gap_layer is not None:
                    def mbi_extras(mbi_type):
                        return [
                            ('mbi_type', f"'{mbi_type}'"),
                            ('mbi_category', 'NULL'),
                            ('mbi_status',
                             "CASE WHEN \"mbi_level\" IN ('1_Inter-Region', '2_Inter-Province') THEN '2_Pending' END"),
                            ('mbi_remarks', 'NULL'),
                        ]

                    overlap_layer_refactor = processing.run("native:refactorfields", {
                        'INPUT': overlap_layer,
                        'OUTPUT': 'memory:',
                        'FIELDS_MAPPING': pair_field_mapping(mbi_extras('Overlap'))
                    }, context=context, feedback=feedback)

                    gap_layer_refactor = processing.run("native:refactorfields", {
                        'INPUT': gap_layer,
                        'OUTPUT': 'memory:',
                        'FIELDS_MAPPING': pair_field_mapping(mbi_extras('Gap'))
                    }, context=context, feedback=feedback)

                    for_export_layer = processing.run("native:mergevectorlayers", {
                        'CRS': QgsCoordinateReferenceSystem('EPSG:4326'),
                        'LAYERS': [
                            overlap_layer_refactor['OUTPUT'],
                            gap_layer_refactor['OUTPUT']],
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)

                    # Check if file exists and delete it to allow overwriting
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            feedback.pushInfo(f"Existing file {file_path} found and removed for overwrite.")
                        except Exception as e:
                            feedback.reportError(f"Could not remove existing file: {str(e)}")

                    # Also drops the 'layer'/'path' fields added by the merge.
                    for_export_layer_refactor = processing.run("native:refactorfields", {
                        'INPUT': for_export_layer['OUTPUT'],
                        'OUTPUT': file_path,
                        'FIELDS_MAPPING': pair_field_mapping(
                            [(n, f'"{n}"') for n, _ in mbi_extras('')])
                    }, context=context, feedback=feedback)

                    layer_details = QgsProcessingContext.LayerDetails(
                        f"Gaps and Overlap ({timestamp})",
                        context.project(),
                        "OUTPUT"
                    )

                    processor = MBIStylePostProcessor(style_path)
                    self.post_processors.append(processor)
                    layer_details.setPostProcessor(processor)

                    context.addLayerToLoadOnCompletion(file_path, layer_details)
                    feedback.pushInfo("Layer queued for native loading in group")

            if run_mode == 0:  # Running both gaps and overlaps
                if summary_final_status_filtered is not None and gap_summary_final_status_filtered is not None:
                    export_layer_final(summary_final_status_filtered, gap_summary_final_status_filtered, export_file_path)
                else:
                    feedback.pushInfo("Skipping merged GPKG export: one or both discrepancy layers are empty or skipped.")

            feedback.pushInfo("Done setting layer style")

            # Final progress update for the Gaps block
            feedback.setProgress(progress_end)

        # --- 8. RETURN OUTPUT ---
        # Return an empty dictionary since layers are added directly to the project
        return {}


# ======================== CONSOLE RUNNER ========================

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


def run_mbi_checker():
    bgy_layers = _resolve_layers(BGY_LAYER_NAMES, QgsWkbTypes.PolygonGeometry, 'Barangay polygon')
    bldg_layers = _resolve_layers(BLDG_LAYER_NAMES, QgsWkbTypes.PointGeometry, 'Building point')
    print("Barangay layers: " + ", ".join(l.name() for l in bgy_layers))
    print("Building layers: " + ", ".join(l.name() for l in bldg_layers))
    if STYLE_PATH and not os.path.exists(STYLE_PATH):
        print(f"WARNING: style file not found, outputs will not be styled: {STYLE_PATH}")

    alg = GapsOverlaps()
    alg.initAlgorithm()

    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    feedback = ConsoleFeedback()
    context.setFeedback(feedback)

    params = {
        'INPUT1': bgy_layers,
        'INPUT2': bldg_layers,
        'RUN_MODE': RUN_MODE,
        'EXPORT_MBI': EXPORT_MBI,
    }

    start = datetime.now()
    try:
        alg.processAlgorithm(params, context, feedback)
    except QgsProcessingException as e:
        print(f"ERROR: {e}")
        return
    load_results(context)
    print(f"MBI Checker finished in {datetime.now() - start}.")


def open_mbi_dialog():
    """Open the MBI Checker dialog."""
    try:
        from qgis.utils import iface
        if iface is None or iface.mainWindow() is None:
            print("MBI Checker dialog requires the QGIS graphical interface.")
            return
    except (ImportError, AttributeError):
        print("MBI Checker dialog requires the QGIS graphical interface.")
        return

    # Restore wait cursor if opened from GMD CO Processing panel
    try:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
    except Exception:
        pass

    alg = GapsOverlaps()
    provider = QgsApplication.processingRegistry().providerById("script")
    if provider is not None:
        alg.setProvider(provider)
    alg.initAlgorithm()
    processing.execAlgorithmDialog(alg)


# When the file is dragged onto QGIS / run via "Run Script", QGIS itself execs
# this file and then opens the dialog for the GapsOverlaps class it finds.
# That runner execs without __name__, so we must not open a second dialog.
_RUN_BY_QGIS_SCRIPT_RUNNER = "__name__" not in globals()

if __name__ == "__main__" or _RUN_BY_QGIS_SCRIPT_RUNNER:
    if BGY_LAYER_NAMES and BLDG_LAYER_NAMES:
        run_mbi_checker()                 # headless, using the CONFIG layer names
        if _RUN_BY_QGIS_SCRIPT_RUNNER:
            del GapsOverlaps              # stop QGIS from also opening the dialog
    elif not _RUN_BY_QGIS_SCRIPT_RUNNER:
        open_mbi_dialog()                 # pick layers in the dialog

