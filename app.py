import hashlib
import hmac
import io
import os
import uuid
from datetime import date, datetime

from dotenv import load_dotenv
from flask import Flask, g, jsonify, make_response, render_template, request, send_file
from flask_login import current_user, login_required
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import auth_bp, init_auth
from billing import billing_bp
from config import Config
from converter import build_zip, convert_image_bytes, safe_stem
from db import db
from epayco_billing import epayco_bp
from models import AnonymousUsage, Subscription, UsagePeriod

load_dotenv()


def _month_window(now: datetime):
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _get_active_subscription():
    """Allow access for status 'active' or 'past_due' for paid provisioning."""
    if not current_user.is_authenticated:
        return None

    sub = (
        Subscription.query.filter_by(user_id=current_user.id, provider="paddle")
        .order_by(Subscription.updated_at.desc())
        .first()
    )
    if not sub:
        return None

    if sub.status in {"active", "past_due"}:
        return sub

    return None


def _get_plan_quota_for_price_id(price_id: str):
    for p in Config.load_plans():
        if p["price_id"] == price_id:
            return p["monthly_quota"], p["name"]
    return None, None


def _get_active_paid_usage_period():
    """Returns an active paid UsagePeriod, usually created by ePayco packs."""
    if not current_user.is_authenticated:
        return None

    now = datetime.utcnow()

    return (
        UsagePeriod.query.filter_by(user_id=current_user.id)
        .filter(UsagePeriod.period_start <= now, UsagePeriod.period_end > now)
        .order_by(UsagePeriod.quota.desc(), UsagePeriod.period_end.desc())
        .first()
    )


def _current_entitlement():
    """
    Decides the quota + time window for the logged-in user.

    Priority:
    1) Paddle active subscription
    2) ePayco/paid pack active UsagePeriod
    3) Free monthly plan
    """
    now = datetime.utcnow()

    sub = _get_active_subscription()
    if sub and sub.price_id:
        quota, plan_name = _get_plan_quota_for_price_id(sub.price_id)
        if quota and sub.current_period_start and sub.current_period_end:
            return {
                "plan_name": plan_name,
                "quota": int(quota),
                "period_start": sub.current_period_start,
                "period_end": sub.current_period_end,
                "is_paid": True,
                "next_billed_at": sub.next_billed_at,
                "subscription_status": sub.status,
                "usage_id": None,
            }

    paid_usage = _get_active_paid_usage_period()
    if paid_usage and int(paid_usage.quota) > int(Config.FREE_MONTHLY_QUOTA):
        plan_name = "Paid"
        for p in Config.load_plans():
            if int(p.get("monthly_quota", 0)) == int(paid_usage.quota):
                plan_name = p.get("name", "Paid")
                break

        return {
            "plan_name": plan_name,
            "quota": int(paid_usage.quota),
            "period_start": paid_usage.period_start,
            "period_end": paid_usage.period_end,
            "is_paid": True,
            "next_billed_at": None,
            "subscription_status": "active",
            "usage_id": paid_usage.id,
        }

    start, end = _month_window(now)
    return {
        "plan_name": "Free",
        "quota": int(Config.FREE_MONTHLY_QUOTA),
        "period_start": start,
        "period_end": end,
        "is_paid": False,
        "next_billed_at": None,
        "subscription_status": None,
        "usage_id": None,
    }


def _get_or_create_usage(entitlement):
    """Returns the UsagePeriod that should count logged-in consumption."""
    usage_id = entitlement.get("usage_id")
    if usage_id:
        return db.session.get(UsagePeriod, usage_id)

    usage = UsagePeriod.query.filter_by(
        user_id=current_user.id,
        period_start=entitlement["period_start"],
        period_end=entitlement["period_end"],
    ).first()

    if not usage:
        usage = UsagePeriod(
            user_id=current_user.id,
            period_start=entitlement["period_start"],
            period_end=entitlement["period_end"],
            quota=entitlement["quota"],
            used=0,
        )
        db.session.add(usage)
        db.session.commit()

    if usage.quota != entitlement["quota"]:
        usage.quota = entitlement["quota"]
        db.session.commit()

    return usage


