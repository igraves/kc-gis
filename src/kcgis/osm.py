"""OpenStreetMap ways via the Overpass API, with per-feature provenance from the last edit's changeset.

OSM data is © OpenStreetMap contributors, available under the Open Database License (ODbL).
"""

from __future__ import annotations

import time
from typing import Any

import requests

from .arcgis import USER_AGENT
from .usgs import point_in_rings

OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_API = "https://api.openstreetmap.org/api/0.6"
BUSY = {429, 502, 503, 504}
KEEP_TAGS = (
    "name", "highway", "surface", "mtb:scale", "mtb:scale:imba", "oneway", "bicycle", "foot", "horse",
    "width", "trail_visibility", "operator", "source",
)


class OSMError(RuntimeError):
    pass


def _get_json(session: requests.Session, method: str, url: str, retries: int = 3, **kwargs: Any) -> dict[str, Any]:
    """JSON from url, backing off when a busy Overpass server answers 429 or 5xx."""
    for attempt in range(retries + 1):
        try:
            resp = session.request(method, url, timeout=180, **kwargs)
            if resp.status_code in BUSY and attempt < retries:
                time.sleep(15 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise OSMError(f"{url}: {exc}") from exc
    raise AssertionError("unreachable")


def changeset_tags(session: requests.Session, changeset: int) -> dict[str, str]:
    data = _get_json(session, "GET", f"{OSM_API}/changeset/{changeset}.json")
    return (data.get("changeset") or (data.get("elements") or [{}])[0]).get("tags", {})


def fetch_ways(
    selector: str, rings: list[list[list[float]]], session: requests.Session | None = None
) -> list[dict[str, Any]]:
    """Ways matching an Overpass tag selector (e.g. '["highway"="path"]') with any vertex inside the rings.

    Each feature carries its last edit (user, time, changeset) and that changeset's source/imagery tags.
    """
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    xs = [pt[0] for ring in rings for pt in ring]
    ys = [pt[1] for ring in rings for pt in ring]
    query = f"[out:json][timeout:120];way{selector}({min(ys)},{min(xs)},{max(ys)},{max(xs)});out geom tags meta;"
    elements = _get_json(session, "POST", OVERPASS, data={"data": query})["elements"]

    ways = [
        w for w in elements
        if w["type"] == "way" and any(point_in_rings(p["lon"], p["lat"], rings) for p in w.get("geometry", []))
    ]
    sources = {cs: changeset_tags(session, cs) for cs in sorted({w["changeset"] for w in ways})}

    features = []
    for w in ways:
        cs = sources[w["changeset"]]
        features.append({
            "type": "Feature",
            "id": f"way/{w['id']}",
            "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in w["geometry"]]},
            "properties": {
                **{k: w["tags"][k] for k in KEEP_TAGS if k in w["tags"]},
                "osm_id": w["id"],
                "last_edited": w["timestamp"],
                "last_editor": w["user"],
                "changeset": w["changeset"],
                "changeset_comment": cs.get("comment"),
                "changeset_source": cs.get("source"),
                "changeset_imagery": cs.get("imagery_used"),
                "url": f"https://www.openstreetmap.org/way/{w['id']}",
            },
        })
    return features
