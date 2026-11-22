"""Verified clause-level updates where replacing a whole document is incorrect."""

import json
import re
from pathlib import Path


class ClauseUpdates:
    def __init__(self, rules=()):
        self.rules = {}
        for rule in rules:
            # Whitespace varies between PDF copies; retain exact wording otherwise.
            span = re.sub(r"\s+", "", rule["span"])
            if not span or rule["old_chunk"] in self.rules:
                raise ValueError(f"empty or duplicate clause update: {rule['old_chunk']}")
            pattern = re.compile(r"\s*".join(re.escape(character) for character in span))
            self.rules[rule["old_chunk"]] = (pattern, rule["new_chunk"], rule["historical_year"])

    @classmethod
    def load(cls):
        path = Path(__file__).with_name("clause_updates.json")
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def match(self, chunk):
        rule = self.rules.get(chunk["chunk_id"])
        return rule if rule and rule[0].search(chunk["text"]) else None

    def text(self, chunk, historical):
        rule = self.match(chunk)
        if rule and not historical(chunk, rule[2]):
            return rule[0].sub("", chunk["text"]).strip()
        return chunk["text"]
