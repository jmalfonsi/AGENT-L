"""Point d'entrée : `python -m agentl <commande> <fichier>`."""
import contextlib
import sys

from .cli import main

try:
    code = main()
except BrokenPipeError:          # tolère `| head`, `| less`
    code = 0
with contextlib.suppress(BrokenPipeError):
    sys.stdout.flush()
sys.exit(code)
