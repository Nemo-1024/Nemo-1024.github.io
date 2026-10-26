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
Mondays around 10:17 Beijing time (02:17 UTC), or manually from GitHub Actions. It commits the
cache and explicitly dispatches `Deploy site`, because pushes made using the
workflow's `GITHUB_TOKEN` do not automatically trigger another push workflow.
Partial failures still publish successful updates, preserve older counts, and
mark the refresh as failed for visibility. GitHub may delay scheduled runs and
may disable schedules on inactive public repositories; check Actions if stale.

## Add a publication

Add its BibTeX key, exact title, and Semantic Scholar ID or DOI to
`_data/semantic_scholar_papers.json`, then run a refresh. Check the returned record
against the publication before publishing. Do not add example counts to the cache.
