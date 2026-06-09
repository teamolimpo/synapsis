"""
Registry dei provider disponibili per il tool llm.

Aggiungere un nuovo provider = creare un file in providers/ e
aggiungere una riga in PROVIDERS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.llm.providers.base import ProviderProtocol

from tools.llm.providers.gemini import GeminiProvider
from tools.llm.providers.grok import GrokProvider
from tools.llm.providers.openrouter import OpenRouterProvider

# Dizionario provider: chiave = nome CLI, valore = classe provider
PROVIDERS: dict[str, type[ProviderProtocol]] = {
    "grok": GrokProvider,
    "xai": GrokProvider,          # alias per grok
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
}

__all__ = ["PROVIDERS", "GrokProvider", "GeminiProvider", "OpenRouterProvider"]
