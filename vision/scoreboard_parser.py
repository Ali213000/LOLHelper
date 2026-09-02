"""
vision/scoreboard_parser.py — Extracts structured KDA/CS data from OCR text.

League's scoreboard (TAB screen) typically shows rows like:
    SummonerName   3/1/7   145   [item icons]
    EnemyPlayer    8/0/2   210   [item icons]

We use regex to extract kills/deaths/assists (KDA) from OCR text lines and
combine that with champion name data from the Live Client API where possible.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PlayerScoreEntry:
    """
    Represents one row of the scoreboard as parsed from OCR.
    Fields may be None if OCR confidence was low or the pattern didn't match.
    """
    raw_text: str = ""
    kills: Optional[int] = None
    deaths: Optional[int] = None
    assists: Optional[int] = None
    cs: Optional[int] = None

    @property
    def kda_string(self) -> str:
        if None not in (self.kills, self.deaths, self.assists):
            return f"{self.kills}/{self.deaths}/{self.assists}"
        return "?/?/?"

    @property
    def kda_ratio(self) -> float:
        if None in (self.kills, self.deaths, self.assists):
            return 0.0
        return (self.kills + self.assists) / max(1, self.deaths)  # type: ignore

    @property
    def is_fed(self) -> bool:
        """
        Fed heuristic based purely on KDA:
          - Hard-fed:  kills >= 5 AND deaths <= 2
          - Performing: KDA ratio >= 3.0
        """
        if None in (self.kills, self.deaths, self.assists):
            return False
        return (self.kills >= 5 and self.deaths <= 2) or self.kda_ratio >= 3.0  # type: ignore


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches patterns like "5/2/11", "0/7/3", "12/0/1"
_KDA_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{1,2})\b")

# Matches CS values — standalone numbers between 0 and 500
# We try to grab a number after KDA (usually appears to the right of KDA)
_CS_PATTERN = re.compile(r"\b([1-9]\d{0,2}|0)\b")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_ocr_lines(text_lines: list[str]) -> list[PlayerScoreEntry]:
    """
    Parse a list of OCR text lines into PlayerScoreEntry objects.

    Strategy:
    - Scan each line for a KDA pattern (X/Y/Z).
    - Any line containing a KDA pattern is treated as a scoreboard row.
    - Attempt to extract a CS value from the same line (first standalone
      number AFTER the KDA match, within reasonable range 0–500).

    Returns:
        List of PlayerScoreEntry (one per detected KDA row).
    """
    entries: list[PlayerScoreEntry] = []

    for line in text_lines:
        kda_match = _KDA_PATTERN.search(line)
        if kda_match is None:
            continue

        kills   = int(kda_match.group(1))
        deaths  = int(kda_match.group(2))
        assists = int(kda_match.group(3))

        # Sanity check — League caps realistic in-game values
        if any(v > 50 for v in (kills, deaths, assists)):
            logger.debug("Ignoring implausible KDA in line: %r", line)
            continue

        # Try to find CS value after the KDA match
        cs: Optional[int] = None
        remainder = line[kda_match.end():]
        cs_candidates = _CS_PATTERN.findall(remainder)
        for candidate in cs_candidates:
            val = int(candidate)
            if 0 <= val <= 500:
                cs = val
                break

        entry = PlayerScoreEntry(
            raw_text=line,
            kills=kills,
            deaths=deaths,
            assists=assists,
            cs=cs,
        )
        entries.append(entry)
        logger.debug(
            "Parsed: KDA=%s CS=%s fed=%s | raw=%r",
            entry.kda_string, cs, entry.is_fed, line
        )

    return entries


def filter_fed_entries(entries: list[PlayerScoreEntry]) -> list[PlayerScoreEntry]:
    """Return only the entries that meet the 'fed' threshold."""
    return [e for e in entries if e.is_fed]


def summarise_entries(entries: list[PlayerScoreEntry]) -> str:
    """Format entries as a human-readable string (for LLM prompt injection)."""
    lines = []
    for i, e in enumerate(entries, 1):
        cs_str = f"  CS: {e.cs}" if e.cs is not None else ""
        fed_str = " ⚠️ FED" if e.is_fed else ""
        lines.append(f"  Player {i}: {e.kda_string}{cs_str}{fed_str}")
    return "\n".join(lines) if lines else "  No scoreboard data detected."
