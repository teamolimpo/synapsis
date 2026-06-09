"""
Interfaccia comune per i provider LLM del tool llm.

Ogni provider deve implementare il Protocol ProviderProtocol:
- __init__(self, api_key: str) — riceve la chiave API
- chat(self, prompt, model, system) -> str — esegue la chiamata e ritorna il testo
- default_model: str — modello di default per il provider
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tools.llm.image_client import ImageResult


@dataclass
class ModelInfo:
    """
    Informazioni su un modello disponibile da un provider.

    Attributes:
        id: Identificatore del modello (es. 'grok-4-1-fast-non-reasoning')
        is_default: True se e' il modello di default del provider
    """

    id: str
    is_default: bool = False


class ChatSessionProtocol(Protocol):
    """
    Interfaccia per una sessione di chat multi-turn.

    Implementata da oggetti restituiti da provider.start_chat_session().
    La history viene mantenuta internamente dall'implementazione.
    """

    def send(self, prompt: str) -> ChatResponse:
        """
        Invia un messaggio mantenendo la history della sessione.

        Args:
            prompt: Testo del messaggio da inviare

        Returns:
            ChatResponse con testo della risposta e metadati
        """
        ...

    def reset(self) -> None:
        """
        Azzera la history della sessione corrente.

        L'implementazione e' provider-specifica: alcuni SDK supportano
        il reset diretto, altri richiedono la ricreazione della sessione.
        """
        ...


@runtime_checkable
class ProviderProtocol(Protocol):
    """
    Interfaccia comune per tutti i provider LLM.

    Ogni provider concreto deve rispettare questo contratto:
    - ricevere la API key nel costruttore
    - esporre un attributo default_model
    - implementare il metodo chat()
    """

    default_model: str

    def __init__(self, api_key: str) -> None:
        """
        Inizializza il provider con la API key.

        Args:
            api_key: Chiave API per autenticarsi con il provider
        """
        ...

    def chat(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        agent_count: int = 4,
    ) -> ChatResponse:
        """
        Invia un prompt al modello e restituisce la risposta.

        Args:
            prompt: Il testo del prompt da inviare
            model: Modello da usare (None = usa default_model)
            system: Messaggio di sistema opzionale
            agent_count: Numero agenti per modelli multi-agent (4 o 16). Ignorato altrimenti.

        Returns:
            ChatResponse con testo e metadati sulla chiamata
        """
        ...

    def list_models(self) -> list[ModelInfo]:
        """
        Restituisce l'elenco dei modelli disponibili per questo provider.

        Returns:
            Lista di ModelInfo ordinata per id
        """
        ...

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
        Genera un'immagine dal prompt testuale.

        Args:
            prompt: Testo del prompt per la generazione
            model: Modello da usare (None = default del provider)
            size: Dimensione immagine (1K, 2K, 4K)
            ratio: Aspect ratio (1:1, 16:9, etc.)
            negative_prompt: Prompt negativo (modelli che lo supportano)
            seed: Seed per riproducibilita'
            input_image_path: Path per image-to-image (modelli che lo supportano)
            image_config_json: JSON extra per configurazioni avanzate

        Returns:
            ImageResult con l'immagine generata o errore
        """
        ...

    def start_chat_session(
        self,
        model: str | None = None,
        system: str | None = None,
    ) -> ChatSessionProtocol:
        """
        Crea una sessione di chat multi-turn (opzionale).

        I provider che non supportano sessioni stateful sollevano
        NotImplementedError. Verificare con hasattr() prima di chiamare.

        Args:
            model: Modello da usare (None = default del provider)
            system: System prompt opzionale

        Returns:
            Oggetto sessione con metodi send() e reset()

        Raises:
            NotImplementedError: Se il provider non supporta sessioni multi-turn
        """
        raise NotImplementedError(
            f"{type(self).__name__} non supporta sessioni di chat multi-turn."
        )


class ChatResponse:
    """
    Risposta strutturata da un provider LLM.

    Contiene il testo della risposta e metadati opzionali
    (token consumati, tempo impiegato, modello effettivamente usato).
    """

    def __init__(
        self,
        text: str,
        model_used: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        """
        Args:
            text: Testo della risposta del modello
            model_used: Nome del modello effettivamente usato
            input_tokens: Token consumati in input (None se non disponibile)
            output_tokens: Token consumati in output (None se non disponibile)
            elapsed_seconds: Tempo impiegato per la chiamata in secondi
        """
        self.text = text
        self.model_used = model_used
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.elapsed_seconds = elapsed_seconds

    @property
    def total_tokens(self) -> int | None:
        """Token totali (input + output). None se entrambi non disponibili."""
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)
