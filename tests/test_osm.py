from kcgis import osm
from kcgis.osm import fetch_ways

SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.headers = {}
        self.calls = []

    def request(self, method, url, timeout=None, data=None):
        self.calls.append((method, url, data))
        return self.handler(method, url, data)


def way(id_, coords, changeset, **tags):
    return {"type": "way", "id": id_, "changeset": changeset, "user": "mapper", "timestamp": "2026-01-02T03:04:05Z",
            "tags": tags, "geometry": [{"lon": x, "lat": y} for x, y in coords]}


def test_fetch_ways_filters_to_area_and_adds_provenance(monkeypatch):
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    busy = [True]  # one 504 before the real answer

    def handler(method, url, data):
        if url == osm.OVERPASS:
            if busy:
                busy.pop()
                return FakeResponse({}, status_code=504)  # first try is busy, so it retries
            assert "(0,0,10,10)" in data["data"]  # south,west,north,east
            return FakeResponse({"elements": [
                way(1, [(1, 1), (2, 2)], 100, name="Highline", **{"mtb:scale": "3", "fixme": "x"}),
                way(2, [(20, 20), (21, 21)], 200, name="Outside"),
            ]})
        assert url.endswith("/changeset/100.json")  # only changesets of kept ways are looked up
        return FakeResponse({"changeset": {"tags": {"source": "local knowledge", "imagery_used": "Bing", "comment": "MTB"}}})

    session = FakeSession(handler)
    features = fetch_ways('["highway"="path"]', [SQUARE], session=session)
    assert [f["id"] for f in features] == ["way/1"]
    props = features[0]["properties"]
    assert props["name"] == "Highline" and props["mtb:scale"] == "3" and "fixme" not in props
    assert props["last_editor"] == "mapper" and props["changeset"] == 100
    assert props["changeset_source"] == "local knowledge" and props["changeset_imagery"] == "Bing"
    assert props["url"] == "https://www.openstreetmap.org/way/1"
    assert features[0]["geometry"] == {"type": "LineString", "coordinates": [[1, 1], [2, 2]]}
    assert session.headers["User-Agent"].startswith("kcgis/")
