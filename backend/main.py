import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
import secrets
import threading
import time
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import (
    BASE_DIR,
    authenticate_user,
    balance_snapshot_exists,
    create_user,
    ensure_auth_user,
    get_accounts,
    get_balance_snapshots,
    get_stock_detail,
    get_summary,
    get_user_by_username,
    import_history_content,
    import_content,
    import_path,
    latest_price_status,
    list_login_events,
    list_users,
    record_login_event,
    refresh_current_prices,
    save_balance_snapshot,
    save_account,
    update_account,
    update_latest_cash_balance,
)


FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
AUTH_USERNAME = os.environ.get("PORTFOLIO_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("PORTFOLIO_PASSWORD", "changeme")
AUTH_SECRET_KEY = os.environ.get("PORTFOLIO_SECRET_KEY", "local-dev-change-this-secret")
COOKIE_SECURE = os.environ.get("PORTFOLIO_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE = "portfolio_session"
SESSION_SECONDS = int(os.environ.get("PORTFOLIO_SESSION_SECONDS", str(12 * 60 * 60)))
PRICE_REFRESH_SECONDS = int(os.environ.get("PORTFOLIO_PRICE_REFRESH_SECONDS", "60"))
PRICE_REFRESH_ENABLED = os.environ.get("PORTFOLIO_PRICE_REFRESH_ENABLED", "true").lower() not in {"0", "false", "no"}
PRICE_REFRESH_TIMEZONE_NAME = os.environ.get("PORTFOLIO_PRICE_REFRESH_TIMEZONE", "America/Edmonton")
PRICE_REFRESH_START = os.environ.get("PORTFOLIO_PRICE_REFRESH_START", "07:45")
PRICE_REFRESH_END = os.environ.get("PORTFOLIO_PRICE_REFRESH_END", "16:00")
PRICE_REFRESH_TIMEZONE = ZoneInfo(PRICE_REFRESH_TIMEZONE_NAME)

price_refresh_stop = threading.Event()
price_refresh_thread: threading.Thread | None = None

app = FastAPI(title="Local Portfolio Tracker", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):517\d",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AccountPayload(BaseModel):
    name: str
    owner: str = ""
    account_type: str = "Investment"
    base_currency: str = "CAD"
    notes: str = ""
    cash_balance: Optional[float] = None
    cash_currency: str = "CAD"


class ImportPathPayload(BaseModel):
    path: str
    account_name: str = ""
    cash_balance: Optional[float] = None
    cash_currency: str = "CAD"


class LoginPayload(BaseModel):
    username: str
    password: str


class UserPayload(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    active: bool = True


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(value: str) -> str:
    digest = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return _base64url_encode(digest)


def create_session(username: str) -> str:
    payload = {
        "sub": username,
        "exp": int(time.time()) + SESSION_SECONDS,
    }
    body = _base64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify_session(token: str | None) -> Optional[str]:
    if not token or "." not in token:
        return None

    body, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(body)):
        return None

    try:
        payload = json.loads(_base64url_decode(body))
    except Exception:
        return None

    if payload.get("exp", 0) < int(time.time()):
        return None

    username = payload.get("sub")
    return username


def require_auth(request: Request) -> str:
    username = verify_session(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = get_user_by_username(username)
    if not user or not user["active"]:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return username


def require_admin(username: str = Depends(require_auth)) -> str:
    user = get_user_by_username(username)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return username


def parse_clock_time(value: str, default: str) -> clock_time:
    text = (value or default).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return clock_time(int(hour_text), int(minute_text))
    except Exception:
        hour_text, minute_text = default.split(":", 1)
        return clock_time(int(hour_text), int(minute_text))


PRICE_REFRESH_START_TIME = parse_clock_time(PRICE_REFRESH_START, "07:45")
PRICE_REFRESH_END_TIME = parse_clock_time(PRICE_REFRESH_END, "16:00")


def is_price_refresh_window(now: datetime | None = None) -> bool:
    local_now = now.astimezone(PRICE_REFRESH_TIMEZONE) if now else datetime.now(PRICE_REFRESH_TIMEZONE)
    current = local_now.time()
    return PRICE_REFRESH_START_TIME <= current < PRICE_REFRESH_END_TIME


def price_refresh_schedule_status():
    return {
        "enabled": PRICE_REFRESH_ENABLED,
        "timezone": PRICE_REFRESH_TIMEZONE_NAME,
        "start": PRICE_REFRESH_START_TIME.strftime("%H:%M"),
        "end": PRICE_REFRESH_END_TIME.strftime("%H:%M"),
        "in_window": is_price_refresh_window(),
    }


def snapshot_market_date_for_now(now: datetime | None = None) -> str | None:
    local_now = now.astimezone(PRICE_REFRESH_TIMEZONE) if now else datetime.now(PRICE_REFRESH_TIMEZONE)
    current = local_now.time()
    if current >= PRICE_REFRESH_END_TIME:
        return local_now.date().isoformat()
    if current < PRICE_REFRESH_START_TIME:
        return (local_now.date() - timedelta(days=1)).isoformat()
    return None


def local_date_from_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(PRICE_REFRESH_TIMEZONE).date().isoformat()


def maybe_save_end_of_day_snapshot():
    market_date = snapshot_market_date_for_now()
    if not market_date or balance_snapshot_exists(market_date):
        return None

    status = latest_price_status()
    if local_date_from_iso(status.get("latest_fetched_at")) != market_date:
        return None

    return save_balance_snapshot(market_date, source="auto")


def price_refresh_loop():
    while not price_refresh_stop.is_set():
        if is_price_refresh_window():
            refresh_current_prices()
        else:
            maybe_save_end_of_day_snapshot()
        price_refresh_stop.wait(PRICE_REFRESH_SECONDS)


@app.on_event("startup")
def start_background_workers():
    global price_refresh_thread
    ensure_auth_user(AUTH_USERNAME, AUTH_PASSWORD, is_admin=True)
    if not PRICE_REFRESH_ENABLED or price_refresh_thread:
        return
    price_refresh_stop.clear()
    price_refresh_thread = threading.Thread(target=price_refresh_loop, name="price-refresh", daemon=True)
    price_refresh_thread.start()


@app.on_event("shutdown")
def stop_background_workers():
    price_refresh_stop.set()


@app.get("/api/health")
def health():
    return {"ok": True}


def request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


@app.post("/api/login")
def login(payload: LoginPayload, request: Request, response: Response):
    user = authenticate_user(payload.username, payload.password)
    audit_user = user or get_user_by_username(payload.username)
    record_login_event(
        payload.username,
        user_id=audit_user["id"] if audit_user else None,
        success=bool(user),
        ip_address=request_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    response.set_cookie(
        SESSION_COOKIE,
        create_session(user["username"]),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
    return {"ok": True, "username": user["username"], "is_admin": user["is_admin"]}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return {"ok": True}


@app.get("/api/me")
def me(username: str = Depends(require_auth)):
    user = get_user_by_username(username)
    return {"authenticated": True, "username": username, "is_admin": bool(user and user["is_admin"])}


@app.get("/api/users")
def users(username: str = Depends(require_admin)):
    return {"users": list_users(), "login_events": list_login_events(100)}


@app.post("/api/users")
def add_user(payload: UserPayload, username: str = Depends(require_admin)):
    try:
        user = create_user(
            payload.username,
            payload.password,
            is_admin=payload.is_admin,
            active=payload.active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": user, "users": list_users(), "login_events": list_login_events(100)}


@app.get("/api/summary")
def summary(username: str = Depends(require_auth)):
    payload = get_summary()
    payload.setdefault("price_refresh", {})["schedule"] = price_refresh_schedule_status()
    return payload


@app.get("/api/accounts")
def accounts(username: str = Depends(require_auth)):
    return {"accounts": get_accounts()}


@app.get("/api/prices/status")
def price_status(username: str = Depends(require_auth)):
    return {
        **latest_price_status(),
        "schedule": price_refresh_schedule_status(),
    }


@app.post("/api/prices/refresh")
def refresh_prices(username: str = Depends(require_auth)):
    return refresh_current_prices()


@app.get("/api/stocks/{symbol}")
def stock_detail(symbol: str, market: str = "", refresh: bool = False, username: str = Depends(require_auth)):
    try:
        return get_stock_detail(symbol, market, refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/balance-snapshots")
def balance_snapshots(limit: int = 90, username: str = Depends(require_auth)):
    return get_balance_snapshots(limit)


@app.post("/api/balance-snapshots/{market_date}")
def save_snapshot_for_date(market_date: str, username: str = Depends(require_auth)):
    try:
        return save_balance_snapshot(market_date, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts")
def create_or_update_account(payload: AccountPayload, username: str = Depends(require_auth)):
    try:
        result = save_account(
            payload.name,
            payload.owner,
            payload.account_type,
            payload.base_currency,
            payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@app.put("/api/accounts/{account_id}")
def edit_account(account_id: int, payload: AccountPayload, username: str = Depends(require_auth)):
    try:
        result = update_account(
            account_id,
            payload.name,
            payload.owner,
            payload.account_type,
            payload.base_currency,
            payload.notes,
        )
        field_set = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))
        if "cash_balance" in field_set:
            result["cash"] = update_latest_cash_balance(
                account_id,
                payload.cash_balance,
                payload.cash_currency,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@app.post("/api/import")
async def import_upload(
    file: UploadFile = File(...),
    account_name: str = Form(""),
    cash_balance: str = Form(""),
    cash_currency: str = Form("CAD"),
    username: str = Depends(require_auth),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="No CSV file was uploaded.")

    try:
        return import_content(
            content,
            file.filename or "holdings.csv",
            account_name,
            cash_balance,
            cash_currency,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/history/import")
async def import_history_upload(
    file: UploadFile = File(...),
    username: str = Depends(require_auth),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="No CSV file was uploaded.")

    try:
        return import_history_content(content, file.filename or "history.csv")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/import-path")
def import_local_path(payload: ImportPathPayload, username: str = Depends(require_auth)):
    path = Path(payload.path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=400, detail="The requested file path does not exist.")

    try:
        return import_path(path, payload.account_name, payload.cash_balance, payload.cash_currency)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_react_app(full_path: str):
        requested = FRONTEND_DIST / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
