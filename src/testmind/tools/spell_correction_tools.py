"""拼写校正工具集"""
import re

from langchain.tools import tool

from testmind.core.logging import get_logger

_LOGGER = get_logger(__name__)


@tool
def query_dictionary(word: str) -> str:
    """
    查询中文词典获取词语的正确用法、近义词和常见搭配。
    适用于验证疑似错误词汇是否为真实存在的词语。
    """
    # 模拟词典查询（实际应用中可对接权威词典 API）
    dictionary = {
        "万豪": ("品牌名", "万豪国际酒店集团，非房企名称"),
        "万好万家": ("企业名", "浙江万好万家文化股份有限公司"),
        "东日": ("企业名", "浙江东日股份有限公司"),
        "冬日": ("普通词汇", "冬季的白天，非企业名称"),
    }
    result = dictionary.get(word, ("未知词汇", "建议结合上下文判断"))
    return f"词条: {word}\n类型: {result[0]}\n说明: {result[1]}"


@tool
def check_common_errors(text: str) -> str:
    """
    检查文本中常见的中文输入错误模式（如拼音相似错误、形近字错误）。
    """
    # 常见错误模式库：硬编码模式库（维护成本高）
    error_patterns = [
        (r"始曰苯", "使日本", "拼音输入错误"),
        (r"万豪万家", "万好万家", "企业名称混淆"),
        (r"冬日", "东日", "形近字错误（房企名称）"),
        (r"的", "地|得", "的/地/得混用（需结合语法判断）"),
        (r"在.+?在", "", "重复使用'在'字"),
    ]
    # 改进方案：外部配置化
    # import json
    # with open("error_patterns.json") as f:
    # error_patterns = json.load(f) # 支持热更新
    matches = []
    for pattern, correction, error_type in error_patterns:
        if re.search(pattern, text):
            matches.append(f"- '{pattern}' → '{correction}' ({error_type})")
    if matches:
        _LOGGER.info("检测到潜在错误模式:\n" + "\n".join(matches))
        return "检测到潜在错误模式:\n" + "\n".join(matches)
    return "未检测到常见错误模式"
