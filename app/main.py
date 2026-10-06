"""MPA Data Explorer: a small FastAPI service in front of two open APIs.

* Protected-area boundaries: EEA Natura 2000 ArcGIS feature service (CC BY 4.0).
* Occurrences and node names: OBIS API v3 (api.obis.org).
Everything is fetched live, cached in memory, and configured by environment variables.
"""
import asyncio
import csv
import io
import os
import re
import time
from collections import Counter

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from shapely import to_wkt
from shapely.geometry import Point, shape
from shapely.prepared import prep

OBIS = os.getenv("OBIS_API", "https://api.obis.org/v3")
MPA_URL = os.getenv(
    "MPA_QUERY_URL",
    "https://nest.discomap.eea.europa.eu/arcgis/rest/services/Hosted/Natura2000/FeatureServer/0/query",
)
MPA_WHERE = os.getenv("MPA_WHERE", "ms='PT' AND is_marine='True'")
COUNTRY = os.getenv("COUNTRY_LABEL", "Portugal")
TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
NATIONAL_NODE = os.getenv("NATIONAL_NODE", "portugal").lower()  # substring of the national OBIS node name
MAX_RECORDS = int(os.getenv("MAX_RECORDS", "20000"))  # records kept per area (map, panel and CSV)
PAGE = 5000
FIELDS = "id,decimalLatitude,decimalLongitude,scientificName,phylum,class,date_year,node_id,dataset_id,datasetName,institutionCode"

app = FastAPI(title="MPA Data Explorer")
app.add_middleware(GZipMiddleware, minimum_size=1000)
_client = None  # tests replace this with a mocked client
_cache = {}


def client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=60, headers={"User-Agent": "mpa-data-explorer/0.1 (open-source demo)"}
        )
    return _client


async def cached(key, make):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    value = await make()
    _cache[key] = (time.time(), value)
    return value


async def get_json(url, params):
    last = None
    for attempt in range(3):
        try:
            r = await client().get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "error" in data:  # ArcGIS reports errors with HTTP 200
                raise RuntimeError(str(data["error"]))
            return data
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            last = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise HTTPException(502, f"Upstream request failed: {url}: {last}")


# ---------- protected areas ----------

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")  # site codes are interpolated into a query


async def list_areas():
    async def make():
        data = await get_json(MPA_URL, {
            "where": MPA_WHERE, "outFields": "sitecode,sitename,sitetype,area_km2",
            "returnGeometry": "false", "orderByFields": "sitename", "resultRecordCount": 2000, "f": "json",
        })
        return [
            {"code": a["sitecode"], "name": a["sitename"], "type": a.get("sitetype"), "area_km2": a.get("area_km2")}
            for a in (f["attributes"] for f in data.get("features", []))
        ]
    return await cached("areas", make)


async def boundary(code):
    """Return the area as a GeoJSON feature, lightly simplified (about 50 m) for display."""
    if not CODE_RE.match(code):
        raise HTTPException(400, "Invalid site code")

    async def make():
        data = await get_json(MPA_URL, {
            "where": f"sitecode='{code}' AND ({MPA_WHERE})", "outFields": "sitecode,sitename,sitetype,area_km2",
            "outSR": 4326, "geometryPrecision": 5, "maxAllowableOffset": 0.0005, "f": "geojson",
        })
        feats = data.get("features", [])
        if not feats:
            raise HTTPException(404, f"No protected area {code}")
        return feats[0]
    return await cached(("boundary", code), make)


def query_wkt(geom, limit=6000):
    """WKT for the OBIS `geometry` parameter: buffered, then simplified until it fits a URL."""
    padded = geom.buffer(0.001)  # the exact boundary test happens afterwards, so only over-cover here
    out = to_wkt(padded, rounding_precision=4, trim=True)
    for tol in (0.001, 0.003, 0.01, 0.03):
        if len(out) <= limit:
            break
        out = to_wkt(padded.simplify(tol), rounding_precision=4, trim=True)
    return out


# ---------- OBIS records and statistics ----------


async def node_names():
    async def make():
        data = await get_json(f"{OBIS}/node", {"size": 200})
        return {n["id"]: n["name"] for n in data.get("results", [])}
    return await cached("nodes", make)


async def fetch_records(geom):
    """Page through OBIS occurrences for the area; keep those truly inside the boundary."""
    names = await node_names()
    inside = prep(geom)
    base = {"geometry": query_wkt(geom), "size": PAGE, "fields": FIELDS}
    rows, after, fetched, total = [], None, 0, None
    while True:
        data = await get_json(f"{OBIS}/occurrence", {**base, **({"after": after} if after else {})})
        total = data.get("total", 0) if total is None else total
        results = data.get("results", [])
        if not results:
            break
        for r in results:
            lat, lon = r.get("decimalLatitude"), r.get("decimalLongitude")
            if lat is None or lon is None or not inside.covers(Point(lon, lat)):
                continue
            nodes = [names.get(n, n) for n in r.get("node_id") or []]
            rows.append({
                "id": r["id"], "lat": lat, "lon": lon, "name": r.get("scientificName"),
                "phylum": r.get("phylum") or "(unassigned)", "class": r.get("class") or "(unassigned)",
                "year": r.get("date_year"), "node": "; ".join(nodes) or "(none)",
                "dataset": r.get("datasetName") or r.get("dataset_id"),
                "institution": r.get("institutionCode") or "(not given)",
            })
        after, fetched = results[-1]["id"], fetched + len(results)
        if len(rows) >= MAX_RECORDS or fetched >= total:
            break
    return {"rows": rows[:MAX_RECORDS], "fetched": fetched, "total": total, "truncated": fetched < total}


def ranked(counter):
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))


def summarise(rows):
    years = Counter(r["year"] for r in rows if r["year"])
    by_year = [(y, years.get(y, 0)) for y in range(min(years), max(years) + 1)] if years else []
    nodes = Counter(r["node"] for r in rows)
    own = sum(1 for r in rows if NATIONAL_NODE in r["node"].lower())
    return {
        "records": len(rows),
        "no_date": sum(1 for r in rows if not r["year"]),
        "by_year": by_year,
        "by_phylum": ranked(Counter(r["phylum"] for r in rows)),
        "by_class": ranked(Counter(r["class"] for r in rows)),
        "by_node": ranked(nodes),
        "by_institution": ranked(Counter(r["institution"] for r in rows)),
        "species": len({r["name"] for r in rows if r["name"]}),
        "own_node": {"match": NATIONAL_NODE, "records": own, "share": round(own / len(rows), 4) if rows else 0},
    }


async def area_data(code):
    feature = await boundary(code)

    async def make():
        res = await fetch_records(shape(feature["geometry"]))
        return {**res, "stats": summarise(res["rows"])}
    return feature, await cached(("data", code), make)


@app.get("/api/config")
async def config():
    return {"country": COUNTRY, "max_records": MAX_RECORDS, "national_node": NATIONAL_NODE}


@app.get("/api/areas/{code}/data")
async def data(code: str):
    feature, res = await area_data(code)
    return {"boundary": feature, **res}


COLUMNS = ["id", "name", "phylum", "class", "year", "lat", "lon", "node", "institution", "dataset"]


@app.get("/api/areas/{code}/records.csv")
async def records_csv(code: str):
    _, res = await area_data(code)
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(res["rows"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="obis_{code}.csv"'})


@app.get("/api/areas")
async def areas():
    return await list_areas()


@app.get("/healthz")
async def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
