"""
拼写校正节点
"""
import re

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage
from rouge_score import rouge_scorer

from testmind.core.errors import ValidationError
from testmind.infrastructure.llm.llm_client import get_llm_client
from testmind.models.spell_correction_model import CorrectionResult, ErrorDetail
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.tools.spell_correction_tools import check_common_errors, query_dictionary
from testmind.tools.update_time import _now

_LLM = get_llm_client(response_format=False)
_SYSTEM_PROMPT = """你是一个专业的中文文本纠错专家，擅长识别和修正各种类型的中文错误。
【错误类型】
1. 拼写错误：拼音输入错误、形近字错误
2. 语法错误：的/地/得混用、语序错误、成分残缺
3. 用词不当：词语搭配错误、专业术语误用
4. 标点错误：标点缺失、误用或冗余
5. 语义错误：逻辑矛盾、事实错误
【输出要求】
1. 严格按 JSON Schema 输出，不要添加额外说明 ← 防止模型"自由发挥"
2. 无需返回错误位置，由系统根据 error_span 定位回填 ← 避免 LLM 数字符索引出错
3. 修正理由需结合上下文解释 ← 提升可解释性
4. 置信度计算：高确定性错误(0.9+)，需推断错误(0.7-0.9)，不确定(<0.7) ← 引导模型自我评估
【特别注意】
- 保留原文风格和语义，只修正明确错误 ← 避免过度修正
- 专有名词（人名、地名、企业名）需谨慎修正 ← 降低误杀率
- 不确定时宁可不改，置信度设为低值 ← 安全优先原则"""
_AGENT = create_agent(
    _LLM,
    tools=[query_dictionary, check_common_errors],
    system_prompt=_SYSTEM_PROMPT,
    response_format=ToolStrategy(CorrectionResult)
)


class SpellCorrectionNode(BaseNode):
    """
    拼写纠错节点
    """

    name: str = "spell_correction_node"

    def _split_long_text(self, text: str, max_length: int = 300) -> list[str]:
        """
        将长文本拆成多个段落 , 每个段落不超过max_length
        """
        # 优先按照句子分割
        sentences = re.split(r'(?<=[。！？；])', text)
        chunks = []
        current_chunk = ""
        for sentence in sentences:
            if len(current_chunk) + len(sentence) > max_length and current_chunk:
                chunks.append(current_chunk.strip())  # 保存当前块
                current_chunk = sentence  # 新块从当前句开始
            else:
                current_chunk += sentence  # 继续累加

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks if len(chunks) > 1 else [text]

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """计算 Levenshtein 编辑距离"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _calculate_confidence(self, original_text: str, corrected_text: str) -> float:
        """
        计算纠错结果的置信度
        """
        if original_text == corrected_text:
            return 1.0

        # 1.基础分
        base_confidence = 0.6
        # 2.奖励分
        rs = rouge_scorer.RougeScorer(["rougeL"])
        scores = rs.score(original_text, corrected_text)
        rougle_l = scores["rougeL"].fmeasure

        # 3.惩罚分
        lev_distance = self._levenshtein_distance(original_text, corrected_text)
        max_len = max(len(original_text), len(corrected_text))
        edit_ratio = lev_distance / max_len if max_len > 0 else 1
        penalty = edit_ratio * 0.2

        # 4.计算最终置信度
        confidence = base_confidence + rougle_l * .3 - penalty
        return max(0, min(1, confidence))

    def _merge_corrections(self, chunks: list[str], results: list[CorrectionResult]) -> CorrectionResult:
        """将多个段落的纠错结果合并成一个完整的纠错结果"""
        merged_text = ""
        merged_errors = []  # 保持为 ErrorDetail 对象列表
        offset = 0

        for chunk, result in zip(chunks, results):
            # 关键：offset基于原始块长度累加，而非修正后长度！
            merged_text += result.corrected_text
            # 修复：将字典转换回 ErrorDetail 对象
            for error in result.errors:
                # 创建新的 ErrorDetail 对象（保持Pydantic验证）
                adjusted_error = ErrorDetail(
                    position=error.position + offset,
                    error_span=error.error_span,
                    error_type=error.error_type,
                    correction=error.correction,
                    reason=error.reason
                )
                merged_errors.append(adjusted_error)
            offset += len(chunk)

        avg_confidence = sum(r.confidence for r in results) / len(results)
        explanation = f"文本被分割为{len(chunks)}个语义块分别纠错，综合置信度:{avg_confidence:.2f}"

        return CorrectionResult(
            original_text="".join(chunks),
            corrected_text=merged_text,
            errors=merged_errors,  # 确保是 ErrorDetail 对象列表
            confidence=avg_confidence,
            explanation=explanation
        )

    async def process(self, state: GraphState) -> GraphState:
        self.log_step(self.name, "开始进行拼写纠错")
        question = state.get("raw_query", "")
        chunks = self._split_long_text(question)
        if not chunks:
            raise ValidationError(
                message="用户输入的问题不能为空",
                node_name=self.name,
            )
        results = []
        for chunk in chunks:
            result = await _AGENT.ainvoke(
                {
                    "messages": [
                        HumanMessage(content=chunk)
                    ]
                }
            )
            if result is None:
                continue

            correction = result.get("structured_response")
            if correction is None:
                continue

            # 评估纠错结果是否靠谱：取算法置信度与 LLM 自评置信度的较小值
            confidence = self._calculate_confidence(chunk, correction.corrected_text)
            correction.confidence = min(confidence, correction.confidence)
            results.append(correction)

        if not results:
            raise ValidationError(
                message="纠错结果为空",
                node_name=self.name,
            )

        if len(results) > 1:
            final_result = self._merge_corrections(chunks, results)
        else:
            final_result = results[0]

        self.log_step(f"{self.name}-纠错结果", final_result.corrected_text)

        # 低置信度不采纳：返回原文，附上低置信度说明
        if final_result.confidence < 0.4:
            self.log_step(self.name, f"纠错结果置信度太低({final_result.confidence:.2f})，不采纳")
            state["corrected_query"] = question
            state["correction_detail"] = CorrectionResult(
                original_text=question,
                corrected_text=question,
                errors=[],
                confidence=final_result.confidence,
                explanation=f"纠错结果置信度太低，评分为：{final_result.confidence:.2f}，不采纳",
            ).model_dump()
            return state

        state["corrected_query"] = final_result.corrected_text
        state["correction_detail"] = final_result.model_dump()
        state["updated_at"] = _now()
        return state

