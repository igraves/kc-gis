"""USGS Water Data OGC API: active stream gauges with their latest reading."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from .arcgis import USER_AGENT

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
GAGE_HEIGHT = "00065"
DISCHARGE = "00060"
ACTIVE_WINDOW = timedelta(days=30)


class USGSError(RuntimeError):
    pass


def iter_items(session: requests.Session, url: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield features from an OGC API collection, following rel=next links."""
    next_url, next_params = url, {"f": "json", "limit": 1000, **params}
    while next_url:
        try:
            resp = session.get(next_url, params=next_params, timeout=60)
            resp.raise_for_status()
            page = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise USGSError(f"{next_url}: {exc}") from exc
        yield from page.get("features", [])
        next_url = next((l["href"] for l in page.get("links", []) if l.get("rel") == "next"), None)
        next_params = None  # the next link carries the full query


def point_in_rings(x: float, y: float, rings: list[list[list[float]]]) -> bool:
    """Even-odd ray casting across all rings, so holes are excluded."""
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def fetch_gauges(rings: list[list[list[float]]], session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Active stream gauges inside a polygon (lon/lat rings), with the latest gage height and discharge.

    A site counts as active if a continuous gage-height or discharge series reported within ACTIVE_WINDOW.
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    xs = [pt[0] for ring in rings for pt in ring]
    ys = [pt[1] for ring in rings for pt in ring]
    bbox = f"{min(xs)},{min(ys)},{max(xs)},{max(ys)}"

    sites = {
        f["id"]: f
        for f in iter_items(session, f"{API}/monitoring-locations/items", {"bbox": bbox, "site_type_code": "ST"})
        if point_in_rings(*f["geometry"]["coordinates"][:2], rings)
    }
    latest: dict[str, dict[str, Any]] = {}
    for code in (GAGE_HEIGHT, DISCHARGE):
        for f in iter_items(session, f"{API}/latest-continuous/items", {"bbox": bbox, "parameter_code": code}):
            p = f["properties"]
            if p["monitoring_location_id"] in sites:
                latest.setdefault(p["monitoring_location_id"], {})[code] = p

    cutoff = datetime.now(UTC) - ACTIVE_WINDOW
    features = []
    for site_id, readings in sorted(latest.items()):
        site = sites[site_id]["properties"]
        gage, flow = readings.get(GAGE_HEIGHT, {}), readings.get(DISCHARGE, {})
        times = [datetime.fromisoformat(r["time"]) for r in (gage, flow) if r.get("time")]
        if not times or max(times) < cutoff:
            continue
        number = site["monitoring_location_number"]
        features.append({
            "type": "Feature",
            "id": site_id,
            "geometry": sites[site_id]["geometry"],
            "properties": {
                "site_number": number,
                "name": site["monitoring_location_name"],
                "huc12": site["hydrologic_unit_code"],
                "drainage_area_sq_mi": site.get("drainage_area"),
                "gage_height_ft": _num(gage.get("value")),
                "discharge_cfs": _num(flow.get("value")),
                "reading_time": gage.get("time") or flow.get("time"),
                "approval_status": gage.get("approval_status") or flow.get("approval_status"),
                "url": f"https://waterdata.usgs.gov/monitoring-location/{number}/",
            },
        })
    return features


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
