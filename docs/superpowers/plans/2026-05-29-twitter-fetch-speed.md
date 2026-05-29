# Twitter Fetch Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speed up cold OpenCLI Twitter/X fetching without changing `twitter.json` shape or any rendered digest output format.

**Architecture:** Keep the optimization inside `scripts/fetch-twitter.py`'s OpenCLI backend. Add a small OpenCLI precheck cache, conditionally skip repeated capability/doctor checks, prioritize `priority=true` sources in the fetch queue, and emit timing only to logs.

**Tech Stack:** Python 3.8+ standard library, `unittest`, existing `ThreadPoolExecutor`, existing project-local JSON state helpers.

---

## 文件结构

- Modify: `scripts/fetch-twitter.py`
  - Add OpenCLI precheck cache helpers near existing OpenCLI update/version helpers.
  - Update `OpenCliBackend.__init__` to use cached capability/doctor checks.
  - Update `OpenCliBackend.fetch_all` to choose a priority probe and submit priority sources before regular sources.
  - Add small timing helpers that log only; they must not modify source result payloads.
- Modify: `tests/test_fetch_twitter_opencli.py`
  - Add tests for precheck cache config and invalidation.
  - Add tests for cached capability/doctor behavior.
  - Add tests for priority probe and priority-first submission.
  - Add tests proving timing does not appear in result payloads.
- Modify: `README.md`, `README_CN.md`, `SKILL.md`
  - Document `OPENCLI_CHECK_CACHE_TTL_SECONDS` and `OPENCLI_STRICT_CHECK`.
  - Do not alter output examples or digest format docs.

## 显示格式保护

实现过程中不得修改这些文件：

```text
references/templates/discord.md
references/templates/chat.md
references/templates/email.md
references/templates/pdf.md
scripts/merge-sources.py
tests/golden/daily-discord.md
tests/golden/daily-chat.md
```

如果这些文件出现在 `git diff --name-only` 中，停止实现并检查是否误改。

---

### Task 1: Add tests for OpenCLI precheck cache helpers

**Files:**
- Modify: `tests/test_fetch_twitter_opencli.py`
- Later modify: `scripts/fetch-twitter.py`

- [ ] **Step 1: Write failing tests for cache TTL, strict mode, and state path**

Add this test class after `TestOpenCliAutoUpdate`:

```python
class TestOpenCliCheckCache(unittest.TestCase):
    def test_check_cache_ttl_defaults_to_one_day(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(fetch_twitter.get_opencli_check_cache_ttl_seconds(), 86400)

    def test_check_cache_ttl_rejects_invalid_values(self):
        cases = ["", "0", "-1", "not-a-number"]
        for value in cases:
            with self.subTest(value=value):
                with patch.dict(os.environ, {"OPENCLI_CHECK_CACHE_TTL_SECONDS": value}, clear=True):
                    self.assertEqual(fetch_twitter.get_opencli_check_cache_ttl_seconds(), 86400)

    def test_check_cache_ttl_accepts_positive_integer(self):
        with patch.dict(os.environ, {"OPENCLI_CHECK_CACHE_TTL_SECONDS": "120"}, clear=True):
            self.assertEqual(fetch_twitter.get_opencli_check_cache_ttl_seconds(), 120)

    def test_strict_check_defaults_to_false(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(fetch_twitter.get_opencli_strict_check())

    def test_strict_check_accepts_truthy_value(self):
        with patch.dict(os.environ, {"OPENCLI_STRICT_CHECK": "1"}, clear=True):
            self.assertTrue(fetch_twitter.get_opencli_strict_check())

    def test_check_state_path_uses_x_cache_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"X_CACHE_DIR": temp_dir}, clear=True):
                self.assertEqual(
                    fetch_twitter.get_opencli_check_state_path(),
                    Path(temp_dir) / "opencli-check-state.json",
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliCheckCache -v
```

Expected: fail with missing attributes such as `get_opencli_check_cache_ttl_seconds`.

- [ ] **Step 3: Implement config and state path helpers**

Add constants near existing OpenCLI constants in `scripts/fetch-twitter.py`:

```python
OPENCLI_CHECK_CACHE_TTL_SECONDS = 24 * 60 * 60
OPENCLI_CHECK_STATE_FILENAME = "opencli-check-state.json"
```

