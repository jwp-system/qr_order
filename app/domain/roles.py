"""스태프 역할 (security / order_status / board_config 공통).

값은 소문자로 통일: 보드 URL(/kitchen/feed), 폼 값(role=kitchen), 세션 JWT에서 모두 같은 값을 쓴다.
"""
import enum


class Role(str, enum.Enum):
    ADMIN = "admin"
    KITCHEN = "kitchen"
    HALL = "hall"


def can_view_board(session_role: Role, board_role: Role) -> bool:
    """로그인한 역할이 해당 역할의 보드를 쓸 수 있는가. 관리자는 모든 보드, 그 외는 자기 보드만."""
    return session_role is Role.ADMIN or session_role is board_role