"""Provision or rotate a governed user through an explicit administrative command."""

from __future__ import annotations

import asyncio
import os

from passlib.context import CryptContext
from sqlalchemy import text

from backend.db import SessionLocal


ROLES = {"INVESTOR", "REVIEWER", "DATA_OPS", "BROKER_OPS", "ADMIN"}


async def main() -> None:
    if os.getenv("ALLOW_USER_PROVISION") != "1" or os.getenv("BOOTSTRAP_ACKNOWLEDGEMENT") != "create-initial-admin":
        raise RuntimeError("Set ALLOW_USER_PROVISION=1 and BOOTSTRAP_ACKNOWLEDGEMENT=create-initial-admin for this explicit operation")
    username = (os.environ.get("PROVISION_USERNAME") or os.environ.get("PROVISION_ADMIN_EMAIL", "")).strip()
    email = (os.environ.get("PROVISION_EMAIL") or os.environ.get("PROVISION_ADMIN_EMAIL", "")).strip().lower()
    password = os.environ.get("PROVISION_PASSWORD") or os.environ.get("PROVISION_ADMIN_PASSWORD", "")
    role = (os.environ.get("PROVISION_ROLE") or "ADMIN").strip().upper()
    if len(username) < 3 or "@" not in email or len(password) < 12 or role not in ROLES:
        raise RuntimeError("username, email, 12+ character password, and a valid role are required")
    hashed = CryptContext(schemes=["bcrypt"], deprecated="auto").hash(password)
    async with SessionLocal() as db, db.begin():
        existing = await db.execute(
            text("SELECT 1 FROM app_users WHERE username=:username OR email=:email"),
            {"username": username, "email": email},
        )
        if existing.first():
            raise RuntimeError("Refusing to replace an existing provisioned account")
        await db.execute(
            text("""
                INSERT INTO app_users(username,email,hashed_password,role,is_active,token_version)
                VALUES (:username,:email,:password,:role,TRUE,1)
            """),
            {"username": username, "email": email, "password": hashed, "role": role},
        )
    print(f"provisioned {username} as {role}; existing sessions were revoked")


if __name__ == "__main__":
    asyncio.run(main())
