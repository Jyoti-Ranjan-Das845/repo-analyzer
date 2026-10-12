# Score check: datasette

Commit `cf113a5` of [simonw/datasette](https://github.com/simonw/datasette), with a component map written by hand
(11 components, 28 subcomponents) following [map-format.md](../repo_analyzer/docs/map-format.md). This check shaped
several scoring rules; the numbers below are from the current rules.

## Result

| Component | Kind | Own lines | Effort | Per 100 lines |
|---|---|---:|---:|---:|
| Writing data | feature | 10,533 | 45,204 | 429 |
| Web interface | feature | 7,930 | 33,520 | 423 |
| Table browsing | feature | 7,300 | 32,373 | 443 |
| Application core | foundation | 4,011 | 26,350 | 657 |
| Permissions and authentication | feature | 5,082 | 17,235 | 339 |
| Queries | feature | 4,576 | 15,752 | 344 |
| Database layer | foundation | 2,281 | 11,432 | 501 |
| Shared utilities | foundation | 1,057 | 6,883 | 651 |
| Command line and publishing | feature | 1,706 | 5,510 | 323 |
| Developer and test tooling | supporting | 1,019 | 3,532 | 347 |
| Observability | foundation | 953 | 1,924 | 202 |
| **Whole repository** | | 46,448 | 158,798 | 342 |

The ordering is believable:
- **Biggest jobs:** the large feature areas (writing data, the web interface, table browsing).
- **Highest work per line:** the foundations that everything depends on (application core 657, shared utilities 651,
  database layer 501). They have many callers to check and many shared values they change.
- **Least work per line:** small, self-contained areas (observability 202, command line 323).

Spot checks of the evidence are right. For example, Row view's outside code is `display_columns_and_rows`,
`stream_csv`, `render_template` and `expand_foreign_keys`, which it does call.

## What the first run got wrong, and the fixes

| Problem seen | Cause | Fix |
|---|---|---|
| Almost every part had about 40 name clashes | `x.get(...)` on dictionaries matched the 41 `get` methods of the HTTP views | Name clashes count only plain calls (`foo()`), against top-level functions and classes. A call through an object or module is found through that object |
| A 119-line part (Internal client) ranked hardest per line, with 3,245 noise lines | Noise counted the whole 3,364-line `app.py`, though the part is one contiguous block | Noise counts the other code lying **between** the part's own blocks in a file. Each gap counts at most 50 lines, because a longer gap is skipped with one search (one step) |
| The command line had 94 hidden-flow spots | Every stacked `@click.option` line counted separately | One spot per decorated definition, per passed-in function and per run-time call |
| Templates and CSS lowered complexity | Lines with no program logic counted as zero difficulty | Complexity is measured on code lines. Other lines count as typical (1.0). Typical values come from code lines only |
| `list.append`, `cursor.fetchall`, `datetime.now` and builtin `all()` linked to repo functions | Linking by name alone | A call on an object of unknown type is linked only when the name is distinctive (compound or 10+ characters). A plain call reaches only top-level functions, classes or the caller's own class |
| Calls to a function defined inside the caller matched repo-wide names | Nested definitions were not treated as local names | Nested functions and classes are local names of their enclosing function |

Call links went from 8,937 (many guessed) to 6,260. Later rules (below and in the scoring doc) bring them to 5,392.

## Following imports

Calls are also linked through import lines ([effort-scoring.md](effort-scoring.md), "Linking a call to its function").
On this repository that removed 19 links and added 39. Every change was checked:

| What changed | Links | Example |
|---|---:|---|
| Calls into standard or installed libraries are no longer linked to project functions with the same name | −6 | `os.close()` had been linked to `Database.close`, `sqlite3.connect()` to `Database.connect`, `rich`'s `install()` to the `install` command |
| A method calling the imported function it is named after had been linked to itself | −9, +9 | `Datasette.add_query` now links to `stored_queries.add_query` |
| Renamed imports are followed | −3, +3 | `named_parameters as derive_named_parameters` now links to `named_parameters`, not to the other function called `derive_named_parameters` |
| A link that matched only a file's name is gone | −1 | `pm.hook.register_token_handler()`, a plugin call, had matched a different `tokens.py` from the one imported |
| New links through imports | +8 | tests reaching `TestClient` and `telemetry_registry.Attribute`; `display_rows`, `register_actions` |
| Test calls on a client fixture | +19 | `app_client.actor_cookie()` links to `DatasetteClient.actor_cookie` through "the only one in a file the caller imports". Some of these objects are really a `TestClient`; without types this cannot be told apart. They only affect test protection |

The whole-repository effort moved from 159,317 to 158,798 and test protection from 27.0% to 28.2%; no component
moved by more than 2%. On body-parser, where the tests call `bodyParser.json()`, the JSON, raw and text parsers went
from 0% to 100% test protection.

## Still open

1. **Hops carries no information here.** It is 1 for 26 of 28 subcomponents. In Python, most functions are reached
   through dispatch, so nearly every own file is an entry point. Either redefine it or drop it.
2. **Test protection is too low for view code.** Five subcomponents are at 0, mostly view code. Datasette's tests call
   views through HTTP requests (`client.get("/db/table")`), which a call graph cannot follow. Real coverage would need
   the test suite to run, which is the Setup step.
3. **Finding is 30 to 53% of every score.** That comes from the price of 50 per step, which is still a guess.
   Calibrating it against measured effort on real changes is the next step for the weights.
4. **JavaScript UI code gets many outward effects and hidden-flow spots** (DOM property writes, event callbacks). For
   example, `edit-tools.js` alone gives Writing data 110 effects and about 250 callbacks. This is real work, but it
   should be checked against measured effort.
