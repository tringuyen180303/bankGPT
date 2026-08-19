from __future__ import annotations

import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core_console.data import (
    DEMO_PASSWORD,
    DEMO_USER,
    MEMBERS,
    fmt_money,
    parse_amount,
    present,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Core Console")
app.add_middleware(SessionMiddleware, secret_key="core-console-demo-not-secret")


def _authed(request: Request) -> bool:
    return bool(request.session.get("user"))


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    if _authed(request):
        return RedirectResponse("/search", status_code=302)
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    if username.strip() == DEMO_USER and password == DEMO_PASSWORD:
        request.session["user"] = DEMO_USER
        request.session["notice_pending"] = True
        return RedirectResponse("/search", status_code=302)
    return TEMPLATES.TemplateResponse(
        request, "login.html", {"error": "Invalid operator ID or password"}, status_code=401
    )


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, timeout: str | None = None) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if timeout == "1":
        request.session.clear()
        return TEMPLATES.TemplateResponse(request, "expired.html", {})
    return TEMPLATES.TemplateResponse(request, "search.html", {"timeout": False})


@app.get("/search-frame", response_class=HTMLResponse)
def search_frame(request: Request) -> HTMLResponse:
    if not _authed(request):
        return HTMLResponse("<p>Session expired</p>", status_code=401)
    return TEMPLATES.TemplateResponse(request, "search_frame.html", {"error": None})


@app.get("/search-submit", response_model=None)
def search_submit(request: Request, member_id: str = "") -> RedirectResponse | HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    member_id = member_id.strip()
    if not member_id:
        return TEMPLATES.TemplateResponse(
            request,
            "search.html",
            {"timeout": False},
        )
    if member_id not in MEMBERS:
        return RedirectResponse("/not-found", status_code=302)
    return RedirectResponse(f"/member/{member_id}", status_code=302)


@app.get("/not-found", response_class=HTMLResponse)
def not_found(request: Request) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(request, "not_found.html", {})


@app.get("/member/{member_id}", response_class=HTMLResponse)
def member_detail(request: Request, member_id: str) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    member = MEMBERS.get(member_id)
    if not member:
        return RedirectResponse("/not-found", status_code=302)
    notice = bool(request.session.pop("notice_pending", False))
    return TEMPLATES.TemplateResponse(
        request,
        "detail.html",
        {"member": present(member), "member_id": member_id, "notice": notice},
    )


@app.get("/sub-account/{member_id}", response_class=HTMLResponse)
def sub_account_form(request: Request, member_id: str) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if member_id not in MEMBERS:
        return RedirectResponse("/not-found", status_code=302)
    return TEMPLATES.TemplateResponse(
        request,
        "sub_account.html",
        {"member_id": member_id, "stage": "form", "error": None},
    )


@app.post("/sub-account/{member_id}", response_class=HTMLResponse)
def sub_account_post(
    request: Request,
    member_id: str,
    product: str = Form(""),
    nickname: str = Form(""),
    confirm: str = Form(""),
) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if not nickname.strip() and not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "sub_account.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Nickname is required",
            },
        )
    if not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "sub_account.html",
            {
                "member_id": member_id,
                "stage": "review",
                "product": product,
                "nickname": nickname,
                "error": None,
            },
        )
    return TEMPLATES.TemplateResponse(
        request,
        "sub_account.html",
        {
            "member_id": member_id,
            "stage": "done",
            "confirmation": secrets.token_hex(4).upper(),
            "error": None,
        },
    )


@app.get("/close/{member_id}", response_class=HTMLResponse)
def close_account(request: Request, member_id: str) -> HTMLResponse:
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(request, "denied.html", {"member_id": member_id})


def _need_member(request: Request, member_id: str):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    member = MEMBERS.get(member_id)
    if not member:
        return RedirectResponse("/not-found", status_code=302)
    return member


@app.get("/payment/{member_id}", response_class=HTMLResponse)
def payment_form(request: Request, member_id: str) -> HTMLResponse:
    member = _need_member(request, member_id)
    if isinstance(member, RedirectResponse):
        return member
    if not member["has_credit"]:
        return TEMPLATES.TemplateResponse(
            request, "no_credit.html", {"member_id": member_id, "title": "Post payment"}
        )
    return TEMPLATES.TemplateResponse(
        request,
        "payment.html",
        {"member_id": member_id, "stage": "form", "error": None, "amount": ""},
    )


