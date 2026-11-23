"""SunSight: building-level sun and view exposure, cell by cell.

    .venv/Scripts/python.exe -m uvicorn main:app --port 8010

MVC: models/ (data + analysis), controllers/ (FastAPI routers), views/ (static UI).
"""
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import VIEWS_DIR
from controllers import regions, tiles

app = FastAPI(title="SunSight", version="0.2.0")
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.include_router(regions.router)
app.include_router(tiles.router)
app.mount("/static", StaticFiles(directory=VIEWS_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(VIEWS_DIR / "index.html")
