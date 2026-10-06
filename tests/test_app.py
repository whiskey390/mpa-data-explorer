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


def obis_upstream(request: httpx.Request):
    q = dict(request.url.params)
    if request.url.path.endswith("/node"):
        return httpx.Response(200, json={"results": [{"id": "n1", "name": "OBIS Portugal"}, {"id": "n2", "name": "OBIS UK"}]})
    if request.url.path.endswith("/occurrence"):
        def rec(i, lon, lat, **kw):
            return {"id": f"r{i}", "decimalLongitude": lon, "decimalLatitude": lat, "scientificName": "Sp a",
                    "phylum": "Chordata", "class": "Aves", "date_year": 2010, "node_id": ["n1"],
                    "institutionCode": "IPMA", **kw}
        if q.get("after") is None:
            return httpx.Response(200, json={"total": 5, "results": [
                rec(1, -8.5, 38.5), rec(2, -8.4, 38.4, date_year=2012, node_id=["n2"], institutionCode="MBA"),
                rec(3, -7.0, 38.5)]})  # outside the square: filtered by the exact test
        return httpx.Response(200, json={"total": 5, "results": [
            rec(4, -8.6, 38.6, phylum=None, date_year=None), rec(5, -8.3, 38.3, date_year=2010)]})
    return fake_upstream(request)


def test_data_stats_and_csv():
    main._cache.clear()
    main._client = httpx.AsyncClient(transport=httpx.MockTransport(obis_upstream))
    main.PAGE = 3
    try:
        with TestClient(main.app) as c:
            d = c.get("/api/areas/PTCON0010/data").json()
            s = d["stats"]
            assert s["records"] == 4 and d["fetched"] == 5 and not d["truncated"]
            assert s["by_year"] == [[2010, 2], [2011, 0], [2012, 1]]  # gap year shown as zero
            assert s["no_date"] == 1
            assert dict(s["by_phylum"]) == {"Chordata": 3, "(unassigned)": 1}
            assert dict(s["by_node"]) == {"OBIS Portugal": 3, "OBIS UK": 1}
            assert s["own_node"]["share"] == 0.75
            assert dict(s["by_institution"]) == {"IPMA": 3, "MBA": 1}
            text = c.get("/api/areas/PTCON0010/records.csv").text.splitlines()
            assert text[0].startswith("id,name,phylum") and len(text) == 5
            assert c.get("/api/areas/NOPE00/data").status_code == 404
    finally:
        main.PAGE = 5000
        main._client = None


def test_truncation_flag():
    main._cache.clear()
    main._client = httpx.AsyncClient(transport=httpx.MockTransport(obis_upstream))
    main.PAGE, main.MAX_RECORDS = 3, 2
    try:
        with TestClient(main.app) as c:
            d = c.get("/api/areas/PTCON0010/data").json()
            assert d["truncated"] and len(d["rows"]) == 2
    finally:
        main.PAGE, main.MAX_RECORDS = 5000, 20000
        main._client = None
