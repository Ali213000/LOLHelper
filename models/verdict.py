from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Verdict:
    item: str
    reason: str  # "prescrit", "net", "léger avantage", "équivalents", "bottes"
    confidence: float
    alt: Optional[str] = None
    tied_items: List[str] = field(default_factory=list)

    @classmethod
    def tie(cls, items: List[str], reason: str, conf: float) -> "Verdict":
        return cls(item=items[0], reason=reason, confidence=conf, tied_items=items)
