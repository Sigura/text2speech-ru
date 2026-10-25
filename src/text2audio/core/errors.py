"""Errors shared by the synthesis stages."""

from __future__ import annotations


class SynthesisError(Exception):
    """The book cannot be synthesized as configured (missing pack, plugin, env…)."""
