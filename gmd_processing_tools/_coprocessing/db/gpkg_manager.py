# -*- coding: utf-8 -*-
"""
CO Processing — Consolidated EA GeoPackage Database Manager
------------------------------------------------------------
Manages a persistent, updatable master GeoPackage (`consolidated_ea.gpkg`)
that merges and deduplicates spatial Enumeration Area (EA) polygon layers
across multiple municipal or provincial uploads.

Features:
  - Normalizes all input geometries to EPSG:4326 (WGS 84).
  - Enforces MultiPolygon geometry.
  - Automatically calculates 11-digit PSGC geocodes.
  - Spatial upsert: updates existing geocodes or appends new EAs.
  - Tracks source file name and update timestamp for provenance.
  - Ultra-fast indexed queries via SQLite/OGR.
"""

import os
import sqlite3
import datetime
from typing import Dict, List, Optional, Tuple, Any, Union

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

import uuid
import re

try:
    from ..core.algorithm import build_geocode, GPKG_EARF_FIELDS, to_whole_number_str
except (ImportError, ValueError):
    try:
        from core.algorithm import build_geocode, GPKG_EARF_FIELDS, to_whole_number_str
    except (ImportError, ValueError):
        try:
            from algorithm import build_geocode, GPKG_EARF_FIELDS, to_whole_number_str
        except (ImportError, ValueError):
            def to_whole_number_str(val) -> str:
                if val is None:
                    return ""
                s = str(val).strip()
                if s.endswith(".0"):
                    s = s[:-2].strip()
                return s


GPKG_FILENAME = "consolidated_ea.gpkg"
MASTER_LAYER_NAME = "consolidated_ea"
TARGET_CRS_AUTHID = "EPSG:4326"


