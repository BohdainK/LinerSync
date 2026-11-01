"""Live job updates over server-sent events."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from linersync.server.api.common import Services
from linersync.server.auth import require_session
from linersync.server.events import sse_stream

router = APIRouter(prefix="/api/events", dependencies=[Depends(require_session)])


@router.get(
    "",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Job updates"}
    },
)
async def events(request: Request, services: Services) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(services.events, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
