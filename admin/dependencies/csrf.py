"""
Управление CSRF токенами
"""
import secrets
from fastapi import Request


def generate_csrf_token() -> str:
    """Generate a secure CSRF token"""
    return secrets.token_urlsafe(32)


def validate_csrf_token(request: Request, token: str) -> bool:
    """Validate CSRF token from session"""
    session_token = request.session.get("csrf_token")
    if not session_token or not token or session_token != token:
        return False
    return True


def get_csrf_token(request: Request) -> str:
    """Get or generate CSRF token for session"""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = generate_csrf_token()
    return request.session["csrf_token"]

