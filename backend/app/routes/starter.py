"""The starter papers, open to everyone. Nothing here records anything: a
visitor's reading isn't tracked until they sign in."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from .. import starter
from .papers import serve_html, serve_pdf

router = APIRouter(prefix="/api/starter")


def _find(key: str) -> starter.Starter:
    paper = starter.find(key)
    if paper is None:
        raise HTTPException(404, "Not a starter paper. Sign in to read any paper.")
    return paper


@router.get("")
def listing():
    return starter.listing()


@router.get("/{key}/pdf")
def starter_pdf(key: str, request: Request):
    return serve_pdf(request.app.state.settings, _find(key).arxiv_id, None)


@router.get("/{key}/html")
def starter_html(key: str, request: Request, theme: Literal["light", "dark"] = "light"):
    return serve_html(request.app.state.settings, _find(key).arxiv_id, None, theme)
