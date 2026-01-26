#!/usr/bin/env python3
"""
Resolve $import references in Z-Wave device configuration files.

This script takes a manufacturer ID and device JSON file name, then resolves
all $import references to produce a fully specified JSON configuration.

Usage:
    python resolve_device_config.py <manufacturer_id> <device_filename> [-o <output_filename>]

Example:
    python resolve_device_config.py 0x027a zen77.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any, Dict, List, Tuple


class JsonDiffer:
    """Handles semantic JSON comparison and diff generation."""

    @staticmethod
    def normalize_json(obj: Any) -> Any:
        """
        Normalize JSON object for comparison by sorting keys and lists.
        This ensures that objects with same content but different order compare as equal.
        """
        if isinstance(obj, dict):
            return {k: JsonDiffer.normalize_json(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            # Sort lists of dicts by their normalized representation
            # For non-dict items, keep original order
            return [JsonDiffer.normalize_json(item) for item in obj]
        else:
            return obj

    @staticmethod
    def json_objects_equal(obj1: Any, obj2: Any) -> bool:
        """Compare two JSON objects for semantic equality."""
        return JsonDiffer.normalize_json(obj1) == JsonDiffer.normalize_json(obj2)

    @staticmethod
    def generate_diff_report(old_obj: Dict, new_obj: Dict, path: str = "") -> List[str]:
        """
        Generate a human-readable diff report between two JSON objects.
        Returns a list of difference descriptions.
        """
        diffs = []

        if isinstance(old_obj, dict) and isinstance(new_obj, dict):
            # Check for removed keys
            for key in old_obj:
                if key not in new_obj:
                    diffs.append(f"- Removed key: {path}.{key}" if path else f"- Removed key: {key}")

            # Check for added keys
            for key in new_obj:
                if key not in old_obj:
                    diffs.append(f"+ Added key: {path}.{key}" if path else f"+ Added key: {key}")

            # Check for modified values
            for key in old_obj:
                if key in new_obj:
                    new_path = f"{path}.{key}" if path else key
                    if not JsonDiffer.json_objects_equal(old_obj[key], new_obj[key]):
                        if isinstance(old_obj[key], (dict, list)) and isinstance(new_obj[key], (dict, list)):
                            diffs.extend(JsonDiffer.generate_diff_report(old_obj[key], new_obj[key], new_path))
                        else:
                            diffs.append(f"  Modified: {new_path}")
                            diffs.append(f"    - Old: {json.dumps(old_obj[key])}")
                            diffs.append(f"    + New: {json.dumps(new_obj[key])}")

        elif isinstance(old_obj, list) and isinstance(new_obj, list):
            if len(old_obj) != len(new_obj):
                diffs.append(f"  Modified array length at {path}: {len(old_obj)} -> {len(new_obj)}")

            # For arrays, show if content differs (don't dive deep into every element)
            if not JsonDiffer.json_objects_equal(old_obj, new_obj):
                diffs.append(f"  Modified array content at {path}")

        return diffs


class JsonCommentStripper:
    """Handles JSON files with // comments."""

    @staticmethod
    def strip_comments(text: str) -> str:
        """Remove // style comments from JSON text."""
        lines = []
        for line in text.split('\n'):
            # Find // that's not inside a string
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


