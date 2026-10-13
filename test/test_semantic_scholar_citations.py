"""Regression checks for the site's citation cache and API failure handling."""

import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "citations", Path(__file__).resolve().parents[1] / "bin/update_semantic_scholar_citations.py"
)
citations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(citations)

PAPER = {"id": "a" * 40, "title": "A Test Paper"}


def response(count=0, title="A Test Paper"):
    return json.dumps({"paperId": "a" * 40, "title": title, "citationCount": count})


class CitationRefreshTests(unittest.TestCase):
    def test_zero_is_a_valid_count(self):
        result = citations.fetch_paper(PAPER, "test-key", request=lambda *_: (200, response()), sleep=lambda _: None)
        self.assertEqual(result["citation_count"], 0)
        self.assertTrue(result["url"].endswith("a" * 40))

    def test_rate_limit_retries_are_spaced(self):
        replies = iter([(429, ""), (200, response(56))])
        delays = []
        result = citations.fetch_paper(PAPER, "test-key", request=lambda *_: next(replies), sleep=delays.append)
        self.assertEqual(result["citation_count"], 56)
        self.assertEqual(delays, [2.0, 5, 2.0])

    def test_repeated_rate_limit_has_bounded_retries(self):
        calls = []

        def limited(*_):
            calls.append(1)
            return 429, ""

        with self.assertRaises(RuntimeError):
            citations.fetch_paper(PAPER, "test-key", request=limited, sleep=lambda _: None)
        self.assertEqual(len(calls), 4)

    def test_wrong_paper_and_invalid_counts_are_rejected(self):
        invalid = [(4, "Different paper"), (None, PAPER["title"]), (-1, PAPER["title"]), (True, PAPER["title"])]
        for count, title in invalid:
            with self.subTest(count=count, title=title), self.assertRaises(ValueError):
                citations.fetch_paper(PAPER, "test-key", request=lambda *_: (200, response(count, title)), sleep=lambda _: None)

    def test_failure_keeps_old_count_and_timestamp_without_inventing_zero(self):
        old = {"citation_count": 21, "updated_at": "2026-09-07T00:00:00+00:00"}

        def fail(*_):
            raise RuntimeError("HTTP 429")

        result, failed = citations.refresh({"old": PAPER, "new": PAPER}, {"old": old}, "test-key", fetch=fail)
        self.assertEqual(result, {"old": old})
        self.assertEqual(failed, ["old", "new"])

    def test_partial_failure_does_not_discard_successes(self):
        papers = {"good": PAPER, "bad": {"id": "bad", "title": "Unavailable"}}

        def fetch(paper, _):
            if paper["id"] == "bad":
                raise RuntimeError("HTTP 404")
            return {"citation_count": 56}

        result, failed = citations.refresh(papers, {"bad": {"citation_count": 19}}, "test-key", fetch=fetch)
        self.assertEqual(result, {"good": {"citation_count": 56}, "bad": {"citation_count": 19}})
        self.assertEqual(failed, ["bad"])


if __name__ == "__main__":
    unittest.main()
