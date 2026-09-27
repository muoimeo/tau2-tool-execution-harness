"""A2 provider-retry and provider-observability implementation.

These modules extend A1 only around model-provider generation failures. They
never retry a native tool call or a harness validation rejection.
"""
