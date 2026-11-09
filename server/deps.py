"""FastAPI 依赖：当前登录用户。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.auth_service import auth_service

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Dict[str, Any]:
    if not cred or not cred.credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        payload = auth_service.decode_token(cred.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    user_id = str(payload.get("sub") or "")
    user = auth_service.get_user_by_id(user_id)
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="账号无效或已停用")
    return user


def get_current_user_id(user: Dict[str, Any] = Depends(get_current_user)) -> str:
    return str(user["id"])
