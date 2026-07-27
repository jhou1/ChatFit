import re

def update_readme():
    with open("README.md", "r", encoding="utf-8") as f:
        content = f.read()

    # Define the new Agent Evaluation section
    new_eval_section = """## Agent Evaluation (Agent 能力评测)

ChatFit 采用了一套“测试即文档”的严谨评测架构，实现了工程代码与业务评测用例的完全解耦。

1. **唯一数据源 (JSONL Golden Test Set)**：
   测试用例维护在 `evaluation/chatfit_golden_test_set.jsonl` 中。每一条 Case 严格定义了用户的输入（`user_input`）、期望的确定性轨迹（`expected_trajectory_eval`）以及基于场景动态配置的裁判打分基准（`rubrics`）。

2. **多维动态 Rubric LLM-as-a-Judge**：
   摒弃硬编码，根据用例场景在 JSONL 中动态分配 7 大核心维度的权重进行 LLM 裁判打分：
   * 多轮上下文一致性 (Multi-turn Context Consistency)
   * 任务完成率 (Task Completion Rate)
   * 工具选择 (Tool Selection)
   * 轨迹合理性 (Trajectory Rationality)
   * 澄清能力 (Clarification Capability)
   * 安全边界 (Safety Boundaries)
   * 交互质量 (Interaction Quality)

3. **四大量化指标与门禁 (Release Gate Scorecard)**：
   评测引擎会根据生成的 `Trajectory` 和 Judge 分数，聚合产出四大核心量化指标：
   * **TCR (任务完成率)**：成功闭环的用例占比。
   * **TA (工具与参数准确率)**：精准阻断幻觉参数的工具调用率。
   * **CCR (上下文一致性)**：多轮与跨域记忆的接力通过率。
   * **ERR (异常恢复率)**：面对模糊意图的主动澄清或拒绝能力。
   最终在终端与 `evaluation/latest_report.md` 中输出报告，门禁拦截失败时返回非零退出码。

```bash
# 启动真实的 LLM 和 Agent Graph 跑通 43 条全量测试集，并生成评估报告
make eval

# 极速/本地调试模式 (高并发，关闭 LLM Judge 打分)
uv run python evaluation/runner.py --concurrency 10 --no-judge
```"""

    # Replace the old Agent Evaluation section
    # Use regex to find everything from "## Agent Evaluation" to the next "## "
    pattern_eval = re.compile(r"## Agent Evaluation.*?((?=\n## Data|## 数据)|(?=\n## ))", re.DOTALL)
    
    if pattern_eval.search(content):
        content = pattern_eval.sub(new_eval_section + "\n\n", content)
    else:
        print("Could not find the Agent Evaluation section")
        return

    # Update directory tree
    content = content.replace("├── evaluation/             # 数据集 schema、确定性 Grader 与 Scorecard\n├── scripts/                # Evaluation 等运维脚本\n├── tests/\n│   ├── eval/               # Agent Code Grader\n│   └── test_*.py           # 单元与 API 回归测试",
                              "├── evaluation/             # JSONL测试集、动态Rubric、Runner与评分器\n├── scripts/                # 运维脚本\n├── tests/                  # 工程质量(单元与API测试，与Agent能力评测解耦)")

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    update_readme()
