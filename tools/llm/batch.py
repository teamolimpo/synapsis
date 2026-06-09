"""
Logica batch per il tool llm.

Gestisce:
- Espansione di glob e lista file
- Lettura e rendering di template Markdown (sezione ## Prompt)
- Esecuzione chiamate API per ogni file di input
- Salvataggio risultati con progress tracking su stderr

Utilizzo tipico (da cli.py):
    results = run_batch(provider, template_path, input_paths, output_dir)
"""

from __future__ import annotations

import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from tools.llm.providers.base import ProviderProtocol


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

_PLACEHOLDER_KBA = "{{kba_text}}"
_PLACEHOLDER_FILENAME = "{{filename}}"
_PLACEHOLDER_DATE = "{{date}}"


# ---------------------------------------------------------------------------
# Parsing template
# ---------------------------------------------------------------------------


def extract_prompt_section(template_text: str) -> str:
    """
    Estrae il contenuto della sezione '## Prompt' da un file Markdown.

    Considera tutto il testo dopo '## Prompt' fino alla successiva heading
    di livello 1 o 2 (oppure fine file).

    Args:
        template_text: Contenuto grezzo del file Markdown

    Returns:
        Testo della sezione prompt, con whitespace iniziale/finale rimosso

    Raises:
        ValueError: Se la sezione '## Prompt' non e' presente nel file
    """
    # Cerca la heading ## Prompt (case-insensitive, eventuali spazi extra)
    pattern = re.compile(
        r"^##\s+Prompt\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(template_text)
    if not match:
        raise ValueError(
            "Il file template non contiene una sezione '## Prompt'. "
            "Aggiungi una heading '## Prompt' seguita dal testo del prompt."
        )

    # Testo dopo la heading
    after_heading = template_text[match.end() :]

    # Trova la prossima heading di livello 1 o 2
    next_heading = re.search(r"^#{1,2}\s", after_heading, re.MULTILINE)
    if next_heading:
        section = after_heading[: next_heading.start()]
    else:
        section = after_heading

    return section.strip()


# ---------------------------------------------------------------------------
# Rendering template
# ---------------------------------------------------------------------------


def render_template(
    template: str,
    kba_text: str,
    filename: str,
    extra_vars: dict[str, str] | None = None,
) -> str:
    """
    Sostituisce i placeholder nel template con i valori concreti.

    Placeholder supportati:
    - {{kba_text}}  — contenuto del file di input
    - {{filename}}  — nome del file di input senza estensione
    - {{date}}      — data odierna in formato ISO (YYYY-MM-DD)
    - {{KEY}}       — variabile personalizzata passata via --var KEY=VALUE

    Args:
        template: Testo del template con placeholder
        kba_text: Contenuto del file di input da iniettare
        filename: Nome del file di input (senza estensione)
        extra_vars: Dizionario di variabili aggiuntive KEY->VALUE (opzionale)

    Returns:
        Template con tutti i placeholder sostituiti
    """
    today = date.today().isoformat()
    result = template
    result = result.replace(_PLACEHOLDER_KBA, kba_text)
    result = result.replace(_PLACEHOLDER_FILENAME, filename)
    result = result.replace(_PLACEHOLDER_DATE, today)
    if extra_vars:
        for k, v in extra_vars.items():
            result = result.replace(f"{{{{{k}}}}}", v)
    return result


# ---------------------------------------------------------------------------
# Espansione input
# ---------------------------------------------------------------------------


def expand_inputs(raw_inputs: list[str]) -> list[Path]:
    """
    Espande una lista di stringhe (path o glob) in una lista di Path esistenti.

    Usa Path.glob() per espandere i pattern — sicuro su Windows con backslash.
    I duplicati vengono rimossi mantenendo l'ordine di apparizione.

    Args:
        raw_inputs: Lista di stringhe che possono essere path assoluti,
                    path relativi, o glob pattern

    Returns:
        Lista di Path esistenti, deduplicata e ordinata

    Raises:
        ValueError: Se nessun file viene trovato dopo l'espansione
    """
    seen: set[Path] = set()
    result: list[Path] = []

    for raw in raw_inputs:
        # Proviamo prima come path diretto
        direct = Path(raw)
        if direct.is_file():
            if direct not in seen:
                seen.add(direct)
                result.append(direct)
            continue

        # Altrimenti trattiamo come glob pattern
        # Separiamo la parte fissa (directory base) dalla parte glob
        # Usiamo Path("").glob() sulla root corretta per compatibilita' Windows
        raw_path = Path(raw)
        parts = raw_path.parts

        # Trova il primo segmento con wildcard
        base_parts: list[str] = []
        glob_parts: list[str] = []
        wildcard_found = False
        for part in parts:
            if not wildcard_found and ("*" in part or "?" in part or "[" in part):
                wildcard_found = True
            if wildcard_found:
                glob_parts.append(part)
            else:
                base_parts.append(part)

        if not wildcard_found:
            # E' un path che non esiste — lo ignoriamo con un warning
            logger.warning(f"File non trovato e non e' un glob pattern: {raw}")
            continue

        if base_parts:
            base = Path(*base_parts)
        else:
            base = Path()

        glob_pattern = "/".join(glob_parts)

        try:
            matches = sorted(base.glob(glob_pattern))
        except Exception as exc:
            logger.warning(f"Errore espansione glob '{raw}': {exc}")
            continue

        for match in matches:
            if match.is_file() and match not in seen:
                seen.add(match)
                result.append(match)

    if not result:
        raise ValueError(f"Nessun file trovato per i pattern: {', '.join(raw_inputs)}")

    return result


# ---------------------------------------------------------------------------
# Esecuzione batch
# ---------------------------------------------------------------------------


def run_merge(
    provider: ProviderProtocol,
    provider_name: str,
    template: str,
    input_files: list[Path],
    output_dir: Path | None,
    model: str | None = None,
    system: str | None = None,
    extra_vars: dict[str, str] | None = None,
) -> int:
    """
    Esegue una singola chiamata API con tutti i file di input concatenati.

    Differisce da run_batch() in quanto non itera sui file: li unisce in un
    unico blocco di testo separato da divisori, sostituisce {{kba_text}} con
    il blocco risultante e fa una sola chiamata al provider.

    Il nome del file di output e' costruito dal primo file di input:
        <primo_stem>_merged-<provider>.md

    Se output_dir e' None, la risposta va su stdout.

    Args:
        provider: Istanza del provider LLM
        provider_name: Nome del provider per il nome del file di output
        template: Testo del template (gia' estratto dalla sezione ## Prompt)
        input_files: Lista di Path dei file da unire in una sola chiamata
        output_dir: Directory dove salvare il risultato (None = stdout)
        model: Override modello (None = default provider)
        system: System prompt opzionale

    Returns:
        0 se la chiamata ha avuto successo, 1 altrimenti
    """
    # Costruisce il blocco testo unificato
    sections: list[str] = []
    for f in input_files:
        try:
            content = f.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"ERRORE lettura '{f.name}': {exc}", file=sys.stderr)
            logger.error(f"run_merge: errore lettura '{f}' — {exc}")
            return 1
        sections.append(f"--- {f.name} ---\n{content}")

    merged_text = "\n\n".join(sections)

    # Il filename placeholder usa il primo file
    first_stem = input_files[0].stem
    prompt = render_template(template, merged_text, first_stem, extra_vars)

    print(
        f"[merge] {len(input_files)} file → 1 chiamata API ...",
        end=" ",
        flush=True,
        file=sys.stderr,
    )

    t_start = time.monotonic()
    try:
        response = provider.chat(prompt=prompt, model=model, system=system)
    except RuntimeError as exc:
        elapsed = time.monotonic() - t_start
        print(f"ERRORE API ({elapsed:.1f}s): {exc}", file=sys.stderr)
        logger.error(f"run_merge: errore API — {exc}")
        return 1

    elapsed = time.monotonic() - t_start

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{first_stem}_merged-{provider_name}.md"
        try:
            out_file.write_text(response.text, encoding="utf-8")
            print(f"ok ({elapsed:.1f}s) -> {out_file.name}", file=sys.stderr)
        except Exception as exc:
            print(f"ok ma ERRORE scrittura ({elapsed:.1f}s): {exc}", file=sys.stderr)
            logger.error(f"run_merge: errore scrittura '{out_file}' — {exc}")
            return 1
    else:
        print(f"ok ({elapsed:.1f}s)", file=sys.stderr)
        print(response.text)

    return 0


