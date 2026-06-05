import base64
import hashlib
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests
from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required, current_user

from db import db
from models import UsagePeriod, Payment

epayco_bp = Blueprint("epayco", __name__)


def _basic_auth_header(public_key: str, private_key: str) -> str:
    raw = f"{public_key}:{private_key}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def _public_base_url() -> str:
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        base = request.host_url.rstrip("/")
    return base


def _epayco_login_token() -> str:
    pub = (current_app.config.get("EPAYCO_PUBLIC_KEY") or "").strip()
    priv = (current_app.config.get("EPAYCO_PRIVATE_KEY") or "").strip()
    if not pub or not priv:
        raise RuntimeError("Missing EPAYCO_PUBLIC_KEY / EPAYCO_PRIVATE_KEY")

    r = requests.post(
        "https://apify.epayco.co/login",
        headers={
            "Content-Type": "application/json",
            "Authorization": _basic_auth_header(pub, priv),
        },
        timeout=20,
    )
    r.raise_for_status()
    token = r.json().get("token")
    if not token:
        raise RuntimeError("ePayco login did not return token")
    return token


def _find_plan_by_code(plan_code: str) -> Optional[Dict[str, Any]]:
    plan_code = (plan_code or "").strip().lower()
    for p in current_app.config.get("PLANS", []):
        if (p.get("epayco_plan_code") or "").strip().lower() == plan_code:
            return p
    return None


def _grant_credits(user_id: str, plan_code: str, quota: int, amount: int, currency: str, ref_payco: str, tx_id: str):
    # Idempotencia: si ya procesamos esta transacción, no hacemos nada
    existing = Payment.query.filter_by(provider="epayco", provider_transaction_id=tx_id).first()
    if existing:
        return

    pay = Payment(
        provider="epayco",
        provider_transaction_id=tx_id,
        ref_payco=ref_payco,
        user_id=user_id,
        plan_code=plan_code,
        quota=quota,
        amount=amount,
        currency=currency,
        status="approved",
    )
    db.session.add(pay)

    # “Mensual” simple: 30 días desde la compra.
    # Si el usuario ya tiene un pack activo, lo encadenamos para no perder tiempo.
    now = datetime.utcnow().replace(microsecond=0)
    last = (
        UsagePeriod.query.filter_by(user_id=user_id)
        .order_by(UsagePeriod.period_end.desc())
        .first()
    )

    start = now
    if last and last.period_end and last.period_end > start:
        start = last.period_end

    end = (start + timedelta(days=30)).replace(microsecond=0)

    usage = UsagePeriod(
        user_id=user_id,
        period_start=start,
        period_end=end,
        quota=int(quota),
        used=0,
    )
    db.session.add(usage)
    db.session.commit()


def _validate_signature(form: Dict[str, str]) -> bool:
    """
    Firma SHA256:
    sha256(p_cust_id_cliente^p_key^x_ref_payco^x_transaction_id^x_amount^x_currency_code)
    y comparar con x_signature. :contentReference[oaicite:6]{index=6}
    """
    customer_id = (current_app.config.get("EPAYCO_P_CUST_ID_CLIENTE") or "").strip()
    p_key = (current_app.config.get("EPAYCO_P_KEY") or "").strip()
    if not customer_id or not p_key:
        return False

    x_ref_payco = str(form.get("x_ref_payco", "")).strip()
    x_transaction_id = str(form.get("x_transaction_id", "")).strip()
    x_amount = str(form.get("x_amount", "")).strip()
    x_currency = str(form.get("x_currency_code", "")).strip()
    x_signature = str(form.get("x_signature", "")).strip()

    base = f"{customer_id}^{p_key}^{x_ref_payco}^{x_transaction_id}^{x_amount}^{x_currency}"
    expected = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return expected == x_signature


@login_required
@epayco_bp.post("/api/billing/epayco/create-session")
def create_session():
    body = request.get_json(silent=True) or {}
    plan_code = str(body.get("plan_code", "")).strip().lower()
    plan = _find_plan_by_code(plan_code)
    if not plan:
        return jsonify(error="Invalid plan_code."), 400

    amount_cop = int(plan.get("epayco_amount_cop") or 0)
    quota = int(plan.get("monthly_quota") or 0)
    if amount_cop <= 0 or quota <= 0:
        return jsonify(error="Plan missing epayco_amount_cop/monthly_quota."), 400

    token = _epayco_login_token()
    base = _public_base_url()

    invoice = f"HF-{current_user.id[:8]}-{plan_code}-{int(time.time() * 1000)}"

    payload = {
        "checkout_version": "2",
        "name": f"{current_app.config.get('APP_NAME', 'HEICFlow')} - {plan.get('name', plan_code)}",
        "currency": "COP",
        "amount": amount_cop,
        "invoice": invoice,
        "response": f"{base}/payment/epayco/response",
        "confirmation": f"{base}/webhooks/epayco/confirmation",
        "method": "POST",
        "extras": {
            "extra1": current_user.id,      # user_id
            "extra2": plan_code,            # plan_code
            "extra3": str(quota),           # quota
        },
        "billing": {
            "email": current_user.email,
            "name": current_user.name or current_user.email,
        },
        "lang": "ES",
        "country": "CO",
    }

    current_app.logger.info("ePayco session payload: %s", payload)

    r = requests.post(
        "https://apify.epayco.co/payment/session/create",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=20,
    )
    if not r.ok:
        return jsonify(error=f"ePayco session/create failed ({r.status_code}): {r.text}"), 400

    data = r.json()
    session_id = (data.get("data") or {}).get("sessionId")
    if not session_id:
        return jsonify(error=f"ePayco did not return sessionId: {r.text}"), 400

    test_flag = str(current_app.config.get("EPAYCO_TEST", "true")).lower() == "true"
    return jsonify(sessionId=session_id, test=test_flag)


