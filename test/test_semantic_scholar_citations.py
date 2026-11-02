"""Regression checks for the site's citation cache and API failure handling."""

import importlib.util
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile

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


class WeeklyRecoveryTests(unittest.TestCase):
    now = datetime(2026, 11, 3, 2, 17, tzinfo=timezone.utc)

    def record(self, timestamp="2026-11-02T09:36:54+00:00", count=21):
        return {
            "paper_id": "a" * 40,
            "title": PAPER["title"],
            "citation_count": count,
            "updated_at": timestamp,
        }

    def test_recovery_only_selects_missing_or_previous_week_records(self):
        papers = dict.fromkeys(("fresh", "stale", "missing"), PAPER)
        cache = {"fresh": self.record(), "stale": self.record("2026-10-26T10:32:40+00:00")}
        self.assertEqual(citations.pending_papers(papers, cache, self.now), {"stale": PAPER, "missing": PAPER})

    def test_monday_resets_even_after_sunday_recovery(self):
        now = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
        cache = {"paper": self.record("2026-11-08T23:59:59+00:00")}
        self.assertEqual(citations.pending_papers({"paper": PAPER}, cache, now), {"paper": PAPER})

    def test_week_boundary_is_utc_and_zero_is_fresh(self):
        for timestamp, expected in (
            ("2026-11-02T00:00:00+00:00", {}),
            ("2026-11-02T08:00:00+08:00", {}),
            ("2026-11-02T07:59:59+08:00", {"paper": PAPER}),
        ):
            with self.subTest(timestamp=timestamp):
                cache = {"paper": self.record(timestamp, count=0)}
                self.assertEqual(citations.pending_papers({"paper": PAPER}, cache, self.now), expected)

    def test_invalid_or_future_cache_entries_remain_pending(self):
        invalid = [None, {}, self.record("bad date"), self.record("2026-11-02T09:00:00"),
                   self.record("2026-11-04T00:00:00+00:00"), self.record(count=-1), self.record(count=True),
                   {**self.record(), "title": "Another paper"}, {**self.record(), "paper_id": "invalid"}]
        for record in invalid:
            with self.subTest(record=record):
                self.assertEqual(citations.pending_papers({"paper": PAPER}, {"paper": record}, self.now), {"paper": PAPER})

    def test_failed_paper_remains_pending_until_a_success(self):
        papers = {"good": PAPER, "bad": {**PAPER, "id": "b" * 40}}
        cache = {key: self.record("2026-10-26T00:00:00+00:00") for key in papers}

        def fetch(paper, _):
            if paper["id"] == "b" * 40:
                raise RuntimeError("Semantic Scholar returned HTTP 429")
            return self.record()

        updated, failures = citations.refresh(papers, cache, "fake-key", fetch=fetch)
        self.assertEqual(failures, ["bad"])
        pending = citations.pending_papers(papers, updated, self.now)
        self.assertEqual(pending, {"bad": papers["bad"]})
        updated, failures = citations.refresh(pending, updated, "fake-key", fetch=lambda *_: self.record())
        self.assertFalse(failures)
        self.assertEqual(citations.pending_papers(papers, updated, self.now), {})

    def run_cli(self, args, cache, selected):
        with tempfile.TemporaryDirectory() as directory:
            papers_path = Path(directory) / "papers.json"
            cache_path = Path(directory) / "cache.json"
            summary_path = Path(directory) / "summary.md"
            papers_path.write_text(json.dumps({"paper": PAPER}))
            cache_path.write_text(json.dumps(cache))
            original = cache_path.read_bytes()
            output = io.StringIO()
            with patch.object(citations, "PAPERS_PATH", papers_path), \
                 patch.object(citations, "CACHE_PATH", cache_path), \
                 patch.object(citations, "pending_papers", return_value=selected), \
                 patch.object(citations, "refresh") as refresh, \
                 patch.object(citations.sys, "argv", ["updater", *args]), \
                 patch.dict(citations.os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=True), \
                 redirect_stdout(output):
                result = citations.main()
            refresh.assert_not_called()
            self.assertEqual(result, 0)
            self.assertEqual(cache_path.read_bytes(), original)
            return output.getvalue()

    def test_check_mode_needs_no_key_and_does_not_write_or_request(self):
        self.assertEqual(self.run_cli(["--check-stale"], {}, {"paper": PAPER}), "true\n")
        self.assertEqual(self.run_cli(["--check-stale"], {"paper": self.record()}, {}), "false\n")

    def test_recovery_noop_needs_no_key_or_cache_write(self):
        self.assertIn("No pending publications", self.run_cli(["--stale-only"], {"paper": self.record()}, {}))

    def test_cli_refreshes_only_pending_and_preserves_fresh_cache(self):
        papers = {"fresh": PAPER, "stale": PAPER}
        fresh = self.record()
        old = self.record("2026-10-26T00:00:00+00:00")
        for failed in ([], ["stale"]):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                papers_path = Path(directory) / "papers.json"
                cache_path = Path(directory) / "cache.json"
                summary_path = Path(directory) / "summary.md"
                papers_path.write_text(json.dumps(papers))
                cache = {"fresh": fresh, "stale": old}
                cache_path.write_text(json.dumps(cache))
                updated = {"fresh": fresh, "stale": old if failed else self.record(count=22)}
                with patch.object(citations, "PAPERS_PATH", papers_path), \
                     patch.object(citations, "CACHE_PATH", cache_path), \
                     patch.object(citations, "pending_papers", return_value={"stale": PAPER}), \
                     patch.object(citations, "refresh", return_value=(updated, failed)) as refresh, \
                     patch.object(citations.sys, "argv", ["updater", "--stale-only"]), \
                     patch.dict(citations.os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "fake-key", "GITHUB_STEP_SUMMARY": str(summary_path)}, clear=True):
                    result = citations.main()
                refresh.assert_called_once_with({"stale": PAPER}, cache, "fake-key")
                self.assertEqual(result, 1 if failed else 0)
                self.assertEqual(json.loads(cache_path.read_text()), updated)
                self.assertFalse(cache_path.with_suffix(".json.tmp").exists())
                self.assertIn(f"{len(failed)} failed, 1 skipped", summary_path.read_text())
                self.assertNotIn("fake-key", summary_path.read_text())


if __name__ == "__main__":
    unittest.main()
