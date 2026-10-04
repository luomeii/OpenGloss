# -*- coding: utf-8 -*-
"""路径解析：env > config.json > 相对默认。

之前 14 行绝对路径写死在代码里 → 项目不可迁移。这里统一收口：
- 所有默认值都是**相对于本文件**推导的（pae/ 目录、项目根）
- 可用环境变量覆盖（测试用）：PAE_DB_PATH / PAE_LEMMA_PATH / PAE_DICT_PATH / PAE_ROOT
- 可用 config.json 的 engine 段覆盖（生产用，支持相对路径，相对项目根解析）
"""
import json
import os
from pathlib import Path

PAE_ROOT = Path(os.environ.get("PAE_ROOT") or Path(__file__).resolve().parents[1])
PROJECT_ROOT = PAE_ROOT.parent


def _config():
    try:
        return json.loads((PAE_ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve(value, default):
    """相对路径**按 pae/ 目录**解析（config.json 就写在 pae/ 下，路径形如 ../resources/...）；
    绝对路径原样（兼容旧配置）。"""
    p = Path(value or default)
    if not p.is_absolute():
        p = (PAE_ROOT / p).resolve()
    return str(p)


def db_path():
    return str(Path(os.environ.get("PAE_DB_PATH") or (PAE_ROOT / "pae.db")))


def _engine_cfg(key, default):
    eng = _config().get("engine") or {}
    return eng.get(key) or default


def lemma_path():
    env = os.environ.get("PAE_LEMMA_PATH")
    if env:
        return str(Path(env))
    # 兜底默认必须与 config.json 的写法一致（../resources/...）：_resolve 是按 pae/ 解析的，
    # 少了 "../" 会指到 pae/resources/（不存在）——config.json 读不到时就会找错目录。
    return _resolve(_engine_cfg("lemma_path", "../resources/ECDICT/lemma.en.txt"),
                    "../resources/ECDICT/lemma.en.txt")


def dict_path():
    env = os.environ.get("PAE_DICT_PATH")
    if env:
        return str(Path(env))
    return _resolve(_engine_cfg("dict_path", "../resources/ECDICT/dict.sqlite"),
                    "../resources/ECDICT/dict.sqlite")


def describe():
    return {"pae_root": str(PAE_ROOT), "project_root": str(PROJECT_ROOT),
            "db": db_path(), "lemma": lemma_path(), "dict": dict_path(),
            "lemma_exists": Path(lemma_path()).exists(), "dict_exists": Path(dict_path()).exists()}