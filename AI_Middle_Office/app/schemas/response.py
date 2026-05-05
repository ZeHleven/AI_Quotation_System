from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int = 200
    message: str = "ok"
    data: Any = None


class PageResponse(ApiResponse):
    total: int = 0
    page: int = 1
    page_size: int = 20
