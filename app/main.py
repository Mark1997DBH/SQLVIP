"""SQLVIP FastAPI application."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.converter import (
    MAX_INPUT_LENGTH,
    ConversionError,
    convert_sql,
    dialect_catalog,
)

def _static_dir() -> Path:
    """Resolve static assets both from source and from a PyInstaller bundle."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "app" / "static"
    return Path(__file__).resolve().parent / "static"


STATIC_DIR = _static_dir()

app = FastAPI(
    title="SQLVIP API",
    version=__version__,
    description="DB2 / PostgreSQL / Oracle / MySQL / 达梦 SQL 方言转换服务",
    docs_url="/api/docs",
    redoc_url=None,
)


class ConvertRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=MAX_INPUT_LENGTH)
    source: Literal["db2", "postgres", "oracle", "mysql", "dameng"]
    target: Literal["db2", "postgres", "oracle", "mysql", "dameng"]
    pretty: bool = True


@app.exception_handler(ConversionError)
async def handle_conversion_error(
    request: Request, exc: ConversionError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": exc.as_dict()},
    )


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", tags=["system"])
async def health() -> dict:
    return {"ok": True, "version": __version__, "service": "SQLVIP"}


@app.get("/api/dialects", tags=["conversion"])
async def dialects() -> dict:
    return {"ok": True, "dialects": dialect_catalog()}


@app.post("/api/convert", tags=["conversion"])
async def convert(payload: ConvertRequest) -> dict:
    result = convert_sql(
        payload.sql,
        payload.source,
        payload.target,
        pretty=payload.pretty,
    )
    return {"ok": True, **result.as_dict()}


# Serve CSS/JS from the same origin; no build step or external CDN is required.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
