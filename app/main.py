"""MPA Data Explorer: a small FastAPI service in front of two open APIs.

* Protected-area boundaries: EEA Natura 2000 ArcGIS feature service (CC BY 4.0).
* Occurrences and node names: OBIS API v3 (api.obis.org).
Everything is fetched live, cached in memory, and configured by environment variables.
"""
import asyncio
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from shapely import to_wkt
from shapely.geometry import shape

OBIS = os.getenv("OBIS_API", "https://api.obis.org/v3")
MPA_URL = os.getenv(
    "MPA_QUERY_URL",
    "https://nest.discomap.eea.europa.eu/arcgis/rest/services/Hosted/Natura2000/FeatureServer/0/query",
)
MPA_WHERE = os.getenv("MPA_WHERE", "ms='PT' AND is_marine='True'")
COUNTRY = os.getenv("COUNTRY_LABEL", "Portugal")
TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

app = FastAPI(title="MPA Data Explorer")
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


@app.get("/api/config")
async def config():
    return {"country": COUNTRY}


@app.get("/api/areas")
async def areas():
    return await list_areas()


@app.get("/healthz")
async def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
