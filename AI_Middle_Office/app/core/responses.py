from typing import Any


def api_ok(data: Any = None, message: str = "ok", **extra: Any) -> dict:
    response = {"code": 200, "message": message, "data": data}
    response.update(extra)
    return response


def api_page(data: list, total: int, page: int, page_size: int, message: str = "ok", **extra: Any) -> dict:
    return api_ok(
        data=data,
        message=message,
        total=total,
        page=page,
        page_size=page_size,
        **extra,
    )
