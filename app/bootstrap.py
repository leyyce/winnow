"""
Application bootstrap — initializes the registry via auto-discovery.

Call ``bootstrap()`` exactly once at application startup (e.g. from the
FastAPI ``lifespan`` handler in ``app/main.py``).  It is intentionally
idempotent: calling it multiple times (e.g. during tests) is safe because
``_Registry.load`` simply overwrites the existing entry for a given project_id.

Auto-Discovery
--------------
``bootstrap()`` scans every module inside the ``app.registry.projects``
package using :mod:`pkgutil` and :mod:`importlib`.  Any class found that:

* is a concrete subclass of :class:`~app.registry.base.ProjectBuilder`, and
* is **not** ``ProjectBuilder`` itself,

is automatically instantiated (zero-argument constructor) and loaded into the
registry.

Error isolation
---------------
A badly-formatted or broken project module will be skipped with a logged
warning rather than crashing the entire application.  Each module import and
each registry load is wrapped individually so that one bad project never
prevents the remaining projects from loading.

Adding a new project
--------------------
1. Create ``app/registry/projects/<your_project>.py`` with a ``ProjectBuilder``
   subclass.
2. That's it — no other file needs to change (Open/Closed Principle).
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

import app.registry.projects as _projects_pkg
from app.registry.base import ProjectBuilder
from app.registry.manager import registry

logger = logging.getLogger(__name__)


def bootstrap() -> None:
    """Discover and load all ProjectBuilder subclasses found in app.registry.projects."""
    for module_info in pkgutil.walk_packages(
        path=_projects_pkg.__path__,
        prefix=_projects_pkg.__name__ + ".",
    ):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            logger.warning(
                "bootstrap: failed to import project module %r — skipping.",
                module_info.name,
                exc_info=True,
            )
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ProjectBuilder)
                and obj is not ProjectBuilder
                and obj.__module__ == module.__name__  # defined here, not re-imported
            ):
                try:
                    registry.load(obj())
                except Exception:
                    logger.warning(
                        "bootstrap: failed to load project builder %r from %r — skipping.",
                        obj.__name__,
                        module_info.name,
                        exc_info=True,
                    )