Add helpers near `get_x_cache_dir`:

```python
def get_opencli_check_cache_ttl_seconds() -> int:
    """Return the TTL for cached OpenCLI capability and doctor checks."""
    raw_value = os.getenv("OPENCLI_CHECK_CACHE_TTL_SECONDS", "").strip()
    if not raw_value:
        return OPENCLI_CHECK_CACHE_TTL_SECONDS
    try:
        value = int(raw_value)
    except ValueError:
        logging.warning(
            "Invalid OPENCLI_CHECK_CACHE_TTL_SECONDS=%r; using %s",
            raw_value,
            OPENCLI_CHECK_CACHE_TTL_SECONDS,
        )
        return OPENCLI_CHECK_CACHE_TTL_SECONDS
    if value <= 0:
        logging.warning(
            "Invalid OPENCLI_CHECK_CACHE_TTL_SECONDS=%r; using %s",
            raw_value,
            OPENCLI_CHECK_CACHE_TTL_SECONDS,
        )
        return OPENCLI_CHECK_CACHE_TTL_SECONDS
    return value


def get_opencli_strict_check() -> bool:
    """Return true when OpenCLI prechecks should always run."""
    return _env_bool("OPENCLI_STRICT_CHECK", False)


def get_opencli_check_state_path() -> Path:
    """Return the project-local OpenCLI precheck state path."""
    return get_x_cache_dir() / OPENCLI_CHECK_STATE_FILENAME
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliCheckCache -v
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py
git commit -m "test: cover OpenCLI check cache config"
```

---

### Task 2: Add OpenCLI precheck cache state

**Files:**
- Modify: `tests/test_fetch_twitter_opencli.py`
- Modify: `scripts/fetch-twitter.py`

- [ ] **Step 1: Write failing tests for fresh, stale, and mismatched cache entries**

Add these methods to `TestOpenCliCheckCache`:

```python
    def test_fresh_check_state_matches_identity(self):
        now = 1779984000
        state = {
            "opencli_path": "/bin/opencli",
            "opencli_version": "1.7.22",
            "capability_checked_at": now - 10,
            "doctor_checked_at": now - 20,
            "doctor_status": "ok",
        }

        self.assertTrue(
            fetch_twitter.is_opencli_check_cache_fresh(
                state,
                "/bin/opencli",
                "1.7.22",
                now,
                86400,
                "capability_checked_at",
            )
        )
        self.assertTrue(
            fetch_twitter.is_opencli_check_cache_fresh(
                state,
                "/bin/opencli",
                "1.7.22",
                now,
                86400,
                "doctor_checked_at",
            )
        )

    def test_check_state_is_stale_when_ttl_expires(self):
        now = 1779984000
        state = {
            "opencli_path": "/bin/opencli",
            "opencli_version": "1.7.22",
            "capability_checked_at": now - 86500,
        }

        self.assertFalse(
            fetch_twitter.is_opencli_check_cache_fresh(
                state,
                "/bin/opencli",
                "1.7.22",
                now,
                86400,
                "capability_checked_at",
            )
        )

    def test_check_state_is_stale_when_identity_changes(self):
        now = 1779984000
        state = {
            "opencli_path": "/old/opencli",
            "opencli_version": "1.7.22",
            "capability_checked_at": now - 10,
        }

        self.assertFalse(
            fetch_twitter.is_opencli_check_cache_fresh(
                state,
                "/bin/opencli",
                "1.7.22",
                now,
                86400,
                "capability_checked_at",
            )
        )

    def test_record_check_state_preserves_existing_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "opencli-check-state.json"
            store = fetch_twitter.JsonStateStore(path)
            store.save({"doctor_checked_at": 10, "doctor_status": "ok"})

            fetch_twitter.record_opencli_check_state(
                store,
                "/bin/opencli",
                "1.7.22",
                "capability_checked_at",
                now=20,
            )

            self.assertEqual(
                store.load(),
                {
                    "opencli_path": "/bin/opencli",
                    "opencli_version": "1.7.22",
                    "doctor_checked_at": 10,
                    "doctor_status": "ok",
                    "capability_checked_at": 20,
                },
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliCheckCache -v
```

