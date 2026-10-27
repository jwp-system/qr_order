"""테이블 상태 도메인: 상태별 표시명과 고객 접근 규칙.

  AVAILABLE  주문 가능
  PAUSED     주문 중지 — QR로 메뉴/주문 내역은 볼 수 있지만 새 주문은 받지 않는다
  DISABLED   사용 안 함 — QR 접속 불가
"""
from dataclasses import dataclass

from app.database.models import TableStatus


@dataclass(frozen=True)
class TableStatusMeta:
    label: str
    desc: str


TABLE_STATUS_META: dict[TableStatus, TableStatusMeta] = {
    TableStatus.AVAILABLE: TableStatusMeta("주문 가능", "고객이 QR로 주문할 수 있습니다."),
    TableStatus.PAUSED: TableStatusMeta("주문 중지", "메뉴는 보이지만 새 주문은 받지 않습니다 (브레이크 타임, 예약석 등)."),
    TableStatus.DISABLED: TableStatusMeta("사용 안 함", "QR로 접속할 수 없습니다."),
}


def can_order(status: TableStatus) -> bool:
    """고객이 새 주문을 넣을 수 있는가"""
    return status is TableStatus.AVAILABLE
