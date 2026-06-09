"""
Provider xAI/Grok per il tool llm.

Supporta due path:
- Modelli standard (grok-4-1-*, grok-4.20-0309-*): usa SDK OpenAI con base_url xAI,
  completamente compatibile con il formato OpenAI chat completions.
- Modelli multi-agent (grok-4.20-multi-agent-*): usa xai_sdk con streaming,
  coordina piu' agenti AI in parallelo per ricerche approfondite.
   Richiede: uv add xai-sdk

Riferimento API: https://docs.x.ai/api
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
from loguru import logger

from tools.llm.image_client import ImageResult
from tools.llm.providers.base import ChatResponse, ModelInfo

if TYPE_CHECKING:
    pass

# Sottostringa che identifica i modelli multi-agent
_MULTI_AGENT_MARKER = "multi-agent"

# Modelli multi-agent noti (aggiunta manuale — l'API list non li espone)
_KNOWN_MULTI_AGENT_MODELS: list[str] = [
    "grok-4.20-multi-agent-0309",
]


class GrokProvider:
    """
    Provider per xAI/Grok.

    Modello di default: grok-4-1-fast-non-reasoning
    (il piu' economico e veloce, ideale per consulti rapidi)
    """

    default_model: str = "grok-4-1-fast-non-reasoning"
    _BASE_URL: str = "https://api.x.ai/v1"

    def __init__(self, api_key: str) -> None:
        """
        Inizializza il client OpenAI configurato per xAI.

        La api_key viene anche salvata per il path xai_sdk (multi-agent).

        Args:
            api_key: Chiave API xAI (ottenibile da https://console.x.ai)

        Raises:
            ImportError: Se la libreria openai non e' installata
        """
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "La libreria 'openai' non e' installata. Esegui: uv add openai"
            ) from exc

        self._api_key = api_key
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._BASE_URL,
        )
        logger.debug(f"GrokProvider inizializzato con base_url={self._BASE_URL}")

    # ------------------------------------------------------------------
    # Path multi-agent (xai_sdk)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_multi_agent(model: str) -> bool:
        return _MULTI_AGENT_MARKER in model.lower()

    def _chat_multi_agent(
        self,
        prompt: str,
        model: str,
        agent_count: int = 4,
    ) -> ChatResponse:
        """
        Chiama un modello multi-agent tramite xai_sdk con streaming.

        Args:
            prompt: Testo del prompt
            model: Identificatore modello (es. 'grok-4.20-multi-agent-0309')
            agent_count: Numero agenti (4 = veloce, 16 = approfondito)

        Returns:
            ChatResponse con testo aggregato e token di tutti gli agenti

        Raises:
            ImportError: Se xai_sdk non e' installato
            RuntimeError: Se la chiamata API fallisce
        """
        try:
            from xai_sdk import Client as XaiClient
            from xai_sdk.chat import user as xai_user
        except ImportError as exc:
            raise ImportError(
                "La libreria 'xai_sdk' non e' installata. Esegui: uv add xai-sdk"
            ) from exc

        logger.debug(f"GrokProvider (multi-agent): modello={model}, agent_count={agent_count}")

        try:
            xai_client = XaiClient(api_key=self._api_key)
            chat = xai_client.chat.create(model=model, agent_count=agent_count)
            chat.append(xai_user(prompt))
        except Exception as exc:
            logger.error(f"GrokProvider (multi-agent): errore inizializzazione — {exc}")
            raise RuntimeError(f"Errore multi-agent Grok: {exc}") from exc

        start = time.monotonic()
        text_parts: list[str] = []
        final_response = None

        try:
            for response, chunk in chat.stream():
                if chunk.content:
                    text_parts.append(chunk.content)
                final_response = response
        except Exception as exc:
            logger.error(f"GrokProvider (multi-agent): errore streaming — {exc}")
            raise RuntimeError(f"Errore streaming multi-agent Grok: {exc}") from exc

        elapsed = time.monotonic() - start
        text = "".join(text_parts)

        # Estrai token dall'oggetto usage di xai_sdk
        input_tokens: int | None = None
        output_tokens: int | None = None
        if final_response is not None:
            usage = getattr(final_response, "usage", None)
            if usage is not None:
                # xai_sdk usa nomi diversi da openai — proviamo entrambi
                input_tokens = getattr(usage, "input_tokens", None) or getattr(
                    usage, "prompt_tokens", None
                )
                output_tokens = getattr(usage, "output_tokens", None) or getattr(
                    usage, "completion_tokens", None
                )

        logger.debug(
            f"GrokProvider (multi-agent): risposta in {elapsed:.2f}s, "
            f"agenti={agent_count}, input={input_tokens}, output={output_tokens}"
        )

        return ChatResponse(
            text=text,
            model_used=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Path standard (openai SDK)
    # ------------------------------------------------------------------

    def chat(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        agent_count: int = 4,
    ) -> ChatResponse:
        """
        Invia un prompt a Grok e restituisce la risposta.

        Per modelli multi-agent usa xai_sdk con streaming.
        Per tutti gli altri usa il path OpenAI-compatibile standard.

        Args:
            prompt: Testo del prompt da inviare
            model: Override del modello (None = usa default_model)
            system: Messaggio di sistema opzionale
            agent_count: Numero agenti per modelli multi-agent (4 o 16)

        Returns:
            ChatResponse con testo della risposta e metadati token/tempo

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        effective_model = model or self.default_model

        if self._is_multi_agent(effective_model):
            return self._chat_multi_agent(prompt, effective_model, agent_count)

        # Path standard OpenAI-compatibile
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        logger.debug(
            f"GrokProvider: chiamata a modello={effective_model}, messaggi={len(messages)}"
        )

        start = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=effective_model,
                messages=messages,  # type: ignore[arg-type]
            )
        except Exception as exc:
            logger.error(f"GrokProvider: errore durante la chiamata API — {exc}")
            raise RuntimeError(f"Errore chiamata Grok: {exc}") from exc

        elapsed = time.monotonic() - start

        text = response.choices[0].message.content or ""
        input_tokens: int | None = None
        output_tokens: int | None = None

        if response.usage:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens

        logger.debug(
            f"GrokProvider: risposta ricevuta in {elapsed:.2f}s, "
            f"token input={input_tokens}, output={output_tokens}"
        )

        return ChatResponse(
            text=text,
            model_used=effective_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Path Responses API (httpx — diretto)
    # ------------------------------------------------------------------

    def _chat_responses_api(
        self,
        prompt: str,
        model: str,
        system: str | None = None,
        previous_response_id: str | None = None,
        web_search: bool = False,
    ) -> tuple[ChatResponse, str]:
        """
        Chiama la Responses API xAI tramite httpx.

        Non usa OpenAI SDK — endpoint nativo https://api.x.ai/v1/responses.
        Supporta conversazioni stateful tramite previous_response_id e
        ricerca web tramite search_parameters.

        Args:
            prompt: Testo del prompt utente
            model: Identificatore modello
            system: System prompt opzionale
            previous_response_id: ID della risposta precedente per conversazioni stateful
            web_search: Se True abilita search_parameters mode=auto

        Returns:
            Tupla (ChatResponse, response_id) dove response_id serve per il turno successivo

        Raises:
            RuntimeError: Se la chiamata HTTP fallisce o restituisce status != 200
        """
        url = "https://api.x.ai/v1/responses"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        body: dict = {
            "model": model,
            "input": prompt,
            "store": True,
        }
        if system:
            body["system"] = system
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        if web_search:
            body["search_parameters"] = {"mode": "auto"}

        logger.debug(
            f"GrokProvider (responses-api): modello={model}, "
            f"web_search={web_search}, prev_id={previous_response_id}"
        )

        start = time.monotonic()
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.RequestError as exc:
            logger.error(f"GrokProvider (responses-api): errore connessione — {exc}")
            raise RuntimeError(f"Errore connessione Responses API: {exc}") from exc

        if resp.status_code != 200:
            logger.error(
                f"GrokProvider (responses-api): HTTP {resp.status_code} — {resp.text[:300]}"
            )
            raise RuntimeError(
                f"Responses API ha restituito HTTP {resp.status_code}: {resp.text[:300]}"
            )

        elapsed = time.monotonic() - start

        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Risposta non JSON dalla Responses API: {exc}") from exc

        # Estrai testo — la struttura puo' variare leggermente tra versioni API
        text_parts: list[str] = []
        for item in data.get("output", []):
            # Forma 1: item.text diretto
            if "text" in item and isinstance(item["text"], str):
                text_parts.append(item["text"])
                continue
            # Forma 2: item.content[].text (type == "output_text")
            for content_block in item.get("content", []):
                if content_block.get("type") == "output_text" and "text" in content_block:
                    text_parts.append(content_block["text"])

        text = "".join(text_parts)
        response_id: str = data.get("id", "")

        usage = data.get("usage", {})
        input_tokens: int | None = usage.get("input_tokens")
        output_tokens: int | None = usage.get("output_tokens")

        logger.debug(
            f"GrokProvider (responses-api): risposta in {elapsed:.2f}s, "
            f"response_id={response_id}, input={input_tokens}, output={output_tokens}"
        )

        return (
            ChatResponse(
                text=text,
                model_used=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_seconds=elapsed,
            ),
            response_id,
        )

    def start_chat_session(
        self,
        model: str | None = None,
        system: str | None = None,
        web_search: bool = False,
    ) -> GrokChatSession:
        """
        Crea una sessione di chat stateful tramite la Responses API.

        La sessione mantiene il previous_response_id tra i turni, permettendo
        conversazioni multi-turn senza dover reinviare l'intera history.

        Args:
            model: Override modello (None = usa default_model)
            system: System prompt opzionale
            web_search: Se True abilita la ricerca web per ogni turno della sessione

        Returns:
            GrokChatSession pronta per ricevere messaggi
        """
        effective_model = model or self.default_model
        return GrokChatSession(
            provider=self,
            model=effective_model,
            system=system,
            web_search=web_search,
        )

    def list_models(self) -> list[ModelInfo]:
        """
        Recupera i modelli disponibili dall'API xAI e aggiunge i modelli
        multi-agent noti (non esposti dall'endpoint /v1/models).

        Returns:
            Lista di ModelInfo ordinata per id

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        logger.debug("GrokProvider: recupero lista modelli")
        try:
            page = self._client.models.list()
            ids = set(m.id for m in page)
        except Exception as exc:
            logger.error(f"GrokProvider: errore list_models — {exc}")
            raise RuntimeError(f"Errore recupero modelli Grok: {exc}") from exc

        # Aggiunge modelli multi-agent (non esposti da /v1/models)
        ids.update(_KNOWN_MULTI_AGENT_MODELS)

        models = [ModelInfo(id=mid, is_default=(mid == self.default_model)) for mid in sorted(ids)]
        logger.debug(f"GrokProvider: {len(models)} modelli trovati")
        return models

    # ------------------------------------------------------------------
    # Image generation (xai_sdk nativo)
    # ------------------------------------------------------------------

    def generate_image(
        self,
        prompt: str,
        model: str | None = None,
        size: str = "1K",
        ratio: str = "1:1",
        negative_prompt: str | None = None,
        seed: int | None = None,
        input_image_path: str | None = None,
        image_config_json: str | None = None,
    ) -> ImageResult:
        """
        Genera un'immagine usando l'SDK nativo xai_sdk.

        Usa ``client.image.sample()`` che restituisce i bytes dell'immagine
        direttamente in formato base64.

        Args:
            prompt: Testo del prompt per la generazione
            model: Modello (default: grok-imagine-image-quality)
            size: Dimensione (1K, 2K, 4K) — mappata a risoluzione
            ratio: Aspect ratio (1:1, 16:9, etc.)
            negative_prompt: Non supportato da xAI
            seed: Seed per riproducibilita'
            input_image_path: Non supportato da xAI
            image_config_json: Non supportato da xAI

        Returns:
            ImageResult con l'immagine in base64 o errore
        """
        try:
            from xai_sdk import Client as XaiClient
        except ImportError as exc:
            raise ImportError(
                "La libreria 'xai_sdk' non e' installata. Esegui: uv add xai-sdk"
            ) from exc

        import base64

        effective_model = model or "grok-imagine-image-quality"

        logger.debug(
            f"GrokProvider.generate_image: modello={effective_model}, ratio={ratio}, size={size}"
        )

        try:
            xai_client = XaiClient(api_key=self._api_key)
            response = xai_client.image.sample(
                prompt=prompt,
                model=effective_model,
                aspect_ratio=ratio,
                resolution=size.lower(),
                image_format="base64",
            )
        except Exception as exc:
            logger.error(f"GrokProvider.generate_image: errore API — {exc}")
            return ImageResult(
                success=False,
                error_type="generic",
                error_message=f"Errore generazione immagine xAI: {exc}",
                model=effective_model,
            )

        # response.image contiene i bytes o gia' base64
        img_data = response.image
        if isinstance(img_data, bytes):
            b64 = base64.b64encode(img_data).decode()
        else:
            b64 = str(img_data)

        logger.debug(f"GrokProvider.generate_image: immagine generata ({len(b64)} chars base64)")

        return ImageResult(
            success=True,
            image_base64=b64,
            mime_type="image/jpeg",
            model=effective_model,
            cost=0.05,  # flat pricing per grok-imagine-image-quality
        )


# ---------------------------------------------------------------------------
# Sessione stateful via Responses API
# ---------------------------------------------------------------------------


class GrokChatSession:
    """
    Sessione di chat stateful con Grok tramite la Responses API.

    Mantiene il previous_response_id tra i turni: ogni risposta ricevuta
    aggiorna l'ID interno che viene passato al turno successivo, permettendo
    a xAI di recuperare il contesto lato server senza reinviare la history.

    Usage::

        session = provider.start_chat_session(model="grok-4-1-fast-non-reasoning")
        r1 = session.send("Ciao, come ti chiami?")
        r2 = session.send("Cosa sai fare?")
        session.reset()  # azzera il contesto — prossima send riparte da zero
    """

    def __init__(
        self,
        provider: GrokProvider,
        model: str,
        system: str | None,
        web_search: bool,
    ) -> None:
        """
        Inizializza la sessione.

        Args:
            provider: Istanza GrokProvider da cui chiamare _chat_responses_api
            model: Identificatore modello da usare per tutta la sessione
            system: System prompt fisso per tutta la sessione (opzionale)
            web_search: Se True abilita search_parameters in ogni turno
        """
        self._provider = provider
        self.model = model
        self._system = system
        self._web_search = web_search
        self._previous_response_id: str | None = None

    def send(self, prompt: str) -> ChatResponse:
        """
        Invia un messaggio e aggiorna il contesto della sessione.

        Il previous_response_id accumulato viene passato all'API per mantenere
        la continuita' della conversazione lato server.

        Args:
            prompt: Testo del messaggio utente

        Returns:
            ChatResponse con la risposta del modello

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        response, response_id = self._provider._chat_responses_api(
            prompt=prompt,
            model=self.model,
            system=self._system,
            previous_response_id=self._previous_response_id,
            web_search=self._web_search,
        )
        if response_id:
            self._previous_response_id = response_id
        return response

    def reset(self) -> None:
        """
        Azzera il previous_response_id.

        Il turno successivo a reset() iniziera' una nuova conversazione
        senza contesto, come se la sessione fosse appena creata.
        """
        self._previous_response_id = None
        logger.debug(f"GrokChatSession: sessione azzerata (modello={self.model})")
