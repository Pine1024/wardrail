"""模型路由：解析 models.yaml，按路由键前缀匹配并按 priority 降级。

models.yaml 结构简单（两层映射 + 列表 + 扁平 dict），依赖白名单里没有
pyyaml，故内置一个极简解析器，仅支持本项目的配置格式。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ModelEntry:
    name: str
    endpoint: str
    api_key_env: str
    priority: int


def _parse_scalar(value: str) -> str | int:
    value = value.strip().strip('"').strip("'")
    return int(value) if value.isdigit() else value


def parse_models_yaml(text: str) -> list[ModelEntry]:
    """解析 models.yaml 中 models 列表，每项为扁平 key: value 字典。"""
    entries: list[ModelEntry] = []
    current: dict[str, str | int] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "models:":
            continue
        if stripped.startswith("- "):  # 新的模型条目
            if current:
                entries.append(ModelEntry(**current))  # type: ignore[arg-type]
            current = {}
            stripped = stripped[2:]
        if current is None:
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            current[key.strip()] = _parse_scalar(val)
    if current:
        entries.append(ModelEntry(**current))  # type: ignore[arg-type]
    return entries


class ModelRouter:
    """根据 "private/qwen2.5-14b" 这类路由键选择模型，不可用时降级。"""

    def __init__(self, config_path: str | Path = "models.yaml") -> None:
        path = Path(config_path)
        if not path.exists():
            path = Path(__file__).parent.parent / "models.yaml"
        self.models: list[ModelEntry] = sorted(
            parse_models_yaml(path.read_text(encoding="utf-8")),
            key=lambda m: m.priority,
        )

    def resolve(self, model: str) -> tuple[ModelEntry, list[ModelEntry]]:
        """返回 (首选模型, 降级候选列表)。

        首选为名称匹配（前缀匹配）的最高优先级模型；
        降级候选为剩余全池按 priority 升序排列。
        """
        matched = [m for m in self.models if m.name == model or model.startswith(m.name)]
        primary = matched[0] if matched else self.models[0]
        fallbacks = [m for m in self.models if m is not primary]
        return primary, fallbacks

    def get_api_key(self, entry: ModelEntry) -> str | None:
        env_name = entry.api_key_env
        return os.environ.get(env_name) if env_name else None
