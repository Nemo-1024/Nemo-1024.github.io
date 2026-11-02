# Publication citation badges

Each mapped publication shows its last successfully retrieved Semantic Scholar
citation count. The badge links to the citing papers; its tooltip shows the UTC
update time. A paper with no successful lookup has no badge. A verified zero is
displayed as zero. Counts are Semantic Scholar counts, not Google Scholar counts.

## Update and publish

Store the API key in the repository Actions secret `SEMANTIC_SCHOLAR_API_KEY`.
Never put the key in a committed file or browser JavaScript. For a local refresh:

```sh
python3 bin/update_semantic_scholar_citations.py --prompt-key
```

The updater uses Python's standard library and curl. It waits ten seconds before
each request. Network errors, HTTP 408/429, and server errors are retried up to
three times with additional waits of 30, 90, and 180 seconds (40, 100, and 190
seconds including the normal request interval). Other HTTP errors such as 401,
403, and 404 fail immediately. Retry logs include the attempt number, HTTP status
or curl exit code, and the next wait duration, without response bodies or secrets.
The workflow allows 30 minutes for the three publications' worst-case retries.
Each failed paper keeps its cached count and timestamp. The updater validates
titles and nonnegative integer counts.
Successful data is written atomically to `_data/semantic_scholar_citations.json`.

Once published on the default branch, `Update citation counts` runs weekly on
Mondays around 10:17 Beijing time (02:17 UTC). Tuesday through Sunday at the same
time, a recovery check selects **only** papers without a successful refresh in
the current UTC week (starting Monday 00:00 UTC / 08:00 Beijing). Successful
papers are skipped, including valid zero counts. Invalid/missing cache entries
remain pending. A successful recovery later in the week does not postpone the
next Monday's refresh. This keeps successful updates weekly while giving failed
lookups another chance on later days, rather than only a few minutes of retries.
If everything is current, recovery runs make no API calls, commits, or deployments.
Manual runs from GitHub Actions still force a refresh of all mapped papers.

For a read-only pending check (no key required), use
`python3 bin/update_semantic_scholar_citations.py --check-stale`.
To perform only pending lookups locally, add `--stale-only` to the local refresh
command above. Selection uses successful timestamps, never counts alone; an
unchanged count can still be a successful refresh.

When a refresh is attempted, the workflow commits the cache and explicitly
dispatches `Deploy site`, because pushes made using the
workflow's `GITHUB_TOKEN` do not automatically trigger another push workflow.
Partial failures still save successful updates and attempt deployment, preserve
older counts, and mark the refresh as failed for visibility. The Actions step
summary separates succeeded, failed, and skipped papers. Daily recovery does not
guarantee API availability, and a deployment failure is separate from an API
failure. GitHub may delay scheduled runs and
may disable schedules on inactive public repositories; check Actions if stale.

`Deploy site` pins `ubuntu-24.04` with Ruby `3.3.5`; the latter has no prebuilt
package for Ubuntu 26.04. Do not change this runner back to `ubuntu-latest`
without checking Ruby/runner compatibility. Deployment must complete before
updated cached counts appear on the public homepage.

## Add a publication

Add its BibTeX key, exact title, and Semantic Scholar ID or DOI to
`_data/semantic_scholar_papers.json`, then run a refresh. Check the returned record
against the publication before publishing. Do not add example counts to the cache.