Expected: fail with missing `is_opencli_check_cache_fresh` and `record_opencli_check_state`.

- [ ] **Step 3: Implement cache freshness and recording helpers**

Add near the helpers from Task 1:

```python
def is_opencli_check_cache_fresh(
    state: Dict[str, Any],
    command: str,
    version: str,
    now: float,
    ttl_seconds: int,
    checked_at_key: str,
) -> bool:
    """Return true when a cached OpenCLI precheck result can be reused."""
    if not state:
        return False
    if state.get("opencli_path") != command:
        return False
    if state.get("opencli_version") != version:
        return False
    checked_at = state.get(checked_at_key)
    try:
        checked_at_value = float(checked_at)
    except (TypeError, ValueError):
        return False
    return checked_at_value > 0 and now - checked_at_value < ttl_seconds


def record_opencli_check_state(
    store: JsonStateStore,
    command: str,
    version: str,
    checked_at_key: str,
    now: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist one successful OpenCLI precheck timestamp."""
    current = int(time.time() if now is None else now)
    state = store.load()
    state["opencli_path"] = command
    state["opencli_version"] = version
    state[checked_at_key] = current
    if extra:
        state.update(extra)
    store.save(state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliCheckCache -v
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py
git commit -m "feat: add OpenCLI precheck cache state"
```

---

### Task 3: Use cached capability and doctor checks

**Files:**
- Modify: `tests/test_fetch_twitter_opencli.py`
- Modify: `scripts/fetch-twitter.py`

- [ ] **Step 1: Write failing backend tests for cached prechecks**

Add these methods to `TestOpenCliBackend`:

```python
    @patch.dict(os.environ, {"OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN": "0"}, clear=True)
    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_cached_opencli_prechecks_skip_capability_and_doctor(self, run_mock, _resolve_mock):
        now = 1779984000
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "opencli-check-state.json"
            fetch_twitter.JsonStateStore(state_path).save({
                "opencli_path": "/bin/opencli",
                "opencli_version": "1.7.22",
                "capability_checked_at": now - 10,
                "doctor_checked_at": now - 10,
                "doctor_status": "ok",
            })
            with ExitStack() as stack:
                stack.enter_context(patch("fetch_twitter.get_opencli_check_state_path", return_value=state_path))
                stack.enter_context(patch("fetch_twitter.time.time", return_value=now))
                stack.enter_context(patch("fetch_twitter.snapshot_chrome_windows", return_value=None))

                backend = fetch_twitter.OpenCliBackend(no_cache=True)

        self.assertEqual(backend.command, "/bin/opencli")
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertNotIn(["/bin/opencli", "twitter", "tweets", "--help"], commands)
        self.assertNotIn(["/bin/opencli", "doctor"], commands)

    @patch.dict(os.environ, {"OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN": "0"}, clear=True)
    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_strict_opencli_check_ignores_precheck_cache(self, run_mock, _resolve_mock):
        now = 1779984000
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "opencli-check-state.json"
            fetch_twitter.JsonStateStore(state_path).save({
                "opencli_path": "/bin/opencli",
                "opencli_version": "1.7.22",
                "capability_checked_at": now - 10,
                "doctor_checked_at": now - 10,
                "doctor_status": "ok",
            })
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {"OPENCLI_STRICT_CHECK": "1"}))
                stack.enter_context(patch("fetch_twitter.get_opencli_check_state_path", return_value=state_path))
                stack.enter_context(patch("fetch_twitter.time.time", return_value=now))
                stack.enter_context(patch("fetch_twitter.snapshot_chrome_windows", return_value=None))

                fetch_twitter.OpenCliBackend(no_cache=True)

        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["/bin/opencli", "twitter", "tweets", "--help"], commands)
        self.assertIn(["/bin/opencli", "doctor"], commands)

    @patch.dict(os.environ, {"OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN": "0"}, clear=True)
    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_successful_prechecks_are_recorded(self, run_mock, _resolve_mock):
        now = 1779984000
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "opencli-check-state.json"
            with ExitStack() as stack:
                stack.enter_context(patch("fetch_twitter.get_opencli_check_state_path", return_value=state_path))
                stack.enter_context(patch("fetch_twitter.time.time", return_value=now))
                stack.enter_context(patch("fetch_twitter.snapshot_chrome_windows", return_value=None))

                fetch_twitter.OpenCliBackend(no_cache=True)

            state = fetch_twitter.JsonStateStore(state_path).load()

        self.assertEqual(state["opencli_path"], "/bin/opencli")
        self.assertEqual(state["opencli_version"], "1.7.22")
        self.assertEqual(state["capability_checked_at"], now)
        self.assertEqual(state["doctor_checked_at"], now)
        self.assertEqual(state["doctor_status"], "ok")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend -v
```

