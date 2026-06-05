import json
import os


class Config:
    """
    App configuration loaded from environment variables.

    Production notes:
    - Keep .env out of Git.
    - Set these values in Azure App Service / Container Apps configuration.
    - Use PostgreSQL for paid accounts and production usage tracking.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Upload safety limits. Keep these conservative while validating demand.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH_BYTES", str(60 * 1024 * 1024)))
    MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_FILE_SIZE_BYTES", str(10 * 1024 * 1024)))

    # Batch limits
    GUEST_MAX_FILES_PER_BATCH = int(os.environ.get("GUEST_MAX_FILES_PER_BATCH", "5"))
    MAX_FILES_PER_BATCH = int(os.environ.get("MAX_FILES_PER_BATCH", "10"))

    # Anonymous/free visitor limits for the ad-supported public converter.
    GUEST_DAILY_FILES = int(os.environ.get("GUEST_DAILY_FILES", "5"))
    GUEST_DAILY_MB = int(os.environ.get("GUEST_DAILY_MB", "25"))
    GUEST_IP_DAILY_FILES = int(os.environ.get("GUEST_IP_DAILY_FILES", "50"))
    GUEST_IP_DAILY_MB = int(os.environ.get("GUEST_IP_DAILY_MB", "250"))

    # Logged-in free quota per month. Pro/packs override this through plans.
    FREE_MONTHLY_QUOTA = int(os.environ.get("FREE_MONTHLY_QUOTA", "50"))

    # Branding / public URL
    APP_NAME = os.environ.get("APP_NAME", "HEICFlow")
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")

    # Google AdSense. Leave empty until the site is approved.
    ADSENSE_CLIENT = os.environ.get("ADSENSE_CLIENT", "")  # Example: ca-pub-1234567890
    ADSENSE_SLOT_TOP = os.environ.get("ADSENSE_SLOT_TOP", "")
    ADSENSE_SLOT_AFTER_CONVERT = os.environ.get("ADSENSE_SLOT_AFTER_CONVERT", "")
    ADSENSE_SLOT_FOOTER = os.environ.get("ADSENSE_SLOT_FOOTER", "")

    # Google OAuth (Login with Gmail)
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # Paddle
    PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")
    PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
    PADDLE_CLIENT_TOKEN = os.environ.get("PADDLE_CLIENT_TOKEN", "")

    # ePayco
    EPAYCO_PUBLIC_KEY = os.environ.get("EPAYCO_PUBLIC_KEY", "")
    EPAYCO_PRIVATE_KEY = os.environ.get("EPAYCO_PRIVATE_KEY", "")
    EPAYCO_P_CUST_ID_CLIENTE = os.environ.get("EPAYCO_P_CUST_ID_CLIENTE", "")
    EPAYCO_P_KEY = os.environ.get("EPAYCO_P_KEY", "")
    EPAYCO_TEST = os.environ.get("EPAYCO_TEST", "true")

    # Plans are configured via JSON so prices/quotas can change without code edits.
    # Example:
    # PLANS_JSON='[
    #   {"name":"Pro","price_id":"pri_xxx","monthly_label":"$19.900 COP/mo","monthly_quota":1000,
    #    "epayco_plan_code":"pro_monthly","epayco_amount_cop":19900},
    #   {"name":"Business","price_id":"pri_yyy","monthly_label":"$99.000 COP/mo","monthly_quota":6000,
    #    "epayco_plan_code":"business_monthly","epayco_amount_cop":99000}
    # ]'
    PLANS_JSON = os.environ.get("PLANS_JSON", "[]")

    @staticmethod
    def load_plans():
        try:
            plans = json.loads(Config.PLANS_JSON)
            if not isinstance(plans, list):
                return []
            normalized = []
            for p in plans:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name", "")).strip()
                price_id = str(p.get("price_id", "")).strip()
                monthly_label = str(p.get("monthly_label", "")).strip()
                monthly_quota = int(p.get("monthly_quota", 0))
                epayco_plan_code = str(p.get("epayco_plan_code", "")).strip()
                epayco_amount_cop = int(p.get("epayco_amount_cop", 0) or 0)
                if name and price_id and monthly_quota > 0:
                    normalized.append(
                        {
                            "name": name,
                            "price_id": price_id,
                            "monthly_label": monthly_label,
                            "monthly_quota": monthly_quota,
                            "epayco_plan_code": epayco_plan_code,
                            "epayco_amount_cop": epayco_amount_cop,
                        }
                    )
            return normalized
        except Exception:
            return []
