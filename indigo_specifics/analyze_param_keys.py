#!/usr/bin/env python3
"""
Analyze all device config files to find unique keys in paramInformation dictionaries.
"""

import json
import re
from pathlib import Path
from typing import Set

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

def extract_param_keys(obj, keys: Set[str]):
    """Recursively find paramInformation arrays and extract keys from their dictionaries."""
    if isinstance(obj, dict):
        # Check if this dict has a paramInformation key
        if 'paramInformation' in obj and isinstance(obj['paramInformation'], list):
            # Process each item in the paramInformation list
            for param in obj['paramInformation']:
                if isinstance(param, dict):
                    # Add all keys from this parameter dictionary
                    keys.update(param.keys())

        # Recursively process all values in this dict
        for value in obj.values():
            extract_param_keys(value, keys)

    elif isinstance(obj, list):
        # Recursively process all items in this list
        for item in obj:
            extract_param_keys(item, keys)

def main():
    base_dir = Path(__file__).parent.parent / "packages" / "config" / "config" / "devices"

    all_keys = set()
    file_count = 0
    error_count = 0

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

            # Extract keys from paramInformation
            extract_param_keys(data, all_keys)

            file_count += 1

        except Exception as e:
            error_count += 1
            # Silently continue on errors

    print(f"\nProcessed {file_count} files successfully ({error_count} errors)\n")
    print(f"Found {len(all_keys)} unique keys in paramInformation dictionaries:\n")

    # Sort and print the keys
    for key in sorted(all_keys):
        print(f"  - {key}")

if __name__ == "__main__":
    main()
