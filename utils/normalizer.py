import json
import re

class PracticeNormalizer:
    def __init__(self, config_path: str = "config/synonyms.json"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        # reverse index aliases and keys
        self.alias_to_key = {}
        for k, v in self.config.items():
            for alias in [k] + v["aliases"]:
                self.alias_to_key[alias.lower()] = k

    def normalize(self, practice: str) -> str:
        search_key = re.sub(r"[.-]", " ", practice.lower().strip())
        if search_key in self.alias_to_key:
            return self.alias_to_key[search_key]

        # fuzzy match
        sorted_aliases = sorted(self.alias_to_key.keys(), key=len, reverse=True)
        for alias in sorted_aliases:
            if alias in search_key:
                return self.alias_to_key[alias]

        return None
