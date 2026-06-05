import hmac
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from flask import Blueprint, current_app, jsonify, request, abort
from flask_login import current_user, login_required

from db import db
from models import Subscription, User

billing_bp = Blueprint("billing", __name__)


def _parse_rfc3339(dt: Optional[str]) -> Optional[datetime]:
    if not dt:
        return None
    try:
        # Paddle uses RFC3339 / ISO8601, often ending with Z
        return datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def paddle_api_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    api_key = current_app.config["PADDLE_API_KEY"]
    if not api_key:
        raise RuntimeError("PADDLE_API_KEY not configured")

    r = requests.post(
        f"https://api.paddle.com{path}",
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def verify_paddle_signature(raw_body: bytes, header_value: str, tolerance_seconds: int = 300) -> bool:
    """
    Paddle signature verification (manual):
    - Parse `Paddle-Signature`: ts=...;h1=...
    - signed_payload = ts + ":" + raw_body (raw body must be unchanged)
    - expected = HMAC-SHA256(secret, signed_payload)
    - compare to h1

    Docs: Paddle signature verification guide. :contentReference[oaicite:6]{index=6}
    """
    if not header_value:
        return False

    parts = {}
    for piece in header_value.split(";"):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts")
    h1 = parts.get("h1")
    if not ts or not h1:
        return False

    try:
        ts_int = int(ts)
    except Exception:
        return False

    if abs(int(time.time()) - ts_int) > tolerance_seconds:
        return False

    secret = current_app.config["PADDLE_WEBHOOK_SECRET"]
    if not secret:
        return False

    signed = ts.encode("utf-8") + b":" + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


def _get_plans():
    return current_app.config["PLANS"]


def _plan_for_price_id(price_id: str) -> Optional[Dict[str, Any]]:
    for p in _get_plans():
        if p["price_id"] == price_id:
            return p
    return None


@login_required
@billing_bp.post("/api/billing/create-checkout")
def create_checkout():
    """
    Creates a Paddle transaction and returns transaction_id.

    Paddle.js opens checkout using this transactionId. :contentReference[oaicite:7]{index=7}
    """
    body = request.get_json(silent=True) or {}
    price_id = str(body.get("price_id", "")).strip()

    plan = _plan_for_price_id(price_id)
    if not plan:
        return jsonify(error="Invalid plan."), 400

    payload = {
        "collection_mode": "automatic",
        "items": [{"price_id": price_id, "quantity": 1}],
        "custom_data": {
            "user_id": current_user.id,
            "user_email": current_user.email,
            "price_id": price_id,
        },
    }

    try:
        resp = paddle_api_post("/transactions", payload)
        transaction_id = resp.get("data", {}).get("id")
        if not transaction_id:
            return jsonify(error="Could not create checkout."), 400
        return jsonify(transaction_id=transaction_id)
    except Exception:
        return jsonify(error="Could not create checkout. Check Paddle API key/settings."), 400


@billing_bp.post("/webhooks/paddle")
def paddle_webhook():
    """
    Handles Paddle webhooks:
    - Verify signature using raw body + Paddle-Signature header :contentReference[oaicite:8]{index=8}
    - Read event_type, occurred_at, and data :contentReference[oaicite:9]{index=9}
    - Update subscription in our DB
    """
    raw = request.get_data(cache=False)
    sig = request.headers.get("Paddle-Signature", "")

    if not verify_paddle_signature(raw, sig):
        abort(401)

    event = request.get_json(force=True, silent=False)

    # Payload shape can vary by endpoint/SDK; support both:
    # - { event_type, occurred_at, data, ... }
    # - { payload: { event_type, occurred_at, data, ... } }
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        e_type = payload.get("event_type")
        occurred_at = payload.get("occurred_at")
        data = payload.get("data") or {}
        event_id = payload.get("event_id")
    else:
        e_type = event.get("event_type")
        occurred_at = event.get("occurred_at")
        data = event.get("data") or {}
        event_id = event.get("event_id")

    occurred_dt = _parse_rfc3339(occurred_at)

    if e_type and e_type.startswith("subscription."):
        _handle_subscription_event(e_type, data, occurred_dt, event_id)

    return jsonify(ok=True)


def _handle_subscription_event(event_type: str, sub_data: Dict[str, Any], occurred_dt: Optional[datetime], event_id: Optional[str]):
    """
    subscription.* events include the subscription entity in `data` and
    contain fields like `status`, `next_billed_at`, and `current_billing_period`. :contentReference[oaicite:10]{index=10}
    """
    sub_id = str(sub_data.get("id", "")).strip()
    status = str(sub_data.get("status", "")).strip()

    if not sub_id or not status:
        return

    # Identify user using custom_data.user_id (recommended because it stays stable)
    custom_data = sub_data.get("custom_data") or {}
    user_id = str(custom_data.get("user_id", "")).strip()

    user = None
    if user_id:
        user = db.session.get(User, user_id)

    # Fallback: match by email if present
    if not user:
        email = (custom_data.get("user_email") or "").strip().lower()
        if email:
            user = User.query.filter_by(email=email).first()

    if not user:
        return

    # Determine which price is on the subscription (usually the first item)
    price_id = None
    items = sub_data.get("items") or []
    if isinstance(items, list) and items:
        first = items[0] or {}
        if isinstance(first, dict):
            if isinstance(first.get("price"), dict):
                price_id = first["price"].get("id")
            if not price_id:
                price_id = first.get("price_id")

    next_billed_at = _parse_rfc3339(sub_data.get("next_billed_at"))

    period_start = None
    period_end = None
    cbp = sub_data.get("current_billing_period")
    if isinstance(cbp, dict):
        period_start = _parse_rfc3339(cbp.get("starts_at"))
        period_end = _parse_rfc3339(cbp.get("ends_at"))

    existing = Subscription.query.filter_by(paddle_subscription_id=sub_id).first()

    # Paddle can deliver webhooks out of order; use occurred_at to ignore older updates. :contentReference[oaicite:11]{index=11}
    if existing and existing.last_event_occurred_at and occurred_dt:
        if occurred_dt <= existing.last_event_occurred_at:
            return

    if not existing:
        existing = Subscription(
            user_id=user.id,
            provider="paddle",
            paddle_subscription_id=sub_id,
            status=status,
        )
        db.session.add(existing)

    existing.status = status
    if price_id:
        existing.price_id = price_id
    existing.next_billed_at = next_billed_at
    existing.current_period_start = period_start
    existing.current_period_end = period_end
    existing.last_event_occurred_at = occurred_dt

    db.session.commit()