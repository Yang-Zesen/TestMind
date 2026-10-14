import json

import pytest


def test_requirement_design_has_source_and_positive_negative_boundary_coverage():
    from testmind.models.test_case import CaseSourceInput, design_cases

    source = CaseSourceInput(project_id="demo-project", kind="requirement", title="登录", content="用户使用正确密码登录；连续失败5次锁定账号。")
    cases = design_cases(source)
    assert {case.kind for case in cases} == {"positive", "negative", "boundary"}
    assert all(case.steps and case.expected and case.factors for case in cases)


def test_openapi_design_uses_actual_required_and_numeric_boundaries():
    from testmind.models.test_case import CaseSourceInput, design_cases

    content = json.dumps({"openapi":"3.0.3", "paths":{"/orders":{"post":{"requestBody":{"required":True,"content":{"application/json":{"schema":{"type":"object","required":["count"],"properties":{"count":{"type":"integer","minimum":1,"maximum":10}}}}}},"responses":{"201":{"description":"created"},"400":{"description":"invalid"}}}}}})
    cases = design_cases(CaseSourceInput(project_id="p", kind="openapi", title="Orders", content=content))
    assert all("POST /orders" in case.title for case in cases)
    assert any("count" in case.expected for case in cases if case.kind == "negative")
    assert any("1" in " ".join(case.steps) and "10" in " ".join(case.steps) for case in cases if case.kind == "boundary")


def test_openapi_external_references_are_rejected_without_fetching():
    from testmind.models.test_case import CaseSourceInput

    with pytest.raises(ValueError):
        CaseSourceInput(project_id="p", kind="openapi", title="Bad", content=json.dumps({"openapi":"3.0.3","paths":{},"components":{"schemas":{"x":{"$ref":"https://example.com"}}}}))