@epayco_bp.get("/payment/epayco/response")
def epayco_response():
    """
    Response NO es lo más confiable, pero sirve perfecto para activar en local
    consultando el endpoint de validación por ref_payco. :contentReference[oaicite:9]{index=9}
    """
    ref_payco = (request.args.get("ref_payco") or "").strip()
    if not ref_payco:
        return "<h3>Falta ref_payco</h3>", 400

    # Validación oficial por referencia ref_payco :contentReference[oaicite:10]{index=10}
    vr = requests.get(
        f"https://secure.epayco.co/validation/v1/reference/{ref_payco}",
        headers={"Content-Type": "application/json"},
        timeout=20,
    )

    if not vr.ok:
        return f"<h3>No pude validar la transacción</h3><pre>{vr.status_code} {vr.text}</pre>", 400

    j = vr.json()
    data = j.get("data") or {}

    # Aprobación: puede venir como x_response="Aceptada"
    x_response = str(data.get("x_response", "")).strip()
    approved = x_response.lower() == "aceptada"

    if not approved:
        return (
            f"<h2>Pago no aprobado</h2>"
            f"<p>Estado: {x_response or 'desconocido'}</p>"
            f"<p><a href='/pricing'>Volver</a></p>"
        )

    user_id = str(data.get("x_extra1", "")).strip()
    plan_code = str(data.get("x_extra2", "")).strip().lower()
    quota_str = str(data.get("x_extra3", "")).strip()

    tx_id = str(data.get("x_transaction_id", "")).strip()
    x_ref_payco = str(data.get("x_ref_payco", ref_payco)).strip()

    amount = int(float(str(data.get("x_amount", "0")).replace(",", ".")) or 0)
    currency = str(data.get("x_currency_code", "COP")).strip() or "COP"

    if not user_id or not plan_code or not tx_id:
        return "<h3>No pude asociar el pago a tu usuario/plan (extras faltantes).</h3>", 400

    quota = int(quota_str) if quota_str.isdigit() else 0
    if quota <= 0:
        plan = _find_plan_by_code(plan_code)
        quota = int(plan.get("monthly_quota") or 0) if plan else 0

    _grant_credits(user_id=user_id, plan_code=plan_code, quota=quota, amount=amount, currency=currency, ref_payco=x_ref_payco, tx_id=tx_id)

    return (
        "<h2>Pago aprobado ✅</h2>"
        "<p>Créditos activados. Ya puedes convertir.</p>"
        "<p><a href='/account'>Ir a mi cuenta</a></p>"
    )


@epayco_bp.route("/webhooks/epayco/confirmation", methods=["POST", "GET"])
def epayco_confirmation():
    """
    Webhook confiable: ePayco lo invoca y puede reintentar. Debe responder 200. :contentReference[oaicite:11]{index=11}
    """
    form = dict(request.values)

    if not _validate_signature(form):
        return "Invalid signature", 400

    x_response = str(form.get("x_response", "")).strip()
    if x_response.lower() != "aceptada":
        return "OK", 200

    user_id = str(form.get("x_extra1", "")).strip()
    plan_code = str(form.get("x_extra2", "")).strip().lower()
    quota_str = str(form.get("x_extra3", "")).strip()

    tx_id = str(form.get("x_transaction_id", "")).strip()
    ref_payco = str(form.get("x_ref_payco", "")).strip()

    amount = int(float(str(form.get("x_amount", "0")).replace(",", ".")) or 0)
    currency = str(form.get("x_currency_code", "COP")).strip() or "COP"

    if not user_id or not plan_code or not tx_id:
        return "OK", 200

    quota = int(quota_str) if quota_str.isdigit() else 0
    if quota <= 0:
        plan = _find_plan_by_code(plan_code)
        quota = int(plan.get("monthly_quota") or 0) if plan else 0

    if quota > 0:
        _grant_credits(user_id=user_id, plan_code=plan_code, quota=quota, amount=amount, currency=currency, ref_payco=ref_payco, tx_id=tx_id)

    return "OK", 200