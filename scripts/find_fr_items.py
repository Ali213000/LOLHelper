import re

# Find all string literals that look like English item names (capitalized, multi-word)
with open("services/stat_analyzer.py", encoding="utf-8") as f:
    lines = f.readlines()

pat = re.compile(r'"([A-Z][a-z]+(?:[ \'][A-Za-z][a-z]*)+)"')
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith("#"):
        continue
    for m in pat.finditer(line):
        name = m.group(1)
        # Skip pure French words (heuristic: no common English item words)
        english_words = {"of", "the", "Guardian", "Angel", "Cleaver", "Rabadon",
                         "Veil", "Stasis", "Shroud", "Force", "Bane", "Edge",
                         "Black", "Trinity", "Hydra", "Dance", "Maw", "Gage"}
        words = set(name.split())
        if words & english_words:
            print(f"L{i:4d}: {name!r}")
