"""
Configurazione centralizzata per il tool llm.

Gestisce:
- Caricamento del file .env dalla root del progetto
- Lettura delle API key dalle variabili d'ambiente
- Costanti (provider default, modelli default, versione)
- Configurazione modelli immagine (prezzi, dimensioni, validazione)
- Helper per stima costi e risoluzione immagini

Le API key NON vengono mai hardcoded qui — vengono lette
esclusivamente da variabili d'ambiente dopo il caricamento di .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from tools.common.paths import project_root

# ---------------------------------------------------------------------------
# Root del progetto
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = project_root()

# ---------------------------------------------------------------------------
# Caricamento .env
# ---------------------------------------------------------------------------
# python-dotenv non fallisce se .env non esiste — si limita a non caricare nulla.
# Questo e' il comportamento corretto: l'utente potrebbe usare variabili
# d'ambiente dirette senza file .env.
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
except ImportError:
    # Se python-dotenv non e' installato, l'errore verra' segnalato
    # quando si tenta di usare una API key mancante.
    pass

# ---------------------------------------------------------------------------
# Costanti operative
# ---------------------------------------------------------------------------

# Provider usato se --provider non viene specificato
DEFAULT_PROVIDER: str = "openrouter"

# Versione del tool (usata nell'output --verbose)
TOOL_VERSION: str = "0.2.0"

# Nomi delle variabili d'ambiente per le API key
ENV_KEY_GROK: str = "XAI_API_KEY"
ENV_KEY_GEMINI: str = "GEMINI_API_KEY"
ENV_KEY_OPENROUTER: str = "OPENROUTER_API_KEY"

# Mappa provider -> variabile d'ambiente attesa
PROVIDER_ENV_KEYS: dict[str, str] = {
    "grok": ENV_KEY_GROK,
    "xai": ENV_KEY_GROK,          # alias per grok
    "gemini": ENV_KEY_GEMINI,
    "openrouter": ENV_KEY_OPENROUTER,
}


# ---------------------------------------------------------------------------
# Funzione di recupero API key
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Prezzi noti per modello (aggiornabili a mano)
# Formato: model_id -> (input_usd_per_million, output_usd_per_million)
# ---------------------------------------------------------------------------

KNOWN_PRICES: dict[str, tuple[float, float]] = {
    # xAI / Grok — prezzi aprile 2026
    "grok-4-1-fast-non-reasoning": (0.20, 0.50),
    "grok-4-1-fast-reasoning": (0.20, 0.50),
    "grok-4-0709": (3.00, 15.00),
    "grok-code-fast-1": (0.20, 1.50),
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
    "grok-4.20-0309-non-reasoning": (2.00, 6.00),
    "grok-4.20-0309-reasoning": (2.00, 6.00),
    "grok-4.20-multi-agent-0309": (2.00, 6.00),
    # Google / Gemini — prezzi aprile 2026
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    # OpenRouter — modelli piu' usati (prezzi variabili per provider)
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/o3-mini": (1.10, 4.40),
    "openai/o4-mini": (1.10, 4.40),
    "anthropic/claude-sonnet-4-20250514": (3.00, 15.00),
    "anthropic/claude-haiku-3-5": (0.80, 4.00),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "google/gemini-2.5-pro": (1.25, 10.00),
    "deepseek/deepseek-chat-v3-0324": (0.27, 1.10),
    "meta-llama/llama-4-maverick": (0.20, 0.20),
    "qwen/qwen-2.5-72b-instruct": (0.35, 0.40),
    "qwen/qwen3.5-72b": (0.25, 0.25),
    "qwen/qwen3.5-35b-a3b": (0.14, 1.00),
}

# ---------------------------------------------------------------------------
# Image model pricing map (merged from tools/image_gen/config.py)
# ---------------------------------------------------------------------------

MODEL_PRICES: dict[str, dict[str, Any]] = {
    "openai/gpt-5-image": {
        "type": "token",
        "input": 10.0,
        "output": 10.0,
        "per": "1M_tokens",
        "name": "GPT-5 Image",
    },
    "openai/gpt-5-image-mini": {
        "type": "token",
        "input": 2.50,
        "output": 2.00,
        "per": "1M_tokens",
        "name": "GPT-5 Image Mini",
    },
    "openai/gpt-5.4-image-2": {
        "type": "token",
        "input": 8.0,
        "output": 15.0,
        "per": "1M_tokens",
        "name": "GPT-5.4 Image 2",
    },
    "google/gemini-2.5-flash-image": {
        "type": "token",
        "input": 0.30,
        "output": 2.50,
        "per": "1M_tokens",
        "name": "Gemini 2.5 Flash Image",
    },
    "google/gemini-3.1-flash-image": {
        "type": "token",
        "input": 0.50,
        "output": 3.0,
        "per": "1M_tokens",
        "name": "Gemini 3.1 Flash Image",
    },
    "google/gemini-3-pro-image-preview": {
        "type": "token",
        "input": 2.0,
        "output": 12.0,
        "per": "1M_tokens",
        "name": "Gemini 3 Pro Image Preview",
    },
    "black-forest-labs/flux-2-pro": {
        "type": "mp",
        "input_per_mp": 0.015,
        "output_per_mp": 0.03,
        "per": "megapixel",
        "name": "FLUX 2 Pro",
    },
    "black-forest-labs/flux-2-max": {
        "type": "mp",
        "input_per_mp": 0.03,
        "output_per_mp": 0.07,
        "per": "megapixel",
        "name": "FLUX 2 Max",
    },
    "black-forest-labs/flux-2-flex": {
        "type": "mp",
        "per_mp": 0.06,
        "per": "megapixel",
        "name": "FLUX 2 Flex",
    },
    "black-forest-labs/flux-2-klein-4b": {
        "type": "mp",
        "first_mp": 0.014,
        "subsequent_mp": 0.001,
        "per": "megapixel",
        "name": "FLUX 2 Klein 4B",
    },
    "x-ai/grok-imagine-image-quality": {
        "type": "token",
        "input": 0,
        "output": 0,
        "special": "priced_per_token",
        "total": 11.98,
        "per": "1M_tokens",
        "name": "Grok Imagine Image Quality",
    },
    "bytedance-seed/seedream-4.5": {
        "type": "fixed",
        "per_image": 0.04,
        "per": "image",
        "name": "Seedream 4.5",
    },
}

# Image generation defaults
DEFAULT_IMAGE_MODEL: str = "openai/gpt-5-image-mini"
DEFAULT_IMAGE_SIZE: str = "1K"
DEFAULT_IMAGE_RATIO: str = "1:1"

VALID_IMAGE_SIZES: set[str] = {"1K", "2K", "4K"}
VALID_IMAGE_RATIOS: set[str] = {"1:1", "16:9", "9:16", "4:3", "3:2"}

OPENROUTER_API_URL: str = "https://openrouter.ai/api/v1/chat/completions"
IMAGE_REQUEST_TIMEOUT: int = 180  # seconds
IMAGE_MAX_RETRIES: int = 2

SIZE_TO_MP: dict[str, int] = {
    "1K": 1,
    "2K": 4,
    "4K": 16,
}

RATIO_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (1024, 768),
    "3:2": (1024, 683),
}


def estimate_image_cost(model_id: str, size: str = "1K", prompt_tokens: int = 200) -> float:
    """Estimate the cost of an image generation request.

    Args:
        model_id: OpenRouter model identifier.
        size: Image size key (1K, 2K, 4K).
        prompt_tokens: Estimated token count for the prompt (default 200).

    Returns:
        Estimated cost in USD.
    """
    cfg = MODEL_PRICES.get(model_id)
    if cfg is None:
        return 0.0

    if cfg["type"] == "fixed":
        return float(cfg["per_image"])

    if cfg["type"] == "token":
        if cfg.get("special") == "priced_per_token":
            return (prompt_tokens / 1_000_000) * float(cfg["total"])
        inp = float(cfg["input"])
        out = float(cfg["output"])
        return (prompt_tokens / 1_000_000) * inp + (100 / 1_000_000) * out

    if cfg["type"] == "mp":
        mp = SIZE_TO_MP.get(size, 1)
        if "per_mp" in cfg:
            return float(cfg["per_mp"]) * mp
        if "first_mp" in cfg and "subsequent_mp" in cfg:
            extra = mp - 1
            return float(cfg["first_mp"]) + max(0, extra) * float(cfg["subsequent_mp"])
        if "input_per_mp" in cfg and "output_per_mp" in cfg:
            return (float(cfg["input_per_mp"]) + float(cfg["output_per_mp"])) * mp

    return 0.0


def estimate_resolution(size: str, ratio: str) -> tuple[int, int]:
    """Estimate image resolution in pixels for a given size and aspect ratio.

    Args:
        size: Image size key (1K, 2K, 4K).
        ratio: Aspect ratio key (1:1, 16:9, etc.).

    Returns:
        Tuple of (width, height) in pixels.
    """
    base = RATIO_DIMENSIONS.get(ratio, (1024, 1024))
    w, h = base
    if size == "2K":
        w, h = w * 2, h * 2
    elif size == "4K":
        w, h = w * 4, h * 4
    return (w, h)


# ---------------------------------------------------------------------------
# Directory prompt predefinita
# ---------------------------------------------------------------------------

PROMPTS_DIR: Path = PROJECT_ROOT / "lib" / "Prompts"


def get_api_key(provider: str) -> str:
    """
    Legge e ritorna la API key per il provider specificato.

    La chiave viene cercata nelle variabili d'ambiente (dopo il caricamento
    di .env). Se non trovata, stampa un messaggio di errore esplicativo
    su stderr ed esce con codice 1.

    Args:
        provider: Nome del provider ("grok" o "gemini")

    Returns:
        La API key come stringa non vuota

    Raises:
        SystemExit(1): Se la chiave non e' presente nelle variabili d'ambiente
    """
    env_var = PROVIDER_ENV_KEYS.get(provider)
    if env_var is None:
        print(
            f"Errore: provider '{provider}' non riconosciuto. "
            f"Provider disponibili: {', '.join(PROVIDER_ENV_KEYS)}",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get(env_var, "").strip()
    if not api_key:
        _print_missing_key_error(provider, env_var)
        sys.exit(1)

    return api_key


def _print_missing_key_error(provider: str, env_var: str) -> None:
    """
    Stampa su stderr un messaggio di errore dettagliato per chiave mancante.

    Args:
        provider: Nome del provider
        env_var: Nome della variabile d'ambiente attesa
    """
    env_file = PROJECT_ROOT / ".env"
    lines = [
        f"Errore: API key per '{provider}' non trovata.",
        "",
        f"La variabile d'ambiente richiesta e': {env_var}",
        "",
        "Come configurarla:",
        f"  1. Crea (o modifica) il file: {env_file}",
        "  2. Aggiungi la riga:",
        f"         {env_var}=la-tua-chiave-api",
        "",
    ]

    if provider == "grok":
        lines += [
            "  3. Ottieni la chiave da: https://console.x.ai",
        ]
    elif provider == "gemini":
        lines += [
            "  3. Ottieni la chiave da: https://aistudio.google.com/apikey",
        ]
    elif provider == "openrouter":
        lines += [
            "  3. Ottieni la chiave da: https://openrouter.ai/keys",
        ]

    lines += [
        "",
        "Nota: il file .env e' escluso da git (.gitignore) — non verra' mai committato.",
    ]

    print("\n".join(lines), file=sys.stderr)
