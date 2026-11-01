"""Minimal ArcGIS REST client: layer metadata, counts, and paginated GeoJSON queries."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import requests

DEFAULT_TIMEOUT = 60
USER_AGENT = "kcgis/0.1 (Jackson County MO GIS tracker)"


class ArcGISError(RuntimeError):
    pass


class ArcGISClient:
    def __init__(self, session: requests.Session | None = None, retries: int = 3, timeout: int = DEFAULT_TIMEOUT):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.retries = retries
        self.timeout = timeout

    def _request(self, url: str, params: dict[str, Any], method: str = "GET") -> dict[str, Any]:
        params = {"f": "json", **params}
        for attempt in range(self.retries):
            try:
                if method == "POST":
                    resp = self.session.post(url, data=params, timeout=self.timeout)
                else:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == self.retries - 1:
                    raise ArcGISError(f"{url}: {exc}") from exc
                time.sleep(2**attempt)
                continue
            # ArcGIS reports most failures as HTTP 200 with an "error" body.
            if "error" in data:
                raise ArcGISError(f"{url}: {data['error']}")
            return data
        raise AssertionError("unreachable")

    def info(self, url: str) -> dict[str, Any]:
        """Service directory JSON for a server root, folder, service, or layer."""
        return self._request(url, {})

    def count(self, layer_url: str, where: str = "1=1", geometry: dict | None = None) -> int:
        params = {"where": where, "returnCountOnly": "true", **_geometry_params(geometry)}
        return self._request(f"{layer_url}/query", params, method="POST")["count"]

    def iter_features(
        self,
        layer_url: str,
        where: str = "1=1",
        geometry: dict | None = None,
        out_fields: str = "*",
        page_size: int | None = None,
        max_allowable_offset: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield GeoJSON features (EPSG:4326), paging with resultOffset or, if unsupported, objectIds."""
        layer = self.info(layer_url)
        page_size = page_size or layer.get("maxRecordCount") or 1000
        oid_field = layer.get("objectIdField") or next(
            (f["name"] for f in layer.get("fields") or [] if f.get("type") == "esriFieldTypeOID"), None
        )
        base = {
            "where": where,
            "outFields": out_fields,
            "outSR": 4326,
            "geometryPrecision": 6,  # ~10 cm; keeps files small
            "returnGeometry": "true",
            "f": "geojson",
            **_geometry_params(geometry),
        }
        if max_allowable_offset:
            base["maxAllowableOffset"] = max_allowable_offset  # server-side generalization, in degrees
        if not (layer.get("advancedQueryCapabilities") or {}).get("supportsPagination", False):
            yield from self._iter_by_object_ids(layer_url, base, where, geometry, min(page_size, 500))
            return
        offset = 0
        while True:
            params = {**base, "resultOffset": offset, "resultRecordCount": page_size}
            if oid_field:
                params["orderByFields"] = oid_field  # stable ordering across pages
            page = self._request(f"{layer_url}/query", params, method="POST")
            features = page.get("features", [])
            yield from features
            exceeded = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
            if not features or (not exceeded and len(features) < page_size):
                return
            offset += len(features)

    def _iter_by_object_ids(self, layer_url, base, where, geometry, page_size) -> Iterator[dict[str, Any]]:
        """Fallback for older services: list matching object IDs, then fetch them in chunks."""
        params = {"where": where, "returnIdsOnly": "true", **_geometry_params(geometry)}
        ids = sorted(self._request(f"{layer_url}/query", params, method="POST").get("objectIds") or [])
        for i in range(0, len(ids), page_size):
            chunk = ",".join(map(str, ids[i : i + page_size]))
            page = self._request(f"{layer_url}/query", {**base, "objectIds": chunk}, method="POST")
            yield from page.get("features", [])


def _geometry_params(geometry: dict | None) -> dict[str, Any]:
    """Spatial filter params for an esriJSON polygon in EPSG:4326."""
    if geometry is None:
        return {}
    return {
        "geometry": json.dumps(geometry),
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }


def geojson_to_esri_polygon(geom: dict[str, Any]) -> dict[str, Any]:
    """Convert a GeoJSON Polygon/MultiPolygon to an esriJSON polygon (rings flattened)."""
    if geom["type"] == "Polygon":
        rings = geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        rings = [ring for poly in geom["coordinates"] for ring in poly]
    else:
        raise ValueError(f"cannot clip to {geom['type']}")
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def walk_catalog(client: ArcGISClient, root: str) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield (service_path, service_type, layer) for every Map/Feature service layer under root."""
    root_info = client.info(root)
    services = list(root_info.get("services", []))
    for folder in root_info.get("folders", []):
        try:
            services += client.info(f"{root}/{folder}").get("services", [])
        except ArcGISError:
            continue  # secured folders answer "Token Required"
    seen = set()
    for svc in services:
        if svc["type"] not in ("MapServer", "FeatureServer") or svc["name"] in seen:
            continue
        seen.add(svc["name"])
        try:
            detail = client.info(f"{root}/{svc['name']}/{svc['type']}")
        except ArcGISError:
            continue
        for layer in detail.get("layers", []):
            if layer.get("geometryType"):  # skip group layers
                yield svc["name"], svc["type"], layer
