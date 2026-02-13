#!/usr/bin/env python3
"""
M3U Playlist Organizer - Uses Gemini AI to rename and recategorize channels.
"""

import os
import sys
import json
import time
import re
from urllib.parse import urlparse
from collections import defaultdict
from pathlib import Path

import requests
from ipytv import playlist

PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    from . import config
except ImportError:
    try:
        import config
    except ImportError:
        config = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None
    print(
        "Warning: google-generativeai not installed. Run: pip install google-generativeai"
    )

OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CHUNK_SIZE = 300


def get_gemini_api_key():
    api_key = getattr(config, "GEMINI_API_KEY", None) if config else None
    if api_key:
        return api_key
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return api_key
    api_key = input("Enter your Gemini API key: ").strip()
    return api_key


def get_playlist_source():
    url = getattr(config, "URL", "") if config else ""
    file_path = getattr(config, "FILE_PATH", "") if config else ""
    if url:
        return url
    if file_path:
        return file_path
    user_input = input("Enter M3U URL or file path: ").strip()
    return user_input


def load_playlist(source):
    try:
        if urlparse(source).scheme in ("http", "https"):
            resp = requests.get(source)
            resp.raise_for_status()
            playlist_content = resp.text
            pl = playlist.loads(playlist_content)
            original_lines = playlist_content.splitlines()
        else:
            with open(source, "r", encoding="utf-8") as f:
                playlist_content = f.read()
            pl = playlist.loadf(source)
            original_lines = playlist_content.splitlines()

        url_to_extinf = {}
        current_extinf = None
        for line in original_lines:
            if line.startswith("#EXTINF"):
                current_extinf = line
            elif line and not line.startswith("#") and current_extinf:
                url_to_extinf[line] = current_extinf
                current_extinf = None

        for ch in pl.get_channels():
            ch.original_extinf = url_to_extinf.get(ch.url, None)
            ch.extgrp = getattr(ch, "extgrp", None)

        return pl
    except FileNotFoundError:
        print(f"Error: File not found: {source}")
    except Exception as e:
        print(f"Error loading playlist: {e}")
    return None


def extract_channel_info(channels):
    channel_list = []
    for idx, ch in enumerate(channels):
        group = getattr(ch, "group_title", None) or getattr(ch, "group", None) or ""
        channel_list.append(
            {
                "id": idx,
                "name": ch.name or "",
                "current_group": group,
            }
        )
    return channel_list


def build_gemini_prompt(channel_chunk, existing_groups=None):
    existing_groups_text = ""
    if existing_groups:
        existing_groups_text = f"""
IMPORTANT: Use these existing group names when appropriate to maintain consistency:
{json.dumps(existing_groups, indent=2)}

Only create new groups if the channel truly doesn't fit any existing category.
"""

    prompt = f"""You are an expert at organizing IPTV channel playlists. Analyze these channels and:
1. Suggest a proper, standardized channel name (clean up messy names, fix capitalization, remove quality tags like HD/FHD/4K from the name unless it's part of the official channel name)
2. Assign an appropriate group/category

{existing_groups_text}

Guidelines for groups:
- Use clear, concise category names (e.g., "Sports", "News", "Movies", "Entertainment", "Kids", "Documentary", "Music", "Religious", "Local", "International")
- For regional channels, use format like "USA", "UK", "Brazil", "Spain", etc.
- Keep sports channels in "Sports" unless they're regional sports networks
- News channels go in "News"
- Movie/cinema channels go in "Movies"
- Children's content goes in "Kids"
- Keep the number of unique groups reasonable (aim for 15-30 total categories)

IMPORTANT: Keep the same channel with different sources - just standardize the name.

Here are the channels to organize:
{json.dumps(channel_chunk, indent=2)}

Respond with ONLY a valid JSON array in this exact format, no other text:
[
  {{"id": 0, "new_name": "Channel Name", "new_group": "Category"}},
  ...
]
"""
    return prompt


