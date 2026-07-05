"""Аутентификация и управление пользователями (ТЗ: соответствие политикам ИБ).

Пользователи хранятся узлами :User в Neo4j; пароли — bcrypt (никогда не в
открытом виде и не в логах). Сессии — stateless JWT (HS256). Роли — те же 5,
что и в RBAC поиска. Админ проекта создаёт/меняет/удаляет пользователей.

Функции чистые (принимают neo4j driver) — тестируются и переиспользуются в
src/api.py (REST) и src/app.py (Streamlit-логин).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
import jwt

from src.config import env, nfc

ROLES = ["researcher", "analyst", "project_lead", "admin", "external_partner"]
ADMIN_ROLES = {"admin"}                      # кто может управлять пользователями

_JWT_SECRET = env("JWT_SECRET", "nornickel-kg-dev-secret-change-in-prod")
_JWT_ALG = "HS256"
_TOKEN_TTL_H = int(env("JWT_TTL_HOURS", "12"))


# ── пароли ───────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """bcrypt-хеш (соль внутри). bcrypt берёт максимум 72 байта — усечём явно."""
    pw = (password or "").encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw((password or "").encode("utf-8")[:72],
                              (password_hash or "").encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT ──────────────────────────────────────────────────────────────────────
def issue_token(username: str, role: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": username, "role": role, "iat": now,
               "exp": now + _dt.timedelta(hours=_TOKEN_TTL_H)}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALG)


def decode_token(token: str) -> dict | None:
    """Валидный payload {sub, role, exp} или None (истёк/подделан/пуст)."""
    if not token:
        return None
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALG])
    except jwt.PyJWTError:
        return None


def is_admin(role: str) -> bool:
    return role in ADMIN_ROLES


# ── схема + CRUD пользователей (Neo4j) ───────────────────────────────────────
def ensure_schema(drv) -> None:
    with drv.session() as s:
        s.run("CREATE CONSTRAINT user_username IF NOT EXISTS "
              "FOR (u:User) REQUIRE u.username IS UNIQUE")


def _row_to_user(r) -> dict:
    """Публичная форма пользователя — БЕЗ password_hash."""
    return {"username": r["username"], "role": r["role"],
            "full_name": r.get("full_name"), "active": r.get("active", True),
            "created_at": r.get("created_at"), "created_by": r.get("created_by")}


def get_user(drv, username: str, with_hash: bool = False):
    with drv.session() as s:
        rec = s.run("MATCH (u:User {username:$u}) RETURN u", u=nfc(username)).single()
    if not rec:
        return None
    u = dict(rec["u"])
    return u if with_hash else _row_to_user(u)


def list_users(drv) -> list:
    with drv.session() as s:
        return [_row_to_user(dict(r["u"])) for r in
                s.run("MATCH (u:User) RETURN u ORDER BY u.username")]


def create_user(drv, username: str, password: str, role: str,
                created_by: str = "system", full_name: str = "") -> dict:
    username = nfc((username or "").strip())
    if not username or not password:
        raise ValueError("username и password обязательны")
    if role not in ROLES:
        raise ValueError(f"неизвестная роль: {role}")
    if len(password) < 6:
        raise ValueError("пароль минимум 6 символов")
    with drv.session() as s:
        if s.run("MATCH (u:User {username:$u}) RETURN u", u=username).single():
            raise ValueError(f"пользователь {username} уже существует")
        rec = s.run(
            "CREATE (u:User {username:$u, password_hash:$h, role:$r, "
            "full_name:$fn, active:true, created_at:$ts, created_by:$cb}) RETURN u",
            u=username, h=hash_password(password), r=role, fn=nfc(full_name or ""),
            ts=_dt.datetime.now(_dt.timezone.utc).isoformat(), cb=nfc(created_by)).single()
    return _row_to_user(dict(rec["u"]))


def update_user(drv, username: str, role: str | None = None,
                password: str | None = None, active: bool | None = None,
                full_name: str | None = None) -> dict:
    username = nfc(username)
    sets, params = [], {"u": username}
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"неизвестная роль: {role}")
        sets.append("u.role=$role"); params["role"] = role
    if password is not None:
        if len(password) < 6:
            raise ValueError("пароль минимум 6 символов")
        sets.append("u.password_hash=$h"); params["h"] = hash_password(password)
    if active is not None:
        sets.append("u.active=$act"); params["act"] = bool(active)
    if full_name is not None:
        sets.append("u.full_name=$fn"); params["fn"] = nfc(full_name)
    if not sets:
        raise ValueError("нечего обновлять")
    with drv.session() as s:
        rec = s.run(f"MATCH (u:User {{username:$u}}) SET {', '.join(sets)} RETURN u",
                    **params).single()
    if not rec:
        raise ValueError(f"пользователь {username} не найден")
    return _row_to_user(dict(rec["u"]))


def delete_user(drv, username: str) -> bool:
    with drv.session() as s:
        rec = s.run("MATCH (u:User {username:$u}) DETACH DELETE u RETURN count(*) AS c",
                    u=nfc(username)).single()
    return bool(rec and rec["c"])


def authenticate(drv, username: str, password: str):
    """Проверка логина+пароля. Возвращает публичного пользователя или None.
    Неактивные (active=false) не пускаются."""
    u = get_user(drv, username, with_hash=True)
    if not u or not u.get("active", True):
        return None
    if not verify_password(password, u.get("password_hash", "")):
        return None
    return _row_to_user(u)


def seed_admin(drv) -> str | None:
    """Если пользователей нет — создать первого админа. Пароль из env
    ADMIN_PASSWORD (иначе дефолтный с предупреждением). Возвращает сообщение."""
    ensure_schema(drv)
    with drv.session() as s:
        n = s.run("MATCH (u:User) RETURN count(u) AS c").single()["c"]
    if n:
        return None
    pw = env("ADMIN_PASSWORD", "admin123")
    create_user(drv, "admin", pw, "admin", created_by="seed",
                full_name="Администратор проекта")
    warn = "" if env("ADMIN_PASSWORD") else " (ДЕФОЛТНЫЙ — смените ADMIN_PASSWORD!)"
    return f"создан admin/{pw}{warn}"


if __name__ == "__main__":  # смоук без Neo4j: пароли + JWT
    h = hash_password("Секрет123")
    assert verify_password("Секрет123", h) and not verify_password("wrong", h)
    t = issue_token("alice", "admin")
    p = decode_token(t)
    assert p["sub"] == "alice" and p["role"] == "admin" and is_admin(p["role"])
    assert decode_token("garbage") is None
    print("auth self-check OK (bcrypt + JWT)")
