#!/usr/bin/env python3
"""
Analyze all device config files to find unique top-level description values.
"""

import json
import re
from pathlib import Path
from collections import Counter

def strip_comments(text: str) -> str:
    """Remove // style comments from JSON text."""
    lines = []
    for line in text.split('\n'):
        in_string = False
        escaped = False
        comment_pos = -1

        for i, char in enumerate(line):
            if escaped:
                escaped = False
                continue

            if char == '\\':
                escaped = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if not in_string and char == '/' and i + 1 < len(line) and line[i + 1] == '/':
                comment_pos = i
                break

        if comment_pos >= 0:
            lines.append(line[:comment_pos].rstrip())
        else:
            lines.append(line)

    return '\n'.join(lines)

def main():
    base_dir = Path(__file__).parent.parent / "packages" / "config" / "config" / "devices"

    descriptions = []
    file_count = 0
    error_count = 0
    no_description_count = 0

    # Find all JSON files
    json_files = list(base_dir.rglob("*.json"))

    print(f"Analyzing {len(json_files)} JSON files...")

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Strip comments and parse
            clean_content = strip_comments(content)
            data = json.loads(clean_content)

            # Check for top-level description
            if isinstance(data, dict) and 'description' in data:
                desc_value = data['description']
                # Convert to JSON string for comparison if it's not a simple string
                if isinstance(desc_value, (list, dict)):
                    descriptions.append(json.dumps(desc_value, sort_keys=True))
                else:
                    descriptions.append(str(desc_value))
            else:
                no_description_count += 1

            file_count += 1

        except Exception as e:
            error_count += 1
            # Silently continue on errors

    print(f"\nProcessed {file_count} files successfully ({error_count} errors)")
    print(f"Files without top-level description: {no_description_count}\n")

    # Count unique values
    description_counts = Counter(descriptions)
    print(f"Found {len(description_counts)} unique top-level description values:\n")

    # Sort by frequency (most common first)
    for desc, count in description_counts.most_common():
        # Try to pretty-print JSON if it's a JSON string
        try:
            parsed = json.loads(desc)
            if isinstance(parsed, list):
                desc_display = f"LIST with {len(parsed)} items: {desc[:100]}..."
            else:
                desc_display = desc
        except:
            desc_display = desc

        print(f"  [{count:4d}] {desc_display}")

if __name__ == "__main__":
    main()
