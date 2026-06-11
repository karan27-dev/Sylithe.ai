"""Serves the Sylithe agent console (single static file, zero build step)."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE_PATH = Path(__file__).parent / "static" / "dashboard.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return _PAGE_PATH.read_text()