Expected: cached precheck test fails because capability and doctor still run.

- [ ] **Step 3: Implement cached prechecks in `OpenCliBackend.__init__`**

In `OpenCliBackend.__init__`, compute the version once and use the state store:

```python
        self._check_state_store = JsonStateStore(get_opencli_check_state_path())
        self._opencli_version = _get_opencli_version(self.command)
        if self._opencli_version is None:
            minimum_text = _opencli_version_str(_min_opencli_version())
            raise OpenCliBackendError(
                "opencli_version_unknown",
                f"Could not determine OpenCLI version. Install OpenCLI {minimum_text} or later.",
            )
        current_tuple = _parse_opencli_version(self._opencli_version)
        if current_tuple is None:
            raise OpenCliBackendError(
                "opencli_version_unknown",
                "Could not parse OpenCLI version output.",
            )
        minimum = _min_opencli_version()
        if current_tuple < minimum:
            raise OpenCliBackendError(
                "opencli_version_too_old",
                f"OpenCLI is too old (current={self._opencli_version}); upgrade to {_opencli_version_str(minimum)} or later.",
            )
```

Replace the direct `_ensure_opencli_min_version`, `_verify_capability`, and `_run_doctor` calls with:

```python
            self._before_chrome_windows = snapshot_chrome_windows()
            self._verify_capability_cached()
            self._run_doctor_cached()
```

Add methods inside `OpenCliBackend`:

```python
    def _precheck_cache_fresh(self, checked_at_key: str) -> bool:
        if get_opencli_strict_check():
            return False
        state = self._check_state_store.load()
        return is_opencli_check_cache_fresh(
            state,
            self.command,
            self._opencli_version,
            time.time(),
            get_opencli_check_cache_ttl_seconds(),
            checked_at_key,
        )

    def _verify_capability_cached(self) -> None:
        if self._precheck_cache_fresh("capability_checked_at"):
            logging.debug("OpenCLI capability check cache hit.")
            return
        self._verify_capability()
        record_opencli_check_state(
            self._check_state_store,
            self.command,
            self._opencli_version,
            "capability_checked_at",
        )

    def _run_doctor_cached(self) -> None:
        if self._precheck_cache_fresh("doctor_checked_at"):
            logging.debug("OpenCLI doctor check cache hit.")
            return
        self._run_doctor()
        record_opencli_check_state(
            self._check_state_store,
            self.command,
            self._opencli_version,
            "doctor_checked_at",
            extra={"doctor_status": "ok"},
        )
```

Update auto-update version checks so they use `self._opencli_version` after update. If a forced update can change the version, refresh `self._opencli_version = _get_opencli_version(self.command)` after `_ensure_opencli_latest`.

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend -v
```

Expected: `OK`.

- [ ] **Step 5: Run full Twitter test module**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py
git commit -m "feat: cache OpenCLI prechecks"
```

---

### Task 4: Prioritize source scheduling without changing result shape

**Files:**
- Modify: `tests/test_fetch_twitter_opencli.py`
- Modify: `scripts/fetch-twitter.py`

- [ ] **Step 1: Write failing tests for priority probe and priority-first submission**

Add these methods to `TestOpenCliBackend`:

