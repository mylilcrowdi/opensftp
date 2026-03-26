"""
Command registry and fuzzy matcher for the command palette.

Provides a centralised registry of all available commands, fuzzy matching
for quick filtering, and an execute mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


def fuzzy_match(query: str, text: str) -> Optional[int]:
    """Score how well *query* matches *text*. Higher is better, None/0 = no match.

    Scoring:
    - Empty query matches everything (score 1)
    - Exact prefix gets a large bonus
    - Consecutive character matches score higher than scattered
    - Case insensitive
    """
    if not query:
        return 1

    q = query.lower()
    t = text.lower()

    # Exact substring match
    idx = t.find(q)
    if idx != -1:
        # Prefix match bonus
        bonus = 100 if idx == 0 else 50
        # Word boundary bonus (match at start of a word)
        if idx > 0 and t[idx - 1] in " _-./":
            bonus = 80
        return bonus + len(q)

    # Fuzzy: match all query chars in order
    score = 0
    qi = 0
    prev_match = -2  # last matched position
    consecutive = 0

    for ti, ch in enumerate(t):
        if qi < len(q) and ch == q[qi]:
            qi += 1
            if ti == prev_match + 1:
                consecutive += 1
                score += 3 + consecutive  # consecutive bonus grows
            else:
                consecutive = 0
                score += 1
            # Word boundary bonus
            if ti == 0 or t[ti - 1] in " _-./":
                score += 5
            prev_match = ti

    if qi < len(q):
        return 0  # not all query chars matched

    return score


@dataclass
class Command:
    """A single command that can be executed from the palette."""
    id: str
    name: str
    category: str
    handler: Callable[[], None]
    shortcut: Optional[str] = None
    icon: Optional[str] = None
    enabled: bool = True
    enabled_when: Optional[Callable[[], bool]] = field(default=None, repr=False)

    def is_enabled(self) -> bool:
        if self.enabled_when is not None:
            return self.enabled_when()
        return self.enabled


class CommandRegistry:
    """Central registry of all available commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.id] = cmd

    def get(self, cmd_id: str) -> Optional[Command]:
        return self._commands.get(cmd_id)

    def all(self) -> list[Command]:
        return list(self._commands.values())

    def categories(self) -> list[str]:
        return sorted(set(c.category for c in self._commands.values()))

    def by_category(self, category: str) -> list[Command]:
        return [c for c in self._commands.values() if c.category == category]

    def search(self, query: str, include_disabled: bool = True) -> list[Command]:
        """Return commands matching *query*, sorted by relevance."""
        results: list[tuple[int, Command]] = []
        for cmd in self._commands.values():
            if not include_disabled and not cmd.is_enabled():
                continue
            score = fuzzy_match(query, cmd.name)
            if score and score > 0:
                results.append((score, cmd))
        results.sort(key=lambda x: x[0], reverse=True)
        return [cmd for _, cmd in results]

    def execute(self, cmd_id: str) -> None:
        cmd = self._commands.get(cmd_id)
        if cmd is None:
            raise KeyError(f"Unknown command: {cmd_id}")
        cmd.handler()
