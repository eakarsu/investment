"""Provision or rotate a governed user through an explicit administrative command."""

from __future__ import annotations

import asyncio
import os

import bcrypt
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
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    async with SessionLocal() as db, db.begin():
        existing = await db.execute(
            text("SELECT id,username,email FROM app_users WHERE username=:username OR email=:email"),
            {"username": username, "email": email},
        )
        row = existing.first()
        if row:
            if row.username != username or row.email != email:
                raise RuntimeError("Provisioned username or email belongs to another account")
            await db.execute(
                text("""
                    UPDATE app_users SET hashed_password=:password,role=:role,is_active=TRUE,
                      token_version=token_version+1 WHERE id=:id
                """),
                {"id": row.id, "password": hashed, "role": role},
            )
        else:
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