def get_default_gpkg_path() -> str:
    """Returns the default GeoPackage path in the plugin directory."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("db", "core", "ui") else os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(base_dir, "data", GPKG_FILENAME)
    if os.path.exists(data_path):
        return data_path
    root_path = os.path.join(base_dir, GPKG_FILENAME)
    if os.path.exists(root_path):
        return root_path
    return data_path


class EAGeoPackageDatabase:
    """
    Manages the consolidated spatial GeoPackage for Enumeration Areas.
    """

    def __init__(self, gpkg_path: Optional[str] = None):
        self.gpkg_path = gpkg_path or get_default_gpkg_path()
        self.layer_name = MASTER_LAYER_NAME
        self.target_crs = QgsCoordinateReferenceSystem(TARGET_CRS_AUTHID)
        self.init_db()

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.gpkg_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_layer(self) -> QgsVectorLayer:
        """Returns a QgsVectorLayer connected to the consolidated GeoPackage."""
        uri = f"{self.gpkg_path}|layername={self.layer_name}"
        return QgsVectorLayer(uri, self.layer_name, "ogr")

    def init_db(self):
        """Initializes the master GeoPackage layer if it does not yet exist."""
        if os.path.exists(self.gpkg_path):
            layer = self.get_layer()
            if layer.isValid():
                self._ensure_indexes()
                return

        fields = QgsFields()
        fields.append(QgsField("geocode", QVariant.String))
        fields.append(QgsField("region_code", QVariant.String))
        fields.append(QgsField("province_code", QVariant.String))
        fields.append(QgsField("city_mun_code", QVariant.String))
        fields.append(QgsField("barangay_code", QVariant.String))
        fields.append(QgsField("ean", QVariant.String))
        fields.append(QgsField("no_of_eas", QVariant.String))
        fields.append(QgsField("areaname", QVariant.String))
        fields.append(QgsField("source_file", QVariant.String))
        fields.append(QgsField("updated_at", QVariant.String))

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = self.layer_name

        transform_context = QgsProject.instance().transformContext() or QgsCoordinateTransformContext()
        writer = QgsVectorFileWriter.create(
            self.gpkg_path,
            fields,
            QgsWkbTypes.MultiPolygon,
            self.target_crs,
            transform_context,
            options,
        )
        del writer
        self._ensure_indexes()

    def _ensure_indexes(self):
        """Ensures index on geocode column for rapid lookups."""
        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.layer_name}_gc ON {self.layer_name}(geocode);")
                conn.commit()
        except Exception:
            pass

    def feature_count(self) -> int:
        """Returns the total number of features in the consolidated layer."""
        if not os.path.exists(self.gpkg_path):
            return 0
        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {self.layer_name}")
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception:
            layer = self.get_layer()
            return layer.featureCount() if layer.isValid() else 0

    def is_populated(self) -> bool:
        return self.feature_count() > 0

    def clear(self):
        """Clears all records from the consolidated GeoPackage."""
        if not os.path.exists(self.gpkg_path):
            return
        layer = self.get_layer()
        if layer.isValid():
            fids = [f.id() for f in layer.getFeatures()]
            if fids:
                layer.startEditing()
                layer.deleteFeatures(fids)
                layer.commitChanges()
        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute("VACUUM")
                conn.commit()
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Returns summary statistics for the consolidated GeoPackage."""
        stats = {
            "total_features": 0,
            "unique_geocodes": 0,
            "provinces": 0,
            "sources": 0,
            "source_files": [],
            "source_files_count": 0,
            "gpkg_path": self.gpkg_path,
            "file_size_mb": round(os.path.getsize(self.gpkg_path) / (1024 * 1024), 2) if os.path.exists(self.gpkg_path) else 0.0,
            "last_updated": "-",
        }
        if not os.path.exists(self.gpkg_path):
            return stats
        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(DISTINCT geocode) as unique_gc,
                        COUNT(DISTINCT province_code) as provs,
                        COUNT(DISTINCT source_file) as sources,
                        MAX(updated_at) as last_updated
                    FROM {self.layer_name}
                """)
                row = cur.fetchone()
                if row:
                    stats["total_features"] = row["total"] or 0
                    stats["unique_geocodes"] = row["unique_gc"] or 0
                    stats["provinces"] = row["provs"] or 0
                    stats["sources"] = row["sources"] or 0
                    stats["last_updated"] = row["last_updated"] or "-"

                cur.execute(f"SELECT DISTINCT source_file, COUNT(*) FROM {self.layer_name} GROUP BY source_file")
                src_list = []
                for s, cnt in cur.fetchall():
                    if s:
                        src_list.append(f"{s} ({cnt:,} features)")
                stats["source_files"] = src_list
                stats["source_files_count"] = len(src_list)
        except Exception:
            stats["total_features"] = self.feature_count()
        return stats

    def consolidate_layers(
        self,
        layers_or_paths: List[Union[str, QgsVectorLayer]],
        mode: str = "upsert",
        progress_callback=None,
    ) -> Dict[str, int]:
        """
        Consolidates multiple GeoPackage layers or files into the master layer.
        
        :param layers_or_paths: List of file paths or QgsVectorLayer instances.
        :param mode: 'upsert' (update existing geocodes or insert new),
                     'replace' (clear master layer first), or 'append'.
        :param progress_callback: Optional callable(pct: int, msg: str).
        :return: Dict with 'total', 'inserted', 'updated', 'skipped'.
        """
        self.init_db()

        if mode == "replace":
            self.clear()

        dest_layer = self.get_layer()
        if not dest_layer.isValid():
            raise RuntimeError(f"Could not open consolidated GeoPackage layer at {self.gpkg_path}")

        # Map existing geocode -> fid in destination layer
        existing_map: Dict[str, int] = {}
        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT fid, geocode FROM {self.layer_name} WHERE geocode IS NOT NULL")
                for r in cur.fetchall():
                    if r["geocode"]:
                        existing_map[r["geocode"]] = r["fid"]
        except Exception:
            for f in dest_layer.getFeatures():
                gc = f["geocode"]
                if gc:
                    existing_map[str(gc)] = f.id()

        total_files = len(layers_or_paths)
        inserted_count = 0
        updated_count = 0
        skipped_count = 0

        dest_fields = dest_layer.fields()
        dest_layer.startEditing()

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for f_idx, item in enumerate(layers_or_paths):
            if isinstance(item, QgsVectorLayer):
                src_layer = item
                src_name = src_layer.name() or f"Layer_{f_idx + 1}"
                if not src_layer.isValid() or src_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
                    continue
            else:
                fpath = str(item).strip()
                if not fpath or not os.path.exists(fpath):
                    continue
                src_name = os.path.basename(fpath)
                try:
                    from .database import get_gpkg_polygon_layer_info
                    has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(fpath)
                except Exception:
                    try:
                        from COProcessing.db.database import get_gpkg_polygon_layer_info
                        has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(fpath)
                    except Exception:
                        try:
                            from db.database import get_gpkg_polygon_layer_info
                            has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(fpath)
                        except Exception:
                            try:
                                from database import get_gpkg_polygon_layer_info
                                has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(fpath)
                            except Exception:
                                has_poly, poly_tables, desc = True, [], ""
                if not has_poly and poly_tables is not None and len(poly_tables) == 0 and "non-polygon" in desc.lower():
                    continue
                if poly_tables:
                    src_layer = QgsVectorLayer(f"{fpath}|layername={poly_tables[0]}", f"{src_name} - {poly_tables[0]}", "ogr")
                else:
                    src_layer = QgsVectorLayer(fpath, src_name, "ogr")
                if not src_layer.isValid() or src_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
                    continue

            if progress_callback:
                pct = int((f_idx / total_files) * 100)
                progress_callback(pct, f"Consolidating ({f_idx + 1}/{total_files}): {src_name}...")

            # Coordinate transformation if necessary
            reproject = False
            transform = None
            if src_layer.crs().isValid() and src_layer.crs() != self.target_crs:
                reproject = True
                transform_context = QgsProject.instance().transformContext() or QgsCoordinateTransformContext()
                transform = QgsCoordinateTransform(src_layer.crs(), self.target_crs, transform_context)

            # Field mapping cache for source layer
            src_fields = src_layer.fields()
            fnames_lower = {f.name().lower(): f.name() for f in src_fields}

            def _get_val(cand_names, feat):
                for c in cand_names:
                    if c.lower() in fnames_lower:
                        v = feat[fnames_lower[c.lower()]]
                        if v is not None and str(v).strip().lower() != "null":
                            return str(v).strip()
                return ""

            new_features_to_add: List[QgsFeature] = []

            for feat in src_layer.getFeatures():
                geom = feat.geometry()
                if not geom or geom.isNull() or geom.isEmpty():
                    skipped_count += 1
                    continue

                # Ensure geometry is strictly Polygon or MultiPolygon
                if geom.type() != QgsWkbTypes.PolygonGeometry:
                    skipped_count += 1
                    continue

                if reproject and transform:
                    try:
                        geom.transform(transform)
                    except Exception:
                        skipped_count += 1
                        continue

                # Ensure MultiPolygon representation
                if not geom.isMultipart():
                    geom.convertToMultiType()

                attrs_dict = {f.name(): feat[f.name()] for f in src_fields}
                gc = build_geocode(attrs_dict, GPKG_EARF_FIELDS)
                if not gc:
                    skipped_count += 1
                    continue

                prov = _get_val(["Prov", "province_code", "prov"], feat)
                mun = _get_val(["Mun", "city_mun_code", "mun"], feat)
                brgy = _get_val(["Brgy", "barangay_code", "brgy"], feat)
                ean = _get_val(["EA", "ean", "ea_code"], feat)
                reg = _get_val(["Region", "region_code", "reg"], feat)
                area = _get_val(["areaname", "area_name", "name"], feat)
                n_ea = _get_val(["No_EA", "no_of_eas", "eas", "count"], feat)

                # Upsert check
                if gc in existing_map and existing_map[gc] != -1:
                    # Update existing feature geometry & attributes
                    fid = existing_map[gc]
                    dest_layer.changeGeometry(fid, geom)
                    
                    # Update attribute fields
                    attr_updates = {
                        dest_fields.indexOf("areaname"): area,
                        dest_fields.indexOf("no_of_eas"): n_ea,
                        dest_fields.indexOf("source_file"): src_name,
                        dest_fields.indexOf("updated_at"): now_str,
                    }
                    dest_layer.changeAttributeValues(fid, attr_updates)
                    updated_count += 1
                else:
                    # Create new feature
                    new_feat = QgsFeature(dest_fields)
                    new_feat.setGeometry(geom)
                    new_feat.setAttribute("geocode", gc)
                    new_feat.setAttribute("region_code", reg)
                    new_feat.setAttribute("province_code", prov)
                    new_feat.setAttribute("city_mun_code", mun)
                    new_feat.setAttribute("barangay_code", brgy)
                    new_feat.setAttribute("ean", ean)
                    new_feat.setAttribute("no_of_eas", n_ea)
                    new_feat.setAttribute("areaname", area)
                    new_feat.setAttribute("source_file", src_name)
                    new_feat.setAttribute("updated_at", now_str)

                    new_features_to_add.append(new_feat)
                    existing_map[gc] = -1
                    inserted_count += 1

            if new_features_to_add:
                dest_layer.addFeatures(new_features_to_add)

        dest_layer.commitChanges()
        self._ensure_indexes()

        if progress_callback:
            progress_callback(100, f"Consolidation complete: {inserted_count} added, {updated_count} updated.")

        return {
            "total_files": total_files,
            "inserted": inserted_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_records": self.feature_count(),
        }

    def query_records(
        self,
        search: Optional[str] = None,
        limit: int = 500,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Fast indexed query with pagination and search across consolidated GeoPackage records.
        """
        if not os.path.exists(self.gpkg_path):
            return [], 0

        clauses = []
        params = []
        if search:
            s_clean = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(areaname) LIKE ? OR geocode LIKE ? OR ean LIKE ? OR province_code LIKE ? OR source_file LIKE ?)")
            params.extend([s_clean, s_clean, s_clean, s_clean, s_clean])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {self.layer_name} {where_sql}", params)
                total = cur.fetchone()[0]

                query_params = list(params) + [limit, offset]
                cur.execute(f"""
                    SELECT fid, geocode, areaname, no_of_eas, province_code, city_mun_code,
                           barangay_code, ean, region_code, source_file, updated_at
                    FROM {self.layer_name}
                    {where_sql}
                    ORDER BY fid ASC
                    LIMIT ? OFFSET ?
                """, query_params)
                rows = [dict(r) for r in cur.fetchall()]
                return rows, total
        except Exception:
            return [], 0



