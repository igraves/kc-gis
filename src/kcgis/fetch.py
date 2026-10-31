"""Download registry layers to data/ and track how they change between fetches.

Outputs:
  data/<layer>.geojson   one FeatureCollection per layer (EPSG:4326)
  data/manifest.json     latest fetch metadata per layer (read by the web viewer)
  data/history.jsonl     append-only log of every fetch, for change tracking
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .arcgis import ArcGISClient, geojson_to_esri_polygon
from .sources import PROJECT_ROOT, Layer, Registry
from .usgs import fetch_gauges

DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_json(path: Path, obj: Any, **kwargs: Any) -> bytes:
    payload = json.dumps(obj, **kwargs).encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
    return payload


def _last_edit(info: dict[str, Any]) -> str | None:
    ms = (info.get("editingInfo") or {}).get("lastEditDate")
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


class Fetcher:
    def __init__(self, registry: Registry, data_dir: Path = DEFAULT_DATA_DIR, client: ArcGISClient | None = None):
        self.registry = registry
        self.data_dir = data_dir
        self.client = client or ArcGISClient()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._clip_geometries: dict[str, dict] = {}

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"

    @property
    def history_path(self) -> Path:
        return self.data_dir / "history.jsonl"

    def load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {"county": self.registry.county_name, "layers": {}}

    def clip_geometry(self, name: str | None = None) -> dict:
        """A polygon layer (default: the county boundary) as one esriJSON polygon, fetching it if needed."""
        name = name or self.registry.boundary_layer
        if name not in self._clip_geometries:
            path = self.data_dir / f"{name}.geojson"
            if not path.exists():
                self.fetch(self.registry.layers[name])
            features = json.loads(path.read_text())["features"]
            rings = [r for f in features for r in geojson_to_esri_polygon(f["geometry"])["rings"]]
            self._clip_geometries[name] = {"rings": rings, "spatialReference": {"wkid": 4326}}
        return self._clip_geometries[name]

    def _query_args(self, layer: Layer) -> dict[str, Any]:
        geometry = self.clip_geometry(layer.clip_layer) if layer.clip else None
        return {"where": layer.where, "geometry": geometry}

    def _download(self, layer: Layer, log) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Features plus layer metadata (geometry type, last edit) for any layer kind."""
        if layer.kind == "usgs_gauges":
            rings = self.clip_geometry(layer.clip_layer)["rings"]
            return fetch_gauges(rings, session=self.client.session), {"geometryType": "esriGeometryPoint"}
        info = self.client.info(layer.url)
        features = []
        query = self._query_args(layer)
        for feature in self.client.iter_features(
            layer.url, out_fields=layer.fields, max_allowable_offset=layer.simplify, **query
        ):
            features.append(feature)
            if len(features) % 10000 == 0:
                log(f"  {layer.name}: {len(features)} features…")
        return features, info

    def fetch(self, layer: Layer, log=print) -> dict[str, Any]:
        features, info = self._download(layer, log)
        collection = {"type": "FeatureCollection", "name": layer.name, "features": features}
        payload = _write_json(self.data_dir / f"{layer.name}.geojson", collection, separators=(",", ":"))

        manifest = self.load_manifest()
        previous = manifest["layers"].get(layer.name, {})
        entry = {
            **asdict(layer),
            "file": f"{layer.name}.geojson",
            "server": self.registry.servers[layer.source].title,
            "geometry_type": info.get("geometryType"),
            "fetched_at": _now(),
            "count": len(features),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source_last_edit": _last_edit(info),
        }
        entry["changed"] = previous.get("sha256") != entry["sha256"]
        manifest["layers"][layer.name] = entry
        manifest["groups"] = {k: asdict(g) for k, g in self.registry.groups.items()}
        manifest["updated_at"] = entry["fetched_at"]
        _write_json(self.manifest_path, manifest, indent=2)

        with self.history_path.open("a") as fh:
            record = {k: entry[k] for k in ("fetched_at", "count", "sha256", "source_last_edit", "changed")}
            fh.write(json.dumps({"layer": layer.name, "previous_count": previous.get("count"), **record}) + "\n")
        return entry

    def status(self, layer: Layer) -> dict[str, Any]:
        """Compare the remote layer against the last fetch without downloading features."""
        local = self.load_manifest()["layers"].get(layer.name)
        if layer.kind == "usgs_gauges":
            return {"layer": layer.name, "state": "live data", "local_count": local and local["count"],
                    "remote_count": None, "fetched_at": local and local["fetched_at"], "source_last_edit": None}
        info = self.client.info(layer.url)
        remote_count = self.client.count(layer.url, **self._query_args(layer))
        remote_edit = _last_edit(info)
        if local is None:
            state = "not fetched"
        elif remote_count != local["count"] or (remote_edit and remote_edit != local.get("source_last_edit")):
            state = "changed upstream"
        else:
            state = "up to date"
        return {
            "layer": layer.name,
            "state": state,
            "local_count": local and local["count"],
            "remote_count": remote_count,
            "fetched_at": local and local["fetched_at"],
            "source_last_edit": remote_edit,
        }
