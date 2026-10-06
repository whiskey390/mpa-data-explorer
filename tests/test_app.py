import httpx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from app import main

SQUARE = {"type": "Polygon", "coordinates": [[[-9, 38], [-8, 38], [-8, 39], [-9, 39], [-9, 38]]]}


def fake_upstream(request: httpx.Request):
    url = str(request.url)
    q = dict(request.url.params)
    if "FeatureServer" in url:
        if q.get("f") == "geojson":
            if "PTCON0010" in q["where"]:
                return httpx.Response(200, json={"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": {"sitecode": "PTCON0010", "sitename": "Test Site"}, "geometry": SQUARE}]})
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        return httpx.Response(200, json={"features": [
            {"attributes": {"sitecode": "PTCON0010", "sitename": "Test Site", "sitetype": "B", "area_km2": 10.0}}]})
    return httpx.Response(404)


@pytest.fixture()
def api():
    main._cache.clear()
    main._client = httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream))
    with TestClient(main.app) as c:
        yield c
    main._client = None


def test_area_list(api):
    r = api.get("/api/areas")
    assert r.json() == [{"code": "PTCON0010", "name": "Test Site", "type": "B", "area_km2": 10.0}]


def test_bad_code_rejected(api):
    assert api.get("/api/areas/x'%20OR%201=1--/data").status_code in (400, 404)


def test_query_wkt_shrinks_and_covers():
    from shapely.geometry import Point
    import math
    pts = [(math.cos(t / 500 * 6.2832) * 0.5, math.sin(t / 500 * 6.2832) * 0.5) for t in range(500)]
    from shapely.geometry import Polygon
    from shapely import wkt
    poly = Polygon(pts)
    out = main.query_wkt(poly, limit=2000)
    assert len(out) < 6000
    assert wkt.loads(out).buffer(0).contains(Point(0, 0))
