"""매장 관리자 메뉴 관리 (/admin/menu) — 관리자 로그인 필수.

  GET  /admin/menu                              메뉴 관리 화면
  GET  /admin/menu/body                         메뉴 목록 프래그먼트 (변경 후 다시 그리기용)
  POST /admin/menu/categories[/{id}[/delete]]   카테고리 추가 / 수정 / 삭제
  GET  /admin/menu/items/new, /items/{id}       메뉴 추가 / 수정 모달
  POST /admin/menu/items[/{id}[/delete]]        메뉴 추가 / 수정 / 삭제
  POST /admin/menu/items/{id}/flags/{flag}      메뉴 상태 토글 (품절, 숨김, 인기 ...)
  POST /admin/menu/items/{id}/groups            옵션 그룹 추가
  POST /admin/menu/groups/{id}[/delete|/details]  옵션 그룹 수정 / 삭제 / 옵션 항목 추가
  POST /admin/menu/details/{id}[/delete]        옵션 항목 수정 / 삭제

화면 갱신 규칙 (HTMX):
  - 목록이 바뀌면 응답 헤더 HX-Trigger: menu-changed -> #menu-body가 /admin/menu/body를 다시 가져온다.
  - 폼 검증 실패는 422 + 오류가 표시된 같은 폼 (페이지 스크립트가 422도 swap하도록 설정).
매장은 요청 값이 아니라 로그인 세션의 store_id로 정한다.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.core.templating import templates
from app.database.models import Item, OptionType
from app.database.session import get_db
from app.domain.menu import ITEM_FLAGS
from app.services import menu_service as svc
from app.services.store_service import get_store_or_404

Session = Dict[str, Any]

router = APIRouter(prefix="/admin/menu", tags=["Store Admin Menu"], dependencies=[Depends(require_admin)])

MENU_CHANGED = "menu-changed"
UNPROCESSABLE = 422


def _render(request: Request, name: str, context: dict, *, status_code: int = 200, changed: bool = False) -> HTMLResponse:
    response = templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)
    if changed:
        response.headers["HX-Trigger"] = MENU_CHANGED
    return response


def _changed_empty() -> HTMLResponse:
    """삭제 후 빈 응답 (대상 요소 제거/모달 닫기) + 목록 다시 그리기"""
    return HTMLResponse(content="", headers={"HX-Trigger": MENU_CHANGED})


# =====================================================================
# 화면 / 목록
# =====================================================================
async def _body(
    request: Request, db: AsyncSession, store_id: int, *, error: str | None = None, changed: bool = False
) -> HTMLResponse:
    categories = await svc.get_menu(db, store_id)
    return _render(
        request, "store_admin/fragments/menu_body.html",
        {"categories": categories, "error": error},
        status_code=UNPROCESSABLE if error else 200, changed=changed,
    )


@router.get("", response_class=HTMLResponse)
async def menu_page(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    store = await get_store_or_404(db, session["store_id"])
    categories = await svc.get_menu(db, store.id)
    return _render(request, "store_admin/menu.html", {"store": store, "categories": categories, "error": None})


@router.get("/body", response_class=HTMLResponse)
async def menu_body(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _body(request, db, session["store_id"])


# =====================================================================
# 카테고리
# =====================================================================
@router.post("/categories", response_class=HTMLResponse)
async def create_category(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        await svc.create_category(db, session["store_id"], await request.form())
    except svc.MenuError as e:
        await db.rollback()
        return await _body(request, db, session["store_id"], error=e.message)
    return await _body(request, db, session["store_id"])


@router.post("/categories/{category_id}", response_class=HTMLResponse)
async def update_category(
    request: Request, category_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    category = await svc.get_category(db, session["store_id"], category_id)
    try:
        await svc.update_category(db, category, await request.form())
    except svc.MenuError as e:
        await db.rollback()
        return await _body(request, db, session["store_id"], error=e.message)
    return await _body(request, db, session["store_id"])


@router.post("/categories/{category_id}/delete", response_class=HTMLResponse)
async def delete_category(
    request: Request, category_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    category = await svc.get_category(db, session["store_id"], category_id)
    try:
        await svc.delete_category(db, category)
    except svc.MenuError as e:
        return await _body(request, db, session["store_id"], error=e.message)
    return await _body(request, db, session["store_id"])


# =====================================================================
# 메뉴 (추가/수정 모달)
# =====================================================================
def _item_values(item: Item) -> dict:
    values = {
        "category_id": str(item.category_id),
        "name": item.name,
        "price": str(item.price),
        "description": item.description or "",
        "image_url": item.image_url or "",
        "display_order": str(item.display_order),
    }
    values.update({flag: "on" for flag in ITEM_FLAGS if getattr(item, flag)})
    return values


async def _item_modal(
    request: Request, db: AsyncSession, store_id: int, *,
    item: Item | None, values: dict,
    error: str | None = None, message: str | None = None, changed: bool = False,
) -> HTMLResponse:
    categories = await svc.get_menu(db, store_id)
    return _render(
        request, "store_admin/fragments/menu_item_modal.html",
        {
            "item": item, "values": values, "categories": categories,
            "option_types": OptionType, "error": error, "message": message,
        },
        status_code=UNPROCESSABLE if error else 200, changed=changed,
    )


@router.get("/items/new", response_class=HTMLResponse)
async def new_item_modal(
    request: Request, category_id: int | None = None,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    values = {"category_id": str(category_id) if category_id else ""}
    return await _item_modal(request, db, session["store_id"], item=None, values=values)


@router.post("/items", response_class=HTMLResponse)
async def create_item(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    store_id = session["store_id"]
    form = await request.form()
    try:
        created = await svc.create_item(db, store_id, form)
    except svc.MenuError as e:
        await db.rollback()
        return await _item_modal(request, db, store_id, item=None, values=dict(form), error=e.message)
    # 바로 옵션을 추가할 수 있도록 수정 모드로 다시 연다
    item = await svc.get_item(db, store_id, created.id)
    return await _item_modal(
        request, db, store_id, item=item, values=_item_values(item),
        message="메뉴가 추가되었습니다. 필요하면 아래에서 옵션을 추가하세요.", changed=True,
    )


@router.get("/items/{item_id}", response_class=HTMLResponse)
async def edit_item_modal(
    request: Request, item_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    item = await svc.get_item(db, session["store_id"], item_id)
    return await _item_modal(request, db, session["store_id"], item=item, values=_item_values(item))


@router.post("/items/{item_id}", response_class=HTMLResponse)
async def update_item(
    request: Request, item_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    item = await svc.get_item(db, store_id, item_id)
    form = await request.form()
    try:
        await svc.update_item(db, store_id, item, form)
    except svc.MenuError as e:
        await db.rollback()
        item = await svc.get_item(db, store_id, item_id)
        return await _item_modal(request, db, store_id, item=item, values=dict(form), error=e.message)
    item = await svc.get_item(db, store_id, item_id)
    return await _item_modal(
        request, db, store_id, item=item, values=_item_values(item), message="저장되었습니다.", changed=True,
    )


@router.post("/items/{item_id}/delete", response_class=HTMLResponse)
async def delete_item(
    item_id: int, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    item = await svc.get_item(db, session["store_id"], item_id)
    await svc.delete_item(db, item)
    return _changed_empty()


@router.post("/items/{item_id}/flags/{flag}", response_class=HTMLResponse)
async def toggle_item_flag(
    request: Request, item_id: int, flag: str,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    item = await svc.get_item(db, session["store_id"], item_id)
    await svc.toggle_flag(db, item, flag)
    return _render(request, "store_admin/fragments/menu_item_row.html", {"item": item})


# =====================================================================
# 옵션 그룹 / 옵션 항목 (수정 모달 안의 옵션 패널을 다시 그린다)
# =====================================================================
async def _options_panel(
    request: Request, db: AsyncSession, store_id: int, item_id: int, *, error: str | None = None,
) -> HTMLResponse:
    item = await svc.get_item(db, store_id, item_id)
    return _render(
        request, "store_admin/fragments/menu_options.html",
        {"item": item, "option_types": OptionType, "options_error": error},
        status_code=UNPROCESSABLE if error else 200, changed=not error,  # 목록의 옵션 수 갱신
    )


async def _options_action(request: Request, db: AsyncSession, store_id: int, item_id: int, action) -> HTMLResponse:
    """옵션 변경 공통 처리: 실행 -> 실패 시 롤백 후 오류 표시 -> 패널 다시 그리기"""
    try:
        await action()
    except svc.MenuError as e:
        await db.rollback()
        return await _options_panel(request, db, store_id, item_id, error=e.message)
    return await _options_panel(request, db, store_id, item_id)


@router.post("/items/{item_id}/groups", response_class=HTMLResponse)
async def create_group(
    request: Request, item_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    item = await svc.get_item(db, store_id, item_id)
    form = await request.form()
    return await _options_action(request, db, store_id, item.id, lambda: svc.create_group(db, item, form))


@router.post("/groups/{group_id}", response_class=HTMLResponse)
async def update_group(
    request: Request, group_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    group = await svc.get_group(db, store_id, group_id)
    form = await request.form()
    return await _options_action(request, db, store_id, group.item_id, lambda: svc.update_group(db, group, form))


@router.post("/groups/{group_id}/delete", response_class=HTMLResponse)
async def delete_group(
    request: Request, group_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    group = await svc.get_group(db, store_id, group_id)
    return await _options_action(request, db, store_id, group.item_id, lambda: svc.delete_group(db, group))


@router.post("/groups/{group_id}/details", response_class=HTMLResponse)
async def create_detail(
    request: Request, group_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    group = await svc.get_group(db, store_id, group_id)
    form = await request.form()
    return await _options_action(request, db, store_id, group.item_id, lambda: svc.create_detail(db, group, form))


@router.post("/details/{detail_id}", response_class=HTMLResponse)
async def update_detail(
    request: Request, detail_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    detail = await svc.get_detail(db, store_id, detail_id)
    group = await svc.get_group(db, store_id, detail.group_id)
    form = await request.form()
    return await _options_action(request, db, store_id, group.item_id, lambda: svc.update_detail(db, detail, form))


@router.post("/details/{detail_id}/delete", response_class=HTMLResponse)
async def delete_detail(
    request: Request, detail_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    detail = await svc.get_detail(db, store_id, detail_id)
    group = await svc.get_group(db, store_id, detail.group_id)
    return await _options_action(request, db, store_id, group.item_id, lambda: svc.delete_detail(db, detail))
