# Setup scoring

**Setup** is the effort to get a repo from `git clone` to "dependencies installed, tests run, and the app starts (if it is an app)". It is paid once per repo.

Score unit: **points** (1 point = the effort to understand 100 lines of ordinary code). All point values below are starting guesses, to be calibrated (see the end of this document).

> **Open question:** we are not yet sure how important Setup is compared to the other four efforts. We need to decide whether it is part of the total or shown separately.

## Rules every question must pass

1. **It applies to every repo.** It can always be answered, even if the answer is "none" (0 points).
2. **More of it means more setup effort for anyone.** It is about the repo, not the person.
3. **It can be answered from the repo's files.**

## Step 0: collect the files

List every file. Skip `node_modules/`, `vendor/`, `.git/`, `dist/`, `build/`, `examples/` and test fixtures. Sort all paths so that the order is the same on every machine.

| Group | Files | Used for |
|---|---|---|
| Manifests | `package.json`, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle`, `Gemfile`, `composer.json`, `*.csproj` | Languages and libraries |
| Recipes | `.github/workflows/*.yml`, `.gitlab-ci.yml`, `Dockerfile`, `docker-compose.yml`, `.devcontainer/`, `Makefile`, `scripts/setup*` | Steps that are known to work |
| Settings | `.env.example`, `.env.sample`, `.env.template` | Settings and keys |
| Docs | `README*`, `CONTRIBUTING*`, `docs/*install*`, `docs/*setup*`, `docs/*development*` | Whether steps are explained |
| Code | All source files | Settings and services the code uses |

## The 8 questions

### Q1. What language tools must be installed?
- **Check:**
  1. Map each manifest to its tool (`package.json` → Node, `pyproject.toml` → Python, `go.mod` → Go, `Cargo.toml` → Rust, `pom.xml`/`build.gradle` → Java, `CMakeLists.txt` → C/C++ compiler + CMake, …).
  2. Add any tool the CI file installs (for example `actions/setup-python`).
  3. Check whether each tool's version is pinned: `.nvmrc`, `.python-version`, `engines` in `package.json`, `rust-toolchain`, `.tool-versions`, or the `go` line in `go.mod`.
- **Score:** 1 per tool, +0.5 if the version is not pinned.

### Q2. What libraries must be installed?
- **Check:**
  1. Find the package manager for each manifest.
  2. Look for its lockfile: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `uv.lock`, `poetry.lock`, `Cargo.lock`, `go.sum`, `Gemfile.lock`, …
  3. If there is a private registry (`.npmrc` with an auth token, pip `--extra-index-url`, Maven `settings.xml` servers), send it to Q6.
- **Score:** 0.5 per package manager if locked, 1.0 if not. Individual packages are not counted, because one command installs them all.

### Q3. What system software must be installed?
- **Check:**
  1. **Written down:** find `apt-get install`, `apt install`, `brew install`, `yum`/`dnf install` and `apk add` in recipes and docs, and pull out the package names.
  2. **Implied:** compare the project's libraries against the lookup file (`psycopg2` → `libpq`, `mysqlclient` → MySQL client, `GDAL`/`rasterio` → GDAL, `torch` with CUDA/`cupy` → CUDA, `pytesseract` → Tesseract, node `canvas` → Cairo, …).
  3. Remove duplicates, and drop basics every machine has (`git`, `curl`, `ca-certificates`).
- **Score:** 1 per normal package, 3 per heavy one (CUDA, GDAL, Android SDK, …). Special hardware adds a warning such as "needs GPU".

### Q4. What services must be running?
- **Check:**
  1. Services with an `image:` in `docker-compose.yml`. Skip services with `build:`, since those are the project itself.
  2. `services:` blocks in CI files.
  3. Connection strings in settings (`DATABASE_URL=postgres://…`, `REDIS_URL`, `MONGO_URI`).
  4. Client libraries, using the lookup file (`psycopg`/`pg`/`asyncpg` → Postgres, `redis`/`ioredis` → Redis, `pymongo`/`mongoose` → MongoDB, `kafkajs` → Kafka, …).
  5. Merge everything by service type. SQLite is not a service.
- **Score:** 1.5 per normal service, 2.5 per heavy one (Kafka, Elasticsearch, Cassandra).

### Q5. What settings must be filled in?
- **Check:** combine the keys in the settings template with the environment variables read in code (`os.environ[...]`, `os.getenv`, `process.env.X`, `os.Getenv`, `System.getenv`, `ENV[...]`). Then put each key in one group:

  | Group | Example | Points |
  |---|---|---|
  | Has a default in code | `os.getenv("PORT", "8000")` | 0 |
  | Example value works as-is | `REDIS_URL=redis://localhost` | 0.1 |
  | Empty or placeholder | `JWT_SECRET=`, `changeme`, `your-key-here` | 0.3 |
  | Key for an outside service | `STRIPE_SECRET_KEY` | Moves to Q6 |

### Q6. What outside accounts are needed?
- **Check:**
  1. Collect the keys sent from Q5 (matched to providers with the lookup file), the SDKs in dependencies (`stripe`, `boto3`, `google-cloud-*`, `openai`, …), and the private registries sent from Q2.
  2. Group them by provider.
  3. A provider scores 0 if a local stand-in exists (LocalStack, MinIO, Mailpit, Firebase emulator), or if it is optional (has a default in code, or the docs say "optional").
- **Score:**

  | Kind of account | Points |
  |---|---|
  | Self-serve with a free or test mode | 2 |
  | Real cloud account or paid-only service | 4 |
  | Private or company access | 4, plus a "needs access from the team" warning |

### Q7. What must be prepared before it runs?
| Look for | Means | Points |
|---|---|---|
| `alembic.ini`, `migrations/`, `prisma/migrations`, `db/migrate` | Database migrations | 0.5 |
| `seed.py`, `prisma/seed.ts`, `db/seeds.rb` | Starting data | 0.5 |
| `.proto`, `codegen.yml`, a `generate` target in the Makefile | Code generation | 0.5 |
| `.gitmodules` | Submodules | 0.5 |
| Git LFS, download scripts, "download the dataset/weights" in docs | Large downloads | 1.0 |

Steps that run automatically, such as a `postinstall` script, score 0.

### Q8. Is it clear how to run it and check it works?
| What we found | Points |
|---|---|
| Test command explained in docs (`pytest`, `npm test`, `go test`, `make test`, …) | 0 |
| Only in CI, `package.json` scripts or the Makefile | 0.5 |
| Tests exist but there is no command anywhere, or there are no tests | 1.0 |
| It is an app and the start command is unclear | +0.5 |

## Adjusters (applied to every step found in Q1–Q7)

**Explained?** Search the docs for the step's name:

| Where the step appears | Multiply by |
|---|---|
| In the docs | × 1.0 |
| Only in a recipe (CI, Dockerfile, script) | × 1.3 |
| Only implied (lookup file or code) | × 2.0 |

**Automated?**

| Helper | Covers | Multiply by |
|---|---|---|
| devcontainer or Nix | Q1–Q3 | × 0.2 |
| docker-compose | The services it lists | × 0.2 |
| Setup script or `make setup` | The steps whose commands appear in it | × 0.5 |

## Final score

**Setup points = sum over all steps of (points × explained × automated), plus Q8.**

| Points | Label |
|---|---|
| 0–3 | Easy |
| 3–7 | Medium |
| 7–12 | Hard |
| 12+ | Very hard |

These bands will later be replaced by percentiles from a benchmark of about 100 repos.

**Output:** the total and label, the score for each question, the top 3 costs, any warnings, and a setup checklist built from the steps found.

### Worked example (Next.js + FastAPI web app)
| Question | Found | Calculation | Score |
|---|---|---|---|
| Q1 | Node, Python (pinned, explained) | 2 × 1.0 | 2.0 |
| Q2 | npm, uv (locked) | 1.0 × 1.0 | 1.0 |
| Q3 | `libpq` (only in CI) | 1 × 1.3 | 1.3 |
| Q4 | Postgres, Redis (docker-compose) | 3 × 1.0 × 0.2 | 0.6 |
| Q5 | 8 ready, 2 placeholders | 1.4 × 1.0 | 1.4 |
| Q6 | Stripe, SendGrid | 4 × 1.0 | 4.0 |
| Q7 | Migrations | 0.5 × 1.0 | 0.5 |
| Q8 | Commands in README | — | 0 |
| **Total** | | | **10.8 (Hard)** |

## Determinism

The score is deterministic: **same commit + same rules version = same score.**

- Each result stores the commit ID and the rules version.
- Lookup tables and point values are versioned. Changing either one means a new rules version.
- All lists are sorted, so the output (including the checklist order) is identical on every machine.
- The score never uses an AI model, network lookups (such as PyPI or the GitHub API) or the repo's own code.

**Deterministic does not mean correct.** These heuristics can be consistently wrong:
- "Explained" is a keyword search, so a passing mention counts as explained.
- Optional accounts are detected through defaults in code or the word "optional" in docs.
- Environment variable names that are built dynamically are missed.
- Unusual shell syntax in install commands can be missed.
- Lookup tables only know the libraries we have listed.

## Calibration (offline, not deterministic)

Set up about 100 benchmark repos in an isolated sandbox using an AI agent, and record the steps, errors, retries and success. Adjust the point values until the static scores match, then release a new rules version. The sandbox never has access to the host machine or to any secrets, because installing a repo runs its code.
