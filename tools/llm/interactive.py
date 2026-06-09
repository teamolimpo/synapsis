"""
Modalita' interattiva del tool llm.

Presenta un menu testuale che guida l'utente nella scelta di:
- Un prompt template da Team/Prompts/ oppure testo libero
- File di input (opzionale, per batch)
- Provider e modello
- Salvataggio del risultato

Il discovery dei prompt avviene scansionando Team/Prompts/**/*.md
e leggendo il frontmatter YAML (campi 'title' e 'description').
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from tools.llm.providers.base import ProviderProtocol


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

_SEPARATOR = "-" * 40


# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict[str, str]:
    """
    Estrae il frontmatter YAML da un file Markdown.

    Gestisce il blocco delimitato da '---' iniziale e finale.
    Parsing minimale riga per riga (no dipendenza PyYAML).

    Args:
        text: Contenuto grezzo del file Markdown

    Returns:
        Dizionario con le chiavi/valori del frontmatter (stringhe)
    """
    result: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return result

    in_frontmatter = True
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and in_frontmatter:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")

    return result


def discover_prompts(prompts_dir: Path) -> list[dict[str, str]]:
    """
    Scansiona la directory dei prompt e restituisce i template disponibili.

    Per ogni file .md trovato legge il frontmatter YAML cercando i campi
    'title' e 'description'. Se mancano usa il percorso relativo come titolo.

    Args:
        prompts_dir: Directory radice dove cercare i file .md

    Returns:
        Lista di dict con chiavi: 'path', 'title', 'description', 'label'
        Ordinata per label (percorso relativo senza estensione)
    """
    if not prompts_dir.is_dir():
        logger.debug(f"Directory prompt non trovata: {prompts_dir}")
        return []

    results: list[dict[str, str]] = []
    for md_file in sorted(prompts_dir.glob("**/*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Impossibile leggere {md_file}: {exc}")
            continue

        fm = _parse_frontmatter(text)
        # Label = percorso relativo senza estensione (es. "team/analisi-impatto")
        try:
            label = md_file.relative_to(prompts_dir).with_suffix("").as_posix()
        except ValueError:
            label = md_file.stem

        title = fm.get("title") or label
        description = fm.get("description") or ""

        results.append(
            {
                "path": str(md_file),
                "title": title,
                "description": description,
                "label": label,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Utility I/O
# ---------------------------------------------------------------------------


def _input(prompt_text: str) -> str:
    """
    Wrapper su input() che gestisce EOF (Ctrl+Z su Windows, Ctrl+D su Unix).

    Args:
        prompt_text: Testo da mostrare prima del cursore

    Returns:
        Stringa inserita dall'utente, oppure "" in caso di EOF
    """
    try:
        return input(prompt_text)
    except EOFError:
        return ""


def _read_multiline(intro: str = "") -> str:
    """
    Legge testo multiriga da stdin fino a una riga vuota o EOF.

    Args:
        intro: Messaggio introduttivo da stampare prima della lettura

    Returns:
        Testo inserito dall'utente (righe unite con newline)
    """
    if intro:
        print(intro)
    lines: list[str] = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Menu principale
# ---------------------------------------------------------------------------


def run_interactive(
    providers_map: dict[str, type],
    get_api_key_fn: callable[[str], str],
    default_provider: str,
    prompts_dir: Path,
    web_search: bool = False,
) -> int:
    """
    Avvia il menu interattivo del tool llm.

    Guida l'utente passo per passo nella scelta del prompt, del file di
    input (opzionale), del provider e del modello. Alla fine chiede se
    salvare il risultato su file.

    Args:
        providers_map: Dizionario nome -> classe provider (es. PROVIDERS)
        get_api_key_fn: Funzione che data una stringa provider ritorna la API key
        default_provider: Provider selezionato di default
        prompts_dir: Directory radice per la discovery dei prompt
        web_search: Se True abilita la ricerca web nella sessione chat (solo Grok)

    Returns:
        Exit code (0 = successo, 1 = errore o uscita senza completare)
    """
    print("\n=== LLM — Team Olimpo ===\n")

    # --- Discovery prompt ---
    prompt_templates = discover_prompts(prompts_dir)

    # --- Menu scelta prompt ---
    print("Prompt disponibili:")
    for i, pt in enumerate(prompt_templates, start=1):
        desc_part = f"  — {pt['description']}" if pt["description"] else ""
        print(f"  [{i}] {pt['label']}{desc_part}")
    free_idx = len(prompt_templates) + 1
    print(f"  [{free_idx}] Testo libero")

    default_choice = str(free_idx)
    raw_choice = _input(f"Scelta [default: {default_choice}]: ").strip()
    if raw_choice == "":
        raw_choice = default_choice

    try:
        choice_idx = int(raw_choice)
    except ValueError:
        print("Scelta non valida. Uscita.", file=sys.stderr)
        return 1

    # --- Risoluzione prompt ---
    template_text: str | None = None
    prompt_text: str | None = None

    if 1 <= choice_idx <= len(prompt_templates):
        selected = prompt_templates[choice_idx - 1]
        try:
            raw_md = Path(selected["path"]).read_text(encoding="utf-8")
        except Exception as exc:
            print(f"Errore lettura template: {exc}", file=sys.stderr)
            return 1

        # Estrai sezione ## Prompt
        from tools.llm.batch import extract_prompt_section

        try:
            template_text = extract_prompt_section(raw_md)
        except ValueError as exc:
            print(f"Errore template: {exc}", file=sys.stderr)
            return 1

        # --- Input file opzionale ---
        print()
        print("File di input (glob, path, o invio per testo libero):")
        print("  Esempi: Library/documents/*.md  oppure  Library/documents/nk-2400-0150.md")
        raw_input_path = _input("> ").strip()

        if raw_input_path:
            from tools.llm.batch import expand_inputs, render_template

            try:
                input_files = expand_inputs([raw_input_path])
            except ValueError as exc:
                print(f"Errore: {exc}", file=sys.stderr)
                return 1

            if len(input_files) == 1:
                # Singolo file — esegue direttamente senza loop batch
                try:
                    kba_text = input_files[0].read_text(encoding="utf-8")
                except Exception as exc:
                    print(f"Errore lettura file: {exc}", file=sys.stderr)
                    return 1
                prompt_text = render_template(
                    template_text,
                    kba_text,
                    input_files[0].stem,
                )
            else:
                # Batch interattivo
                print(f"\nTrovati {len(input_files)} file. Avvio elaborazione batch...\n")
                provider_name, provider_instance = _resolve_provider(
                    providers_map, get_api_key_fn, default_provider
                )
                if provider_instance is None:
                    return 1
                model_override = _ask_model(provider_instance)
                print()
                from tools.llm.batch import run_batch

                errors = run_batch(
                    provider=provider_instance,
                    provider_name=provider_name,
                    template=template_text,
                    input_files=input_files,
                    output_dir=None,
                    model=model_override or None,
                )
                return 0 if errors == 0 else 1
        else:
            # Nessun file — chiede testo libero per {{kba_text}}
            print()
            kba_text = _read_multiline(
                "Testo da inserire nel prompt (termina con una riga vuota o Ctrl+Z):"
            )
            if not kba_text.strip():
                print("Nessun testo inserito. Uscita.", file=sys.stderr)
                return 1
            from tools.llm.batch import render_template

            prompt_text = render_template(template_text, kba_text, "input")

    elif choice_idx == free_idx:
        # Modalita' chat multi-turn: se il provider supporta sessioni stateful,
        # avvia un loop interattivo con history persistente.
        print()
        provider_name, provider_instance = _resolve_provider(
            providers_map, get_api_key_fn, default_provider
        )
        if provider_instance is None:
            return 1
        model_override = _ask_model(provider_instance)
        effective_model = model_override or None

        print()
        system_raw = _input("System prompt (invio per nessuno): ").strip()
        system_text = system_raw if system_raw else None

        # Verifica supporto sessioni multi-turn
        if hasattr(provider_instance, "start_chat_session"):
            return _run_chat_loop(
                provider=provider_instance,
                provider_name=provider_name,
                model=effective_model,
                system=system_text,
                web_search=web_search,
            )
        else:
            # Fallback: singola chiamata stateless
            prompt_text = _read_multiline(
                "Prompt (termina con una riga vuota o Ctrl+Z su Windows):"
            )
            if not prompt_text.strip():
                print("Nessun prompt inserito. Uscita.", file=sys.stderr)
                return 1
            print("\n[Invio richiesta...]\n")
            t_start = time.monotonic()
            try:
                response = provider_instance.chat(
                    prompt=prompt_text,
                    model=effective_model,
                    system=system_text,
                )
            except RuntimeError as exc:
                print(f"Errore: {exc}", file=sys.stderr)
                return 1
            elapsed = time.monotonic() - t_start
            print(_SEPARATOR)
            print("--- Risposta ---")
            print(response.text)
            print(_SEPARATOR)
            print(
                f"(Provider: {provider_name} | Modello: {response.model_used} | Tempo: {elapsed:.1f}s)"
            )
            return 0
    else:
        print("Scelta non valida. Uscita.", file=sys.stderr)
        return 1

    # --- Scelta provider e modello (per percorsi template) ---
    print()
    provider_name, provider_instance = _resolve_provider(
        providers_map, get_api_key_fn, default_provider
    )
    if provider_instance is None:
        return 1
    model_override = _ask_model(provider_instance)

    # --- Chiamata API ---
    print("\n[Invio richiesta...]\n")
    t_start = time.monotonic()
    try:
        response = provider_instance.chat(
            prompt=prompt_text,
            model=model_override or None,
        )
    except RuntimeError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - t_start

    # --- Output risposta ---
    print(_SEPARATOR)
    print("--- Risposta ---")
    print(response.text)
    print(_SEPARATOR)
    print(f"(Provider: {provider_name} | Modello: {response.model_used} | Tempo: {elapsed:.1f}s)")

    # --- Salvataggio opzionale ---
    print()
    save_raw = _input("Salvare in file? [s/N]: ").strip().lower()
    if save_raw in ("s", "si", "y", "yes"):
        suggested = f"llm-output-{provider_name}.txt"
        raw_path = _input(f"Percorso file [{suggested}]: ").strip()
        save_path = Path(raw_path) if raw_path else Path(suggested)
        try:
            save_path.write_text(response.text, encoding="utf-8")
            print(f"Salvato in: {save_path.resolve()}")
        except Exception as exc:
            print(f"Errore salvataggio: {exc}", file=sys.stderr)
            return 1

    return 0


# ---------------------------------------------------------------------------
# Helper interni
# ---------------------------------------------------------------------------


def _resolve_provider(
    providers_map: dict[str, type],
    get_api_key_fn: callable[[str], str],
    default_provider: str,
) -> tuple[str, ProviderProtocol | None]:
    """
    Chiede all'utente il provider e inizializza l'istanza.

    Returns:
        Tupla (nome_provider, istanza) oppure (nome, None) in caso di errore
    """
    provider_names = "/".join(sorted(providers_map.keys()))
    raw = _input(f"Provider [{provider_names}, default: {default_provider}]: ").strip().lower()
    provider_name = raw if raw in providers_map else default_provider

    try:
        api_key = get_api_key_fn(provider_name)
    except SystemExit:
        return provider_name, None

    provider_class = providers_map[provider_name]
    try:
        instance = provider_class(api_key=api_key)
    except ImportError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return provider_name, None

    return provider_name, instance  # type: ignore[return-value]


def _run_chat_loop(
    provider: ProviderProtocol,
    provider_name: str,
    model: str | None,
    system: str | None,
    web_search: bool = False,
) -> int:
    """
    Avvia un loop di chat multi-turn con history persistente.

    Chiama provider.start_chat_session() e usa session.send() per ogni turno.
    Comandi speciali riconosciuti (case-insensitive):
    - /reset        — azzera la history della sessione corrente
    - /exit         — esce dal loop
    - quit          — sinonimo di /exit
    - /websearch on  — abilita ricerca web per i turni successivi (solo Grok)
    - /websearch off — disabilita ricerca web

    Args:
        provider: Istanza provider con supporto start_chat_session()
        provider_name: Nome del provider (per output)
        model: Override modello (None = default del provider)
        system: System prompt opzionale
        web_search: Stato iniziale della ricerca web (solo Grok Responses API)

    Returns:
        Exit code (0 = uscita normale, 1 = errore)
    """
    current_web_search = web_search

    def _create_session() -> object:
        return provider.start_chat_session(  # type: ignore[attr-defined]
            model=model,
            system=system,
            web_search=current_web_search,
        )

    try:
        session = _create_session()
    except RuntimeError as exc:
        print(f"Errore avvio sessione: {exc}", file=sys.stderr)
        return 1

    print()
    ws_status = " | web-search ON" if current_web_search else ""
    print(f"=== Chat multi-turn ({provider_name} | {session.model}{ws_status}) ===")  # type: ignore[attr-defined]
    print("Comandi: /reset  azzera history   /websearch on|off  ricerca web   /exit  esci")
    print(_SEPARATOR)

    while True:
        try:
            user_input = _input("Tu: ").strip()
        except KeyboardInterrupt:
            print("\nInterrotto. Uscita.")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ("/exit", "quit"):
            print("Uscita dalla chat.")
            break

        if cmd == "/reset":
            session.reset()  # type: ignore[attr-defined]
            try:
                session = _create_session()
                print("[Sessione ricreata — history azzerata]")
            except RuntimeError as exc:
                print(f"Errore ricreazione sessione: {exc}", file=sys.stderr)
                return 1
            continue

        if cmd in ("/websearch on", "/websearch off"):
            new_state = cmd.endswith("on")
            if not hasattr(provider, "_chat_responses_api"):
                print("[web-search disponibile solo per Grok con Responses API]")
            elif new_state == current_web_search:
                status_str = "gia' attiva" if new_state else "gia' disattiva"
                print(f"[Ricerca web {status_str}]")
            else:
                current_web_search = new_state
                session.reset()  # type: ignore[attr-defined]
                try:
                    session = _create_session()
                    on_off = "attivata" if current_web_search else "disattivata"
                    print(f"[Ricerca web {on_off} — sessione ricreata]")
                except RuntimeError as exc:
                    print(f"Errore ricreazione sessione: {exc}", file=sys.stderr)
                    return 1
            continue

        print("[...]")
        try:
            response = session.send(user_input)  # type: ignore[attr-defined]
        except RuntimeError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1

        print(f"\nAssistente: {response.text}")
        tokens_info = ""
        if response.total_tokens is not None:
            tokens_info = f" | Token: {response.total_tokens}"
        elapsed_info = (
            f" | Tempo: {response.elapsed_seconds:.1f}s" if response.elapsed_seconds else ""
        )
        print(f"({response.model_used}{tokens_info}{elapsed_info})")
        print(_SEPARATOR)

    return 0


def _ask_model(provider: ProviderProtocol) -> str:
    """
    Chiede all'utente il modello da usare (invio = usa il default).

    Args:
        provider: Istanza provider da cui leggere il default

    Returns:
        Stringa modello (vuota se l'utente vuole il default)
    """
    default = provider.default_model
    raw = _input(f"Modello [invio per default: {default}]: ").strip()
    return raw