def _hash_value(value: str) -> str:
    secret = Config.SECRET_KEY or "dev-secret-change-me"
    return hmac.new(secret.encode("utf-8"), (value or "").encode("utf-8"), hashlib.sha256).hexdigest()


def _get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _light_fingerprint(ip: str) -> str:
    user_agent = request.headers.get("User-Agent", "")[:300]
    accept_language = request.headers.get("Accept-Language", "")[:80]
    # Keep this intentionally light. It is only an abuse signal, not strong identification.
    return f"{user_agent}|{accept_language}|{ip}"


def _get_or_create_anon_usage(scope: str, key_hash: str) -> AnonymousUsage:
    today = date.today()
    usage = AnonymousUsage.query.filter_by(usage_date=today, scope=scope, key_hash=key_hash).first()
    if not usage:
        usage = AnonymousUsage(usage_date=today, scope=scope, key_hash=key_hash)
        db.session.add(usage)
        db.session.flush()
    return usage


def _uploaded_file_size(file_storage) -> int:
    stream = file_storage.stream
    try:
        pos = stream.tell()
    except Exception:
        pos = 0
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(pos or 0)
    return int(size or 0)


def _validate_files(files, max_files: int):
    clean = [f for f in files if (f.filename or "").strip()]
    if not clean:
        return None, jsonify(error="Please choose at least one file."), 400
    if len(clean) > max_files:
        return None, jsonify(error=f"Max {max_files} files per batch."), 400

    total_bytes = 0
    for f in clean:
        size = _uploaded_file_size(f)
        total_bytes += size
        f.stream.seek(0)
        if size <= 0:
            return None, jsonify(error=f"{f.filename} appears to be empty."), 400
        if size > Config.MAX_FILE_SIZE_BYTES:
            mb = Config.MAX_FILE_SIZE_BYTES / (1024 * 1024)
            return None, jsonify(error=f"{f.filename} is too large. Max {mb:.0f} MB per file."), 400

    return (clean, total_bytes / (1024 * 1024)), None, None


def _check_guest_allowance(files_count: int, mb_count: float):
    visitor_hash = _hash_value(getattr(g, "visitor_id", ""))
    ip = _get_client_ip()
    ip_hash = _hash_value(ip)
    fingerprint_hash = _hash_value(_light_fingerprint(ip))

    visitor_usage = _get_or_create_anon_usage("visitor", visitor_hash)
    ip_usage = _get_or_create_anon_usage("ip", ip_hash)
    fingerprint_usage = _get_or_create_anon_usage("fingerprint", fingerprint_hash)

    if visitor_usage.files_converted + files_count > Config.GUEST_DAILY_FILES:
        remaining = max(0, Config.GUEST_DAILY_FILES - visitor_usage.files_converted)
        return None, (
            jsonify(
                error=f"Free daily limit reached. You have {remaining} guest conversions left today. Create a free account or upgrade for more.",
                code="guest_quota_exceeded",
            ),
            402,
        )

    if visitor_usage.mb_converted + mb_count > Config.GUEST_DAILY_MB:
        remaining = max(0, Config.GUEST_DAILY_MB - visitor_usage.mb_converted)
        return None, (
            jsonify(
                error=f"Free daily MB limit reached. You have {remaining:.1f} MB left today. Create a free account or upgrade for larger batches.",
                code="guest_mb_exceeded",
            ),
            402,
        )

    if ip_usage.files_converted + files_count > Config.GUEST_IP_DAILY_FILES:
        return None, (
            jsonify(error="Too many free conversions from this network today. Please create an account or try again later.", code="network_limit"),
            429,
        )

    if ip_usage.mb_converted + mb_count > Config.GUEST_IP_DAILY_MB:
        return None, (
            jsonify(error="Too much free traffic from this network today. Please create an account or try again later.", code="network_mb_limit"),
            429,
        )

    return (visitor_usage, ip_usage, fingerprint_usage), None


