from __future__ import annotations

from enum import Enum


class ChoiceEnum(str, Enum):
    @classmethod
    def labels(cls) -> dict[str, str]:
        return {member.value: member.name.replace("_", " ").title() for member in cls}


def build_choices(enum_cls: type[ChoiceEnum]) -> list[tuple[str, str]]:
    labels = enum_cls.labels()
    return [(member.value, labels[member.value]) for member in enum_cls]
