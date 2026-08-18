# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Wiki HTTP endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.responses import response_from_result
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest

router = APIRouter(prefix="/api/v1", tags=["wiki"])


class BuildWikiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_uris: list[str] = Field(min_length=1)
    wiki_root_uri: str = "viking://wiki/"
    card_input_mode: Literal["summary", "raw_chunk"] = "summary"
    max_card_input_chars: int = 20000
    telemetry: TelemetryRequest = False


class ClearWikiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wiki_root_uri: str = "viking://wiki/"
    telemetry: TelemetryRequest = False


@router.post("/wiki/build")
async def build_wiki(
    request: BuildWikiRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()

    async def _build() -> dict:
        return await service.wiki.build_wiki(
            resource_uris=request.resource_uris,
            ctx=_ctx,
            wiki_root_uri=request.wiki_root_uri,
            card_input_mode=request.card_input_mode,
            max_card_input_chars=request.max_card_input_chars,
        )

    execution = await run_operation(
        operation="wiki.build",
        telemetry=request.telemetry,
        fn=_build,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)


@router.post("/wiki/clear")
async def clear_wiki(
    request: ClearWikiRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    service = get_service()

    async def _clear() -> dict:
        return await service.wiki.clear_wiki(
            ctx=_ctx,
            wiki_root_uri=request.wiki_root_uri,
        )

    execution = await run_operation(
        operation="wiki.clear",
        telemetry=request.telemetry,
        fn=_clear,
    )
    return response_from_result(execution.result, telemetry=execution.telemetry)
