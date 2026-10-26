"""Regression checks for the site's citation cache and API failure handling."""

import importlib.util
from contextlib import redirect_stderr
import io
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
        self.assertEqual(delays, [10.0, 30, 10.0])

    def test_repeated_rate_limit_has_bounded_retries(self):
        calls = []
        delays = []

        def limited(*_):
            calls.append(1)
            return 429, ""

        with self.assertRaises(RuntimeError):
            citations.fetch_paper(PAPER, "test-key", request=limited, sleep=delays.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(delays, [10.0, 30, 10.0, 90, 10.0, 180, 10.0])

    def test_network_timeout_retries_with_safe_diagnostics(self):
        calls = []
        delays = []
        log = io.StringIO()

        def request(*_):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("Network request failed (curl exit 28)")
            return 200, response(19)

        with redirect_stderr(log):
            result = citations.fetch_paper(PAPER, "test-secret", request=request, sleep=delays.append)
        self.assertEqual(result["citation_count"], 19)
        self.assertEqual(delays, [10.0, 30, 10.0])
        self.assertIn("curl exit 28", log.getvalue())
        self.assertIn("attempt 1/4", log.getvalue())
        self.assertIn("next request in 40s", log.getvalue())
        self.assertNotIn("test-secret", log.getvalue())

    def test_transient_http_errors_retry(self):
        for status in (408, 500, 502, 503):
            with self.subTest(status=status):
                replies = iter([(status, ""), (200, response(19))])
                delays = []
                result = citations.fetch_paper(PAPER, "test-key", request=lambda *_: next(replies), sleep=delays.append)
                self.assertEqual(result["citation_count"], 19)
                self.assertEqual(delays, [10.0, 30, 10.0])

    def test_permanent_http_errors_do_not_retry(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                delays = []
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                    citations.fetch_paper(PAPER, "test-key", request=lambda *_: (status, ""), sleep=delays.append)
                self.assertEqual(delays, [10.0])

    def test_final_failure_logs_status_but_preserves_cache(self):
        log = io.StringIO()

        def fail(*_):
            raise RuntimeError("Semantic Scholar returned HTTP 429")

        with redirect_stderr(log):
            result, failed = citations.refresh({"paper": PAPER}, {"paper": {"citation_count": 19}}, "test-secret", fetch=fail)
        self.assertEqual(result, {"paper": {"citation_count": 19}})
        self.assertEqual(failed, ["paper"])
        self.assertIn("HTTP 429", log.getvalue())
        self.assertNotIn("test-secret", log.getvalue())

    def test_untrusted_error_text_is_not_logged(self):
        self.assertEqual(citations.safe_error_detail(RuntimeError("remote response containing test-secret")), "RuntimeError")
        self.assertEqual(citations.safe_error_detail(ValueError("test-secret")), "ValueError")

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
