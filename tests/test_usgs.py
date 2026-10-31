from datetime import UTC, datetime, timedelta

from kcgis.usgs import fetch_gauges, point_in_rings

SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


def test_point_in_rings_respects_holes():
    assert point_in_rings(1, 1, [SQUARE])
    assert not point_in_rings(11, 1, [SQUARE])
    assert not point_in_rings(5, 5, [SQUARE, HOLE])


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        return FakeResponse(self.handler(url, params))


def site(number, x, y):
    return {
        "id": f"USGS-{number}",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {"monitoring_location_number": number, "monitoring_location_name": f"Site {number}",
                       "hydrologic_unit_code": "103001010106", "drainage_area": 1.0},
    }


def reading(number, code, value, time):
    return {"properties": {"monitoring_location_id": f"USGS-{number}", "parameter_code": code,
                           "value": value, "time": time.isoformat(), "approval_status": "Provisional"}}


def test_fetch_gauges_keeps_active_sites_inside_area():
    now = datetime.now(UTC)
    stale = now - timedelta(days=400)

    def handler(url, params):
        # Sites come back over two pages; "out" sits in the polygon's hole.
        if url.endswith("monitoring-locations/items"):
            return {"features": [site("1", 1, 1), site("2", 2, 2)], "links": [{"rel": "next", "href": "page2"}]}
        if url == "page2":
            assert params is None  # the next link carries the full query
            return {"features": [site("out", 5, 5)], "links": []}
        code = params["parameter_code"]
        return {"features": [
            reading("1", code, "3.5", now),
            reading("2", code, "1.0", stale),
            reading("out", code, "9.9", now),
        ]}

    features = fetch_gauges([SQUARE, HOLE], session=FakeSession(handler))
    assert [f["properties"]["site_number"] for f in features] == ["1"]
    props = features[0]["properties"]
    assert props["gage_height_ft"] == 3.5 and props["discharge_cfs"] == 3.5
    assert props["url"] == "https://waterdata.usgs.gov/monitoring-location/1/"
