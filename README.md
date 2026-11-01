# linkedin-engine

A small engine that searches LinkedIn job postings for the roles **you** care about, filters out the noise, ranks what's left, and keeps a **history of the skills** employers ask for.

It ships configured for geospatial / GIS engineering roles (Spain, EU, US, UK), but every search term, region and limit can be changed in a personal config file. No LinkedIn account is needed: it uses LinkedIn's public "guest" job endpoints.

You can use it from a **local web page** (no command line needed: pick roles and locations, then watch jobs arrive as they're found) or from the **CLI**. Each run produces two HTML reports:

- **`reports/latest.html`**: new jobs since the last run in a job-search layout (list on the left, details on the right), with region, text, workplace, seniority and minimum-relevance filters, detected skills, and remote/hybrid and visa-sponsorship badges.
- **`reports/trends.html`**: how often each skill appears in job postings over time, by region and category.

## How it works

1. **Search.** Every query is searched in every region (LinkedIn `geoId`). The engine fetches up to `max_results_per_query` jobs posted within the configured time window.
2. **Deduplicate.** Jobs already seen are skipped. Reposts (same company, normalized title and country within 30 days) are linked to the original.
3. **Filter.** Jobs are hidden if the title matches an exclusion pattern, the description is in a language you don't accept, or it requires another language, security clearance or citizenship.
4. **Score.** A rule-based relevance score adds points for title keywords and for "domain" skills found in the description. Jobs below the threshold are hidden.
5. **Store & report.** Everything is stored in SQLite (`data/jobs.db`), including hidden jobs, so you can re-score the history after changing the config.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- macOS for desktop notifications and scheduling (optional; the engine itself runs anywhere)

## Quick start: web UI

The easiest way to use it. Everything runs on your computer: searches go from your own connection to LinkedIn, at your own pace, and the page is only reachable from your machine (`127.0.0.1`).

1. [Download the project](https://github.com/aleixsegura/linkedin-engine/archive/refs/heads/main.zip) and unzip it (or `git clone` it).
2. Double-click the launcher:
   - **macOS**: `Start linkedin-engine.command`. The first time, macOS may refuse to open a downloaded script: right-click it → **Open** → **Open**.
   - **Windows**: `Start linkedin-engine.bat`.

   It installs [uv](https://docs.astral.sh/uv/) if needed (which downloads Python), installs the dependencies and opens the search page in your browser. Keep the window open while you use it; close it to stop.
3. Type or pick the roles you're interested in, choose locations, how recent the jobs should be and how many to fetch, and click **Search**.

Jobs appear one by one as they're fetched and checked, with progress and a **Cancel** button. When the search ends you get links to the full report and the skill trends. Searches from the web UI are stored in the same history as CLI runs.

Two options on the form:

- **Only show jobs that match my profile's relevance rules**: applies the title exclusions and relevance threshold from your config. The built-in profile is tuned for geospatial roles, so this is turned off automatically when you search for other roles; it's turned on when the roles match your saved profile.
- **Save these choices as my default search**: writes the roles, locations and limits to `config/config.local.yaml`, so they're preselected next time and used by `engine run` and scheduled runs.

From a terminal, the same page starts with:

```bash
uv run engine ui                 # opens http://127.0.0.1:<free port>/ in your browser
uv run engine ui --port 8765 --no-browser
```

Only one search runs at a time, so the request volume stays the same as a single CLI run.

## Quick start: CLI

```bash
git clone https://github.com/aleixsegura/linkedin-engine.git
cd linkedin-engine
uv sync
uv run engine init      # creates config/config.local.yaml, your personal profile
# edit config/config.local.yaml: roles, regions, limits
uv run engine config    # check the effective settings
uv run engine run --open
```

## Configure your own search

Settings come from two layers:

| File | Tracked in git | Purpose |
| --- | --- | --- |
| `config/config.yaml` | yes | Defaults (the geospatial profile) and every available option |
| `config/config.local.yaml` | no | **Your** profile, merged on top of the defaults |
| `config/skills.yaml` | yes | Skills taxonomy used for detection and trends |
| `config/skills.local.yaml` | no | Extra skill categories or skills, merged on top |

In the local file, nested settings are merged key by key; lists, single values and `regions` replace the default entirely. A minimal profile looks like this:

```yaml
# config/config.local.yaml
queries:                      # roles / keywords to search
  - Data Engineer
  - Analytics Engineer

regions:                      # label -> LinkedIn geoId
  ES: 105646813
  DE: 101282230

search:
  posted_within_seconds: 604800   # last 7 days
  max_results_per_query: 50       # max jobs per query × region

title_exclude:                # regex, case-insensitive
  - '\bintern(ship)?\b'

scoring:
  threshold: 0                # 0 = show everything that passes the filters
  title_terms:
    weight: 4
    patterns: ['\bdata\b']
  domain_skill_categories: ['data_engineering', 'databases']
```

See [`config/config.local.example.yaml`](config/config.local.example.yaml) for a commented template and [`config/config.yaml`](config/config.yaml) for every option (language filters, clearance and sponsorship patterns, remote/hybrid detection, scoring weights).

### Finding a geoId

Search for jobs on linkedin.com, choose a location, and copy the `geoId=` value from the URL. Some common ones:

| Location | geoId |
| --- | --- |
| Spain | 105646813 |
| European Union | 91000000 |
| United States | 103644278 |
| United Kingdom | 101165590 |
| Germany | 101282230 |
| France | 105015875 |
| Netherlands | 102890719 |
| Worldwide | 92000000 |

### Tuning relevance

The score of a job is:

- `title_terms.weight` if the title matches any of `title_terms.patterns`
- `+ engineering_title_terms.weight` if the title matches any of those patterns
- `+ negative_terms.weight` (usually negative) if the title matches any of those patterns
- `+ domain_skill_weight` for each skill found in a category listed in `domain_skill_categories` (glob patterns such as `geo_*`), up to `domain_skill_cap`

Jobs scoring below `threshold` are hidden as "low relevance". When starting with a new role, keep `threshold: 0`, look at a couple of reports, then add title patterns and raise it. After changing the config, run `uv run engine rescore` to re-apply it to the whole history.

### Adding skills

Add categories or skills in `config/skills.local.yaml`, using the same format as `config/skills.yaml`:

```yaml
data_engineering:
  dbt: null                     # null = match the literal name
  Dagster: null
  Apache Iceberg: ['\biceberg\b']
```

## Usage

```bash
uv run engine run                     # search (if the minimum days have passed), store, build reports
uv run engine run --force             # ignore the minimum days between runs
uv run engine run --open              # open the report when done

# one-off searches without touching the config files
uv run engine run --force -q "Frontend Engineer" -q "React Developer" -r ES=105646813 --max-results 30

uv run engine ui                      # local web UI with streamed results
uv run engine config                  # show effective queries, regions and limits
uv run engine report [RUN_ID]         # rebuild the new-jobs report (default: latest run)
uv run engine trends                  # rebuild reports/trends.html
uv run engine rescore                 # re-apply filters and scoring to the whole history
uv run engine backup                  # back up the database (also done before every run)
uv run pytest                         # run the tests
```

`--max-results` is rounded up to LinkedIn's page size (10). Searching many queries × regions takes a while because requests are spaced out with random delays (`search.min_delay_seconds` / `max_delay_seconds`) to avoid rate limiting.

## Scheduled runs (macOS)

```bash
scripts/install_launchd.sh        # run daily at 10:00
scripts/install_launchd.sh 8      # or at another hour
```

The engine only searches when at least `schedule.min_days_between_runs` days (default 3) have passed since the last successful run. Logs go to `~/Library/Logs/linkedin-engine.log`. Install `terminal-notifier` (`brew install terminal-notifier`) so that clicking the notification opens the report.

## Project layout

```text
config/                 defaults, local profile template and skills taxonomy
src/engine/
  cli.py                command-line interface (Typer)
  web.py                local web UI (Starlette, Server-Sent Events)
  config.py             config loading and local overrides
  pipeline.py           run orchestration, dedup, rescoring, backups
  analysis.py           filters, language detection, skills, relevance score
  report.py             HTML reports (Jinja2 templates in templates/, shared with the web UI)
  sources/              job sources (LinkedIn guest endpoints)
tests/                  unit tests with HTML fixtures
scripts/                launchd installer
Start linkedin-engine.*  double-click launchers for the web UI (macOS .command, Windows .bat)
```

## Notes

- Hidden jobs are still stored in `data/jobs.db`. Jobs hidden for language or clearance reasons still count towards the skill trends; jobs hidden for title, low relevance or missing description don't.
- `data/`, `reports/`, `backups/` and your `*.local.yaml` files are git-ignored.
- This project relies on undocumented public LinkedIn endpoints that may change or rate-limit at any time. Use it for personal job searching, at a moderate pace, and respect LinkedIn's terms of service.
