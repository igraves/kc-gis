# kc-gis

Pulls Jackson County, MO GIS data from public ArcGIS REST services, tracks how it changes over time, and makes maps from it.

- **Python (`kcgis`)** downloads layers to GeoJSON, records each fetch, checks the servers for changes, finds new layers, and renders static maps.
- **JavaScript (`web/`)** is a MapLibre viewer with layer toggles and feature popups for everything you've fetched.

## Data sources

| Source | REST root | Notes |
|---|---|---|
| [Jackson County, MO GIS](https://www.jacksongov.org/Government/Departments/Geographic-Information-Systems) | `https://jcgis.jacksongov.org/arcgis/rest/services` | Boundaries, districts, zoning, TIF/CID/TDD, parcels. [Parcel Viewer](https://jcgis.jacksongov.org/parcelviewer) |
| [Mid-America Regional Council (MARC)](https://www.marc.org/data-and-maps) | `https://gis2.marc2.org/arcgis/rest/services` | Regional census, parks, trails, flood zones, transit, facilities, forecasts |
| [City of Kansas City, MO](https://kcmo.maps.arcgis.com) | `https://services.arcgis.com/4o5uMWTHuOhUVJPd/arcgis/rest/services` | Blue River restoration areas, capital projects, stream setbacks, parcels (ArcGIS Online) |
| [U.S. Geological Survey](https://waterdata.usgs.gov) | `hydro.nationalmap.gov` (WBD), `api.waterdata.usgs.gov` (gauges) | Watershed boundaries; live stream gauge readings |

MARC covers a nine-county region. Its layers are limited to Jackson County either by a `where` filter (for example `CountyFIPS = '29095'`) or by `clip = true`, which keeps only features that intersect the county boundary. Clipping happens on the server.

The data is for reference only and is not survey accurate. Check each layer's license and description on its REST page before reusing it; some MARC services are restricted to specific programs.

## Blue River

The `blue_river` group (layers prefixed `br_`) brings together two efforts:

- **Jackson County's [Blue River Parkway Master Plan](https://www.makeyourdayhere.com/Home/Featured-Content-2/Blue-River-Parkway-Master-Planning)** (adopted Dec 5, 2022; 2,267 acres along 15 miles of the river). The plan is published only as PDFs, so the map uses the existing parkway, trails, and MetroGreen greenway (existing and planned) from MARC. The plan PDFs are linked in the viewer.
- **Kansas City, MO's Blue River work:** ARPA restoration areas, Public Works, Parks, and KC Water capital projects, brownfields, the city's Blue River Trail and half-mile corridor, stream setback zones, and parcels near the river.
- **Context:** the watershed (MARC), HUC12 sub-watersheds (USGS), river centerline, 2016 stream health scores, and active USGS gauges with their latest gage height and discharge. Gauges count as active if they reported in the past 30 days.

```sh
uv run kcgis fetch $(uv run kcgis list | awk '/^br_/{print $1}')
uv run kcgis fetch br_gauges          # refresh gauge readings
uv run kcgis map br_parkway br_restoration_areas br_cip_parks br_greenway br_parkway_trails br_river br_gauges \
  --extent br_parkway -o maps/blue_river_parkway.png
```

In the viewer, the Blue River section has a "Zoom to Blue River Watershed" button and a list of the plan documents.

The city layers appear to be working layers behind a staff web map ([Blue River Projects](https://experience.arcgis.com/experience/da4be96b50014965aeab1fe1406cc18e)), not a formal open-data release. Confirm with the city before republishing them. Private-parcel owner names and addresses, and project contact details, are deliberately not fetched (see `fields` in `sources.toml`).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Node 20+.

```sh
uv sync --extra maps        # Python deps (drop --extra maps if you don't need static maps)
cd web && npm install
```

## Usage

```sh
uv run kcgis list                     # registry layers + local fetch state
uv run kcgis fetch                    # fetch every non-large layer (~30 s, ~40 MB)
uv run kcgis fetch parks schools      # fetch specific layers
uv run kcgis fetch parcels            # large layers (~311k parcels) only when named
uv run kcgis status                   # compare remote counts/edit dates to the last fetch

uv run kcgis catalog marc --grep transit   # browse every layer on a server (* = already in registry)
uv run kcgis catalog jackson

uv run kcgis map parks trails_bikeways streams -o maps/parks.png
uv run kcgis map forecast_2050_tracts --column PctPopChange2020to2050 --title "Projected population change 2020–2050 (%)"
uv run kcgis map br_parkway br_parkway_trails --extent br_parkway   # frame to a layer instead of the county
```

Web viewer (reads `data/` directly):

```sh
cd web && npm run dev       # http://localhost:5173
npm run build               # static site in web/dist, including the data
```

## Adding a layer

Find it with `kcgis catalog`, then add an entry to `sources.toml`:

```toml
[layers.bridges]
source = "marc"
title = "Bridges"
url = "https://gis2.marc2.org/arcgis/rest/services/Transportation/Bridges/MapServer/0"
clip = true
color = "#555555"
```

Other options:

- `where`: an SQL filter.
- `fields`: the fields to fetch. Use it to leave out personal data.
- `clip`: `true` for the county boundary, or the name of another polygon layer, such as `"br_watershed"`.
- `group`: shows the layer under a themed heading defined in `[groups]`, which can have a zoom layer and linked documents.
- `large`: skips the layer unless it's named.
- `simplify`: server-side generalization in degrees, for very detailed polygons.
- `kind = "usgs_gauges"`: fetches active USGS stream gauges inside the `clip` layer.

## Tracking changes

Each fetch writes:

- `data/<layer>.geojson`: the features, in EPSG:4326
- `data/manifest.json`: the latest count, size, SHA-256, fetch time, and upstream last-edit date for each layer. The web viewer reads this file.
- `data/history.jsonl`: an append-only log of every fetch, including the previous count and whether the content changed

`kcgis status` checks for upstream changes without downloading. To run it on a schedule, use cron or CI to run `kcgis fetch` and keep `history.jsonl`.

## Layout

```
sources.toml          layer registry
src/kcgis/
  arcgis.py           REST client (paging, object-ID fallback, spatial filter)
  usgs.py             USGS Water Data OGC API (active gauges + latest readings)
  sources.py          registry loader
  fetch.py            fetch + manifest/history + status
  staticmap.py        static maps (geopandas/matplotlib)
  cli.py              `kcgis` command
web/                  Vite + MapLibre viewer
tests/                pytest suite (no network)
```
