"""The library as context for any MCP client.

A stdio Model Context Protocol server that *proxies the running API* rather
than opening the database itself. The API already solves the hard parts —
the embedding model warms once, on its thread, with a real "warming" state;
search runs on CPU precisely so it cannot fight the worker for VRAM — and a
second process reimplementing any of that would drift. The cost is honest and
documented: the API must be running (`uv run scinet-up`), and the tools say
exactly that when it is not.
"""
