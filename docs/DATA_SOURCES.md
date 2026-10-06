# Data sources and APIs register

Everything the app touches, with licence and how it is used. All free, no account or key.

| # | Source | Endpoint | Used for | Licence / terms | Checked |
|---|---|---|---|---|---|
| 1 | OBIS API v3, occurrences | `https://api.obis.org/v3/occurrence` (`geometry` WKT, `fields`, `size`, `after`) | Records inside an area | Per-dataset (CC0, CC-BY, CC-BY-NC); citation per dataset | Response shape read from live calls |
| 2 | OBIS API v3, nodes | `https://api.obis.org/v3/node` | Node id to node name | As above | Live |
| 3 | OBIS API v3, datasets | `https://api.obis.org/v3/dataset` | Dataset titles, node links (`nodes`), `institutes` (often null) | As above | Live |
| 4 | OBIS statistics / grid | `/v3/statistics/years`, `/v3/occurrence/grid/{precision}` | Possible exact totals (not used yet, see README) | As above | Live |
| 5 | OBIS full export | `s3://obis-open-data` (GeoParquet, no credentials, us-east-1) | Offline alternative for very large areas | CC BY-NC 4.0 | Registry page only |
| 6 | EEA Natura 2000 (end-2023) | `https://nest.discomap.eea.europa.eu/arcgis/rest/services/Hosted/Natura2000/FeatureServer/0/query` | Protected-area list and boundaries for Portugal (`ms='PT'`, `is_marine='True'`) | CC BY 4.0, EEA | Fields and GeoJSON output verified live; marine-site list query hit a rate limit and was not read |
| 7 | Leaflet 1.9.4 | vendored from npm | Map library | BSD-2 | n/a |
| 8 | Base maps | OpenStreetMap, CARTO Positron, OpenTopoMap, Esri World Imagery tiles | Background maps | Attribution required; each provider's tile usage policy | n/a |

## Considered and not used

| Source | Why not |
|---|---|
| WDPA / Protected Planet | Non-commercial terms, no redistribution, API needs a token |
| Marine Regions EEZ (MRGID 5688, Portugal) | CC-BY and usable, but EEZ outline is not needed once each area is queried directly |
| EMODnet Human Activities WFS | Open, but the protected-area layer name could not be confirmed |
