from kcgis.arcgis import ArcGISClient, ArcGISError, geojson_to_esri_polygon


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    """Routes requests to a handler and records them."""

    def __init__(self, handler):
        self.handler = handler
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.handler(url, params))

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        return FakeResponse(self.handler(url, data))


def feature(i):
    return {"type": "Feature", "id": i, "geometry": None, "properties": {"OBJECTID": i}}


def test_paginates_with_result_offset():
    total = 5

    def handler(url, params):
        if not url.endswith("/query"):
            return {"maxRecordCount": 2, "objectIdField": "OBJECTID",
                    "advancedQueryCapabilities": {"supportsPagination": True}}
        start = params["resultOffset"]
        ids = range(start, min(start + params["resultRecordCount"], total))
        return {"features": [feature(i) for i in ids], "exceededTransferLimit": start + 2 < total}

    session = FakeSession(handler)
    features = list(ArcGISClient(session=session).iter_features("https://x/MapServer/0"))
    assert [f["id"] for f in features] == [0, 1, 2, 3, 4]
    query = [p for u, p in session.calls if u.endswith("/query")]
    assert [p["resultOffset"] for p in query] == [0, 2, 4]
    assert all(p["outSR"] == 4326 and p["orderByFields"] == "OBJECTID" for p in query)


def test_falls_back_to_object_ids_without_pagination():
    def handler(url, params):
        if not url.endswith("/query"):
            return {"maxRecordCount": 2, "advancedQueryCapabilities": {"supportsPagination": False}}
        if params.get("returnIdsOnly"):
            return {"objectIds": [9, 3, 7]}
        return {"features": [feature(int(i)) for i in params["objectIds"].split(",")]}

    session = FakeSession(handler)
    features = list(ArcGISClient(session=session).iter_features("https://x/MapServer/0"))
    assert [f["id"] for f in features] == [3, 7, 9]
    chunks = [p["objectIds"] for u, p in session.calls if u.endswith("/query") and "objectIds" in p]
    assert chunks == ["3,7", "9"]


def test_error_body_raises():
    session = FakeSession(lambda url, params: {"error": {"code": 400, "message": "bad"}})
    client = ArcGISClient(session=session, retries=1)
    try:
        client.info("https://x/MapServer/0")
    except ArcGISError as exc:
        assert "bad" in str(exc)
    else:
        raise AssertionError("expected ArcGISError")


def test_geojson_to_esri_polygon_flattens_multipolygon():
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    esri = geojson_to_esri_polygon({"type": "MultiPolygon", "coordinates": [[ring], [ring]]})
    assert esri == {"rings": [ring, ring], "spatialReference": {"wkid": 4326}}