class DeviceConfigResolver:
    """Resolves $import references in Z-Wave device configuration files."""

    def __init__(self, base_dir: Path):
        """
        Initialize the resolver.

        Args:
            base_dir: Base directory for device configurations
                     (e.g., packages/config/config/devices)
        """
        self.base_dir = base_dir
        self.template_cache: Dict[str, Dict[str, Any]] = {}

    def load_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Load a JSON file, handling // comments."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Strip comments before parsing
        clean_content = JsonCommentStripper.strip_comments(content)
        return json.loads(clean_content)

    def resolve_import_path(self, import_path: str, current_file: Path) -> tuple[Path, str]:
        """
        Resolve an import path to a file path and template key.

        Args:
            import_path: Import path like "~/templates/master_template.json#key"
                        or "templates/zooz_template.json#key"
            current_file: Path to the current file being processed

        Returns:
            Tuple of (template_file_path, template_key)
        """
        # Split path and key
        if '#' not in import_path:
            raise ValueError(f"Import path must contain '#': {import_path}")

        path_part, key_part = import_path.split('#', 1)

        # Resolve the file path
        if path_part == "":
            template_path = current_file
        elif path_part.startswith('~/'):
            # Relative to base directory
            template_path = self.base_dir / path_part[2:]
        else:
            # Relative to current file's directory
            template_path = current_file.parent / path_part

        return template_path, key_part

    def get_template_value(self, template_file: Path, key: str) -> Any:
        """
        Get a value from a template file by key.

        Args:
            template_file: Path to the template file
            key: Key to look up in the template

        Returns:
            The template value (which may itself contain $import)
        """
        # Cache template files to avoid repeated loading
        cache_key = str(template_file)
        if cache_key not in self.template_cache:
            self.template_cache[cache_key] = self.load_json_file(template_file)

        template_data = self.template_cache[cache_key]

        if key not in template_data:
            raise KeyError(f"Key '{key}' not found in template {template_file}")

        return template_data[key]

    def resolve_imports(self, data: Any, current_file: Path) -> Any:
        """
        Recursively resolve all $import references in data structure.

        Args:
            data: JSON data structure (dict, list, or primitive)
            current_file: Path to the file being processed

        Returns:
            Data structure with all imports resolved
        """
        if isinstance(data, dict):
            # Check if this dict has a $import
            if '$import' in data:
                import_path = data['$import']

                # Resolve the import
                template_file, template_key = self.resolve_import_path(import_path, current_file)
                template_value = self.get_template_value(template_file, template_key)

                # Recursively resolve imports in the template value
                resolved_template = self.resolve_imports(template_value, template_file)

                # Merge: template values are overridden by local values
                if isinstance(resolved_template, dict):
                    result = resolved_template.copy()
                    for k, v in data.items():
                        if k != '$import':
                            # Recursively resolve the override value
                            result[k] = self.resolve_imports(v, current_file)
                    return result
                else:
                    # Template value is not a dict, can't merge
                    # Keep other keys from original
                    if len(data) == 1:
                        return resolved_template
                    else:
                        raise ValueError(f"Cannot merge non-dict template value with other keys")
            else:
                # No $import, recursively process all values
                return {k: self.resolve_imports(v, current_file) for k, v in data.items()}

        elif isinstance(data, list):
            # Recursively process list items
            return [self.resolve_imports(item, current_file) for item in data]

        else:
            # Primitive value, return as-is
            return data

    def resolve_device_config(self, manufacturer_id: str, device_filename: str) -> Dict[str, Any]:
        """
        Resolve a device configuration file.

        Args:
            manufacturer_id: Manufacturer ID (e.g., "0x027a")
            device_filename: Device JSON filename (e.g., "zen77.json")

        Returns:
            Fully resolved configuration as a dictionary
        """
        device_file = self.base_dir / manufacturer_id / device_filename

        if not device_file.exists():
            raise FileNotFoundError(f"Device file not found: {device_file}")

        # Load the device configuration
        device_config = self.load_json_file(device_file)

        # Resolve all imports
        resolved_config = self.resolve_imports(device_config, device_file)

        return resolved_config


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Resolve $import references in Z-Wave device configuration files."
    )
    parser.add_argument(
        "manufacturer_id",
        nargs='?',
        help="Manufacturer ID (e.g., 0x027a)"
    )
    parser.add_argument(
        "device_filename",
        nargs='?',
        help="Device JSON filename (e.g., zen77.json)"
    )

    args = parser.parse_args()

    # Determine base directory (packages/config/config/devices)
    script_dir = Path(__file__).parent.parent  # Go up from indigo_specifics to repo root
    base_dir = script_dir / "packages" / "config" / "config" / "devices"

    if not base_dir.exists():
        if args.manufacturer_id and args.device_filename:
            print("error")
            sys.exit(1)
        else:
            print(f"Error: Base directory not found: {base_dir}")
            sys.exit(1)

    # Silent mode: both manufacturer_id and device_filename provided
    silent_mode = args.manufacturer_id and args.device_filename

    # If command-line arguments provided, process once and exit
    if args.manufacturer_id and args.device_filename:
        # Append .json if filename doesn't end with it
        device_filename = args.device_filename
        if not device_filename.endswith('.json'):
            device_filename += '.json'

        process_device(base_dir, script_dir, args.manufacturer_id, device_filename, silent_mode)
        return

    # Interactive mode: loop until user exits
    # Load manufacturers.json for search
    manufacturers_file = script_dir / "packages" / "config" / "config" / "manufacturers.json"
    if not manufacturers_file.exists():
        print(f"Error: Manufacturers file not found: {manufacturers_file}")
        sys.exit(1)

    with open(manufacturers_file, 'r', encoding='utf-8') as f:
        manufacturers_content = JsonCommentStripper.strip_comments(f.read())
        manufacturers = json.loads(manufacturers_content)

    while True:
        # Prompt for manufacturer_id
        manufacturer_id = None
        while not manufacturer_id:
            # Prompt for manufacturer name to search
            search_name = input("Enter manufacturer name to search (min 3 characters) or hex ID (or press Enter to exit): ").strip()

            # Exit if user presses Enter with no input
            if not search_name:
                sys.exit(0)

            # Check if user entered a hex ID directly
            if search_name.lower().startswith('0x'):
                manufacturer_id = search_name
                break

            if len(search_name) < 3:
                print("Error: Search term must be at least 3 characters\n")
                continue

            # Search for matching manufacturers (case-insensitive)
            search_lower = search_name.lower()
            matches = [(mfr_id, mfr_name) for mfr_id, mfr_name in manufacturers.items()
                       if search_lower in mfr_name.lower()]

            if matches:
                print(f"\nFound {len(matches)} matching manufacturer(s):")
                for mfr_id, mfr_name in matches:
                    print(f"  {mfr_id}: {mfr_name}")
                print()

                # If only one match, use it directly
                if len(matches) == 1:
                    manufacturer_id = matches[0][0]
                    print(f"Using manufacturer ID: {manufacturer_id}")
                else:
                    # Prompt for manufacturer ID
                    manufacturer_id = input("Enter manufacturer ID (e.g., 0x027a): ").strip()
                    if not manufacturer_id:
                        print("Error: Manufacturer ID is required\n")
            else:
                print("No matching manufacturers found. Please try again.\n")

        # Prompt for device_filename
        device_filename = input("Enter device filename (e.g., zen77.json) or press Enter to list files: ").strip()

        if not device_filename:
            # List all JSON files in the manufacturer directory
            manufacturer_dir = base_dir / manufacturer_id

            if not manufacturer_dir.exists():
                print(f"Error: Manufacturer directory not found: {manufacturer_dir}")
                continue

            # Get all .json files in the directory
            json_files = sorted([f.name for f in manufacturer_dir.glob("*.json")])

            if json_files:
                print(f"\nAvailable device files in {manufacturer_id}:")
                for filename in json_files:
                    print(f"  {filename}")
                print()
            else:
                print(f"No JSON files found in {manufacturer_dir}\n")

            # Prompt again for device filename
            device_filename = input("Enter device filename: ").strip()
            if not device_filename:
                print("Error: Device filename is required")
                continue

        # Append .json if filename doesn't end with it
        if not device_filename.endswith('.json'):
            device_filename += '.json'

        # Process the device
        process_device(base_dir, script_dir, manufacturer_id, device_filename, silent_mode)
        print()  # Add blank line before next iteration


