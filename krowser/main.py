from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from krowser.api import routes_graph, routes_meta, routes_resource, routes_resources

app = FastAPI(title="krowser")

app.include_router(routes_meta.router)
app.include_router(routes_resources.router)
app.include_router(routes_resource.router)
app.include_router(routes_graph.router)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
