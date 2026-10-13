#!/usr/bin/env python3
"""Refresh a small, explicitly mapped set of publication citation counts."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
PAPERS_PATH = ROOT / "_data/semantic_scholar_papers.json"
CACHE_PATH = ROOT / "_data/semantic_scholar_citations.json"
API = "https://api.semanticscholar.org/graph/v1/paper/"
INTERVAL = 2.0  # Below the key's cumulative one-request-per-second limit.
BACKOFF = (5, 15, 45)


def normalized_title(title):
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


def request_paper(paper_id, api_key):
    # stdin keeps the key out of process arguments, files, and logs. curl also
    # preserves the documented lowercase x-api-key header name.
    url = API + quote(paper_id, safe=":/") + "?fields=title,citationCount,url"
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--connect-timeout", "10",
         "--max-time", "30", "--header", "@-", "--write-out", "\n%{http_code}", url],
        input="x-api-key: " + api_key + "\n",
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Network request failed (curl exit {result.returncode})")
    body, status = result.stdout.rsplit("\n", 1)
    return int(status), body


def fetch_paper(paper, api_key, request=request_paper, sleep=time.sleep):
    for attempt in range(len(BACKOFF) + 1):
        sleep(INTERVAL)
        try:
            status, body = request(paper["id"], api_key)
        except RuntimeError:
            if attempt == len(BACKOFF):
                raise
            sleep(BACKOFF[attempt])
            continue
        if status == 200:
            data = json.loads(body)
            count = data.get("citationCount")
            if type(count) is not int or count < 0:
                raise ValueError("Missing or invalid citationCount")
            if normalized_title(data.get("title", "")) != normalized_title(paper["title"]):
                raise ValueError("Paper title does not match the configured publication")
            paper_id = data.get("paperId", "")
            if not re.fullmatch(r"[0-9a-f]{40}", paper_id):
                raise ValueError("Missing or invalid Semantic Scholar paper ID")
            return {
                "paper_id": paper_id,
                "title": data["title"],
                "citation_count": count,
                "url": "https://www.semanticscholar.org/paper/" + paper_id,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        if (status == 429 or status >= 500) and attempt < len(BACKOFF):
            sleep(BACKOFF[attempt])
            continue
        raise RuntimeError(f"Semantic Scholar returned HTTP {status}")


def refresh(papers, cache, api_key, fetch=fetch_paper):
    updated = dict(cache)
    failures = []
    for bib_key, paper in papers.items():
        try:
            updated[bib_key] = fetch(paper, api_key)
            print(f"{bib_key}: {updated[bib_key]['citation_count']} citations")
        except (RuntimeError, ValueError, KeyError, OSError) as error:
            # Do not print response bodies/headers: they can contain secrets.
            print(f"{bib_key}: {type(error).__name__}; retaining previous data", file=sys.stderr)
            failures.append(bib_key)
    return updated, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-key", action="store_true", help="Read the API key without echoing it")
    args = parser.parse_args()
    api_key = getpass.getpass("Semantic Scholar API key: ") if args.prompt_key else os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    api_key = api_key.strip()
    if not api_key or "\n" in api_key or "\r" in api_key:
        parser.error("Set SEMANTIC_SCHOLAR_API_KEY or use --prompt-key")
    papers = json.loads(PAPERS_PATH.read_text())
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    updated, failures = refresh(papers, cache, api_key)
    if updated != cache:
        temporary = CACHE_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(CACHE_PATH)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