@app.post("/payment/{member_id}", response_class=HTMLResponse)
def payment_post(
    request: Request,
    member_id: str,
    amount: str = Form(""),
    confirm: str = Form(""),
) -> HTMLResponse:
    member = _need_member(request, member_id)
    if isinstance(member, RedirectResponse):
        return member
    if not member["has_credit"]:
        return TEMPLATES.TemplateResponse(
            request, "no_credit.html", {"member_id": member_id, "title": "Post payment"}
        )
    parsed = parse_amount(amount)
    if parsed is None and not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "payment.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Amount is required",
                "amount": amount,
            },
        )
    if parsed is not None and parsed > member["savings"]:
        return TEMPLATES.TemplateResponse(
            request,
            "payment.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Amount exceeds savings balance",
                "amount": amount,
            },
        )
    if parsed is not None and parsed > member["loan_balance"]:
        return TEMPLATES.TemplateResponse(
            request,
            "payment.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Amount exceeds loan balance",
                "amount": amount,
            },
        )
    if not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "payment.html",
            {
                "member_id": member_id,
                "stage": "review",
                "error": None,
                "amount": fmt_money(parsed or 0),
            },
        )
    pay = parse_amount(amount) or 0
    member["savings"] = round(member["savings"] - pay, 2)
    member["loan_balance"] = round(member["loan_balance"] - pay, 2)
    member["available_credit"] = round(member["available_credit"] + pay, 2)
    return TEMPLATES.TemplateResponse(
        request,
        "payment.html",
        {
            "member_id": member_id,
            "stage": "done",
            "confirmation": secrets.token_hex(4).upper(),
            "error": None,
            "savings": fmt_money(member["savings"]),
            "loan": fmt_money(member["loan_balance"]),
        },
    )


@app.get("/credit-draw/{member_id}", response_class=HTMLResponse)
def draw_form(request: Request, member_id: str) -> HTMLResponse:
    member = _need_member(request, member_id)
    if isinstance(member, RedirectResponse):
        return member
    if not member["has_credit"]:
        return TEMPLATES.TemplateResponse(
            request, "no_credit.html", {"member_id": member_id, "title": "Draw on line"}
        )
    return TEMPLATES.TemplateResponse(
        request,
        "credit_draw.html",
        {"member_id": member_id, "stage": "form", "error": None, "amount": ""},
    )


@app.post("/credit-draw/{member_id}", response_class=HTMLResponse)
def draw_post(
    request: Request,
    member_id: str,
    amount: str = Form(""),
    confirm: str = Form(""),
) -> HTMLResponse:
    member = _need_member(request, member_id)
    if isinstance(member, RedirectResponse):
        return member
    if not member["has_credit"]:
        return TEMPLATES.TemplateResponse(
            request, "no_credit.html", {"member_id": member_id, "title": "Draw on line"}
        )
    parsed = parse_amount(amount)
    if parsed is None and not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "credit_draw.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Amount is required",
                "amount": amount,
            },
        )
    if parsed is not None and parsed > member["available_credit"]:
        return TEMPLATES.TemplateResponse(
            request,
            "credit_draw.html",
            {
                "member_id": member_id,
                "stage": "form",
                "error": "Insufficient available credit",
                "amount": amount,
            },
        )
    if not confirm:
        return TEMPLATES.TemplateResponse(
            request,
            "credit_draw.html",
            {
                "member_id": member_id,
                "stage": "review",
                "error": None,
                "amount": fmt_money(parsed or 0),
            },
        )
    draw = parse_amount(amount) or 0
    member["available_credit"] = round(member["available_credit"] - draw, 2)
    member["loan_balance"] = round(member["loan_balance"] + draw, 2)
    member["savings"] = round(member["savings"] + draw, 2)
    return TEMPLATES.TemplateResponse(
        request,
        "credit_draw.html",
        {
            "member_id": member_id,
            "stage": "done",
            "confirmation": secrets.token_hex(4).upper(),
            "error": None,
            "available": fmt_money(member["available_credit"]),
            "savings": fmt_money(member["savings"]),
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("core_console.app:app", host="127.0.0.1", port=3000, reload=False)


if __name__ == "__main__":
    main()
