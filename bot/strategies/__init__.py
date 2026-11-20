"""Registry of the bots the lab can run. Add a bot by importing it here."""
from __future__ import annotations

from typing import Dict, Type

from bot.interface import Bot
from bot.strategies.forward_teacher import ForwardTeacherBot
from bot.strategies.learned import LearnedBot
from bot.strategies.momentum import MomentumBreakoutBot
from bot.strategies.random_entry import RandomEntryBot

REGISTRY: Dict[str, Type[Bot]] = {c.name: c for c in (RandomEntryBot, MomentumBreakoutBot, ForwardTeacherBot, LearnedBot)}


def make_bot(name: str, params: dict | None = None) -> Bot:
    if name not in REGISTRY:
        raise ValueError(f"unknown bot {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))


def available() -> list:
    return [c.describe() for c in REGISTRY.values()]
