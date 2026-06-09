"""
Provider Google/Gemini per il tool llm.

Usa il nuovo SDK google-genai (NON google-generativeai, deprecato dal 30 nov 2025).

Riferimento SDK: https://googleapis.github.io/python-genai/
"""

from __future__ import annotations

import time

from loguru import logger

from tools.llm.image_client import ImageResult
from tools.llm.providers.base import ChatResponse, ModelInfo


class GeminiChatSession:
    """
    Sessione di chat multi-turn con Gemini.

    Mantiene la history automaticamente tramite l'oggetto sessione
    restituito da client.chats.create() dell'SDK google-genai.
    Non istanziare direttamente: usare GeminiProvider.start_chat_session().
    """

    def __init__(self, session: object, model: str) -> None:
        """
        Inizializza la sessione wrappando l'oggetto SDK.

        Args:
            session: Oggetto restituito da client.chats.create()
            model: Nome del modello in uso (per logging e ChatResponse)
        """
        self._session = session
        self.model = model
        logger.debug(f"GeminiChatSession creata — modello={model}")

    def send(self, prompt: str) -> ChatResponse:
        """
        Invia un messaggio mantenendo la history della sessione.

        Chiama self._session.send_message(prompt) sull'oggetto SDK.
        La history accumulata viene gestita automaticamente dal client.

        Args:
            prompt: Testo del messaggio da inviare

        Returns:
            ChatResponse con testo e metadati

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        logger.debug(f"GeminiChatSession.send — modello={self.model}")
        start = time.monotonic()
        try:
            response = self._session.send_message(prompt)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.error(f"GeminiChatSession: errore durante send_message — {exc}")
            raise RuntimeError(f"Errore chiamata Gemini (sessione): {exc}") from exc

        elapsed = time.monotonic() - start

        text = response.text or ""
        input_tokens: int | None = None
        output_tokens: int | None = None

        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_token_count", None)
            output_tokens = getattr(usage, "candidates_token_count", None)

        logger.debug(
            f"GeminiChatSession: risposta in {elapsed:.2f}s, "
            f"token input={input_tokens}, output={output_tokens}"
        )

        return ChatResponse(
            text=text,
            model_used=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
        )

    def reset(self) -> None:
        """
        Azzera la history della sessione.

        L'SDK google-genai non espone un metodo reset() nativo sull'oggetto
        Chat. Il reset non e' supportato: l'operazione viene loggata come
        warning e la sessione rimane invariata. Per ottenere una sessione
        pulita, richiamare GeminiProvider.start_chat_session() e usare
        il nuovo oggetto restituito.
        """
        logger.warning(
            "GeminiChatSession.reset() non supportato dall'SDK google-genai. "
            "La history NON e' stata azzerata. "
            "Per resettare, richiama provider.start_chat_session() e usa la nuova sessione."
        )


class GeminiProvider:
    """
    Provider per Google/Gemini.

    Utilizza l'SDK google-genai (Client API diretta, senza Vertex AI).
    Richiede solo una GEMINI_API_KEY ottenibile da https://aistudio.google.com/apikey.

    Modello di default: gemini-2.5-flash-lite
    (il piu' leggero e veloce, con free tier, ideale per consulti rapidi)
    """

    default_model: str = "gemini-2.5-flash"

    def __init__(self, api_key: str) -> None:
        """
        Inizializza il client google-genai.

        Args:
            api_key: Chiave API Gemini (ottenibile da https://aistudio.google.com/apikey)

        Raises:
            ImportError: Se la libreria google-genai non e' installata
        """
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "La libreria 'google-genai' non e' installata. Esegui: uv add google-genai"
            ) from exc

        self._client = genai.Client(api_key=api_key)
        logger.debug("GeminiProvider inizializzato")

    def chat(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        agent_count: int = 4,
    ) -> ChatResponse:
        """
        Invia un prompt a Gemini e restituisce la risposta.

        Args:
            prompt: Testo del prompt da inviare
            model: Override del modello (None = usa default_model)
            system: Messaggio di sistema opzionale

        Returns:
            ChatResponse con testo della risposta e metadati token/tempo

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        from google.genai import types as genai_types

        effective_model = model or self.default_model

        # Configurazione opzionale del system prompt
        config: genai_types.GenerateContentConfig | None = None
        if system:
            config = genai_types.GenerateContentConfig(
                system_instruction=system,
            )

        logger.debug(
            f"GeminiProvider: chiamata a modello={effective_model}, system={'si' if system else 'no'}"
        )

        start = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            logger.error(f"GeminiProvider: errore durante la chiamata API — {exc}")
            raise RuntimeError(f"Errore chiamata Gemini: {exc}") from exc

        elapsed = time.monotonic() - start

        text = response.text or ""
        input_tokens: int | None = None
        output_tokens: int | None = None

        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count

        logger.debug(
            f"GeminiProvider: risposta ricevuta in {elapsed:.2f}s, "
            f"token input={input_tokens}, output={output_tokens}"
        )

        return ChatResponse(
            text=text,
            model_used=effective_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
        )

    def start_chat_session(
        self,
        model: str | None = None,
        system: str | None = None,
    ) -> GeminiChatSession:
        """
        Crea una sessione di chat multi-turn con Gemini.

        Usa client.chats.create() dell'SDK google-genai, che mantiene
        la history dei messaggi automaticamente lato client.

        Args:
            model: Modello da usare (None = usa default_model)
            system: System prompt opzionale

        Returns:
            GeminiChatSession pronta per ricevere messaggi via .send()

        Raises:
            RuntimeError: Se la creazione della sessione fallisce
        """
        from google.genai import types as genai_types

        effective_model = model or self.default_model

        config: genai_types.GenerateContentConfig | None = None
        if system:
            config = genai_types.GenerateContentConfig(
                system_instruction=system,
            )

        logger.debug(
            f"GeminiProvider.start_chat_session — modello={effective_model}, "
            f"system={'si' if system else 'no'}"
        )
        try:
            session = self._client.chats.create(
                model=effective_model,
                config=config,
            )
        except Exception as exc:
            logger.error(f"GeminiProvider: errore creazione sessione chat — {exc}")
            raise RuntimeError(f"Errore creazione sessione Gemini: {exc}") from exc

        return GeminiChatSession(session=session, model=effective_model)

    def list_models(self) -> list[ModelInfo]:
        """
        Recupera i modelli disponibili dall'API Gemini.

        Chiama self._client.models.list() e filtra solo i modelli
        che supportano generateContent (modelli di inferenza).

        Returns:
            Lista di ModelInfo ordinata per id

        Raises:
            RuntimeError: Se la chiamata API fallisce
        """
        logger.debug("GeminiProvider: recupero lista modelli")
        try:
            # L'SDK google-genai restituisce un pager — iteriamo direttamente
            all_models = list(self._client.models.list())
        except Exception as exc:
            logger.error(f"GeminiProvider: errore list_models — {exc}")
            raise RuntimeError(f"Errore recupero modelli Gemini: {exc}") from exc

        # Filtro: solo modelli che supportano generateContent
        # e il cui nome inizia con "models/gemini"
        filtered_ids: list[str] = []
        for m in all_models:
            # m.name e' nella forma "models/gemini-2.5-flash-lite"
            name = getattr(m, "name", "") or ""
            supported_actions = getattr(m, "supported_actions", []) or []
            if name.startswith("models/gemini") and "generateContent" in supported_actions:
                # Normalizza: rimuove il prefisso "models/"
                filtered_ids.append(name.removeprefix("models/"))

        models = [
            ModelInfo(id=mid, is_default=(mid == self.default_model))
            for mid in sorted(filtered_ids)
        ]
        logger.debug(f"GeminiProvider: {len(models)} modelli trovati")
        return models

    # ------------------------------------------------------------------
    # Image generation (google-genai nativo — Imagen)
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
        Genera un'immagine usando l'SDK nativo google-genai.

        Usa due pathway:
        - **Gemini Flash Image** (``generate_content`` con ``response_modalities=["IMAGE"]``)
          — default, funziona su free tier
        - **Imagen 4** (``generate_images``) — se il modello richiesto e' un imagen-*

        Args:
            prompt: Testo del prompt per la generazione
            model: Modello (default: gemini-2.0-flash-exp-image-generation, free tier)
            size: Dimensione (1K, 2K)
            ratio: Aspect ratio (1:1, 16:9, etc.)
            negative_prompt: Non supportato
            seed: Non supportato
            input_image_path: Non supportato
            image_config_json: Non supportato

        Returns:
            ImageResult con l'immagine in base64 o errore
        """
        import base64

        from google.genai import types as genai_types

        effective_model = model or "gemini-2.0-flash-exp-image-generation"
        logger.debug(f"GeminiProvider.generate_image: modello={effective_model}, ratio={ratio}")

        try:
            # Pathway Imagen (modelli imagen-*) via generate_images dedicato
            if effective_model.startswith("imagen-"):
                response = self._client.models.generate_images(
                    model=effective_model,
                    prompt=prompt,
                    config=genai_types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=ratio,
                        output_mime_type="image/png",
                    ),
                )
                generated_images = response.generated_images
                if not generated_images:
                    return ImageResult(
                        success=False, error_type="generic",
                        error_message="Nessuna immagine nella risposta",
                        model=effective_model,
                    )
                img = generated_images[0].image
                if img is None or img.image_bytes is None:
                    return ImageResult(
                        success=False, error_type="generic",
                        error_message="Immagine vuota nella risposta",
                        model=effective_model,
                    )
                img_bytes = img.image_bytes

            # Pathway Gemini Image (modelli gemini-*-image) via generate_content multimodale
            else:
                response = self._client.models.generate_content(
                    model=effective_model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=genai_types.ImageConfig(
                            aspect_ratio=ratio,
                            image_size=size.upper(),
                        ),
                    ),
                )
                img_bytes = None
                for part in response.parts:
                    if part.inline_data:
                        img_bytes = part.inline_data.data
                        break
                if img_bytes is None:
                    return ImageResult(
                        success=False, error_type="generic",
                        error_message="Nessuna immagine nella risposta Gemini",
                        model=effective_model,
                    )

        except Exception as exc:
            logger.error(f"GeminiProvider.generate_image: errore API — {exc}")
            return ImageResult(
                success=False,
                error_type="generic",
                error_message=f"Errore generazione immagine Gemini: {exc}",
                model=effective_model,
            )

        b64 = base64.b64encode(img_bytes).decode()
        logger.debug(f"GeminiProvider.generate_image: immagine generata ({len(b64)} chars base64)")

        return ImageResult(
            success=True,
            image_base64=b64,
            mime_type="image/png",
            model=effective_model,
            cost=0.0,  # free tier per gemini-2.0-flash-exp-image-generation
        )
