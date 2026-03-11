"""
Application bootstrap — initializes the registry via auto-discovery.

Call ``bootstrap()`` once at application startup (e.g. from ``app/main.py``
or a FastAPI ``lifespan`` handler). It is intentionally idempotent: calling it
multiple times (e.g. during tests) is safe because ``_Registry.load`` simply
overwrites the existing entry for a given project_id.

Auto-Discovery
--------------
``bootstrap()`` scans every module inside the ``app.registry.projects``
package using :mod:`pkgutil` and :mod:`importlib`.  Any class found that:

* is a concrete subclass of :class:`~app.registry.base.ProjectBuilder`, and
* is **not** ``ProjectBuilder`` itself,

is automatically instantiated (zero-argument constructor) and loaded into the
registry.

Adding a new project
--------------------
1. Create ``app/registry/projects/<your_project>.py`` with a ``ProjectBuilder``
   subclass.
2. That's it — no other file needs to change (Open/Closed Principle).
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import app.registry.projects as _projects_pkg
from app.registry.base import ProjectBuilder
from app.registry.manager import registry


def bootstrap() -> None:
    """Discover and load all ProjectBuilder subclasses found in app.registry.projects."""
    for module_info in pkgutil.walk_packages(
        path=_projects_pkg.__path__,
        prefix=_projects_pkg.__name__ + ".",
    ):
        module = importlib.import_module(module_info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ProjectBuilder)
                and obj is not ProjectBuilder
                and obj.__module__ == module.__name__  # defined here, not re-imported
            ):
                registry.load(obj())


# ── Auto-bootstrap on import so that tests and services work without
#    explicitly calling bootstrap().  Production code should call it
#    explicitly inside the FastAPI lifespan handler for clarity.
bootstrap()
