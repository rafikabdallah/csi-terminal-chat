"""Terminal colour codes.

Plain ANSI escapes - no external dependency. Colour is disabled
automatically when output is not a terminal (e.g. piped to a file),
so log redirection stays clean.
"""

import os
import sys

if os.name == "nt":
    os.system("")

_ENABLED = sys.stdout.isatty()


def _c(code):
    return code if _ENABLED else ""


RESET   = _c("\033[0m")
BOLD    = _c("\033[1m")
DIM     = _c("\033[2m")

RED     = _c("\033[31m")
GREEN   = _c("\033[32m")
YELLOW  = _c("\033[33m")
BLUE    = _c("\033[34m")
MAGENTA = _c("\033[35m")
CYAN    = _c("\033[36m")
GREY    = _c("\033[90m")

BANNER = f"""{CYAN}{BOLD}
   ____ ____ ___   ____ _   _    _  _____
  / ___/ ___|_ _| / ___| | | |  / \\|_   _|
 | |   \\___ \\| | | |   | |_| | / _ \\ | |
 | |___ ___) | | | |___|  _  |/ ___ \\| |
  \\____|____/___| \\____|_| |_/_/   \\_\\_|
{RESET}{GREY}          terminal chat over raw TCP{RESET}
"""
