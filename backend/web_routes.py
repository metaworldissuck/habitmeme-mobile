from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def create_web_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/app", response_class=HTMLResponse)
    def app_shell(request: Request) -> HTMLResponse:
        return templates.TemplateResponse("index.html", {"request": request})

    return router

