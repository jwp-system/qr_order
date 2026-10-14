# --- Status Label & UI Mappings ---
from app.database.models import OrderStatus

STATUS_INFO = {
    OrderStatus.ORDERED: {"label": "주문 접수", "step": 1, "desc": "주문이 접수되었습니다. 매장 확인 대기 중입니다."},
    OrderStatus.ACCEPTED: {"label": "조리 준비", "step": 1, "desc": "매장에서 주문을 확인했습니다. 곧 조리를 시작합니다."},
    OrderStatus.COOKING: {"label": "조리 중", "step": 2, "desc": "주방에서 맛있게 조리 중입니다."},
    OrderStatus.COOKED: {"label": "서빙 준비", "step": 2, "desc": "조리가 완료되었습니다. 서빙을 준비 중입니다."},
    OrderStatus.SERVED: {"label": "서빙 완료", "step": 3, "desc": "음식이 준비되었습니다. 맛있게 드세요!"},
    OrderStatus.COMPLETED: {"label": "주문 완료", "step": 4, "desc": "이용해 주셔서 감사합니다."},
    OrderStatus.CANCELLED: {"label": "주문 취소", "step": 0, "desc": "매장 사정으로 주문이 취소되었습니다."},
}