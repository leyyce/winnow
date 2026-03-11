# app/registry — top-level registry domain
# Public surface: registry singleton + ProjectRegistryEntry + ProjectBuilder ABC
from app.registry.manager import ProjectRegistryEntry, registry
from app.registry.base import ProjectBuilder

__all__ = ["ProjectRegistryEntry", "ProjectBuilder", "registry"]
