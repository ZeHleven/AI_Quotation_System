import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.model_call_log import ModelCallLog


logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitState:
    failure_count: int = 0
    opened_until: Optional[datetime] = None
    last_error: str = ""


_CIRCUITS: dict[str, CircuitState] = {}


VISION_PROMPT = """你是一个专业的装修造价数据提取员。请严格提取这张图片表格中的【所有】行的完整信息。
⚠️ 提取红线：每一行必须完整包含【施工空间】、【施工项目】、【规格/工艺/材料要求】、【预估工程量】（面积/长度）四项数据。
⚠️ 分隔符生死线（极度重要）：
1. 每一行的所有信息自然合并为一句话，这句话【内部绝对不允许】出现分号（请统一用逗号替代）。
2. 【仅在】换行到下一个独立项目时，强制使用全角分号“；”作为分割。
示范格式：客厅直线型吊顶，使用龙牌轻钢龙骨无造型平顶要求做L型抗裂及接缝处理，50平米；厕所铲墙皮，铲除原大白腻子，50平米。
请直接输出结果，严禁包含任何Markdown格式或多余解释。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _circuit_key(provider: str, endpoint_type: str) -> str:
    return f"{provider}:{endpoint_type}"


def _estimate_cost(input_chars: int, output_chars: int) -> float:
    if settings.model_gateway_cost_per_1k_chars <= 0:
        return 0.0
    return round(((input_chars + output_chars) / 1000) * settings.model_gateway_cost_per_1k_chars, 6)


def reset_circuit_breakers() -> None:
    _CIRCUITS.clear()


def get_circuit_status() -> dict:
    now = _utcnow()
    result = {}
    for key, state in _CIRCUITS.items():
        opened_until = state.opened_until
        result[key] = {
            "failure_count": state.failure_count,
            "is_open": bool(opened_until and opened_until > now),
            "opened_until": opened_until.isoformat() if opened_until else None,
            "last_error": state.last_error,
        }
    return result


def _before_call(provider: str, endpoint_type: str) -> None:
    key = _circuit_key(provider, endpoint_type)
    state = _CIRCUITS.setdefault(key, CircuitState())
    if state.opened_until and state.opened_until > _utcnow():
        raise CircuitOpenError(f"模型网关熔断中: {key}，恢复时间 {state.opened_until.isoformat()}")
    if state.opened_until and state.opened_until <= _utcnow():
        state.opened_until = None
        state.failure_count = 0


def _after_success(provider: str, endpoint_type: str) -> None:
    _CIRCUITS[_circuit_key(provider, endpoint_type)] = CircuitState()


def _after_failure(provider: str, endpoint_type: str, error_message: str) -> None:
    key = _circuit_key(provider, endpoint_type)
    state = _CIRCUITS.setdefault(key, CircuitState())
    state.failure_count += 1
    state.last_error = error_message[:500]
    if state.failure_count >= settings.model_gateway_failure_threshold:
        state.opened_until = _utcnow() + timedelta(seconds=settings.model_gateway_circuit_reset_seconds)


def record_model_call(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    status: str,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
    http_status: Optional[int] = None,
    latency_ms: float = 0.0,
    input_chars: int = 0,
    output_chars: int = 0,
    error_message: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            ModelCallLog(
                provider=provider,
                model=model,
                endpoint_type=endpoint_type,
                status=status,
                username=username,
                trace_id=trace_id,
                http_status=http_status,
                latency_ms=round(latency_ms, 2),
                input_chars=input_chars,
                output_chars=output_chars,
                estimated_cost=_estimate_cost(input_chars, output_chars),
                error_message=(error_message or "")[:1000] or None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("model_call_log_failed", extra={"event": "model_call_log_failed", "provider": provider})
    finally:
        db.close()


async def call_glm_vision_extract(
    base64_image: str,
    mime_type: str,
    *,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    provider = "zhipu"
    endpoint_type = "vision_extract"
    model = settings.glm_vision_model
    _before_call(provider, endpoint_type)

    if "请在这里" in settings.zhipu_api_key or re.search(r"[\u4e00-\u9fa5]", settings.zhipu_api_key):
        error_message = "API Key 格式异常(包含中文字符)"
        _after_failure(provider, endpoint_type, error_message)
        record_model_call(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            error_message=error_message,
            input_chars=len(VISION_PROMPT),
        )
        raise ValueError(error_message)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.zhipu_api_key.strip()}"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                ],
            }
        ],
    }

    last_error = "未知错误"
    started = time.perf_counter()
    for delay in [1, 2]:
        try:
            response = await asyncio.to_thread(
                requests.post,
                settings.glm_vision_url,
                headers=headers,
                json=payload,
                timeout=settings.model_gateway_timeout_seconds,
                proxies={"http": None, "https": None},
                verify=False,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                _after_success(provider, endpoint_type)
                record_model_call(
                    provider=provider,
                    model=model,
                    endpoint_type=endpoint_type,
                    status="success",
                    username=username,
                    trace_id=trace_id,
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    input_chars=len(VISION_PROMPT),
                    output_chars=len(content or ""),
                )
                return content

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(delay)

    latency_ms = (time.perf_counter() - started) * 1000
    _after_failure(provider, endpoint_type, last_error)
    record_model_call(
        provider=provider,
        model=model,
        endpoint_type=endpoint_type,
        status="error",
        username=username,
        trace_id=trace_id,
        latency_ms=latency_ms,
        input_chars=len(VISION_PROMPT),
        error_message=last_error,
    )
    raise RuntimeError(last_error)


async def post_json_via_gateway(
    *,
    provider: str,
    model: str,
    endpoint_type: str,
    url: str,
    json_payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
    username: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> requests.Response:
    _before_call(provider, endpoint_type)
    started = time.perf_counter()
    input_chars = len(str(json_payload))
    try:
        response = await asyncio.to_thread(
            requests.post,
            url,
            json=json_payload,
            headers=headers,
            timeout=timeout,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        output_chars = len(response.text or "")

        if 200 <= response.status_code < 300:
            _after_success(provider, endpoint_type)
            status = "success"
            error_message = None
        else:
            error_message = f"HTTP {response.status_code}: {(response.text or '')[:300]}"
            _after_failure(provider, endpoint_type, error_message)
            status = "error"

        record_model_call(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status=status,
            username=username,
            trace_id=trace_id,
            http_status=response.status_code,
            latency_ms=latency_ms,
            input_chars=input_chars,
            output_chars=output_chars,
            error_message=error_message,
        )
        return response
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        error_message = str(exc)
        _after_failure(provider, endpoint_type, error_message)
        record_model_call(
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            status="error",
            username=username,
            trace_id=trace_id,
            latency_ms=latency_ms,
            input_chars=input_chars,
            error_message=error_message,
        )
        raise
