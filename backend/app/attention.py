from typing import Literal


AttentionLevel = Literal["none", "low", "medium", "high"]


def _attention_level(ratio: float) -> AttentionLevel:
    if ratio == 0:
        return "none"
    if ratio <= 0.2:
        return "low"
    if ratio <= 0.4:
        return "medium"
    return "high"
