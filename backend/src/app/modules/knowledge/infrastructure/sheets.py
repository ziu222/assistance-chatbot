"""Adapter Google Sheets (§13.2): đọc bảng catalog bằng service account, chỉ quyền đọc.

Chủ shop chia sẻ sheet cho email của service account (quyền xem); Atlas không giữ OAuth của người
dùng. Chỉ gọi hai host cố định của Google; id sheet và vùng ô được kiểm tra trước khi ghép URL.
"""

import json
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
VALUES_URL = "https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range}"
SPREADSHEET_ID = re.compile(r"[A-Za-z0-9_-]{20,100}")
CELL_RANGE = re.compile(r"[^/?#\\]{1,100}")
TIMEOUT_S = 20.0


class SheetsError(Exception):
    """Lỗi đọc sheet, kèm mã ngắn để ghi vào lần sync (không chứa secret)."""


def service_account_email(credentials_json: str) -> str | None:
    try:
        return json.loads(credentials_json)["client_email"]
    except (ValueError, KeyError, TypeError):
        return None


def _assertion(creds: dict[str, Any], now: int) -> str:
    return jwt.encode(
        {
            "iss": creds["client_email"],
            "scope": SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 600,
        },
        creds["private_key"],
        algorithm="RS256",
        headers={"kid": creds.get("private_key_id", "")},
    )


async def fetch_values(
    credentials_json: str, spreadsheet_id: str, cell_range: str, client: httpx.AsyncClient
) -> list[list[str]]:
    if not SPREADSHEET_ID.fullmatch(spreadsheet_id) or not CELL_RANGE.fullmatch(cell_range):
        raise SheetsError("invalid_sheet_reference")
    try:
        creds = json.loads(credentials_json)
        assertion = _assertion(creds, int(time.time()))
    except (ValueError, KeyError, TypeError, jwt.PyJWTError) as exc:
        raise SheetsError("service_account_not_configured") from exc
    try:
        token = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=TIMEOUT_S,
        )
        if token.status_code != 200:
            raise SheetsError("token_rejected")
        r = await client.get(
            VALUES_URL.format(id=spreadsheet_id, range=quote(cell_range, safe="")),
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise SheetsError("network_error") from exc
    if r.status_code in (403, 404):
        raise SheetsError("sheet_not_shared")  # chưa chia sẻ cho service account, hoặc sai id
    if r.status_code != 200:
        raise SheetsError(f"sheets_http_{r.status_code}")
    return r.json().get("values", [])
