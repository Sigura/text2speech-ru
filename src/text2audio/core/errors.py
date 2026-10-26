"""Errors shared by the synthesis stages."""

from __future__ import annotations


class SynthesisError(Exception):
    """The book cannot be synthesized as configured (missing pack, plugin, env…)."""


class CoverError(Exception):
    """A cover generator failed; the book gets a typographic cover instead (§6.8.2)."""
