# Score check: datasette

Commit `cf113a5` of [simonw/datasette](https://github.com/simonw/datasette), with a component map written by hand
(11 components, 28 subcomponents) following [map-format.md](../repo_analyzer/docs/map-format.md). This check shaped
several scoring rules; the numbers below are from that run.

## Result

| Component | Kind | Own lines | Effort | Per 100 lines |
|---|---|---:|---:|---:|
| Writing data | feature | 10,533 | 44,469 | 422 |
| Web interface | feature | 7,930 | 34,106 | 430 |
| Table browsing | feature | 7,300 | 32,486 | 445 |
| Application core | foundation | 4,011 | 28,225 | 704 |
| Permissions and authentication | feature | 5,082 | 17,911 | 352 |
| Queries | feature | 4,576 | 16,259 | 355 |
| Database layer | foundation | 2,281 | 12,004 | 526 |
| Shared utilities | foundation | 1,057 | 8,326 | 788 |
| Command line and publishing | feature | 1,706 | 5,594 | 328 |
| Developer and test tooling | supporting | 1,019 | 3,689 | 362 |
| Observability | foundation | 953 | 1,986 | 208 |
| **Whole repository** | | 46,448 | 160,091 | 345 |

The ordering is believable:
- **Biggest jobs:** the large feature areas (writing data, the web interface, table browsing).
- **Highest work per line:** the foundations that everything depends on (shared utilities 788, application core 704,
  database layer 526). They have many callers to check and many shared values they change.
- **Least work per line:** small, self-contained areas (observability 208, command line 328).

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

Call links went from 8,937 (many guessed) to 6,260.

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
