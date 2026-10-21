"""知识库：今日认知、交易复盘、失败复盘。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/kb", tags=["knowledge"])


class AdoptRuleBody(BaseModel):
    name: str = ""
    description: str = ""


class PreviewBody(BaseModel):
    trade: dict


class MemoryBody(BaseModel):
    trade_date: str = ""
    facts: dict = {}
    regime: dict = {}
    structure: dict = {}
    events: list = []
    summary: dict = {}
    hypotheses: list = []
    narrative: str = ""


class ValidateBody(BaseModel):
    status: str
    note: str = ""


@router.get("/overview")
async def kb_overview():
    from server.knowledge_review_service import knowledge_review_service
    return knowledge_review_service.overview()


@router.get("/trades")
async def kb_trades(failures: bool = False, view: str = "all"):
    from server.knowledge_review_service import knowledge_review_service
    return knowledge_review_service.list_trades(failures_only=failures, view=view)


@router.post("/trades")
async def kb_trade_create(body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.save_plan(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/execution")
async def kb_trade_execution(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.save_execution(thesis_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/outcome")
async def kb_trade_outcome(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.save_outcome(thesis_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/attribution")
async def kb_trade_attribution(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.save_attribution(thesis_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/lesson")
async def kb_trade_lesson(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.save_lesson(thesis_id, str(body.get("content") or ""))
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/experiment")
async def kb_trade_experiment(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.start_experiment(thesis_id, str(body.get("hypothesis") or ""))
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.put("/trades/{thesis_id}")
async def kb_trade_update(thesis_id: str, body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.update_trade(thesis_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.delete("/trades/{thesis_id}")
async def kb_trade_delete(thesis_id: str):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.delete_trade(thesis_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.get("/trades/{thesis_id}")
async def kb_trade(thesis_id: str):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.get_trade(thesis_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.post("/trades/{thesis_id}/review")
async def kb_review(thesis_id: str):
    from server.knowledge_review_service import knowledge_review_service
    try:
        return await knowledge_review_service.review(thesis_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘失败: {e}")


@router.post("/review-preview")
async def kb_review_preview(body: PreviewBody):
    from server.knowledge_review_service import knowledge_review_service
    try:
        return await knowledge_review_service.preview_review(body.trade)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘失败: {e}")


@router.post("/trades/{thesis_id}/adopt-rule")
async def kb_adopt(thesis_id: str, body: AdoptRuleBody):
    from server.knowledge_review_service import knowledge_review_service
    return knowledge_review_service.adopt_rule(thesis_id, body.name, body.description)


@router.get("/memory")
async def kb_memory(span: str = "all"):
    from server.market_memory_service import market_memory_service
    if span not in ("today", "week", "month", "all"):
        span = "all"
    return market_memory_service.list_days(span)


@router.get("/memory/{trade_date}")
async def kb_memory_day(trade_date: str):
    from server.market_memory_service import market_memory_service
    packed = market_memory_service.get_day(trade_date)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.post("/memory")
async def kb_memory_save(body: MemoryBody):
    from server.market_memory_service import market_memory_service
    try:
        return market_memory_service.save_day(body.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入市场记忆失败: {e}")


@router.post("/memory/hypothesis/{hypothesis_id}/validate")
async def kb_memory_validate(hypothesis_id: str, body: ValidateBody):
    from server.market_memory_service import market_memory_service
    packed = market_memory_service.validate(hypothesis_id, body.status, body.note)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/lessons")
async def kb_lesson_create(body: dict):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.create_lesson(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.delete("/lessons/{lesson_id}")
async def kb_lesson_delete(lesson_id: str):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.delete_lesson(lesson_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.get("/section/{kind}")
async def kb_section(kind: str):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.list_section(kind)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed
