from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTP error rendered as {"detail": ..., "code": ...}."""

    def __init__(self, status: int, detail: str, code: str):
        super().__init__(status_code=status, detail=detail)
        self.code = code


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, f"{what} not found", "not_found")


def forbidden(detail: str = "You don't have permission to do that") -> ApiError:
    return ApiError(403, detail, "forbidden")


def conflict(detail: str, code: str = "conflict") -> ApiError:
    return ApiError(409, detail, code)


def bad_request(detail: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, detail, code)
