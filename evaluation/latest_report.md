# Evaluation Report: 20260727-172213

- **Commit**: `local`
- **Dataset**: `evaluation/chatfit_golden_test_set.jsonl@1`
- **Model**: `gemini-3.5-flash`
- **Release Gate**: **✅ PASS**

## 1. 定量核心能力指标 (Quantitative Agent Metrics)

| 指标名称 (Metric) | 得分 (Score) | 阈值 (Threshold) | 评估公式与意义 (Rationale & Formula) |
| --- | --- | --- | --- |
| **任务完成率 (TCR)** | 100.0% | 90.0% | **意义**: 衡量Agent成功结束会话闭环的能力。<br>**公式**: `Passed Cases / Total Cases` |
| **工具与参数准确率 (TA)** | 100.0% | 95.0% | **意义**: 衡量调用动作、参数提取及防呆机制的精确度，防止污染数据库。<br>**公式**: `1 - (Tool Failures / Tool Dependent Cases)` |
| **上下文一致性 (CCR)** | 100.0% | 85.0% | **意义**: 衡量长程对话中的记忆穿透和多轮状态承接能力，低于85%会产生“智障感”。<br>**公式**: `Passed Memory & Multi-turn Cases / Total Such Cases` |
| **异常恢复率 (ERR)** | 100.0% | 80.0% | **意义**: 衡量模糊意图下的反问澄清能力以及功能越界时的拦截能力。<br>**公式**: `Passed Edge & Clarification Cases / Total Such Cases` |

## 2. LLM-as-a-Judge 软性体验指标 (Qualitative Rubric Metrics)

- **总体 Rubric 综合得分**: **4.71 / 5.0**
- **LLM 裁判覆盖率**: 100.0%

## 3. 失败用例追踪 (Failed Cases Trace)

- ✨ **All cases passed perfectly!** ✨
