#!/usr/bin/env python3
"""
M3U Playlist Filter - Filter channels by keywords in title or group.
"""

import os
import sys
from urllib.parse import urlparse
from pathlib import Path

import requests
from ipytv import playlist

PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    sys.path.insert(0, str(PROJECT_ROOT))
    import config
except ImportError:
    config = None

OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


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


def get_playlist_source():
    url = getattr(config, "URL", "") if config else ""
    file_path = getattr(config, "FILE_PATH", "") if config else ""
    if url:
        return url
    if file_path:
        return file_path
    user_input = input("Enter M3U URL or file path: ").strip()
    return user_input


def get_keywords():
    keywords = getattr(config, "FILTER_KEYWORDS", None) if config else None
    if keywords:
        return [k.strip().lower() for k in keywords if k.strip()]
    user_input = input(
        "Enter keywords to filter (comma-separated, e.g., disney +, espn, hbo): "
    ).strip()
    if not user_input:
        return []
    return [k.strip().lower() for k in user_input.split(",") if k.strip()]


def get_output_filename():
    filename = getattr(config, "FILTER_OUTPUT_FILENAME", None) if config else None
    if filename:
        return filename
    return "filtered_channels.m3u"


def filter_channels(channels, keywords):
    if not keywords:
        return []

    matched = []
    for ch in channels:
        name_lower = ch.name.lower() if ch.name else ""
        group_lower = ""
        group = getattr(ch, "group_title", None) or getattr(ch, "group", None) or ""
        group_lower = group.lower()

        for keyword in keywords:
            if keyword in name_lower or keyword in group_lower:
                matched.append(ch)

    return matched


def write_filtered_m3u(filename, channels):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            extinf = getattr(ch, "original_extinf", None)
            extgrp = getattr(ch, "extgrp", None)

            if extinf:
                f.write(f"{extinf}\n")
            else:
                extinf_attrs = []
                if hasattr(ch, "tvg_id") and ch.tvg_id:
                    extinf_attrs.append(f'tvg-id="{ch.tvg_id}"')
                if hasattr(ch, "tvg_name") and ch.tvg_name:
                    extinf_attrs.append(f'tvg-name="{ch.tvg_name}"')
                if hasattr(ch, "tvg_logo") and ch.tvg_logo:
                    extinf_attrs.append(f'tvg-logo="{ch.tvg_logo}"')
                group = (
                    getattr(ch, "group_title", None) or getattr(ch, "group", None) or ""
                )
                if group:
                    extinf_attrs.append(f'group-title="{group}"')
                extinf_str = " ".join(extinf_attrs)
                f.write(f"#EXTINF:-1 {extinf_str},{ch.name}\n")

            if extgrp:
                f.write(f"#EXTGRP:{extgrp}\n")

            f.write(f"{ch.url}\n")

    return filepath


def main():
    print("=== M3U Playlist Filter ===\n")

    source = get_playlist_source()
    if not source:
        print("No playlist source provided.")
        return

    print(f"Loading playlist from: {source}")
    pl = load_playlist(source)
    if not pl:
        print("Failed to load playlist.")
        return

    channels = pl.get_channels()
    print(f"Loaded {len(channels)} channels.\n")

    keywords = get_keywords()
    if not keywords:
        print("No keywords provided. Exiting.")
        return

    print(f"Filtering by keywords: {', '.join(keywords)}\n")

    matched = filter_channels(channels, keywords)
    print(f"Found {len(matched)} matching channels.")

    if not matched:
        print("No channels matched the keywords.")
        return

    output_filename = get_output_filename()
    output_path = write_filtered_m3u(output_filename, matched)
    print(f"\nFiltered playlist saved to: {output_path}")


if __name__ == "__main__":
    main()
