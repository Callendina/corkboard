"""Auth dependency — reads X-Gatekeeper-* headers set by Caddy forward_auth."""
from dataclasses import dataclass
from fastapi import Request


@dataclass
class RequestUser:
    email: str          # empty string if anon
    role: str           # "admin", "user", "anon"
    is_admin: bool
    display_name: str   # self-view display name


def get_current_user(request: Request) -> RequestUser:
    email = request.headers.get("x-gatekeeper-user", "")
    role = request.headers.get("x-gatekeeper-role", "")
    is_system_admin = request.headers.get("x-gatekeeper-system-admin", "") == "true"

    if not email:
        return RequestUser(email="", role="anon", is_admin=False, display_name="Anonymous")

    is_admin = is_system_admin or role == "admin"
    effective_role = "admin" if is_admin else (role or "user")

    # Simple display name from email
    parts = email.split("@")
    if len(parts) == 2:
        local, domain = parts
        display_name = f"{local[0]}***@{domain[0]}***.{domain.rsplit('.', 1)[-1]}"
    else:
        display_name = email

    return RequestUser(
        email=email,
        role=effective_role,
        is_admin=is_admin,
        display_name=display_name,
    )
