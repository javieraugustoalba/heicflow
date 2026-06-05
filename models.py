import uuid
from flask_login import UserMixin
from datetime import datetime, date

from db import db


def utcnow():
    return datetime.utcnow()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=True)
    google_sub = db.Column(db.String(200), unique=True, nullable=True, index=True)
    picture_url = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    subscriptions = db.relationship("Subscription", backref="user", lazy=True)
    usages = db.relationship("UsagePeriod", backref="user", lazy=True)


class Subscription(db.Model):
    """Tracks Paddle subscription state for a user."""

    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)

    provider = db.Column(db.String(50), nullable=False, default="paddle")

    paddle_subscription_id = db.Column(db.String(200), unique=True, nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False)

    price_id = db.Column(db.String(200), nullable=True, index=True)

    current_period_start = db.Column(db.DateTime, nullable=True)
    current_period_end = db.Column(db.DateTime, nullable=True)
    next_billed_at = db.Column(db.DateTime, nullable=True)

    last_event_occurred_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class UsagePeriod(db.Model):
    """Tracks how many conversion credits a logged-in user used in a period."""

    __tablename__ = "usage_periods"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)

    period_start = db.Column(db.DateTime, nullable=False, index=True)
    period_end = db.Column(db.DateTime, nullable=False, index=True)

    quota = db.Column(db.Integer, nullable=False)
    used = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "period_start", "period_end", name="uq_usage_user_period"),
    )


class AnonymousUsage(db.Model):
    """
    Daily ad-supported usage counters for public visitors.

    We store hashed identifiers, not raw IPs. Scopes:
    - visitor: anonymous cookie id
    - ip: request IP hash
    - fingerprint: light technical fingerprint hash
    """

    __tablename__ = "anonymous_usages"

    id = db.Column(db.Integer, primary_key=True)
    usage_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    scope = db.Column(db.String(30), nullable=False, index=True)
    key_hash = db.Column(db.String(128), nullable=False, index=True)

    files_converted = db.Column(db.Integer, nullable=False, default=0)
    mb_converted = db.Column(db.Float, nullable=False, default=0.0)
    requests_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("usage_date", "scope", "key_hash", name="uq_anon_usage_day_scope_key"),
    )


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    provider = db.Column(db.String(50), nullable=False)  # "epayco"
    provider_transaction_id = db.Column(db.String(200), unique=True, nullable=False, index=True)
    ref_payco = db.Column(db.String(200), nullable=True, index=True)

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    plan_code = db.Column(db.String(50), nullable=True)
    quota = db.Column(db.Integer, nullable=True)

    amount = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(10), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="approved")  # approved/rejected/pending

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
