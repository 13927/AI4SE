from __future__ import annotations

import importlib.resources as pkg_resources
import json
from typing import Any


def load_embedded_schema(name: str) -> dict[str, Any]:
    """
    从包内 resources/schemas 读取 schema（原型阶段：减少外部依赖）。
    """
    pkg = "aise.resources.schemas"
    with pkg_resources.files(pkg).joinpath(name).open("r", encoding="utf-8") as f:
        return json.load(f)

