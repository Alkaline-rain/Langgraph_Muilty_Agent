from hashlib import sha256
from pathlib import Path
from code_assistant.service.contracts import SkillArguments, ToolObservation
from code_assistant.knowledge.contracts import RetrievalArguments

ROOT = Path(__file__).parent / "builtin"
DEFINITIONS = {
    "diagnose_python": ("Python 静态诊断", ("static_check",)),
    "repair_python": ("基于失败证据生成修复候选", ("review", "reflect", "generate_candidate", "diff_versions")),
    "validate_lcel": ("LCEL 静态检查；领域知识通过 retrieve_domain_docs 获取", ("static_check",)),
    "verify_cases": ("Docker 执行与宿主可信用例验收", ("evaluate_trusted_cases",)),
    "retrieve_domain_docs": ("检索与沙箱版本匹配的 LCEL 文档证据", ("search_domain_docs",)),
}


class SkillRegistry:
    def catalog(self):
        return [{"id": key, "version": "1.0.0", "description": value[0],
                 "tools": list(value[1]), "input_schema": self.schema(key).model_json_schema(),
                 "output_schema": ToolObservation.model_json_schema(),
                 "hash": sha256((ROOT / key / "SKILL.md").read_bytes()).hexdigest()}
                for key, value in DEFINITIONS.items()]

    def load(self, skill_id, arguments):
        if skill_id not in DEFINITIONS:
            raise ValueError("unknown_skill")
        parsed = self.schema(skill_id).model_validate(arguments)
        return (ROOT / skill_id / "SKILL.md").read_text(encoding="utf-8"), parsed

    @staticmethod
    def schema(skill_id):
        return RetrievalArguments if skill_id == "retrieve_domain_docs" else SkillArguments

    def authorize(self, skill_id, tool):
        if skill_id not in DEFINITIONS or tool not in DEFINITIONS[skill_id][1]:
            raise ValueError("tool_not_allowed")
