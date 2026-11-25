# dblp-sparql-mcp

MCP server for exhaustive boolean **title** search and complete venue listings over all
of dblp, through the official SPARQL endpoint https://sparql.dblp.org/sparql. It never
contacts dblp.org itself (which sits behind a bot challenge) and does not try to get
around it.

dblp has titles, authors and venues but no abstracts: use this for title-level searches
and "every paper of venue X in year Y" lists, and Scopus or OpenAlex for topic searches.
The query syntax and matching are the same as in
[openreview-mcp](https://github.com/WCL114/openreview-mcp).

## Tools

- `dblp_search(query?, venues?, year_from?, year_to?, types?, limit=20, offset=0, count_only=false, export_path?, refresh=false)`
  - `query`: `AND`, `OR`, `NOT`, parentheses, `"phrases"`, trailing `*`; adjacent words
    are ANDed; precedence `NOT > AND > OR`. Whole words, no stemming:
    `"prompt injection*"` also matches "prompt injections", `"prompt injection"` does not.
  - Without `query`, lists every record of `venues` (dblp keys such as `conf/uss`).
  - `types` defaults to `conference` and `journal`; add `preprint` for arXiv CoRR,
    IACR ePrint, etc. (dblp keeps a preprint and its published version apart).
  - Returns `total`, `parsed`, `candidates`, `filters` and one page of results (key,
    title, authors, venue, year, type, doi, url, dblp_url), newest first.
  - `count_only=true` needs one request; `export_path` writes all matches (up to 5000)
    to a new `.json` or `.ris` file.
- `dblp_find_venue(name, limit=10)`: venue keys by name or acronym ("USENIX Security",
  "CCS", "S&P").

## How it works

1. The query is translated into a QLever text-index query that returns a superset of
   the matches (every word as a prefix, since the index's exact words are
   accent-sensitive while prefixes are not), with year, venue and type joined in.
2. The candidate titles are matched exactly on the client with the openreview-mcp
   matcher.
3. Details (authors etc.) are fetched only for the returned page or the export.

More than 25,000 candidates is an error asking for a narrower search; results are
never truncated. Endpoint failures are reported as errors, never as empty results.

## Etiquette, cache, configuration

Requests are serialized and spaced at least 10 s apart (the endpoint's robots.txt
`Crawl-delay`), and results are cached on disk for 24 hours.

| Variable | Default | Meaning |
|---|---|---|
| `DBLP_SPARQL_MCP_MIN_INTERVAL` | `10` | seconds between requests (keep it at 10 or more) |
| `DBLP_SPARQL_MCP_CACHE_DIR` | `~/.cache/dblp-sparql-mcp` | cache directory |
| `DBLP_SPARQL_MCP_NO_CACHE` | unset | `1` disables the cache |

## Install (Claude Code)

```
uvx --python 3.12 --from git+https://github.com/WCL114/dblp-sparql-mcp@<commit> dblp-sparql-mcp --version
```

Then add to `mcpServers` in `~/.claude.json`:

```json
"dblp": {
  "command": "uvx",
  "args": ["--python", "3.12", "--from", "git+https://github.com/WCL114/dblp-sparql-mcp@<commit>", "dblp-sparql-mcp"]
}
```

## Development

```
uv sync --extra dev --python 3.12
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy src
```

Tests are offline; their data is cut from real endpoint responses.
`src/dblp_sparql_mcp/query.py` is a verbatim copy from openreview-mcp (commit 5fb3044);
keep both copies identical.

## License

MIT, see [LICENSE](LICENSE).
