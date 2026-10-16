from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.domain.order_status import STATUS_META, can_cancel

templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)

# 템플릿에서 STATUS_META[order.status].label 등을 바로 사용
templates.env.globals["STATUS_META"] = STATUS_META
templates.env.globals["can_cancel"] = can_cancel


def now() -> datetime:
    return datetime.now(timezone.utc)