```python
    def test_selects_priority_source_as_probe(self):
        regular = dict(self.source, id="regular-twitter", handle="regular", priority=False)
        priority = dict(self.source, id="priority-twitter", handle="priority", priority=True)

        ordered = fetch_twitter.prioritize_opencli_sources([regular, priority])

        self.assertEqual([item["handle"] for item in ordered], ["priority", "regular"])

    def test_preserves_relative_order_within_priority_groups(self):
        first_regular = dict(self.source, id="regular-a", handle="regular_a", priority=False)
        first_priority = dict(self.source, id="priority-a", handle="priority_a", priority=True)
        second_priority = dict(self.source, id="priority-b", handle="priority_b", priority=True)
        second_regular = dict(self.source, id="regular-b", handle="regular_b", priority=False)

        ordered = fetch_twitter.prioritize_opencli_sources([
            first_regular,
            first_priority,
            second_priority,
            second_regular,
        ])

        self.assertEqual(
            [item["handle"] for item in ordered],
            ["priority_a", "priority_b", "regular_a", "regular_b"],
        )

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_fetch_all_fetches_priority_probe_first(self, run_mock, _resolve_mock):
        regular = dict(self.source, id="regular-twitter", handle="regular", priority=False)
        priority = dict(self.source, id="priority-twitter", handle="priority", priority=True)
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({"tabs": []})),
            self._completed(json.dumps([
                {
                    "id": "priority-post",
                    "author": "priority",
                    "text": "Priority works.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "is_retweet": False,
                }
            ])),
            self._completed(json.dumps([
                {
                    "id": "regular-post",
                    "author": "regular",
                    "text": "Regular works.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "is_retweet": False,
                }
            ])),
            self._completed(json.dumps({"tabs": []})),
            self._completed("lease released"),
        ]

        backend = fetch_twitter.OpenCliBackend(max_workers=1, no_cache=True)
        results = backend.fetch_all([regular, priority], self.cutoff)

        tweet_commands = [
            call.args[0]
            for call in run_mock.call_args_list
            if call.args[0][:2] == ["/bin/opencli", "twitter"] and "tweets" in call.args[0]
        ]
        self.assertEqual(tweet_commands[1][3], "priority")
        self.assertEqual({item["handle"] for item in results}, {"priority", "regular"})
        for result in results:
            self.assertEqual(
                set(result.keys()),
                {
                    "source_id",
                    "source_type",
                    "name",
                    "handle",
                    "priority",
                    "topics",
                    "status",
                    "attempts",
                    "count",
                    "articles",
                },
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend -v
```

Expected: fail with missing `prioritize_opencli_sources` and probe still using first configured source.

- [ ] **Step 3: Implement priority ordering helper**

Add near backend selection helpers:

```python
def prioritize_opencli_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return sources with priority sources first while preserving group order."""
    priority_sources = [source for source in sources if source.get("priority")]
    regular_sources = [source for source in sources if not source.get("priority")]
    return priority_sources + regular_sources
```

- [ ] **Step 4: Update `OpenCliBackend.fetch_all` to use prioritized ordering**

At the top of `fetch_all`, replace direct use of `sources[0]` and `sources[1:]` with:

```python
        ordered_sources = prioritize_opencli_sources(sources)
```

Then use:

```python
            first = self._fetch_user_tweets(ordered_sources[0], cutoff)
            results.append(first)
            logging.info(f"[1/{total}] @{first['handle']}: {first['count']} tweets via OpenCLI")

            remaining = ordered_sources[1:]
```

Keep the existing result payload creation unchanged.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend -v
```

Expected: `OK`.

- [ ] **Step 6: Run full Twitter test module**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli -v
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py
git commit -m "feat: prioritize OpenCLI Twitter fetches"
```

---

### Task 5: Add timing logs without touching output payloads

**Files:**
- Modify: `tests/test_fetch_twitter_opencli.py`
- Modify: `scripts/fetch-twitter.py`

- [ ] **Step 1: Write failing tests for timing logs and payload isolation**

Add this test method to `TestOpenCliBackend`:

```python
    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_timing_logs_do_not_change_result_payload(self, run_mock, _resolve_mock):
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({"tabs": []})),
            self._completed(json.dumps([
                {
                    "id": "123",
                    "author": "sama",
                    "text": "A useful post.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "url": "https://x.com/sama/status/123",
                    "is_retweet": False,
                }
            ])),
            self._completed(json.dumps({"tabs": []})),
            self._completed("lease released"),
        ]

        with self.assertLogs(level="INFO") as logs:
            backend = fetch_twitter.OpenCliBackend(no_cache=True)
            results = backend.fetch_all([self.source], self.cutoff)

        self.assertTrue(any("opencli.phase" in line for line in logs.output))
        payload_text = json.dumps(results, sort_keys=True)
        self.assertNotIn("elapsed", payload_text)
        self.assertNotIn("opencli.phase", payload_text)
        self.assertEqual(results[0]["articles"][0]["tweet_id"], "123")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend.test_timing_logs_do_not_change_result_payload -v
```

