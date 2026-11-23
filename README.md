# SunSight

Which buildings, and which parts of each facade and roof, get the most sun and
the best view? SunSight cuts every roof and facade into ~2 m cells, traces the
horizon each cell sees, integrates a full year of sun positions weighted by the
local 10-year climate, and scores the view (openness, green, water).

## Run

```bash
py -3.10 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe tools/precompute.py vaxjo          # whole city: 42 tiles, ~3.5 min on 22 cores
.venv/Scripts/python.exe -m uvicorn main:app --port 8010
```

Open http://127.0.0.1:8010 (`#vaxjo`, `#vaxjo-centrum`, …). Other presets live in
`config.REGION_PRESETS`; any bbox works with `--bbox w,s,e,n --name ...`.

## Scale model: tiles, not cities

All analysis happens on the global z14 XYZ tile grid (~1.3 km in Sweden):

- **Processing.** Each tile is independent. It has its own local metric frame and a 300 m
  buffer of shadow-casting neighbours, and runs in a process pool. Tiles are cached
  by `TILE_VERSION`, so re-runs and overlapping regions are cheap.
  OSM is fetched once per z12 chunk (split into quadrants if Overpass refuses), and
  climate once per 0.1° cell.
- **Storage.** `data/tiles/14/x/y/` holds `mesh.bin` (surfaces), `v_<metric>.f32`
  (one float per cell slot), `cells.npz` (horizons and metrics), `raster.npz`
  (DSM + land cover, for validation), `buildings.json` and `landcover.json`.
- **Regions.** A region is a named list of tiles plus region-wide aggregates
  (`data/regions/<id>/`: building percentiles, colour domains, CDFs for "overall").
  A country or the world is the same thing with more tiles.

Växjö city: 15,671 buildings, 2.56 M cells, 295 MB on disk. The browser
downloads 3.2 MB of geometry plus ~5.8 MB per metric (gzipped) for the whole city.

## Rendering: the pixel matrix

Every wall is one quad and every roof one polygon. Each surface owns a block of
cell "slots" (its matrix, row-major). A fragment shader turns the surface UV
into a slot index and looks the value up in a float texture, then draws the grid
lines. Geometry stays tiny while every 2 m cell keeps its own colour. The same
meshes are used in the MapLibre custom layer (whole region at once) and in the 3D
inspector. The model-view-projection is composed per tile in double precision,
so there is no jitter at street zoom.

## Validation views

- **Click any cell** to get its sun hours, winter sun, sun access, irradiation, view
  score and components.
- **Validate view** (building panel or cell panel) opens the 3D view at that window,
  in first person. It draws the 72 sight-lines the metric traced: green and water
  runs on the ground, a red marker at the first blocking building, and eye-height
  lines in orbit view. It also shows a polar diagram and the score recomputed from
  those rays next to the stored value.
- **Sun at a moment** (3D view) takes any date and time and shades every cell within
  90 m from its stored horizon, with a real-time shadow map and the sun's path for
  the day.

## Method

1. OSM buildings, water and green; heights from `height`, `building:levels` × 3 m, or a
   per-type default (flagged). 1 m DSM + land-cover raster per tile.
2. Cells: roof grid aligned to the building's main axis; facade strips 2 m × ~1
   storey; party walls dropped.
3. Horizon per cell: 72 azimuth rays out to 500 m, giving the horizon angle,
   first-obstruction distance, and green/water length along the line of sight.
4. Sun: NOAA position in UTC from lat/lon (polar day and night are correct), binned into a
   (month × azimuth × elevation) histogram; one cumulative-sum gather per cell.
5. Climate: Open-Meteo ERA5 2015–2024. Sunshine fraction from irradiance via inverse
   Ångström–Prescott, because raw ERA5 sunshine is ~50% too high in Sweden. Open roofs
   reproduce measured GHI.
6. View: `0.6·openness + 0.2·green + 0.2·water` over ±75° of the facade normal.

## Known limitations / next steps

- **Heights:** 99% of Växjö buildings use a default height (OSM rarely tags it).
  Next: Lantmäteriet laser DSM (open, CC-BY), which also gives terrain, trees and
  roof shapes.
- The view only uses horizontal sight-lines; looking *down* onto green isn't modelled.
- Country scale: viewport-driven tile loading, float16 values, and a z<13 overview
  (per-tile aggregates).
