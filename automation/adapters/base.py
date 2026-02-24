from abc import ABC, abstractmethod

from ..models import ApplyInput, ApplyResult


class PlatformAdapter(ABC):
    platform = "base"

    @abstractmethod
    async def supports(self, apply_input: ApplyInput) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def apply(self, apply_input: ApplyInput) -> ApplyResult:
        raise NotImplementedError