Expected: fail because timing logs are not emitted.

- [ ] **Step 3: Implement timing helper**

Add near logging helpers:

```python
class PhaseTimer:
    """Context manager for lightweight elapsed-time logging."""

    def __init__(self, name: str):
        self.name = name
        self.started_at = 0.0

    def __enter__(self):
        self.started_at = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
        logging.info("opencli.phase name=%s elapsed_ms=%s", self.name, elapsed_ms)
        return False
```

- [ ] **Step 4: Wrap OpenCLI phases**

In `OpenCliBackend.__init__`, wrap slow precheck phases:

```python
            with PhaseTimer("opencli.browser_snapshot"):
                self._before_chrome_windows = snapshot_chrome_windows()
            with PhaseTimer("opencli.capability"):
                self._verify_capability_cached()
            with PhaseTimer("opencli.doctor"):
                self._run_doctor_cached()
```

In `OpenCliBackend.fetch_all`, wrap probe, parallel fetch, and cleanup:

```python
            with PhaseTimer("opencli.probe_fetch"):
                first = self._fetch_user_tweets(ordered_sources[0], cutoff)
```

Wrap the executor block:

```python
            with PhaseTimer("opencli.parallel_fetch"):
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(self._fetch_user_tweets, source, cutoff): source for source in remaining}
                    for future in as_completed(futures):
                        source = futures[future]
                        try:
                            result = future.result()
                        except OpenCliBackendError as exc:
                            result = self._make_error(source, f"{exc.code}: {exc.message[:160]}", 0)
                        results.append(result)
                        done += 1
                        if result["status"] == "ok":
                            logging.info(f"[{done}/{total}] ✅ @{result['handle']}: {result['count']} tweets via OpenCLI")
                        else:
                            logging.warning(f"[{done}/{total}] ❌ @{result['handle']}: {result.get('error', 'unknown')}")
```

In `finally`, wrap cleanup:

```python
            with PhaseTimer("opencli.cleanup"):
                self._cleanup_new_browser_tabs(before_tabs)
                self._release_browser_lease()
                cleanup_new_opencli_chrome_windows(before_chrome_windows)
```

- [ ] **Step 5: Run focused timing test**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli.TestOpenCliBackend.test_timing_logs_do_not_change_result_payload -v
```

Expected: `OK`.

- [ ] **Step 6: Run full Twitter test module**

Run:

```bash
python3 -m unittest tests.test_fetch_twitter_opencli -v
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py
git commit -m "feat: log OpenCLI fetch timings"
```

---

### Task 6: Document new OpenCLI precheck controls

**Files:**
- Modify: `README.md`
- Modify: `README_CN.md`
- Modify: `SKILL.md`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing documentation tests**

In `tests/test_config.py`, extend the existing OpenCLI documentation test with these assertions:

```python
        self.assertIn("OPENCLI_CHECK_CACHE_TTL_SECONDS", readme)
        self.assertIn("OPENCLI_STRICT_CHECK", readme)
        self.assertIn("OPENCLI_CHECK_CACHE_TTL_SECONDS", readme_cn)
        self.assertIn("OPENCLI_STRICT_CHECK", readme_cn)
        self.assertIn("OPENCLI_CHECK_CACHE_TTL_SECONDS", skill)
        self.assertIn("OPENCLI_STRICT_CHECK", skill)
```

Use the local variable names already present in the test. If the test currently reads documents under different variable names, keep those existing names and only add the assertions.

- [ ] **Step 2: Run docs test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_config.TestDocumentationExamples.test_twitter_backend_docs_include_opencli -v
```

Expected: fail because the new env vars are undocumented.

- [ ] **Step 3: Update README docs**

In `README.md`, add these lines to the Twitter/X backend env block:

```bash
export OPENCLI_CHECK_CACHE_TTL_SECONDS="86400"  # optional; cache OpenCLI capability/doctor checks for 24h
export OPENCLI_STRICT_CHECK="0"  # optional; set 1 to run OpenCLI prechecks every time
```

