"""
Autenticación JWT con dos roles: admin y proveedor.
Las contraseñas se guardan hasheadas con bcrypt.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from database import get_db, username_a_email
from models import LoginRequest, LoginResponse, UserInfo

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()

_default_secret = secrets.token_urlsafe(64)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", _default_secret)
if not os.getenv("JWT_SECRET_KEY"):
    print("ADVERTENCIA: JWT_SECRET_KEY no configurada. Tokens no sobrevivirán reinicios.")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.id, u.email, u.password_hash, u.rol, u.proveedor_id, p.nombre as prov_nombre
            FROM usuarios u
            LEFT JOIN proveedores p ON p.id = u.proveedor_id
            WHERE u.email = ? AND u.activo = 1
            """,
            (username_a_email(data.email),),
        )
        row = cur.fetchone()

    if not row or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(row["id"]),
        "email": row["email"],
        "rol": row["rol"],
        "proveedor_id": row["proveedor_id"],
        "exp": expire,
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    return LoginResponse(
        token=token,
        rol=row["rol"],
        email=row["email"],
        proveedor_id=row["proveedor_id"],
        proveedor_nombre=row["prov_nombre"],
    )


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserInfo:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return UserInfo(
            user_id=int(payload["sub"]),
            email=payload["email"],
            rol=payload["rol"],
            proveedor_id=payload.get("proveedor_id"),
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")


def require_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.rol != "admin":
        raise HTTPException(status_code=403, detail="Se requiere rol admin")
    return user


def require_proveedor(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.rol != "proveedor" or not user.proveedor_id:
        raise HTTPException(status_code=403, detail="Se requiere rol proveedor")
    return user


@router.get("/me")
def me(user: UserInfo = Depends(get_current_user)):
    return user
