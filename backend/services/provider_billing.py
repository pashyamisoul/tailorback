"""
Optional: pull official billed usage/cost from OpenAI and Anthropic admin APIs.

These endpoints require an ADMIN/organization key, distinct from the regular
inference key the app uses. Set them in the environment (never in code):

    OPENAI_ADMIN_KEY      (an OpenAI admin key, e.g. sk-admin-...)
    ANTHROPIC_ADMIN_KEY   (an Anthropic admin key, e.g. sk-ant-admin-...)

Gemini has no equivalent per-key billing endpoint (cost lives in Google Cloud
Billing), so it stays estimate-only.

Everything here fails soft: if a key is missing or a call errors, we return
{"available": False, "reason": ...} and never raise into the request.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone

import httpx

OPENAI_COSTS_URL = "https://api.openai.com/v1/organization/costs"
ANTHROPIC_COST_URL = "https://api.anthropic.com/v1/organizations/cost_report"
_TIMEOUT = 30.0


def openai_admin_configured() -> bool:
    return bool(os.environ.get("OPENAI_ADMIN_KEY"))


def anthropic_admin_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_ADMIN_KEY"))


# ---- pure parsers (unit-tested without network) --------------------------
def parse_openai_costs(payload: dict) -> float:
    """Sum data[].results[].amount.value across cost buckets."""
    total = 0.0
    for bucket in (payload or {}).get("data", []) or []:
        for res in bucket.get("results", []) or []:
            amt = res.get("amount") or {}
            try:
                total += float(amt.get("value") or 0)
            except (TypeError, ValueError):
                continue
    return round(total, 4)


def parse_anthropic_costs(payload: dict) -> float:
    """Sum data[].results[].amount across cost buckets (amount may be a string)."""
    total = 0.0
    for bucket in (payload or {}).get("data", []) or []:
        for res in bucket.get("results", []) or []:
            try:
                total += float(res.get("amount") or 0)
            except (TypeError, ValueError):
                continue
    return round(total, 4)


# ---- live fetchers (graceful) --------------------------------------------
def fetch_openai_cost(days: int = 30) -> dict:
    key = os.environ.get("OPENAI_ADMIN_KEY")
    if not key:
        return {"available": False, "reason": "no_admin_key"}
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    params = {"start_time": start, "limit": 180}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        total = 0.0
        for _ in range(24):  # paginate, bounded
            r = httpx.get(OPENAI_COSTS_URL, headers=headers, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            total += parse_openai_costs(d)
            if d.get("has_more") and d.get("next_page"):
                params["page"] = d["next_page"]
            else:
                break
        return {"available": True, "cost_usd": round(total, 2), "days": days}
    except Exception as e:  # noqa: BLE001 - never break the admin page
        return {"available": False, "reason": str(e)[:160]}


def fetch_anthropic_cost(days: int = 30) -> dict:
    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        return {"available": False, "reason": "no_admin_key"}
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    params = {"starting_at": start, "limit": 31}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    try:
        total = 0.0
        for _ in range(24):
            r = httpx.get(ANTHROPIC_COST_URL, headers=headers, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            total += parse_anthropic_costs(d)
            if d.get("has_more") and d.get("next_page"):
                params["page"] = d["next_page"]
            else:
                break
        return {"available": True, "cost_usd": round(total, 2), "days": days}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)[:160]}


def sync_all(days: int = 30) -> dict:
    """Fetch official billed cost from every provider that supports it."""
    return {
        "openai": fetch_openai_cost(days),
        "anthropic": fetch_anthropic_cost(days),
        "gemini": {"available": False, "reason": "no_billing_api"},
    }