Add one sentence near the OpenCLI backend paragraph:

```markdown
The fetcher caches successful OpenCLI capability and doctor checks for `OPENCLI_CHECK_CACHE_TTL_SECONDS` seconds to reduce cold-start overhead; set `OPENCLI_STRICT_CHECK=1` when diagnosing browser bridge or login-state issues.
```

In `README_CN.md`, add these lines to the same env block:

```bash
export OPENCLI_CHECK_CACHE_TTL_SECONDS="86400"  # optional; cache OpenCLI capability/doctor checks for 24h
export OPENCLI_STRICT_CHECK="0"  # optional; set 1 to run OpenCLI prechecks every time
```

Add Chinese prose outside code blocks:

```markdown
抓取器会在 `OPENCLI_CHECK_CACHE_TTL_SECONDS` 秒内复用成功的 OpenCLI capability 和 doctor 检查结果，以降低冷启动固定开销；排查浏览器桥接或登录态问题时可设置 `OPENCLI_STRICT_CHECK=1` 强制每次完整预检查。
```

- [ ] **Step 4: Update SKILL metadata and env docs**

In `SKILL.md`, add env entries to the top metadata JSON:

```json
{"name":"OPENCLI_CHECK_CACHE_TTL_SECONDS","required":false,"description":"Optional TTL in seconds for successful OpenCLI capability and doctor precheck cache. Defaults to 86400."}
```

```json
{"name":"OPENCLI_STRICT_CHECK","required":false,"description":"Set to 1 to force OpenCLI capability and doctor prechecks on every Twitter/X run."}
```

In the environment table, add:

```markdown
| `OPENCLI_CHECK_CACHE_TTL_SECONDS` | No | TTL in seconds for successful OpenCLI capability and doctor precheck cache. Default: `86400`. |
| `OPENCLI_STRICT_CHECK` | No | Set to `1` to force OpenCLI capability and doctor prechecks on every Twitter/X run. |
```

In the OpenCLI operation notes, add:

```markdown
Successful OpenCLI capability and doctor prechecks are cached for `OPENCLI_CHECK_CACHE_TTL_SECONDS` seconds. Use `OPENCLI_STRICT_CHECK=1` when diagnosing browser bridge or X login-state problems.
```

- [ ] **Step 5: Run docs test**

Run:

```bash
python3 -m unittest tests.test_config.TestDocumentationExamples.test_twitter_backend_docs_include_opencli -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add README.md README_CN.md SKILL.md tests/test_config.py
git commit -m "docs: document OpenCLI precheck cache controls"
```

---

### Task 7: Full regression and display-format guard

**Files:**
- Verify only; do not modify display templates or golden files.

- [ ] **Step 1: Run full test suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: `Ran 445` or more tests and `OK`.

- [ ] **Step 2: Check display files were not modified**

Run:

```bash
git diff --name-only
```

Expected: no output containing:

```text
references/templates/
scripts/merge-sources.py
tests/golden/
```

- [ ] **Step 3: Inspect final diff summary**

Run:

```bash
git diff --stat HEAD~4..HEAD
```

Expected: changes limited to:

```text
scripts/fetch-twitter.py
tests/test_fetch_twitter_opencli.py
README.md
README_CN.md
SKILL.md
tests/test_config.py
```

- [ ] **Step 4: Commit final fixes if any were needed**

If Step 1 reveals a bug introduced by implementation, fix it in the smallest file scope and commit:

```bash
git add scripts/fetch-twitter.py tests/test_fetch_twitter_opencli.py README.md README_CN.md SKILL.md tests/test_config.py
git commit -m "fix: stabilize Twitter fetch speed changes"
```

If no fixes were needed, do not create an empty commit.

---

## 自检结果

- Spec 覆盖：fast path、priority-first scheduling、timing logs、cleanup scope、display-format hard constraint 都有对应任务。
- 显示格式保护：Task 7 明确检查 templates、merge 和 golden 文件未被修改。
- 类型一致性：新增 helper 使用 `Dict[str, Any]`、`Optional`、`Path`，这些类型已在 `scripts/fetch-twitter.py` 中导入。
- 测试入口：每个实现任务都有 focused test 和回归命令。
