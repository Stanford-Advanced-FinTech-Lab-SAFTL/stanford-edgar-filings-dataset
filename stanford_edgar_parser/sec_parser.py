#!/usr/bin/env python
"""Compatibility shim for old ``stanford_edgar_parser/sec_parser.py`` usage."""

from __future__ import annotations

import pathlib
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from stanford_edgar_parser import api as _api
else:
    from . import api as _api

__all__ = list(_api.__all__)
globals().update({name: getattr(_api, name) for name in __all__})


if __name__ == "__main__":
    if __package__ in {None, ""}:
        from stanford_edgar_parser.__main__ import main
    else:
        from .__main__ import main
    raise SystemExit(main())