def call_gemini_api(api_key, prompt, retries=3):
    if not genai:
        raise ImportError("google-generativeai package not installed")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    for attempt in range(retries):
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )

            response_text = response.text.strip()

            json_match = re.search(r"\[[\s\S]*\]", response_text)
            if json_match:
                return json.loads(json_match.group())
            else:
                print(
                    f"Warning: Could not find JSON in response, attempt {attempt + 1}"
                )

        except json.JSONDecodeError as e:
            print(f"JSON parse error on attempt {attempt + 1}: {e}")
        except Exception as e:
            print(f"API error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(2**attempt)  

    return None


def process_channels_with_gemini(channels, existing_groups=None):
    try:
        channel_info = extract_channel_info(channels)
        total = len(channel_info)

        print(f"\nProcessing {total} channels through Gemini AI...")

        api_key = get_gemini_api_key()
        if not api_key:
            print("Error: No Gemini API key available")
            return {}, set()

        reorganization_map = {}
        existing_groups = set()

        for i in range(0, total, CHUNK_SIZE):
            chunk = channel_info[i : i + CHUNK_SIZE]
            chunk_end = min(i + CHUNK_SIZE, total)
            print(f"  Processing channels {i + 1} to {chunk_end} of {total}...")

            prompt = build_gemini_prompt(
                chunk, list(existing_groups) if existing_groups else None
            )

            result = call_gemini_api(api_key, prompt)

            if result:
                for item in result:
                    channel_id = item.get("id")
                    if channel_id is not None:
                        original_idx = i + channel_id
                        reorganization_map[original_idx] = {
                            "new_name": item.get("new_name", ""),
                            "new_group": item.get("new_group", ""),
                        }
                        existing_groups.add(item.get("new_group", ""))
            else:
                print(
                    f"  Warning: Failed to process chunk {i + 1}-{chunk_end}, keeping original values"
                )
                for j, ch_info in enumerate(chunk):
                    original_idx = i + j
                    reorganization_map[original_idx] = {
                        "new_name": ch_info["name"],
                        "new_group": ch_info["current_group"],
                    }

            if i + CHUNK_SIZE < total:
                time.sleep(1)

        print(f"\nIdentified {len(existing_groups)} unique groups/categories")
        return reorganization_map, existing_groups
    except Exception as e:
        print(f"Error processing channels with Gemini: {e}")
    return {}, set()


def build_extinf_line(channel, new_name, new_group):
    attrs = []

    duration = "-1"

    tvg_id = getattr(channel, "tvg_id", None) or ""
    if tvg_id:
        attrs.append(f'tvg-id="{tvg_id}"')

    attrs.append(f'tvg-name="{new_name}"')

    tvg_logo = getattr(channel, "tvg_logo", None) or ""
    if tvg_logo:
        attrs.append(f'tvg-logo="{tvg_logo}"')

    attrs.append(f'group-title="{new_group}"')

    attrs_str = " ".join(attrs)
    return f"#EXTINF:{duration} {attrs_str},{new_name}"


def write_organized_m3u(filename, channels, reorganization_map):
    filepath = os.path.join(OUTPUT_DIR, filename)

    organized = []
    for idx, ch in enumerate(channels):
        mapping = reorganization_map.get(idx, {})
        new_name = mapping.get("new_name") or ch.name or "Unknown"
        new_group = (
            mapping.get("new_group")
            or getattr(ch, "group_title", "")
            or "Uncategorized"
        )

        extinf_line = build_extinf_line(ch, new_name, new_group)
        organized.append((new_group, new_name, ch, extinf_line))

    organized.sort(key=lambda x: (x[0].lower(), x[1].lower()))

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        current_group = None
        for group, name, ch, extinf_line in organized:
            if group != current_group:
                if current_group is not None:
                    f.write("\n") 
                f.write(f"# ===== {group} =====\n")
                current_group = group

            f.write(f"{extinf_line}\n")

            extgrp = getattr(ch, "extgrp", None)
            if extgrp:
                f.write(f"#EXTGRP:{new_group}\n")

            f.write(f"{ch.url}\n")

    return filepath


def get_output_filename():
    default = "organized_channels.m3u"
    filename = getattr(config, "ORGANIZER_OUTPUT_FILENAME", None) if config else None
    return filename or default


def main():
    print("=== M3U Playlist Organizer (Gemini AI) ===\n")

    if not genai:
        print("Error: google-generativeai package required.")
        print("Install with: pip install google-generativeai")
        return

    api_key = get_gemini_api_key()
    if not api_key:
        print("No Gemini API key provided. Exiting.")
        return

    source = get_playlist_source()
    if not source:
        print("No playlist source provided. Exiting.")
        return

    print(f"Loading playlist from: {source}")
    pl = load_playlist(source)
    if not pl:
        print("Failed to load playlist. Exiting.")
        return

    channels = pl.get_channels()
    print(f"Loaded {len(channels)} channels.\n")

    if len(channels) == 0:
        print("No channels found in playlist. Exiting.")
        return

    reorganization_map, groups = process_channels_with_gemini(channels)

    print("\nGroups created:")
    for group in sorted(groups):
        count = sum(
            1 for m in reorganization_map.values() if m.get("new_group") == group
        )
        print(f"  - {group}: {count} channels")

    output_filename = get_output_filename()
    output_path = write_organized_m3u(output_filename, channels, reorganization_map)

    print(f"\n✓ Organized playlist saved to: {output_path}")
    print(f"  Original playlist unchanged.")


if __name__ == "__main__":
    main()
