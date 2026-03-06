#!/usr/bin/env python3
from __future__ import annotations

import shlex
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    path = Path(sys.argv[1]).expanduser()
    if not path.exists():
        return 0

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if not k:
            continue
        print(f"export {k}={shlex.quote(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