# ---------------------------------------------------------------------------
# Reconciled Geocode Matching GPKG Exporter
# Schema strictly matches the standard 2026 EARF attribute specifications:
#   fid, ea_geocode, map_uuid, bgy_geocode, province_code, city_mun_code,
#   barangay_code, ean, region_code, region, province, city_mun, barangay,
#   name, code, correspondence_geocode, remarks, hhcount, bldgcount, sy
# ---------------------------------------------------------------------------

MATCHED_GPKG_FIELD_DEFINITIONS = [
    ("ea_geocode", QVariant.String),
    ("map_uuid", QVariant.String),
    ("bgy_geocode", QVariant.String),
    ("province_code", QVariant.String),
    ("city_mun_code", QVariant.String),
    ("barangay_code", QVariant.String),
    ("ean", QVariant.String),
    ("region_code", QVariant.String),
    ("region", QVariant.String),
    ("province", QVariant.String),
    ("city_mun", QVariant.String),
    ("barangay", QVariant.String),
    ("name", QVariant.String),
    ("code", QVariant.String),
    ("correspondence_geocode", QVariant.String),
    ("remarks", QVariant.String),
    ("hhcount", QVariant.Int),
    ("bldgcount", QVariant.Int),
    ("sy", QVariant.String),
]


def export_matched_records_to_gpkg(
    records: List[Dict[str, Any]],
    out_gpkg_path: str,
    layer_name: str = "ea2026",
    crs_auth_id: str = "EPSG:4326",
    geometry_sources: Optional[List[Union[str, QgsVectorLayer]]] = None,
    popcen_db_path: Optional[str] = None,
    gpkg_db_path: Optional[str] = None,
    progress_callback=None,
) -> Tuple[int, int]:
    """
    Exports reconciled matched records to a GeoPackage vector layer matching
    the exact attribute schema specified:
      fid, ea_geocode, map_uuid, bgy_geocode, province_code, city_mun_code,
      barangay_code, ean, region_code, region, province, city_mun, barangay,
      name, code, correspondence_geocode, remarks, hhcount, bldgcount, sy

    Returns:
      (total_features_written, features_with_geometry_count)
    """
    if not records:
        return 0, 0

    target_crs = QgsCoordinateReferenceSystem(crs_auth_id)

    # 1. Geographic lookup preparation from popcen_database.db
    reg_lookup: Dict[str, str] = {}
    prov_lookup: Dict[str, Tuple[str, str]] = {}
    mun_lookup: Dict[str, str] = {}
    brgy_lookup: Dict[str, str] = {}

    pop_path = popcen_db_path
    if not pop_path or not os.path.exists(pop_path):
        from .database import get_default_db_path
        pop_path = get_default_db_path()

    if pop_path and os.path.exists(pop_path):
        try:
            with sqlite3.connect(pop_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT region_code, geographic_name FROM popcen_records WHERE geographic_level LIKE '%1 - Region%'")
                for r in cur.fetchall():
                    if r[0] and r[1]:
                        reg_lookup[str(r[0]).strip()] = str(r[1]).strip()

                cur.execute("SELECT province_code, geographic_name, region_code FROM popcen_records WHERE geographic_level LIKE '%2 - Province%'")
                for r in cur.fetchall():
                    if r[0] and r[1]:
                        prov_lookup[str(r[0]).strip()] = (str(r[1]).strip(), str(r[2] or "").strip())

                cur.execute("SELECT province_code || city_mun_code, geographic_name FROM popcen_records WHERE geographic_level LIKE '%3 - City%'")
                for r in cur.fetchall():
                    if r[0] and r[1]:
                        mun_lookup[str(r[0]).strip()] = str(r[1]).strip()

                cur.execute("SELECT province_code || city_mun_code || barangay_code, geographic_name FROM popcen_records WHERE geographic_level LIKE '%4 - Barangay%'")
                for r in cur.fetchall():
                    if r[0] and r[1]:
                        brgy_lookup[str(r[0]).strip()] = str(r[1]).strip()
        except Exception:
            pass

    # 2. Metadata cache from gpkg_database.db
    gpkg_meta: Dict[str, Dict[str, Any]] = {}
    gpkg_path = gpkg_db_path
    if not gpkg_path or not os.path.exists(gpkg_path):
        from .database import get_default_gpkg_db_path
        gpkg_path = get_default_gpkg_db_path()

    if gpkg_path and os.path.exists(gpkg_path):
        try:
            with sqlite3.connect(gpkg_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT geocode, map_uuid, region, province, city_mun, barangay, name, code, remarks, COALESCE(NULLIF(hh_count, ''), hhcount, ''), COALESCE(NULLIF(bldg_count, ''), bldgcount, ''), sy FROM gpkg_records")
                for row in cur.fetchall():
                    gc = str(row[0] or "").strip()
                    if gc:
                        gpkg_meta[gc] = {
                            "map_uuid": row[1],
                            "region": row[2],
                            "province": row[3],
                            "city_mun": row[4],
                            "barangay": row[5],
                            "name": row[6],
                            "code": row[7],
                            "remarks": row[8],
                            "hh_count": row[9],
                            "bldg_count": row[10],
                            "sy": row[11],
                        }
        except Exception:
            pass

    # 3. Geometry and attribute collection from provided sources
    geom_by_uuid: Dict[str, QgsGeometry] = {}
    geom_by_gc: Dict[str, QgsGeometry] = {}
    geom_by_bgy_ean: Dict[Tuple[str, str], QgsGeometry] = {}
    orig_attrs_by_uuid: Dict[str, Dict[str, Any]] = {}
    orig_attrs_by_gc: Dict[str, Dict[str, Any]] = {}
    orig_attrs_by_bgy_ean: Dict[Tuple[str, str], Dict[str, Any]] = {}
    gpkg_counts_by_uuid: Dict[str, Tuple[Any, Any]] = {}
    gpkg_counts_by_gc: Dict[str, Tuple[Any, Any]] = {}
    gpkg_counts_by_bgy_ean: Dict[Tuple[str, str], Tuple[Any, Any]] = {}

    if geometry_sources:
        for s_idx, src in enumerate(geometry_sources):
            if progress_callback:
                progress_callback(int((s_idx / max(len(geometry_sources), 1)) * 30), "Reading input geometry sources...")

            src_layers: List[QgsVectorLayer] = []
            if isinstance(src, QgsVectorLayer):
                if src.isValid() and src.geometryType() == QgsWkbTypes.PolygonGeometry:
                    src_layers.append(src)
            elif isinstance(src, str) and src.strip():
                raw_path = src.strip()
                base_fpath = raw_path.split("|")[0]
                if os.path.exists(base_fpath):
                    if "|layername=" in raw_path:
                        tbl = raw_path.split("|layername=")[1].split("|")[0]
                        lyr = QgsVectorLayer(raw_path, tbl, "ogr")
                        if lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                            src_layers.append(lyr)
                    else:
                        try:
                            from .database import get_gpkg_polygon_layer_info
                            has_poly, tables, _ = get_gpkg_polygon_layer_info(base_fpath)
                        except Exception:
                            has_poly, tables = True, []

                        if tables:
                            for tbl in tables:
                                lyr = QgsVectorLayer(f"{base_fpath}|layername={tbl}", tbl, "ogr")
                                if lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                                    src_layers.append(lyr)
                        else:
                            lyr = QgsVectorLayer(base_fpath, os.path.basename(base_fpath), "ogr")
                            if lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                                src_layers.append(lyr)

            for lyr in src_layers:
                reproject = lyr.crs().isValid() and lyr.crs() != target_crs
                transform = None
                if reproject:
                    t_ctx = QgsProject.instance().transformContext() or QgsCoordinateTransformContext()
                    transform = QgsCoordinateTransform(lyr.crs(), target_crs, t_ctx)

                field_names = [f.name() for f in lyr.fields()]
                fnames_lower = {f.lower(): f for f in field_names}

                for feat in lyr.getFeatures():
                    if not feat.hasGeometry() or feat.geometry().isEmpty():
                        continue
                    geom = feat.geometry()
                    if reproject and transform:
                        try:
                            geom.transform(transform)
                        except Exception:
                            pass

                    attrs = {f: feat[f] for f in field_names}
                    gc = build_geocode(attrs, GPKG_EARF_FIELDS)

                    m_uuid = ""
                    for u_k in ("map_uuid", "uuid"):
                        if u_k in fnames_lower:
                            val = feat[fnames_lower[u_k]]
                            if val is not None and str(val).strip().lower() not in ("null", "nan", ""):
                                m_uuid = str(val).strip()
                                break

                    ean = ""
                    for e_k in ("new_ean", "ean", "ea", "code"):
                        if e_k in fnames_lower:
                            val = feat[fnames_lower[e_k]]
                            if val is not None and str(val).strip().lower() not in ("null", "nan", ""):
                                ean = to_whole_number_str(val).strip()
                                break

                    bgy = gc[:8] if len(gc) >= 8 else ""

                    f_hh = None
                    for h_k in ("hh_count", "hhcount", "hh"):
                        if h_k in fnames_lower:
                            val = feat[fnames_lower[h_k]]
                            if val is not None and str(val).strip().lower() not in ("null", "nan", ""):
                                f_hh = val
                                break

                    f_bldg = None
                    for b_k in ("bldg_count",):
                        if b_k in fnames_lower:
                            val = feat[fnames_lower[b_k]]
                            if val is not None and str(val).strip().lower() not in ("null", "nan", ""):
                                f_bldg = val
                                break

                    ean_norm = ean.zfill(6) if ean.isdigit() and len(ean) < 6 else ean

                    if m_uuid and m_uuid not in geom_by_uuid:
                        geom_by_uuid[m_uuid] = QgsGeometry(geom)
                        orig_attrs_by_uuid[m_uuid] = attrs
                    if m_uuid and m_uuid not in gpkg_counts_by_uuid and (f_hh is not None or f_bldg is not None):
                        gpkg_counts_by_uuid[m_uuid] = (f_hh, f_bldg)

                    if gc and gc not in geom_by_gc:
                        geom_by_gc[gc] = QgsGeometry(geom)
                        orig_attrs_by_gc[gc] = attrs
                    if gc and gc not in gpkg_counts_by_gc and (f_hh is not None or f_bldg is not None):
                        gpkg_counts_by_gc[gc] = (f_hh, f_bldg)

                    if bgy and ean:
                        k = (bgy, ean_norm)
                        if k not in geom_by_bgy_ean:
                            geom_by_bgy_ean[k] = QgsGeometry(geom)
                            orig_attrs_by_bgy_ean[k] = attrs
                        if k not in gpkg_counts_by_bgy_ean and (f_hh is not None or f_bldg is not None):
                            gpkg_counts_by_bgy_ean[k] = (f_hh, f_bldg)

    # 4. Construct output fields
    fields = QgsFields()
    for fname, ftype in MATCHED_GPKG_FIELD_DEFINITIONS:
        fields.append(QgsField(fname, ftype))

    # 5. Initialize GeoPackage Writer
    out_dir = os.path.dirname(os.path.abspath(out_gpkg_path))
    os.makedirs(out_dir, exist_ok=True)

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    transform_context = QgsProject.instance().transformContext() or QgsCoordinateTransformContext()

    writer = QgsVectorFileWriter.create(
        out_gpkg_path,
        fields,
        QgsWkbTypes.MultiPolygon,
        target_crs,
        transform_context,
        options,
    )

    if writer.hasError() != QgsVectorFileWriter.NoError:
        err_msg = writer.errorMessage()
        del writer
        raise RuntimeError(f"Could not create GeoPackage at {out_gpkg_path}: {err_msg}")

    # 6. Populate features
    total_records = len(records)
    features_with_geom = 0
    seen_map_uuids = set()

    for idx, r in enumerate(records):
        if progress_callback and idx % 200 == 0:
            pct = 30 + int((idx / max(total_records, 1)) * 65)
            progress_callback(pct, f"Exporting feature {idx + 1:,} of {total_records:,}...")

        # Strict requirement: Do not create EAs in the GeoPackage that do not belong to GPKG
        # or do not have valid geometry. Retain only physical GeoPackage EAs as they are.
        if r.get("in_gpkg") == 0 or r.get("has_geometry") == 0:
            continue

        gc = str(r.get("geocode") or "").strip()
        meta = gpkg_meta.get(gc, {})

        p_code = str(r.get("province_code") or "").strip()
        m_code = str(r.get("city_mun_code") or "").strip()
        b_code = str(r.get("barangay_code") or "").strip()
        if not (p_code and m_code and b_code):
            if len(gc) == 14:
                p_code = gc[:3]
                m_code = gc[3:5]
                b_code = gc[5:8]
            elif len(gc) == 16:
                p_code = gc[2:5]
                m_code = gc[5:7]
                b_code = gc[7:10]

        bgy_gc = f"{p_code.zfill(3)}{m_code.zfill(2)}{b_code.zfill(3)}" if p_code else (gc[:8] if len(gc) >= 8 else "")

        # Retain original EAN from GPKG as it is (do NOT overwrite with recoded_ean)
        orig_ean = to_whole_number_str(r.get("ean") or r.get("new_ean") or r.get("new_enumeration_area_code") or "").strip()
        if orig_ean.isdigit() and len(orig_ean) < 6:
            orig_ean = f"{int(orig_ean):03d}000" if len(orig_ean) <= 3 else f"{int(orig_ean):06d}"

        fallback_ean = to_whole_number_str(r.get("new_enumeration_area_code") or r.get("new_ean") or r.get("ean") or "").strip()
        if fallback_ean.isdigit() and len(fallback_ean) < 6:
            fallback_ean = f"{int(fallback_ean):03d}000" if len(fallback_ean) <= 3 else f"{int(fallback_ean):06d}"

        gpkg_ean = orig_ean or fallback_ean
        ea_gc = f"{bgy_gc}{gpkg_ean}" if (bgy_gc and gpkg_ean) else gc
        corr_gc = gc or ea_gc

        m_uuid = str(meta.get("map_uuid") or r.get("map_uuid") or "").strip()

        # Geometry resolution & original GPKG attribute lookup
        matched_geom = None
        orig_attrs: Dict[str, Any] = {}

        if m_uuid and m_uuid in geom_by_uuid:
            matched_geom = geom_by_uuid[m_uuid]
            orig_attrs = orig_attrs_by_uuid.get(m_uuid, {})
        elif gc and gc in geom_by_gc:
            matched_geom = geom_by_gc[gc]
            orig_attrs = orig_attrs_by_gc.get(gc, {})
        elif ea_gc and ea_gc in geom_by_gc:
            matched_geom = geom_by_gc[ea_gc]
            orig_attrs = orig_attrs_by_gc.get(ea_gc, {})
        elif (bgy_gc, orig_ean) in geom_by_bgy_ean:
            matched_geom = geom_by_bgy_ean[(bgy_gc, orig_ean)]
            orig_attrs = orig_attrs_by_bgy_ean.get((bgy_gc, orig_ean), {})
        elif (bgy_gc, fallback_ean) in geom_by_bgy_ean:
            matched_geom = geom_by_bgy_ean[(bgy_gc, fallback_ean)]
            orig_attrs = orig_attrs_by_bgy_ean.get((bgy_gc, fallback_ean), {})

        # Do NOT create EAs in the GPKG if no geometry exists
        if matched_geom is None or matched_geom.isEmpty():
            continue

        # Retain original values from input GPKG feature if available
        if orig_attrs:
            orig_u = str(orig_attrs.get("map_uuid") or orig_attrs.get("uuid") or "").strip()
            if orig_u:
                m_uuid = orig_u
            orig_g_ean = to_whole_number_str(orig_attrs.get("ean") or orig_attrs.get("new_ean") or orig_attrs.get("code") or "").strip()
            if orig_g_ean:
                if orig_g_ean.isdigit() and len(orig_g_ean) < 6:
                    orig_g_ean = f"{int(orig_g_ean):03d}000" if len(orig_g_ean) <= 3 else f"{int(orig_g_ean):06d}"
                gpkg_ean = orig_g_ean
            orig_ea_gc = str(orig_attrs.get("ea_geocode") or orig_attrs.get("geocode") or "").strip()
            if orig_ea_gc:
                ea_gc = orig_ea_gc

        if not m_uuid:
            m_uuid = str(uuid.uuid4())

        seen_map_uuids.add(m_uuid)

        r_code = str(orig_attrs.get("region_code") or r.get("region_code") or "").strip()
        if not r_code:
            r_code = prov_lookup.get(p_code, ("", ""))[1]

        reg_name = orig_attrs.get("region") or r.get("region") or meta.get("region") or reg_lookup.get(r_code, "")
        prov_name = orig_attrs.get("province") or r.get("province") or meta.get("province") or prov_lookup.get(p_code, ("", ""))[0]
        mun_name = orig_attrs.get("city_mun") or r.get("city_mun") or meta.get("city_mun") or mun_lookup.get(p_code + m_code, "")
        brgy_name = orig_attrs.get("barangay") or r.get("barangay") or meta.get("barangay") or r.get("geographic_name") or brgy_lookup.get(p_code + m_code + b_code, "")

        feat = QgsFeature(fields)
        feat.setAttribute("ea_geocode", ea_gc)
        feat.setAttribute("map_uuid", m_uuid)
        feat.setAttribute("bgy_geocode", str(orig_attrs.get("bgy_geocode") or bgy_gc))
        feat.setAttribute("province_code", p_code.zfill(3) if p_code else "")
        feat.setAttribute("city_mun_code", m_code.zfill(2) if m_code else "")
        feat.setAttribute("barangay_code", b_code.zfill(3) if b_code else "")
        feat.setAttribute("ean", gpkg_ean)
        feat.setAttribute("region_code", r_code.zfill(2) if r_code else "")
        feat.setAttribute("region", reg_name)
        feat.setAttribute("province", prov_name)
        feat.setAttribute("city_mun", mun_name)
        feat.setAttribute("barangay", brgy_name)

        # Name: Retain original from GPKG
        ea_name = str(orig_attrs.get("name") or meta.get("name") or r.get("name") or "").strip()
        if not ea_name or ea_name.upper().startswith("BARANGAY"):
            effective_ean = gpkg_ean or (ea_gc[-6:] if len(ea_gc) >= 6 else "")
            ea_name = f"EA {effective_ean}" if effective_ean else "EA"
        feat.setAttribute("name", ea_name)

        # Code: Retain original from GPKG
        feat.setAttribute("code", str(orig_attrs.get("code") or meta.get("code") or r.get("code") or "1004"))

        # Correspondence geocode
        feat.setAttribute("correspondence_geocode", str(orig_attrs.get("correspondence_geocode") or orig_attrs.get("geocode") or corr_gc or ea_gc))

        # Remarks: Retain original GPKG remarks
        feat.setAttribute("remarks", str(orig_attrs.get("remarks") or meta.get("remarks") or r.get("remarks") or r.get("earf_remarks") or ""))

        # Household and building counts: Retain original GPKG counts
        f_hh = orig_attrs.get("hhcount") or orig_attrs.get("hh_count")
        f_bldg = orig_attrs.get("bldgcount") or orig_attrs.get("bldg_count")

        hh = 0
        try:
            hh_src = f_hh if (f_hh is not None and str(f_hh).strip() != "") else (meta.get("hh_count") or meta.get("hhcount") or r.get("gpkg_hh_count") or r.get("hhcount") or 0)
            hh = int(round(float(hh_src)))
        except Exception:
            hh = 0
        feat.setAttribute("hhcount", hh)

        bldg = 0
        try:
            bldg_src = f_bldg if (f_bldg is not None and str(f_bldg).strip() != "") else (meta.get("bldg_count") or r.get("gpkg_bldg_count") or r.get("bldgcount") or 0)
            bldg = int(round(float(bldg_src)))
        except Exception:
            bldg = 0
        feat.setAttribute("bldgcount", bldg)

        feat.setAttribute("sy", str(orig_attrs.get("sy") or meta.get("sy") or r.get("source_year") or r.get("sy") or "2026"))

        feat.setGeometry(QgsGeometry(matched_geom))
        features_with_geom += 1
        writer.addFeature(feat)

    del writer

    if progress_callback:
        progress_callback(100, f"Export complete: {features_with_geom:,} GeoPackage features retained.")

    return features_with_geom, features_with_geom
