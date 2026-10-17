"""消息渠道抽象。新增渠道实现 send_text/reply 即可。"""

from abc import ABC, abstractmethod

from app.db.models import User


class Channel(ABC):
    name: str = ""

    @abstractmethod
    async def send_text(self, session, user: User, text: str) -> None: ...
