from flask import Blueprint, current_app, redirect, request, session, url_for
from flask_login import LoginManager, login_user, logout_user
from authlib.integrations.flask_client import OAuth

from db import db
from models import User

auth_bp = Blueprint("auth", __name__)

login_manager = LoginManager()
oauth = OAuth()


@login_manager.user_loader
def load_user(user_id: str):
    # Loads the user from the database for Flask-Login sessions
    return db.session.get(User, user_id)


def init_auth(app):
    """
    Initializes:
    - Flask-Login session handling
    - Authlib OAuth client
    - Google OpenID Connect registration (if keys exist)
    """
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    oauth.init_app(app)

    google_id = app.config.get("GOOGLE_CLIENT_ID")
    google_secret = app.config.get("GOOGLE_CLIENT_SECRET")

    # Only register Google if the environment variables exist
    if google_id and google_secret:
        oauth.register(
            name="google",
            client_id=google_id,
            client_secret=google_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def _google_client():
    """
    Returns the configured Google OAuth client.
    If Google wasn't registered (missing env vars), returns None.
    """
    return oauth.create_client("google")


@auth_bp.get("/login")
def login():
    """
    Starts Google OAuth flow.
    """
    client = _google_client()
    if client is None:
        return redirect(url_for("pricing", err="Google login not configured (missing GOOGLE_CLIENT_ID/SECRET)."))

    # Flask builds a callback URL based on the request host. Behind proxies (Azure),
    # PUBLIC_BASE_URL forces the correct public HTTPS URL.
    redirect_uri = url_for("auth.callback", _external=True)

    public_base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if public_base:
        redirect_uri = public_base + url_for("auth.callback")

    return client.authorize_redirect(redirect_uri)


@auth_bp.get("/auth/callback")
def callback():
    """
    Finishes Google OAuth flow and creates/logs in local user.
    """
    client = _google_client()
    if client is None:
        return redirect(url_for("pricing", err="Google login not configured (missing GOOGLE_CLIENT_ID/SECRET)."))

    token = client.authorize_access_token()

    # Authlib may include userinfo directly; otherwise fetch it
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = client.get("userinfo").json()

    email = (userinfo.get("email") or "").strip().lower()
    sub = (userinfo.get("sub") or "").strip()
    name = (userinfo.get("name") or "").strip()
    picture = (userinfo.get("picture") or "").strip()

    if not email or not sub:
        return redirect(url_for("pricing", err="Google login failed. Try again."))

    # Find existing user by Google subject OR by email
    user = User.query.filter((User.google_sub == sub) | (User.email == email)).first()

    if not user:
        user = User(email=email, google_sub=sub, name=name, picture_url=picture)
        db.session.add(user)
    else:
        user.email = email
        user.google_sub = sub
        if name:
            user.name = name
        if picture:
            user.picture_url = picture

    db.session.commit()

    login_user(user)

    # After login, go back to where user came from or home
    next_url = request.args.get("next") or url_for("index")
    return redirect(next_url)


@auth_bp.post("/logout")
def logout():
    """
    Logs the user out and clears the session.
    """
    logout_user()
    session.clear()
    return redirect(url_for("index"))