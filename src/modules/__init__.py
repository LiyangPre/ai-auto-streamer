"""Trusted, installed studio modules. Add registrations in builtin.py."""
from .core import ModuleRegistry
from .builtin import MODULES

__all__ = ['ModuleRegistry', 'MODULES']
