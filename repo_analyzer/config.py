"""All fixed numbers and the one shared list of syntax rules. Nothing here is per language: tree-sitter grammars
name their nodes in similar ways (if_statement / if_expression, for_statement / for_in_clause, ...), so one list of
patterns covers them."""

import re

# --- effort formula -------------------------------------------------------------------------------
TRIP_COST = 50                      # one trip (file to open, round, search match, ...) = 50 lines' worth of work
TOKENS_PER_LINE = 10                # only for showing an approximate token figure
# Typical values per 100 lines for the complexity counts. None = use this repo's own average (until a benchmark
# set of repos exists).
TYPICAL_PER_100 = None

# --- files ----------------------------------------------------------------------------------------
WORK_DIR = ".repo-analyzer"         # everything a run writes lives in <project>/.repo-analyzer/
BINARY_SNIFF_BYTES = 8000
MAX_FILE_BYTES = 2_000_000          # larger text files (generated bundles, data dumps) are not analysed
LARGE_PROJECT_FILES = 20_000        # above this many files the run warns that it will take long
# Folders never read when the project is not a git checkout (inside a git checkout, git's own ignore rules
# decide). They hold installed dependencies, caches and build output, not the project's code.
SKIP_DIRS = {".git", ".hg", ".svn", WORK_DIR, "node_modules", "bower_components", ".venv", "venv",
             "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".gradle",
             ".cache", ".next", ".nuxt", ".parcel-cache", ".turbo", ".terraform", "dist", "build", "target",
             ".idea", ".DS_Store"}

# --- definitions (pieces) -------------------------------------------------------------------------
# Grammars whose tags query is written as an addition to another grammar's query. tree-sitter-typescript ships
# its tags.scm (interfaces, signatures, abstract classes) to be used together with tree-sitter-javascript's
# (functions, classes, methods), because the TypeScript grammar extends the JavaScript one.
TAGS_QUERY_EXTENDS = {"typescript": "javascript", "tsx": "javascript"}
# tags-query kinds that are not pieces (they are values, not code blocks)
NON_PIECE_KINDS = {"constant", "variable", "field", "property", "parameter", "label", "local", "event",
                   "enum_variant", "macro_invocation"}
CLASS_KINDS = {"class", "interface", "module", "type", "struct", "enum", "trait", "object", "impl"}
# nodes that wrap a definition and belong to it (decorators, export keyword)
WRAPPER_TYPES = {"decorated_definition", "export_statement", "export_declaration", "ambient_declaration"}
# fallback when a language has no tags query: definition nodes found by name
GENERIC_DEF_RE = re.compile(
    r"^(function|method|class|struct|interface|trait|enum|module|object|protocol|record|constructor|impl)"
    r"(_definition|_declaration|_item|_specifier|_statement)?$")
GENERIC_CLASS_RE = re.compile(r"^(class|struct|interface|trait|enum|module|object|protocol|record|impl)")

# --- calls ----------------------------------------------------------------------------------------
CALL_RE = re.compile(r"(^call$|call_expression|^method_call$|invocation|new_expression|object_creation_expression"
                     r"|function_call|macro_invocation)")
CALLEE_FIELDS = ("function", "constructor", "name", "method", "macro", "type")
INDEX_TYPES = {"subscript", "subscript_expression", "index_expression", "element_access_expression",
               "element_reference", "array_access"}
MEMBER_RE = re.compile(r"(attribute|member_expression|member_access|selector_expression|field_access"
                       r"|field_expression|scoped_identifier|navigation_expression)")
IMPORT_RE = re.compile(r"(import|use_declaration|preproc_include|^using_directive$)")
IMPORT_CALLS = {"require", "require_relative"}     # calls that load a module (the import() call is one too)

# --- imports (imports.py) -------------------------------------------------------------------------
# A folder named by an import stands for its main file when it has one, else for all its files in the importing
# file's language (a Go or Java package).
MODULE_MAIN_NAMES = {"__init__", "index", "mod", "main", "lib"}
# Words of import lines that are syntax: never a module name and never a name the import brings in.
IMPORT_KEYWORDS = {"import", "from", "as", "use", "using", "require", "require_relative", "include", "static",
                   "package", "extern", "crate", "pub", "const", "let", "var", "type", "typeof", "export",
                   "default", "namespace", "module", "self", "super", "this", "load", "with"}
# A name looked up in an imported file is also looked for in the files that file imports, this many steps on
# (index.js or __init__.py files pass on names defined elsewhere).
REEXPORT_DEPTH = 2

# --- hidden flow ----------------------------------------------------------------------------------
DECORATOR_TYPES = {"decorator", "annotation", "marker_annotation", "attribute_list", "attribute_item"}
LAMBDA_TYPES = {"lambda", "lambda_expression", "arrow_function", "function_expression", "func_literal",
                "closure_expression", "anonymous_function", "anonymous_method_expression", "lambda_literal",
                "do_block"}
ARGUMENT_LIST_RE = re.compile(r"^(argument_list|arguments|value_arguments)$")
# a lambda counts as hidden flow only when it is handed to other code (an argument or a stored value)
HANDOFF_PARENT_TYPES = {"keyword_argument", "pair", "dictionary", "list", "array", "object", "tuple",
                        "set", "argument", "value_argument"}

