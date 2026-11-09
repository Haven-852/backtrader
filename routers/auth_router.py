"""
Auth Router
POST /auth/register
POST /auth/login
GET  /auth/me
GET  /auth/quota
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.auth_service import auth_service
from server.deps import get_current_user
from server.quota_service import quota_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: Optional[str] = Field(None, max_length=80)
    invite_code: Optional[str] = None


class LoginBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/register")
async def register(body: RegisterBody):
    try:
        auth_service.ensure()
        result = auth_service.register(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            invite_code=body.invite_code,
        )
        quota_service.ensure()
        result["quota"] = quota_service.today_usage(result["user"]["id"])
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"注册失败: {e}") from e


@router.post("/login")
async def login(body: LoginBody):
    try:
        auth_service.ensure()
        result = auth_service.login(body.email, body.password)
        quota_service.ensure()
        result["quota"] = quota_service.today_usage(result["user"]["id"])
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登录失败: {e}") from e


@router.get("/me")
async def me(user=Depends(get_current_user)):
    try:
        data = auth_service.me(user["id"])
        quota_service.ensure()
        data["quota"] = quota_service.today_usage(user["id"])
        return {"ok": True, **data}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/quota")
async def quota(user=Depends(get_current_user)):
    try:
        quota_service.ensure()
        return {"ok": True, "quota": quota_service.today_usage(user["id"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
