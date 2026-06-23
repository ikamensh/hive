"""Generated fallback used when Hive runs without a Git checkout.

The source of truth for the release line lives in ``hive.version``. Build and
deploy tooling may rewrite this file with the Git-derived version of the tree
they package or ship.
"""

__version__ = "0.1.0"
GIT_SHA = ""
DIRTY = False
SOURCE = "source-fallback"
