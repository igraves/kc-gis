"""Build maps/blue_river_rfp.html: the Blue River Road surplus-parcel RFP parcels with nearby trails,
floodplain, stream setbacks and city projects, as one self-contained page.

Needs the blue_river group (including br_osm_trails) plus trails_bikeways, streams, flood_zones and parks in data/ (`kcgis fetch`).
Run from the repo root:  uv run --extra maps python scripts/blue_river_rfp/build.py
"""

import json
import warnings
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import box

from kcgis.arcgis import ArcGISClient

warnings.filterwarnings("ignore")
HERE = Path(__file__).parent
DATA = Path("data")
OUT = Path("maps/blue_river_rfp.html")
UTM = 32615
PARCELS_URL = "https://jcgis.jacksongov.org/arcgis/rest/services/ParcelViewer/ParcelsAscendRelate/FeatureServer/1"
ROADS_URL = "https://gis2.marc2.org/arcgis/rest/services/Transportation/FunctionalClassification/MapServer/1"
MAPLIBRE_CSS = "https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.min.css"

# Parcels named in the RFP, lettered by size for the map.
labels = {"46-830-01-01-01-0-00-000": "A", "46-810-02-03-00-0-00-000": "B", "46-810-02-02-00-0-00-000": "C"}

client = ArcGISClient()
where = "Name IN ({})".format(",".join(f"'{p}'" for p in labels))
parcels = gpd.GeoDataFrame.from_features(list(client.iter_features(PARCELS_URL, where=where, out_fields="Name")), crs=4326)
assert set(parcels.Name) == set(labels), f"parcels not found: {set(labels) - set(parcels.Name)}"
pm = parcels.to_crs(UTM)
site = pm.union_all()
area_m = site.buffer(900).envelope
area = gpd.GeoSeries([area_m], crs=UTM).to_crs(4326).iloc[0]
near = site.buffer(1500)
w, s, e, n = area.bounds


def load(name):
    g = gpd.read_file(DATA / f"{name}.geojson")
    g = g[g.intersects(area)].copy()
    g["geometry"] = g.geometry.make_valid()
    return g


def clip(g, cols):
    g = g[g.intersects(area)].copy()
    g = gpd.clip(g, area)
    g = g[~g.geometry.is_empty]
    return g[[c for c in cols if c in g.columns] + ["geometry"]]


def fc(g):
    return json.loads(g.to_json(drop_id=True, default=str))  # dates read as Timestamps


setbacks = load("br_stream_setbacks")
flood = load("flood_zones")
out = {}

# RFP parcels with computed metrics
trails_all = load("trails_bikeways").to_crs(UTM)
osm_trails = load("br_osm_trails").to_crs(UTM)
fz = flood.to_crs(UTM)
sfha = fz[fz.SFHA_TF == "T"].union_all()
sb = setbacks.to_crs(UTM)
slope = sb[sb.ZONE.str.startswith("Approximate")].union_all()
sb_all = sb.union_all()
river = load("br_river").to_crs(UTM).union_all()
rows = []
for i, r in pm.iterrows():
    g = r.geometry
    near_trails = trails_all[trails_all.distance(g) < 150].RouteName.dropna().unique().tolist()
    on_site = osm_trails[osm_trails.intersects(g)]
    rows.append({
        "label": labels[r.Name],
        "parcel": r.Name,
        "acres": round(g.area / 4046.856, 1),
        "sfha_pct": round(sfha.intersection(g).area / g.area * 100),
        "slope_pct": round(slope.intersection(g).area / g.area * 100),
        "setback_pct": round(sb_all.intersection(g).area / g.area * 100),
        "river_m": round(river.distance(g)),
        "trail_m": round(trails_all.distance(g).min()),
        "trails": sorted(near_trails),
        "osm_trail_mi": round(on_site.intersection(g).length.sum() / 1609.34, 1),
        "osm_trails": sorted(on_site.name.dropna().unique()),
    })
p = parcels.copy()
for k in ("label", "acres", "sfha_pct", "slope_pct", "setback_pct", "river_m", "trail_m", "osm_trail_mi"):
    p[k] = [row[k] for row in rows]