def process_device(base_dir: Path, script_dir: Path, manufacturer_id: str, device_filename: str, silent_mode: bool):
    """Process a single device configuration file."""
    try:
        # Create resolver and resolve the configuration
        resolver = DeviceConfigResolver(base_dir)
        resolved_config = resolver.resolve_device_config(manufacturer_id, device_filename)

        # Determine output path: <indigo_specifics>/full_definitions/<manufacturer_id>/<device_filename>
        indigo_dir = Path(__file__).parent  # indigo_specifics directory
        output_dir = indigo_dir / "full_definitions" / manufacturer_id
        output_path = output_dir / device_filename

        # Prepare metadata
        current_timestamp = datetime.now().isoformat()
        existing_version = 1
        file_exists = output_path.exists()

        # Check if file already exists and compare
        if file_exists:
            with open(output_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)

            # Get existing version number
            existing_version = existing_data.get('vers', 1)

            # Temporarily add metadata for comparison
            temp_resolved = resolved_config.copy()
            temp_resolved['vers'] = existing_version
            temp_resolved['last_update'] = existing_data.get('last_update', current_timestamp)

            # Use semantic JSON comparison instead of string comparison
            if JsonDiffer.json_objects_equal(existing_data, temp_resolved):
                if silent_mode:
                    print(str(output_path))
                else:
                    print(f"✓ Resolved configuration is identical to existing file: {output_path}")
                    print("No changes needed.")
                return
            else:
                if silent_mode:
                    # In silent mode, increment version and write without confirmation
                    existing_version += 1
                else:
                    # Show the semantic diff
                    print(f"Differences found between new and existing file at: {output_path}\n")

                    diff_report = JsonDiffer.generate_diff_report(existing_data, temp_resolved)
                    if diff_report:
                        for line in diff_report:
                            print(line)
                    else:
                        print("  (Structural differences detected)")

                    # Prompt user for confirmation
                    print("\nOverwrite the existing file? (y/N): ", end='', flush=True)
                    response = input().strip().lower()

                    if response not in ('y', 'yes'):
                        print("Operation cancelled. File not modified.")
                        return

                    # Increment version for updated file
                    existing_version += 1

        # Add metadata to resolved config
        resolved_config['vers'] = existing_version
        resolved_config['last_update'] = current_timestamp

        # Output the resolved configuration with metadata as formatted JSON
        json_output = json.dumps(resolved_config, indent=2) + '\n'

        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_output)

        if silent_mode:
            print(str(output_path))
        else:
            if file_exists:
                print(f"✓ Resolved configuration saved to: {output_path} (version {existing_version})")
            else:
                print(f"✓ Resolved configuration saved to: {output_path} (new file, version 1)")

    except FileNotFoundError as e:
        if silent_mode:
            print("error")
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        if silent_mode:
            print("error")
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
