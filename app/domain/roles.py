"""스태프 역할 (security / order_status / board_config 공통).

값은 소문자로 통일: 보드 URL(/kitchen/feed), 폼 값(role=kitchen), 세션 JWT에서 모두 같은 값을 쓴다.
"""
import enum


class Role(str, enum.Enum):
    ADMIN = "admin"
    KITCHEN = "kitchen"
    HALL = "hall"