out["rfp"] = fc(p[["Name", "label", "acres", "sfha_pct", "slope_pct", "setback_pct", "river_m", "trail_m", "osm_trail_mi", "geometry"]])
out["rfp_rows"] = sorted(rows, key=lambda r: r["label"])
out["rfp_labels"] = [
    {"label": r["label"], "lnglat": list(gpd.GeoSeries([g], crs=UTM).to_crs(4326).iloc[0].representative_point().coords[0])}
    for r, g in zip(rows, pm.geometry)
]

out["parkway"] = fc(clip(load("br_parkway"), ["Name", "AgencyName", "Acres"]))
out["parks"] = fc(clip(load("parks")[lambda d: d.Name != "Blue River Parkway"], ["Name", "AgencyName", "Acres"]))
fl = clip(flood, ["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"])
fl["kind"] = fl.apply(lambda r: "floodway" if r.ZONE_SUBTY == "FLOODWAY" else "sfha" if r.SFHA_TF == "T"
                      else "x500" if isinstance(r.ZONE_SUBTY, str) and "0.2" in r.ZONE_SUBTY else "other", axis=1)
out["flood"] = fc(fl[fl.kind != "other"])
out["setbacks"] = fc(clip(setbacks, ["ZONE"]))
out["streams"] = fc(clip(load("streams"), ["StreamName"]))
out["trails"] = fc(clip(load("trails_bikeways"), ["RouteName", "Status", "FacilityType", "Surface", "Authority"]))
out["osm_trails"] = fc(clip(load("br_osm_trails"), [
    "name", "highway", "surface", "mtb:scale", "oneway", "last_edited", "last_editor",
    "changeset_source", "changeset_imagery", "url"]))
out["greenway"] = fc(clip(load("br_greenway"), ["Name", "Phase", "Type"]))
out["corridor"] = fc(clip(load("br_corridor_buffer"), []))

cip = []
for name, dept in [("br_cip_public_works", "Public Works"), ("br_cip_parks", "Parks & Recreation"), ("br_cip_kc_water", "KC Water")]:
    g = load(name)
    g = g[g.to_crs(UTM).intersects(near) & g.PROJECTNAME.notna()].copy()
    g["dept"] = dept
    cip.append(g)
cipg = gpd.GeoDataFrame(pd.concat(cip), crs=4326)
cipg["dist_m"] = cipg.to_crs(UTM).distance(site).round().astype(int)
cipg = cipg.drop_duplicates("PROJECTNAME").sort_values("dist_m")
cip_cols = ["PROJECTNAME", "PROJECTSTATUS", "CURRENTPHASE", "dept", "dist_m"]
cipg["geometry"] = cipg.geometry.apply(lambda g: g if g.geom_type.endswith("Point") else g.representative_point())
out["cip"] = fc(cipg[cip_cols + ["geometry"]])

# Live pulls: neighboring parcel outlines (IDs only) and major roads
ring = [list(c) for c in box(w, s, e, n).exterior.coords]
nb = list(client.iter_features(
    PARCELS_URL,
    geometry={"rings": [ring], "spatialReference": {"wkid": 4326}}, out_fields="Name"))
out["neighbors"] = {"type": "FeatureCollection", "features": [f for f in nb if f["properties"]["Name"] not in labels]}
roads = list(client.iter_features(
    ROADS_URL,
    geometry={"rings": [ring], "spatialReference": {"wkid": 4326}}, out_fields="LABEL,FCDescription"))
out["roads"] = fc(clip(gpd.GeoDataFrame.from_features(roads, crs=4326), ["LABEL", "FCDescription"]))

out["bounds"] = [w, s, e, n]
out["site_bounds"] = list(parcels.total_bounds)

bundle = json.dumps(out, separators=(",", ":")).replace("</", "<\\/")
html = (HERE / "template.html").read_text()
html = (html.replace("__MAPLIBRE_CSS__", requests.get(MAPLIBRE_CSS, timeout=60).text)
        .replace("__BUNDLE__", bundle)
        .replace("__FETCHED__", date.today().strftime("%B %-d, %Y")))
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(html)
print(f"wrote {OUT} ({len(html) / 1e6:.1f} MB)")
for r in out["rfp_rows"]:
    print(f"  {r['label']} {r['parcel']} {r['acres']} ac, floodplain {r['sfha_pct']}%, MARC trail {r['trail_m']} m, OSM trails on site {r['osm_trail_mi']} mi")