def _increment_guest_usage(usages, files_count: int, mb_count: float):
    for usage in usages:
        usage.files_converted += files_count
        usage.mb_converted += mb_count
        usage.requests_count += 1
    db.session.commit()


def _render_converter(default_format="jpg"):
    plans = Config.load_plans()
    user = current_user if current_user.is_authenticated else None
    entitlement = None
    usage = None
    is_paid = False

    if user and user.is_authenticated:
        entitlement = _current_entitlement()
        usage = _get_or_create_usage(entitlement)
        is_paid = bool(entitlement.get("is_paid"))

    return render_template(
        "index.html",
        plans=plans,
        paddle_client_token=Config.PADDLE_CLIENT_TOKEN,
        user=user,
        entitlement=entitlement,
        usage=usage,
        is_paid=is_paid,
        max_files=Config.MAX_FILES_PER_BATCH if is_paid else (Config.MAX_FILES_PER_BATCH if user and user.is_authenticated else Config.GUEST_MAX_FILES_PER_BATCH),
        guest_daily_files=Config.GUEST_DAILY_FILES,
        guest_daily_mb=Config.GUEST_DAILY_MB,
        free_quota=Config.FREE_MONTHLY_QUOTA,
        default_format=default_format,
    )


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config["PLANS"] = Config.load_plans()
    app.register_blueprint(epayco_bp)

    db.init_app(app)
    with app.app_context():
        db.create_all()

    init_auth(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(billing_bp)

    @app.context_processor
    def inject_globals():
        return {
            "app_name": Config.APP_NAME,
            "public_base_url": Config.PUBLIC_BASE_URL,
            "contact_email": Config.CONTACT_EMAIL,
            "adsense_client": Config.ADSENSE_CLIENT,
            "adsense_slot_top": Config.ADSENSE_SLOT_TOP,
            "adsense_slot_after_convert": Config.ADSENSE_SLOT_AFTER_CONVERT,
            "adsense_slot_footer": Config.ADSENSE_SLOT_FOOTER,
        }

    @app.before_request
    def visitor_cookie():
        visitor_id = request.cookies.get("heicflow_vid", "").strip()
        if not visitor_id or len(visitor_id) > 80:
            visitor_id = str(uuid.uuid4())
            g.set_visitor_cookie = True
        else:
            g.set_visitor_cookie = False
        g.visitor_id = visitor_id

    @app.after_request
    def set_visitor_cookie(resp):
        if getattr(g, "set_visitor_cookie", False):
            resp.set_cookie(
                "heicflow_vid",
                getattr(g, "visitor_id", ""),
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                secure=request.is_secure,
                samesite="Lax",
            )
        return resp

    @app.get("/")
    def index():
        return _render_converter("jpg")

    @app.get("/heic-to-jpg")
    def heic_to_jpg():
        return _render_converter("jpg")

    @app.get("/heic-to-png")
    def heic_to_png():
        return _render_converter("png")

    @app.get("/heic-to-webp")
    def heic_to_webp():
        return _render_converter("webp")

    @app.get("/pricing")
    def pricing():
        plans = app.config["PLANS"]
        user = current_user if current_user.is_authenticated else None
        entitlement = None
        usage = None

        if user and user.is_authenticated:
            entitlement = _current_entitlement()
            usage = _get_or_create_usage(entitlement)

        err = request.args.get("err")

        return render_template(
            "pricing.html",
            plans=plans,
            paddle_client_token=app.config.get("PADDLE_CLIENT_TOKEN", ""),
            user=user,
            entitlement=entitlement,
            usage=usage,
            error=err,
            free_quota=app.config["FREE_MONTHLY_QUOTA"],
        )

    @app.get("/account")
    @login_required
    def account():
        plans = app.config["PLANS"]
        entitlement = _current_entitlement()
        usage = _get_or_create_usage(entitlement)

        return render_template(
            "account.html",
            plans=plans,
            user=current_user,
            entitlement=entitlement,
            usage=usage,
        )

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html", title="Privacy Policy")

    @app.get("/terms")
    def terms():
        return render_template("terms.html", title="Terms of Use")

    @app.get("/contact")
    def contact():
        return render_template("contact.html", title="Contact")

    @app.get("/about")
    def about():
        return render_template("about.html", title="About")

    @app.get("/robots.txt")
    def robots():
        body = "User-agent: *\nAllow: /\nSitemap: "
        base = Config.PUBLIC_BASE_URL or request.url_root.rstrip("/")
        body += f"{base}/sitemap.xml\n"
        resp = make_response(body)
        resp.mimetype = "text/plain"
        return resp

    @app.get("/sitemap.xml")
    def sitemap():
        base = Config.PUBLIC_BASE_URL or request.url_root.rstrip("/")
        urls = ["/", "/heic-to-jpg", "/heic-to-png", "/heic-to-webp", "/pricing", "/privacy", "/terms", "/contact", "/about"]
        xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url in urls:
            xml.append(f"  <url><loc>{base}{url}</loc></url>")
        xml.append("</urlset>")
        resp = make_response("\n".join(xml))
        resp.mimetype = "application/xml"
        return resp

    @app.post("/convert")
    def convert():
        out_fmt = (request.form.get("format") or "").lower().strip()
        if out_fmt not in {"jpg", "jpeg", "png", "webp"}:
            return jsonify(error="Invalid output format."), 400

        quality = (request.form.get("quality") or "standard").lower().strip()
        if quality not in {"standard", "high", "max"}:
            return jsonify(error="Invalid quality level."), 400

        is_logged = current_user.is_authenticated
        entitlement = None
        usage = None
        is_paid = False

        if is_logged:
            entitlement = _current_entitlement()
            usage = _get_or_create_usage(entitlement)
            is_paid = bool(entitlement.get("is_paid"))

        if quality != "standard" and not is_paid:
            return (
                jsonify(
                    error="High and maximum quality conversion are Pro features. Use standard quality for free or upgrade to Pro.",
                    code="quality_requires_pro",
                ),
                402,
            )

        max_files = Config.MAX_FILES_PER_BATCH if is_logged else Config.GUEST_MAX_FILES_PER_BATCH
        validated, error_resp, status = _validate_files(request.files.getlist("files"), max_files)
        if error_resp is not None:
            return error_resp, status

        files, total_mb = validated
        needed = len(files)

        guest_usages = None
        if is_logged:
            remaining = max(0, usage.quota - usage.used)
            if needed > remaining:
                return (
                    jsonify(
                        error=f"Not enough credits. You need {needed}, you have {remaining} left.",
                        code="quota_exceeded",
                    ),
                    402,
                )
        else:
            check_result, quota_error = _check_guest_allowance(needed, total_mb)
            if quota_error:
                return quota_error
            guest_usages = check_result

        converted = []
        try:
            for f in files:
                f.stream.seek(0)
                data, ext, mimetype = convert_image_bytes(f, out_fmt, quality=quality)
                filename = f"{safe_stem(f.filename)}.{ext}"
                converted.append((filename, data, mimetype))
        except Exception:
            return jsonify(error="Could not read/convert one of the files. Try different HEIC/HEIF photos."), 400

        if is_logged:
            usage.used += needed
            db.session.commit()
        else:
            _increment_guest_usage(guest_usages, needed, total_mb)

        if len(converted) == 1:
            filename, data, mimetype = converted[0]
            return send_file(
                io.BytesIO(data),
                mimetype=mimetype,
                as_attachment=True,
                download_name=filename,
                max_age=0,
            )

        zip_buf = build_zip([(name, data) for name, data, _ in converted])
        return send_file(
            zip_buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name="converted.zip",
            max_age=0,
        )

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=True)