# --- writes and assignments -----------------------------------------------------------------------
ASSIGN_RE = re.compile(r"assignment")
DECLARATOR_RE = re.compile(r"(declarator$|^let_declaration$|^short_var_declaration$|^var_spec$|^const_spec$)")
TARGET_FIELDS = ("left", "name", "pattern", "declarator")
PATTERN_RE = re.compile(r"(pattern|tuple|list|expression_list|parenthesized)")
LOCAL_KEYWORDS = {"self", "this", "cls", "super"}      # receivers: writes through them leave the function

# --- decisions (complexity) -----------------------------------------------------------------------
IF_RE = re.compile(r"^(if|elif|elsif|else_if|unless|guard)(_|$)")
TERNARY_TYPES = {"conditional_expression", "ternary_expression", "conditional"}
CASE_TYPES = {"switch_case", "case_clause", "match_arm", "switch_section", "expression_case", "type_case",
              "communication_case", "when", "case_item", "switch_label", "switch_rule", "when_entry", "case_block"}
LOOP_RE = re.compile(r"^(enhanced_)?(for|while|do|foreach|loop|repeat|until)(_|$)")
LOOP_EXCLUDE = {"for_clause", "range_clause", "do_block"}
CATCH_RE = re.compile(r"^(catch|except|rescue)(_|$)")
EXIT_RE = re.compile(r"^(return|break|continue|next|throw|raise)(_statement|_expression)?$")
BOOL_TOKENS = {"&&", "||", "and", "or"}
CONDITION_FIELDS = {"condition", "left", "right", "subject", "value", "iterator", "pattern"}

# --- concurrency ----------------------------------------------------------------------------------
CONCURRENCY_TYPES = {"go_statement", "send_statement", "select_statement", "synchronized_statement",
                     "async_block", "lock_statement"}
CONCURRENCY_TOKENS = {"async", "await"}
CONCURRENCY_CALLS = {"Thread", "Lock", "RLock", "Mutex", "Semaphore", "create_task", "gather", "spawn",
                     "ThreadPoolExecutor", "ProcessPoolExecutor", "run_in_executor"}

# --- names ----------------------------------------------------------------------------------------
NAME_LEAF_TYPES = {"name", "constant"}
READ_TYPES = {"identifier", "simple_identifier"}
RECEIVER_TYPES = {"self", "this"}
SELF_NAMES = {"self", "this", "cls"}               # a call through these stays inside the same class
# Names constructor methods have across mainstream languages (a method named like its class counts too).
CONSTRUCTOR_NAMES = {"__init__", "__new__", "__post_init__", "constructor", "initialize", "init", "new",
                     "__construct"}
# A method called on an object of unknown type (not a name an import line brought in) is linked by name only when
# the name is distinctive: compound names (render_template, parseRows) or long names. Short single words (get,
# append, all, now) are shared with the standard libraries of most languages, so linking them by name would mostly
# be wrong.
DISTINCT_NAME_MIN_LEN = 10
RECEIVER_FIELDS = ("object", "operand", "value", "expression", "receiver", "scope", "path")
# containers whose last statement is the final statement of a function body
BODY_WRAPPERS_RE = re.compile(r"(block|statement_list|body_statement|declaration_list|expression_statement)$")
NOT_READ_FIELDS = {"attribute", "property", "field", "name", "method", "type", "key", "label", "alias",
                   "module_name"}

# --- mapping agent (Omnigent, non-interactive) -------------------------------------------------------
AGENT_MODEL = "claude-opus-5-5[1m]"  # 1M-token context: the agent reads a whole codebase
AGENT_EFFORT = "max"                 # low | medium | high | xhigh | max
AGENT_CLAUDE_PATH = None             # None = the newest `claude` CLI found on this machine
OMNI_PATH_ENV = "REPO_ANALYZER_OMNI"  # environment variable that can point at a specific `omni` executable
AGENT_SESSION_TIMEOUT_SECONDS = 40 * 60   # Omnigent follows one -p session for about 32 minutes
# A session with no new step for this long, while its processes use no CPU, is stuck (for example on a dead
# network connection): it is stopped and the next session continues the work.
AGENT_STALL_SECONDS = 20 * 60
AGENT_STALL_CHECK_SECONDS = 180      # how often the session's CPU use is sampled
AGENT_STALL_CPU_SECONDS = 1.0        # CPU used between two samples below this = idle
# Sessions are chained until the agent reports the map complete. Bigger projects get more sessions:
# one per AGENT_FILES_PER_SESSION text files, between AGENT_MIN_SESSIONS and AGENT_MAX_SESSIONS.
AGENT_FILES_PER_SESSION = 400
AGENT_MIN_SESSIONS = 4
AGENT_MAX_SESSIONS = 20
# A session that fails because the network or the model service is unreachable (a sleeping laptop, an outage)
# is retried with growing waits for up to this long.
AGENT_TRANSIENT_RETRY_MINUTES = 60
AGENT_RETRY_FIRST_WAIT_SECONDS = 60  # the first wait; each next wait doubles, up to 10 minutes
TRANSIENT_ERROR_HINTS = ("can't reach the api", "enotfound", "econnreset", "econnrefused", "etimedout",
                         "network", "overloaded", "rate limit", "429", "500", "502", "503", "504", "529",
                         "timed out", "temporarily unavailable", "connection")
AGENT_SESSION_RETRIES = 1            # other session failures are retried this many times
REPAIR_ROUNDS = 3                    # times a map with problems goes back to the agent
AGENT_DONE_MARKER = "MAP COMPLETE"
AGENT_PREVENT_IDLE_SLEEP = True      # macOS: no idle sleep while an agent session runs (caffeinate)
PREFLIGHT_TIMEOUT_SECONDS = 5 * 60
