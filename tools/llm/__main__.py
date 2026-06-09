"""
Entry point per l'esecuzione del modulo con python -m tools.llm.

Routing manuale:
- Se il primo argomento non preceduto da -- e' 'models': usa app_models
- Altrimenti: usa app (comando principale)
"""

import sys

from tools.llm.cli import app, app_models

# Trova il primo argomento che non sia un flag (non inizia con -)
_first_positional = next(
    (a for a in sys.argv[1:] if not a.startswith("-")),
    None,
)

if _first_positional == "models":
    # Rimuove 'models' da argv e lascia passare il resto (es. --provider grok)
    sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "models"]
    app_models()
else:
    app()
