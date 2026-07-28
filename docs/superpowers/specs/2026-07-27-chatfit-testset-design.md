# ChatFit Golden Test Set Design

## 1. Overview
This document outlines the design and schema for a 30-item "Golden Test Set" for the ChatFit multi-agent system. The test set is designed to evaluate advanced LLM capabilities including intent routing, multi-agent collaboration (Training, Diet, Analyst), complex tool calling, memory management, state updates, and ambiguity clarification.

## 2. Format
The final output will be a single `.jsonl` file. Each line is a valid JSON object representing one complete test case (which may contain multiple dialogue turns).

## 3. Scenario Matrix (30 Cases Total)
We distribute the 30 cases across 6 high-difficulty dimensions:

1. **Ambiguity & Clarification (4 cases)**
   - *Focus*: User provides vague instructions ("I want to lose weight"). Agent must suspend action and ask follow-up questions instead of hallucinating parameters.
2. **Memory & State Override (5 cases)**
   - *Focus*: User alters previously stated information (e.g., changes target weight, fixes a typo in height). Agent must invoke update tools rather than append/create tools.
3. **Multi-Agent Collaboration (7 cases)**
   - *Focus*: Smooth context-aware handoffs between Diet, Training, and Analyst agents. (e.g., Diet agent logs food, Training agent recommends exercises based on that logged food).
4. **Tool Calling & Edge Cases (5 cases)**
   - *Focus*: Handling missing parameters, edge-case values, or scenarios requiring sequential tool chains.
5. **Cross-turn Coreference (5 cases)**
   - *Focus*: Long-context understanding. User refers to objects or states established several turns prior ("Change the dinner from yesterday").
6. **Complex Composite (4 cases)**
   - *Focus*: The ultimate test. Combines multi-agent routing, information updates, and complex tool calls in a single long-context session.

## 4. JSON Schema & Trajectory Evaluation

Each JSON line follows this schema, which includes explicit `expected_trajectory_eval` for automated test runner assertions.

```json
{
  "case_id": "test_B_01",
  "category": "memory_and_override",
  "description": "User modifies target weight, agent must update state.",
  "turns": [
    {
      "turn_id": 1,
      "user_input": "I originally wanted to reach 70kg, but now I want to aim for 65kg.",
      "expected_trajectory_eval": [
        {
          "eval_type": "routing",
          "expected_agent": "TrainingAgent"
        },
        {
          "eval_type": "tool_call",
          "expected_tool": "update_user_profile",
          "expected_params_include": {
            "target_weight": 65
          }
        },
        {
          "eval_type": "tool_avoidance",
          "avoid_tool": "create_user_profile"
        }
      ],
      "expected_response_eval": {
        "must_contain_semantics": "Acknowledge the update from 70kg to 65kg.",
        "tone": "encouraging"
      }
    }
  ]
}
```

### Supported `eval_type` values:
- `routing`: Asserts the orchestrator routed the request to the correct sub-agent.
- `tool_call`: Asserts a specific tool was called with the correct parameters.
- `tool_avoidance`: Asserts a specific tool was **not** called.
- `clarification_trigger`: Asserts the agent paused execution to ask the user for missing required context.