def run_batch(
    provider: ProviderProtocol,
    provider_name: str,
    template: str,
    input_files: list[Path],
    output_dir: Path | None,
    model: str | None = None,
    system: str | None = None,
    skip_existing: bool = False,
    extra_vars: dict[str, str] | None = None,
) -> int:
    """
    Esegue la chiamata API per ogni file di input e gestisce l'output.

    Per ogni file:
    1. Legge il contenuto
    2. Renderizza il template con i placeholder
    3. Chiama il provider
    4. Scrive il risultato su file (se output_dir) o su stdout

    Il progresso viene stampato su stderr nel formato:
        [1/10] nome-file.md ... ok (1.2s)
        [2/10] altro-file.md ... ERRORE: messaggio

    Args:
        provider: Istanza del provider LLM
        provider_name: Nome del provider per i nomi file di output
        template: Testo del template (gia' estratto dalla sezione ## Prompt)
        input_files: Lista di Path dei file da processare
        output_dir: Directory dove salvare i risultati (None = stdout)
        model: Override modello (None = default provider)
        system: System prompt opzionale

    Returns:
        Numero di file elaborati con errore (0 = tutto ok)
    """
    total = len(input_files)
    error_count = 0

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    for idx, input_path in enumerate(input_files, start=1):
        prefix = f"[{idx}/{total}] {input_path.name}"
        print(f"{prefix} ...", end=" ", flush=True, file=sys.stderr)

        # Skip se l'output esiste già
        if skip_existing and output_dir is not None:
            filename_stem = input_path.stem
            expected_out = output_dir / f"{filename_stem}-{provider_name}.json"
            if expected_out.exists():
                print(f"{prefix} skip (già analizzato)", file=sys.stderr)
                continue

        # Lettura file di input
        try:
            kba_text = input_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"ERRORE lettura: {exc}", file=sys.stderr)
            logger.error(f"Batch: errore lettura '{input_path}' — {exc}")
            error_count += 1
            continue

        # Rendering template
        filename_stem = input_path.stem
        prompt = render_template(template, kba_text, filename_stem, extra_vars)

        # Chiamata API
        t_start = time.monotonic()
        try:
            response = provider.chat(prompt=prompt, model=model, system=system)
        except RuntimeError as exc:
            elapsed = time.monotonic() - t_start
            print(f"ERRORE API ({elapsed:.1f}s): {exc}", file=sys.stderr)
            logger.error(f"Batch: errore API per '{input_path}' — {exc}")
            error_count += 1
            continue

        elapsed = time.monotonic() - t_start

        # Output
        if output_dir is not None:
            # Estrae JSON puro dalla risposta (il provider avvolge in ```json...```)
            raw_response = response.text
            json_match = re.search(r"```json\s*([\s\S]*?)\s*```", raw_response)
            output_text = json_match.group(1).strip() if json_match else raw_response.strip()

            out_file = output_dir / f"{filename_stem}-{provider_name}.json"
            try:
                out_file.write_text(output_text, encoding="utf-8")
                print(f"ok ({elapsed:.1f}s) -> {out_file.name}", file=sys.stderr)
            except Exception as exc:
                print(f"ok ma ERRORE scrittura ({elapsed:.1f}s): {exc}", file=sys.stderr)
                logger.error(f"Batch: errore scrittura '{out_file}' — {exc}")
                error_count += 1
        else:
            print(f"ok ({elapsed:.1f}s)", file=sys.stderr)
            separator = f"\n{'=' * 60}\n# {input_path.name}\n{'=' * 60}\n"
            print(separator)
            print(response.text)

    return error_count
