"""Dynamic edits (fx-v1): beats, a deterministic resolver and typed, editable effect cues.

Python decides what happens when (``contracts``, ``resolver``, ``warp``, ``duck``, ``mapping``); the TypeScript
fx-core (``frontend/src/lib/fx/core``) draws it in the browser and, through deno, on the worker. Every module here
is pure (no ffmpeg, cv2 or network) unless its docstring says otherwise. Import submodules directly: this package
re-exports nothing.
"""
