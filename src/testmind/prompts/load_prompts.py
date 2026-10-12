"""Prompt 模板加载与渲染。

模板文件与代码分离（prompts/*.txt），业务代码只调 builder 函数，
模板中的 {xxx} 占位符由 str.format 渲染（JSON 花括号需写成 {{ }}）。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CURRENT_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_template(name: str) -> str:
    return (_CURRENT_DIR / name).read_text(encoding="utf-8")