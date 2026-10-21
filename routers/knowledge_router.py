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


@router.get("/sector-brief")
async def kb_sector_brief(name: str, days: int = 5):
    from server.sector_brief_service import sector_brief_service
    packed = await sector_brief_service.load(name, days)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.get("/opportunities")
async def kb_opportunities():
    from server.opportunity_scan_service import opportunity_scan_service
    return opportunity_scan_service.list_items()


@router.post("/opportunities/scan")
async def kb_opportunities_scan():
    from server.opportunity_scan_service import opportunity_scan_service
    backfill = await opportunity_scan_service.fill_missing_via_agent()
    flow = await opportunity_scan_service.fill_missing_moneyflow()
    packed = opportunity_scan_service.scan()
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    review = await opportunity_scan_service.review_picks(packed.get("items") or [])
    packed["backfill"] = backfill
    packed["flow_backfill"] = flow
    packed["review"] = review
    return packed


@router.get("/ability")
async def kb_ability():
    from server.skill_profile_service import skill_profile_service
    return skill_profile_service.profile()


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


@router.get("/rules/catalog")
async def kb_rule_catalog():
    from server.rule_service import rule_service
    return rule_service.catalog()


@router.get("/rules")
async def kb_rules():
    from server.rule_service import rule_service
    return rule_service.list_rules()


@router.post("/rules/observables")
async def kb_rule_observable(body: dict):
    from server.rule_service import rule_service
    packed = rule_service.add_observable(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/rules/compose")
async def kb_rule_compose(body: dict):
    from server.rule_service import rule_service
    packed = await rule_service.compose(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/rules")
async def kb_rule_create(body: dict):
    from server.rule_service import rule_service
    packed = rule_service.create(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.put("/rules/{rule_id}/content")
async def kb_rule_content(rule_id: str, body: dict):
    from server.rule_service import rule_service
    packed = rule_service.revise(rule_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.delete("/rules/{rule_id}")
async def kb_rule_delete(rule_id: str):
    from server.rule_service import rule_service
    packed = rule_service.delete(rule_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.put("/rules/{rule_id}")
async def kb_rule_update(rule_id: str, body: dict):
    from server.rule_service import rule_service
    packed = rule_service.update(rule_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/rules/{rule_id}/pause")
async def kb_rule_pause(rule_id: str):
    from server.rule_service import rule_service
    packed = rule_service.pause(rule_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/rules/{rule_id}/validate")
async def kb_rule_validate(rule_id: str):
    from server.rule_service import rule_service
    packed = rule_service.validate(rule_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/rules/{rule_id}/promote")
async def kb_rule_promote(rule_id: str):
    from server.rule_service import rule_service
    packed = rule_service.promote(rule_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.get("/external/meta")
async def kb_external_meta():
    from server.external_knowledge_service import external_knowledge_service
    return external_knowledge_service.meta()


@router.get("/external")
async def kb_external_list(source_type: str = "", knowledge_type: str = "", evidence_level: str = ""):
    from server.external_knowledge_service import external_knowledge_service
    return external_knowledge_service.list_items(source_type, knowledge_type, evidence_level)


@router.get("/external/{knowledge_id}")
async def kb_external_get(knowledge_id: str):
    from server.external_knowledge_service import external_knowledge_service
    packed = external_knowledge_service.get(knowledge_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed


@router.post("/external/read")
async def kb_external_read(body: dict):
    from server.external_knowledge_service import external_knowledge_service
    packed = await external_knowledge_service.read_and_save(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/external/{knowledge_id}/claims/{claim_id}/experiment")
async def kb_external_experiment(knowledge_id: str, claim_id: str):
    from server.external_knowledge_service import external_knowledge_service
    packed = external_knowledge_service.to_experiment(knowledge_id, claim_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/external/{knowledge_id}/experiences")
async def kb_external_link(knowledge_id: str, body: dict):
    from server.external_knowledge_service import external_knowledge_service
    packed = external_knowledge_service.link_experience(knowledge_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.get("/experiments/meta")
async def kb_experiment_meta():
    from server.experiment_service import experiment_service
    return experiment_service.meta()


@router.get("/experiments")
async def kb_experiments():
    from server.experiment_service import experiment_service
    return experiment_service.list_items()


@router.post("/experiments/backtest")
async def kb_experiment_backtest(body: dict):
    from server.experiment_service import experiment_service
    packed = await experiment_service.backtest_from(str(body.get("kind") or ""), str(body.get("id") or ""))
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/experiments")
async def kb_experiment_create(body: dict):
    from server.experiment_service import experiment_service
    packed = experiment_service.create(body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.put("/experiments/{experiment_id}")
async def kb_experiment_update(experiment_id: str, body: dict):
    from server.experiment_service import experiment_service
    packed = experiment_service.update(experiment_id, body)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/experiments/{experiment_id}/run")
async def kb_experiment_run(experiment_id: str, body: dict):
    from server.experiment_service import experiment_service
    phase = str(body.get("phase") or "")
    current = experiment_service.get(experiment_id)
    mode = ((current.get("item") or {}).get("parameters") or {}).get("mode")
    if phase == "train" or mode == "full_market":
        if mode == "full_market":
            await experiment_service.fill_market(experiment_id)
        elif phase == "train":
            await experiment_service.fill_inputs(experiment_id)
    packed = experiment_service.run(experiment_id, phase)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.post("/experiments/{experiment_id}/candidate-rule")
async def kb_experiment_rule(experiment_id: str):
    from server.experiment_service import experiment_service
    packed = experiment_service.to_candidate_rule(experiment_id)
    if not packed.get("ok"):
        raise HTTPException(status_code=400, detail=packed.get("error"))
    return packed


@router.get("/section/{kind}")
async def kb_section(kind: str):
    from server.knowledge_review_service import knowledge_review_service
    packed = knowledge_review_service.list_section(kind)
    if not packed.get("ok"):
        raise HTTPException(status_code=404, detail=packed.get("error"))
    return packed
