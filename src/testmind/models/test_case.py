"""Bounded source and case contracts; deterministic demo generation is explicit."""

import hashlib
import json
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class CaseSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_id: str = Field(min_length=1, max_length=200)
    kind: Literal["requirement", "openapi"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=24000)

    @model_validator(mode="after")
    def validate_spec(self) -> "CaseSourceInput":
        if self.kind == "openapi":
            spec = json.loads(self.content)
            if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3.") or not isinstance(spec.get("paths"), dict):
                raise ValueError("openapi_3_document_required")
            def walk(value: Any, depth: int = 0) -> None:
                if depth > 40:
                    raise ValueError("source_nesting_exceeded")
                if isinstance(value, dict):
                    if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
                        raise ValueError("external_references_not_supported")
                    for item in value.values():
                        walk(item, depth + 1)
                elif isinstance(value, list):
                    for item in value:
                        walk(item, depth + 1)
            walk(spec)
            if len(spec["paths"]) > 20:
                raise ValueError("openapi_path_budget_exceeded")
        return self


class CaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["positive", "negative", "boundary"]
    preconditions: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(min_length=1, max_length=12)
    expected: str = Field(min_length=1, max_length=2000)
    factors: list[str] = Field(min_length=1, max_length=12)
    risk: Literal["low", "medium", "high"] = "medium"
    coverage_gaps: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def bound_items(self) -> "CaseBody":
        if any(not item.strip() or len(item) > 1000 for item in [*self.steps, *self.factors, *self.coverage_gaps]):
            raise ValueError("case_item_budget_exceeded")
        return self


class CaseVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaseEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    body: CaseBody


class CaseReview(CaseVersionRequest):
    decision: Literal["approved", "rejected"]


class CaseDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=3)
    session_id: str = Field(default="case-design", min_length=1, max_length=200)
    use_knowledge: bool = False


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    while "$ref" in schema:
        reference = schema["$ref"]
        if reference in seen:
            raise ValueError("cyclic_schema_reference")
        seen.add(reference)
        value: Any = spec
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        if not isinstance(value, dict):
            raise TypeError("invalid_schema_reference")
        schema = value
    return schema


def design_cases(source: CaseSourceInput) -> list[CaseBody]:
    """Offline draft templates; expected behavior stays tied to source text."""
    targets: list[tuple[str, list[str], str, str]] = []
    if source.kind == "requirement":
        rules = [part.strip() for part in re.split(r"[\n；;]", source.content) if part.strip()][:8]
        for rule in rules:
            numbers = re.findall(r"\d+(?:\.\d+)?", rule)
            boundary = "、".join(numbers) if numbers else "来源未给出明确阈值"
            targets.append((source.title, [rule[:500]], rule[:1000], boundary))
    else:
        spec = json.loads(source.content)
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                body = _resolve(operation.get("requestBody", {}), spec)
                schema = _resolve(body.get("content", {}).get("application/json", {}).get("schema", {}), spec)
                factors = list(schema.get("required", []))
                boundaries = []
                for name, raw in schema.get("properties", {}).items():
                    field = _resolve(raw, spec)
                    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
                        if key in field:
                            boundaries.append(f"{name}.{key}={field[key]}")
                    if "enum" in field:
                        boundaries.append(f"{name}.enum={field['enum']}")
                for parameter in operation.get("parameters", []):
                    parameter = _resolve(parameter, spec)
                    if parameter.get("required"):
                        factors.append(parameter["name"])
                statuses = ", ".join(operation.get("responses", {}).keys())
                title = f"{method.upper()} {path}"
                targets.append((title, factors or ["请求格式"], f"按 OpenAPI 校验 {', '.join(factors) or '请求'}；响应契约：{statuses or '未定义'}", "; ".join(boundaries) or "来源未定义数值边界"))
    if not targets:
        raise ValueError("source_has_no_testable_rules")
    cases = []
    for title, factors, expected, boundary in targets[:12]:
        for kind, label, action in (
            ("positive", "正向", "提交符合来源规则的有效输入"),
            ("negative", "负向", "逐项遗漏必填因子或提供不符合来源规则的输入"),
            ("boundary", "边界", f"分别验证边界值及相邻无效值：{boundary}"),
        ):
            cases.append(CaseBody(title=f"{title[:260]} / {label}", kind=cast(Literal["positive", "negative", "boundary"], kind), preconditions="授权测试项目可用；使用独立测试数据", steps=[action, "记录实际结果并与来源契约比较"], expected=expected, factors=factors[:12], coverage_gaps=["需人工补充具体输入、业务错误码和环境前置条件"] if "未定义" in boundary or "未给出" in boundary else [], risk="high" if kind == "negative" else "medium"))
    return cases
