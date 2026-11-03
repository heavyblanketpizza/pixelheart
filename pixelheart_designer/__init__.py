"""The AI designer: a provider-neutral LLM layer that edits a character through validated tools.

This package imports only the standard library, ``pixelheart_core`` and, lazily, the
``anthropic`` SDK. It never imports Qt, never writes files, and only the two
providers touch the network.
"""
