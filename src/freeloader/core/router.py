# Forwarding facade. The actual Router lives at
# `freeloader.router`; this module re-exports it so the gate-3
# expectation `src/freeloader/core/router.py` is satisfied without
# forcing a 30-import-site migration. Both import paths are valid;
# `freeloader.router` is the canonical one used in code, and
# `freeloader.core.router` is the architectural-layer name used in
# documentation and gates.
from freeloader.router import Router

__all__ = ["Router"]
