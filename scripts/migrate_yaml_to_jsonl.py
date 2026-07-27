import yaml
import json
from pathlib import Path

def migrate():
    yaml_path = Path("tests/eval/eval_cases.yaml")
    if not yaml_path.exists():
        print("YAML not found, skipping.")
        return

    jsonl_path = Path("evaluation/chatfit_golden_test_set.jsonl")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
        
    converted_cases = []
    for case in cases:
        new_case = {
            "case_id": case["id"],
            "capability_tags": ["Legacy Migration"],
            "description": case.get("description", "Migrated from eval_cases.yaml"),
            "turns": []
        }
        
        for turn_idx, turn in enumerate(case.get("turns", [])):
            expected_trajectory_eval = []
            
            # Map tools
            for t in turn.get("expected_tools", []):
                assertion = {
                    "eval_type": "tool_call",
                    "expected_tool": t["name"]
                }
                if "args_contain" in t and t["args_contain"]:
                    assertion["expected_args_contain"] = t["args_contain"]
                expected_trajectory_eval.append(assertion)
                
            # Map routes
            for r in turn.get("expected_routes", []):
                expected_trajectory_eval.append({
                    "eval_type": "routing",
                    "expected_agent": r
                })
                
            # Map DB state
            for db in turn.get("expected_db_state", []):
                expected_trajectory_eval.append({
                    "eval_type": "db_state",
                    "query": db["query"],
                    "expected_value": db["expected_value"]
                })
                
            # Response eval
            response_eval = {}
            if "expected_response_contains" in turn and turn["expected_response_contains"]:
                response_eval["must_contain_semantics"] = turn["expected_response_contains"]
            
            new_turn = {
                "turn_id": turn_idx + 1,
                "user_input": turn["user"],
                "expected_trajectory_eval": expected_trajectory_eval
            }
            if response_eval:
                new_turn["expected_response_eval"] = response_eval
                
            new_case["turns"].append(new_turn)
            
        converted_cases.append(new_case)
        
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for c in converted_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
            
    print(f"Successfully migrated {len(converted_cases)} cases.")
    yaml_path.unlink()
    print("Deleted tests/eval/eval_cases.yaml")

if __name__ == "__main__":
    migrate()
