# Bitcoin ETL Parquet Schema

This README describes the schema of the Parquet datasets produced by the ETL script:

- `blocks/` – one row per block
- `txs/` – one row per transaction
- `io/` – one row per transaction input or output ("I/O row")

All are derived from `bitcoind` JSON-RPC (`getblockhash`, `getblock` with verbosity=2).

The ETL writes files partitioned by calendar day:

- `OUT_DIR/blocks/day=YYYY-MM-DD/blocks-<start>-<end>.parquet`
- `OUT_DIR/txs/day=YYYY-MM-DD/txs-<start>-<end>.parquet`
- `OUT_DIR/io/day=YYYY-MM-DD/io-<start>-<end>.parquet`

Most engines (e.g. Polars, PyArrow) will expose a synthetic `day` column based on the partition directory, even though it is not physically stored inside each Parquet file.

---

## Common fields

These columns appear across tables (or are conceptually shared):

| Column     | Type           | Description |
|-----------:|----------------|-------------|
| `height`   | `int64`        | Block height (0-based index of the block in the active chain). |
| `block_hash` | `string`     | 32-byte block header hash (double-SHA256, displayed as 64-hex string, big-endian). |
| `time`     | `datetime` (UTC) | Block/transaction timestamp as reported by Core, converted from Unix seconds to a UTC timestamp. For `blocks` it is header `nTime`; for `txs`/`io` it copies the parent block’s `time`. |
| `day`      | `date`         | Calendar date `YYYY-MM-DD` extracted from `time`. Implemented as a partition column from the directory `day=YYYY-MM-DD`. |

---

## `blocks/` – Block-level table

One row per block in the active chain.

### Columns

| Column      | Type        | Description |
|-------------|-------------|-------------|
| `height`    | `int64`     | Block height in the active chain. Block 0 is the genesis block. |
| `block_hash` | `string`   | Block header hash (`getblockhash <height>`), 64-char hex string. |
| `time`      | `datetime`  | Block header time (`nTime`, from `getblock`), converted to UTC. This is the *block timestamp* as chosen by the miner (subject to consensus rules), not necessarily wall-clock time. |
| `tx_count`  | `int64`     | Number of transactions in the block, including the coinbase transaction. Equal to `len(result["tx"])` from `getblock` v=2. |
| `size`      | `int64`     | Serialized size of the block in bytes, as reported by Core (`size` field in `getblock`). This includes witness data for SegWit blocks. |
| `weight`    | `int64`     | Block **weight** in weight units (WU). As per BIP141, `weight = 3 × stripped_size + total_size`, with a consensus maximum of 4,000,000 WU per block. |
| `day`       | `date`      | Partition date derived from `time` (`time` rounded down to UTC calendar day). Commonly provided as a synthetic column when reading the partitioned dataset. |

---

## `txs/` – Transaction-level table

One row per transaction, with block-level metadata duplicated for convenience.

### Columns

| Column       | Type        | Description |
|--------------|-------------|-------------|
| `height`     | `int64`     | Height of the block containing this transaction. |
| `block_hash` | `string`    | Hash of the containing block. |
| `time`       | `datetime`  | Timestamp of the containing block (header `nTime`), as UTC. All transactions in a block share the same `time`. |
| `txid`       | `string`    | Transaction ID (TXID). This is the double-SHA256 hash of the transaction’s **non-witness** serialization, displayed as a 64-hex string. This ID is used for inputs’ `prev_txid` references and for pre-SegWit txid-based malleability. |
| `hash`       | `string`    | Witness transaction ID (**wtxid**). For SegWit transactions, this is the hash including witness data; for legacy transactions it is identical to `txid`. |
| `size`       | `int64`     | Transaction size in bytes as serialized on the wire (`size` field from RPC). This includes witness data for SegWit transactions. |
| `vsize`      | `int64`     | Transaction **virtual size** in vbytes. Defined as `ceil(weight / 4)`; fee rates are typically expressed as satoshis per vbyte using this quantity. |
| `weight`     | `int64`     | Transaction weight in WU, `3 × stripped_size + total_size`, where `stripped_size` excludes witness data and `total_size` includes it. |
| `vin_count`  | `int64`     | Number of transaction inputs: `len(tx["vin"])`. For a coinbase transaction this is typically 1, with a special coinbase input. |
| `vout_count` | `int64`     | Number of transaction outputs: `len(tx["vout"])`. |
| `day`        | `date`      | Partition date derived from the block’s `time` (same as in `blocks`). |

---

## `io/` – Per-input / per-output table

This table contains one row for each **input** and for each **output address** in every transaction. It is designed to make UTXO-style joins and address-level analysis easy.

### Row model

- Each transaction input (`vin[i]`) produces one row with `dir = "in"`.
- Each transaction output (`vout[i]`) with N addresses produces N rows with `dir = "out"` (one per address).
- Outputs with no decoded address (e.g. non-standard scripts, some `OP_RETURN` outputs) have `address = null` but still appear as an `out` row (or rows) as long as the RPC exposes them as such.

### Columns

| Column      | Type           | Description |
|-------------|----------------|-------------|
| `dir`       | `string`       | Direction of the I/O row: `"in"` for transaction inputs, `"out"` for transaction outputs. |
| `height`    | `int64`        | Height of the block containing this transaction. |
| `time`      | `datetime`     | Block timestamp (same as in `txs`). |
| `txid`      | `string`       | TXID of the transaction this I/O belongs to (same as `txs.txid`). |
| `n`         | `int64`        | For `dir = "out"`: the **vout index** (0-based) within the transaction; for `dir = "in"`: the **vin index** (0-based) of the input. This `(txid, n)` pair identifies a unique position within the transaction. |
| `prev_txid` | `string` \| `null` | For `dir = "in"`: TXID of the **previous** transaction whose output is being spent (comes from `vin[i].txid`). For coinbase inputs this is `null` by consensus. For `dir = "out"` rows this is always `null`. |
| `prev_vout` | `int64` \| `null`  | For `dir = "in"`: index of the previous output being spent (`vin[i].vout`). For coinbase inputs this is `null`. For `dir = "out"` rows this is always `null`. |
| `address`   | `string` \| `null` | Decoded output address, if available from `scriptPubKey`. For standard scripts, this is a legacy/Base58 or Bech32 address (e.g. `1...`, `3...`, `bc1...`). If the script exposes multiple addresses (e.g. certain multisig forms), the same `(txid, n)` will appear in multiple `out` rows, one per address. For non-standard scripts or scripts with no address (such as some `OP_RETURN` outputs), this will be `null`. For `dir = "in"` rows, address is not resolved (always `null` in this ETL). |
| `value`     | `float64` \| `null` | For `dir = "out"`: value of the output **in BTC** (`vout[i].value` from RPC). For `dir = "in"`: always `null` (values of spent outputs are not re-looked-up in this table). |
| `day`       | `date`         | Partition date derived from the parent block’s `time`. |

### Typical joins and usage patterns

- **Block → transactions:** join `blocks` to `txs` on `height` (or `block_hash`) to enrich transaction-level data with block-level attributes (e.g. `txs` already duplicates most of this).
- **Transaction → I/O:** join `txs` to `io` on `txid` (and optionally `height`) to attach per-input/output rows.
- **UTXO resolution:** join `io` as inputs (`dir = "in"`) on `(prev_txid = txid, prev_vout = n)` to `io` as outputs (`dir = "out"`) to recover the value and address of the spent outputs.
- **Address-level flows:** filter `io` on `dir = "out"` to see creation of UTXOs by address, and on `dir = "in"` + a join back to outputs to see spending patterns.

---

## Notes & limitations

- **Active chain only:** The ETL follows the active chain via `getblockhash`/`getblock`. Historical reorg handling is limited to whatever `CONFIRM_LAG` you configure.
- **No mempool data:** Only confirmed on-chain data is captured.
- **Address decoding:** Addresses depend on what `bitcoind` exposes in `scriptPubKey.address` / `scriptPubKey.addresses`. Non-standard scripts may have `address = null`.
- **Input values:** Input `value` is **not** directly stored; you must join inputs to their originating outputs to recover exact amounts.


# Patch Note #0

# Experiment change log (Bitcoin address clustering)

This document summarizes what changed across iterations of the clustering pipeline, why it was changed, how it affected results, and what was ultimately preserved.

---

## Baseline goal

Cluster Bitcoin **addresses** into **entities** (connected components) using Union-Find (UF), where edges come from heuristics:

- **Multi-input heuristic**: addresses that co-spend in one transaction are likely controlled by one entity.
- **Change heuristic**: if one output is inferred as change back to the spender, link it to the spender.

---

# Iteration 0 — Initial “direct IO” approach (fails silently on real data)

### What the algorithm assumed
- Input rows (`dir == vin`) contain input **addresses** directly.
- Output rows (`dir == vout`) contain output **addresses** directly.

### What was actually true in the dataset
- `dir` values were `in/out`, not `vin/vout`.
- **Input rows had address = NULL** (because inputs reference previous outputs and do not directly store addresses).

### Observed outcome
- Either:
  - the run crashed due to unexpected `dir` encoding, or
  - it processed all files but ended with:
    - `n_nodes == 0`
    - “No addresses found … nothing to cluster.”

### Key lesson
**Inputs must be resolved via prevouts (UTXO model).** You cannot cluster inputs from the `in` rows without joining to previous outputs.

**Preserved:** Union-Find clustering logic, overall per-tx heuristic design.

---

# Iteration 1 — Fix `dir` segmentation (`in/out`) and basic checks

### Main change
- Normalize `dir` to lowercase.
- Treat `out` as outputs, and everything else as inputs (later refined to explicit `dir == "in"`).

### Diagnostic checks added
- Print unique `dir` values for first processed file(s).
- Print per-dir row counts and address null/non-null counts.

### Outcome
- Confirmed:
  - `dir ∈ {in, out}`
  - input addresses were null almost always
  - output addresses were mostly present

### Key result impact
- Still produced no meaningful clustering (no input addresses to union).
- But made the data issue explicit and measurable.

**Preserved:** multi-input/change idea, file scanning structure.

---

# Iteration 2 — Add prevout resolution with a SQLite outpoint index (core upgrade)

### Main change (structural)
Introduce an **outpoint database** to resolve input addresses:

1. **Index outputs**: store `(txid, n) -> address` for outputs.
2. For each input row: use `(prev_txid, prev_vout)` to lookup the address of the spent output.

Implementation:
- SQLite table: `outpoints(txid TEXT, n INTEGER, address TEXT, PRIMARY KEY(txid,n))`.

### Why SQLite
- Simple, durable, supports random lookup.
- Avoids keeping an in-memory dictionary of all outpoints (too large).

### Outcome
- Inputs began resolving.
- Multi-input heuristic began firing.
- Cluster formation started working.

### Additional improvements made shortly after
- Chunked lookup (`OR`-clause batches) to reduce per-row `SELECT` overhead.
- Index/PRAGMA tuning (`WAL`, `synchronous=OFF`, cache sizing) to speed up inserts/lookups.

**Preserved:** union rules; changed only how input addresses are obtained.

---

# Iteration 3 — Introduce two time windows: INDEX vs ANALYSIS (correctness + coverage)

### Main change
Use two windows:

- **Index window**: `[INDEX_START, ANALYSIS_END)`
  Build outpoint DB so early-year spends can resolve.
- **Analysis window**: `[ANALYSIS_START, ANALYSIS_END)`
  Apply heuristics only in the target year.

With lookback:
- `INDEX_START = ANALYSIS_START - 365 days` (configurable)

### Effect on results
- Prevout hit-rate increased.
- Multi-input heuristic coverage improved early in the year.
- Reduced artificial fragmentation due to missing inputs.

**Preserved:** heuristics unchanged; only added correct historical context for resolving inputs.

---

# Iteration 4 — Memory and scaling fixes (prevent RAM blowups)

### Main changes
1. **Stop pre-creating UF nodes for every seen output**
   - `PRECREATE_NODES_FOR_ALL_OUTPUT_ADDRS = False` by default.
   - Instead, track output “newness” via a Python set `seen_output_addrs`.

2. **Chunked Parquet writing**
   - Avoid building giant Python lists for `(address, entity_id)` output.
   - Use `pyarrow.parquet.ParquetWriter` in batches (`ENTITY_WRITE_BATCH`).

### Effect on results
- Peak RAM reduced significantly.
- Made year-scale runs feasible on ~40 GB RAM systems.
- Output writing became stable for tens of millions of addresses.

**Preserved:** core UF model and stats; improved memory handling only.

---

# Iteration 5 — Sanity checks + diagnostics (validate giant component)

### Main changes
Add explicit sanity checks:
- largest cluster fraction of nodes
- top-K cluster sizes
- quantiles (median, p90, p99)
- prevout lookup hit-rate

### Observed results (typical run)
- Heavy-tailed distribution + **giant component**
- Example:
  - largest cluster ~55% of nodes
  - median ~2
  - p90 ~6
  - p99 ~23

### Interpretation
- Heavy tail is expected.
- **Largest component dominance is a red flag**: could be real (big custodial/service cluster) or caused by overly permissive unions (especially change).

**Preserved:** results reporting; added validation tooling.

---

# Iteration 6 — Change heuristic hardening (Option B “safe mode” direction)

### Why change was targeted
Change union is the main source of **false bridges**:
- One wrong change link can connect two large components and cause cascading merges.

### Conservative constraints introduced/considered
- Require **exactly one** candidate output after filters.
- Enforce:
  - script/type match with majority input type
  - strong “newness” constraint (never seen output before)
  - optional tx-shape constraints (e.g., `n_out in {2,3}`)
  - optional amount logic (avoid “payment-looking” output)

### Effect on results
- Fewer change unions.
- More fragmentation:
  - more clusters
  - smaller medium clusters
  - (ideally) reduced giant-component size if change was the bridge driver
- Zipf curve becomes steeper and rank-1 dominance should shrink if false bridges were removed.

**Preserved:** multi-input heuristic (core), CoinJoin-ish skip, UF framework.

---

# Iteration 7 — Plotting overhaul (make distributions interpretable)

### Problem
Naïve histograms become unreadable due to:
- heavy-tailed sizes
- one giant outlier dominating x-range

### Main changes
Produce 4 focused plots:
1. All clusters — log-spaced bins, log axes
2. Excluding largest cluster — reveals “typical” entities
3. Zoom ≤ 2048 — bulk behavior
4. Zipf (rank-size) — tail shape and dominance

Improvements:
- visible bin edges (black)
- grid and labeled axes
- distinct colors per plot

### Effect on results
- Distribution became interpretable.
- Could visually confirm “giant outlier + long tail” pattern.
- Easier to compare “before vs after” change-hardening runs.

**Preserved:** same computed cluster sizes; only visualization changed.

---

# What was preserved throughout (stable design decisions)

- **Union-Find** as the clustering backbone (efficient connected components).
- **Multi-input heuristic** as the primary high-signal clustering edge.
- **CoinJoin-ish skip** to avoid collaborative transaction linkage (simple equal-output filter).
- Year-scoped analysis with lookback indexing for correctness.
- Node-level coverage flags (`multi-input`, `change`) for diagnostics.

---

# Net effect summary (high-level)

| Change | Main purpose | Effect on output |
|---|---|---|
| Fix `dir` encoding | Correct segmentation | Enabled correct grouping |
| Prevout resolution (SQLite) | Get input addresses | Made clustering possible |
| INDEX vs ANALYSIS windows | Improve resolution coverage | Higher hit-rate, fewer missing inputs |
| Disable pre-create nodes | Reduce RAM | Fewer forced singletons, stable memory |
| Chunked Parquet writer | Avoid huge lists | Stable output on big runs |
| Sanity stats | Validate correctness | Exposed giant component dominance |
| Conservative change union | Reduce false bridges | Smaller giant cluster (ideally), more clusters |
| Improved plots | Interpret heavy tails | Clearer comparisons across runs |

---

# Current state (as of latest code)

The pipeline now:
- resolves input addresses correctly via outpoint DB,
- clusters using multi-input + conservative change,
- scales to year-level datasets,
- outputs mapping in chunked Parquet,
- includes sanity metrics and interpretable plots.

Primary remaining validation focus:
- whether the largest component is “real service behavior” or residual over-linking.

# Patch Note #0.5

# Experiment Change Log (Markdown)

This summarizes **what changed**, **why it changed**, **how it affected runtime/behavior**, and **what we kept unchanged** across the iterations of your pipeline.

---

## Baseline

### Baseline behavior
- Read each parquet with Polars.
- Normalize `dir` every file (`cast + lowercase`).
- Build an SQLite outpoint index (txid, n → address, value_sats).
- For analysis-window files: resolve inputs by querying SQLite in batches using **OR-chained WHERE clauses**.
- Run:
  - multi-input union heuristic
  - conservative change heuristic
  - coinjoin-like skip
- Agg outputs per `txid` with `group_by().agg(list columns)`.
- Aggressive cleanup: frequent `gc.collect()` calls.

### Observed baseline runtime
- ~**19:45** (your measurement).

---

## Change Set A — Instrumentation and progress reporting

### A1) Added `tqdm` per-parquet progress
**Change**
- Wrapped eligible files iteration in a `tqdm` progress bar.
- Reduced printing to avoid flooding stdout.

**Rationale**
- Provide visibility into progress and phase (preload vs analysis).

**Expected/Observed impact**
- `tqdm` has **non-trivial overhead** if updated too frequently.
- Mitigation added later via `mininterval`, `miniters`, and sparse postfix updates.

**Safety**
- No changes to logic or results; instrumentation only.

**Preserved**
- All heuristics and DB behavior unchanged.

---

## Change Set B — SQLite write path optimizations (outpoints indexing)

### B1) “Commit once per file” → “Long transaction + periodic commits”
**Change**
- Replaced per-file `conn.commit()` with:
  - `conn.isolation_level = None` (manual transaction control)
  - `BEGIN;` once
  - periodic `COMMIT; BEGIN;` after `OUTPOINT_COMMIT_EVERY_ROWS` inserts

**Rationale**
- SQLite commit cost is high; batching inserts is usually the single biggest speed win.

**Impact**
- Typically **large speedup** when commit frequency was a bottleneck.
- Tradeoff: slightly more “work at risk” if the process crashes before a commit.
  - Mitigated by choosing a smaller threshold (e.g. 500k rows).

**Safety**
- Correctness preserved:
  - same inserts
  - same `INSERT OR IGNORE` semantics
- Only changes durability timing.

**Preserved**
- Schema, primary key (`txid,n`), and insert semantics.

---

## Change Set C — SQLite read path optimizations (prevout lookups)

### C1) OR-chunk lookup (baseline)
**Mechanism**
- Chunk keys into groups (e.g. 500)
- `WHERE (txid=? AND n=?) OR ...`
- `fetchall()`

**Pros**
- Very good for **small key sets**
- Avoids temp table creation / btree operations

**Cons**
- Gets slower as chunk size grows (query planning + SQL string building costs).

---

### C2) Temp-table JOIN lookup
**Change**
- Introduced a temp table `keybuf(txid,n)` and did:
  - delete keybuf
  - bulk insert chunk of keys
  - `JOIN outpoints o ON o.txid=k.txid AND o.n=k.n`

**Rationale**
- JOIN can be much faster for large lookups than enormous OR clauses.

**Impact**
- Can improve speed when key set per file is large.
- But can also degrade performance if:
  - the chunk sizes are not tuned,
  - temp table has a PK/index that creates btree maintenance overhead,
  - keybuf is re-created too often,
  - overhead dominates compared to OR for “medium” key counts.

**Safety**
- Correctness preserved as long as:
  - keys are exactly the same
  - join conditions match `(txid,n)` properly

**Preserved**
- Same lookup outputs (address,value_sats) for resolved keys.

---

### C3) Patched JOIN variant (no PK on keybuf)
**Change**
- `keybuf` created **without PRIMARY KEY**.
- Dedup done in Python before insert.
- Avoids btree maintenance costs inside temp table.

**Rationale**
- For large key inserts into temp table, a PK can be a net loss.
- Since you already dedup keys, PK is redundant.

**Impact**
- Reduced overhead of the JOIN path.
- Still can be slower than OR when key sets are small.

**Safety**
- Correctness unchanged: join result correctness does not require keybuf PK.
- Explosion risk is still guarded by outpoints PK uniqueness.

---

### C4) Hybrid lookup strategy (preserved in final)
**Change**
- `lookup_outpoints_hybrid` chooses:
  - OR strategy if `len(keys) < PREVOUT_HYBRID_THRESHOLD`
  - JOIN strategy otherwise

**Rationale**
- Best of both worlds:
  - OR wins small
  - JOIN wins big

**Impact**
- Usually improves overall runtime stability across varying file sizes.
- Threshold tuning matters:
  - If threshold too low → too many JOIN calls (overhead)
  - If too high → OR clauses become too big (SQL overhead)

**Safety**
- Correctness unchanged; only the query method changes.

**Preserved**
- Dedup semantics and returned mapping type.

---

## Change Set D — Python overhead reductions inside the per-file loop

### D1) Avoid `named=True` in `iter_rows`
**Change**
- Use `named=False` wherever possible.

**Rationale**
- Named rows allocate dict-like structures and slow Python iteration.

**Impact**
- Minor-to-moderate speedup depending on row counts.

**Safety**
- No logic change; just row unpacking style.

---

### D2) Add `buffer_size` in `iter_rows`
**Change**
- Use large buffer sizes (e.g. 200k) to reduce cross-language call overhead.

**Rationale**
- Polars → Python iteration overhead becomes a bottleneck with many small calls.

**Impact**
- Typically helpful, but large buffers can increase memory spikes.

**Safety**
- No logic change.

---

### D3) Reduce per-file `gc.collect()`
**Change**
- Move from “collect every file” to “collect every N files”.

**Rationale**
- Full GC is expensive; repeated calls can slow the loop substantially.

**Impact**
- Can reduce runtime if GC was invoked too aggressively.
- Tradeoff: higher peak memory.

**Safety**
- No correctness change.

---

## Change Set E — `dir` normalization optimization

### E1) Detect if normalization is needed once
**Change**
- On first file, inspect `dir` uniques and set `DIR_NEEDS_NORMALIZATION`.
- Only apply lowercasing if it is actually needed.

**Rationale**
- Doing `.str.to_lowercase()` on every file can be expensive.

**Impact**
- Small but real speed win if source already uses lowercased `'in'/'out'`.

**Safety**
- Preserves correctness:
  - normalization applied when needed
  - otherwise no-op

---

## Change Set F — Post-run sanity checks (validation layer)

### F1) Cluster sanity summary (`run_sanity_checks`)
**Change**
- Added a post-run check that:
  - reconstructs cluster size distribution using `Counter(node_to_entity)`
  - verifies `sum(sizes) == n_nodes`
  - prints top-k cluster sizes and percentiles
  - prints DB prevout hit rate

**Rationale**
- Detect silent mapping bugs early.

**Impact**
- Minimal runtime overhead (postprocessing only).
- Increases confidence and debuggability.

**Safety**
- Read-only checks; no mutations.

---

### F2) Prevout sanity checks
**Two modes**
1. **Within-file Polars join sanity**
   - checks integer-likeness of `prev_vout`
   - checks duplicate outpoints `(txid,n)` inside file
   - joins vin→vout within same parquet (limited scope)

2. **DB-based sanity (recommended)**
   - samples vin rows from one parquet
   - runs actual DB lookup
   - reports hit-rate and unresolved examples

**Rationale**
- Validate that your input schema and join keys behave as expected.

**Impact**
- Optional. DB sanity costs one extra lookup pass on a sample.

**Safety**
- Read-only.

---

## Net Results Summary (based on your measured runs)

### Runtime outcome
- Updated patched version: **~20:30**
- Baseline version: **~19:45**

So the patched version, in your environment, is **~45s slower**.

### Most likely reasons the “patched” variant did not win
- JOIN path overhead (temp table delete + insert + join) can dominate if many files have “medium” key counts.
- `tqdm` overhead (even reduced) + extra conditional logic adds small but steady cost.
- Larger Python-side list building (`keys`, `rows`) and extra bookkeeping.
- SQLite’s OR lookup may already be “good enough” for your key sizes, so the hybrid adds overhead without crossing the threshold where JOIN wins.

---

## What was preserved (invariants across changes)

### Heuristics preserved
- CoinJoin-like skip heuristic (same definition).
- Multi-input heuristic union logic.
- Conservative change union gates and logic:
  - dust threshold use
  - fee sanity
  - type checks
  - newness constraint logic
  - shape constraints (2–3 spendable outs)
  - complexity caps (max inputs)

### Data model preserved
- Outpoint identity: `(txid, n)` primary key.
- Outpoint values: `(address, value_sats)` stored in SQLite.
- Address→UF node creation behavior.
- Node flags semantics.

### Output preserved
- Entity clustering logic (UF + root compaction).
- Mapping file format and compression.
- Plot outputs.

---

## What was mainly changed (final state)

### Main functional additions
- **Sanity checks** (cluster and prevout) after run.

### Main performance-related modifications
- **Periodic commits** instead of per-file commits.
- **Hybrid prevout lookup** (OR for small, JOIN for large).
- Reduced Python iteration overhead (buffer sizes, no named rows).
- Reduced normalization work (conditional `dir` normalization).
- Reduced `gc.collect()` frequency.
- `tqdm` with throttled updates.

---

## Recommendation (practical)

If your goal is **pure speed**, the simplest “best bet” in your measurements is:

- Keep **periodic commits** (B1).
- Keep **OR lookup** as default, and set:
  - `PREVOUT_HYBRID_THRESHOLD` high enough that JOIN is rarely used, *unless you confirm JOIN is faster* on your workload.
- Keep low-overhead `tqdm` settings.

If your goal is **robustness + confidence**, keep the sanity checks—they’re low risk and high value.

If you want, collect:
- the distribution of `len(needed_keys)` per file (min/median/p90/max)

…and use it to choose a data-driven `PREVOUT_HYBRID_THRESHOLD` and chunk sizes.


# Patch Note #1

# Experiment Change Log — Entity Clustering Pipeline (2014)

This document summarizes the major experimental changes introduced during the recent iteration cycle (Patch A / Patch B and supporting fixes), how they affected results, and what design invariants were intentionally preserved.

---

## Baseline (Pre-patch) Snapshot

### Core pipeline behavior
- **Outpoint preload** into SQLite to resolve inputs (`prev_txid`, `prev_vout`) → (`address`, `value_sats`).
- **Union-Find** over addresses (nodes), producing entity clusters.
- Heuristics:
  - **H1 (multi-input)** under a conservative “SAFE” policy (`one_output` or `one_or_two_nonmix`).
  - **Tight change heuristic** (2-output default) with type consistency and anti-reuse constraint.
  - **Mixing-like filter** to skip likely CoinJoin / mixers.
- Diagnostics:
  - Summary stats (clusters, top entities, percentiles).
  - Prevout hit-rate sanity.

### Primary issue motivating changes
- Large/mega clusters were either:
  - **Over-prevented** (too strict caps / guards), or
  - **Over-formed** without visibility (hard to distinguish “real” mega-entities from pathological bridging).
- Lack of instrumentation around “why” large merges were being blocked (or allowed).

---

## Patch A — Mega-entity support + stronger ultra-large merge rules

### What changed
1. **Raised absolute cap (`max_merged_component_size`)**
   - Previously: smaller cap could suppress formation of very large entities.
   - Now: cap raised (example used: **10,000,000**) to allow mega-entities, but still acts as a **safety fuse**.

2. **Strengthened merge governance for ultra-large components**
   - Introduced tiered vote requirements (`ultra_change_vote_rules`) above large thresholds:
     - Example tiers:
       - ≥ 250k ⇒ 3 votes
       - ≥ 500k ⇒ 4 votes
       - ≥ 1M  ⇒ 5 votes
   - This is enforced **only on CHANGE merges** (not H1).

3. **Ratio guard refinement for CHANGE merges**
   - Ratio guard prevents merging vastly different component sizes, but:
   - Added a **small-component floor** so singleton/tiny change attachments don’t “freeze” the change heuristic:
     - Ratio guard applies only when:
       - `small >= merge_ratio_small_floor`
       - `big >= merge_ratio_big_cluster_min`

4. **Fix: guard logging state**
   - Resolved failure: `ratio_guard_samples_written` scope (`nonlocal`) issue caused runtime exception.

### Intended effect
- Allow realistic mega-entities to form **when supported**, while reducing “one-off” pathological bridging into enormous clusters.
- Prevent the pipeline from collapsing into a single massive entity due to weak control at the high end.

### Observed effect (2014 run)
- Mega cluster formation became possible under the new ceiling.
- Largest cluster increased substantially:
  - Example observed:
    - Largest cluster size grew from ~**1.5M (4.48%)** to ~**2.31M (6.91%)**.
- Vote gating did **not** dominate runtime:
  - Many merges proceeded without needing repeated confirmations.
  - Skips were primarily **ratio-guard** under CHANGE in the later run.

---

## Patch B — Constraint logging + diagnosing “repeat-edge” scarcity

Patch B was motivated by the hypothesis:
> “If we gate big merges by repeated observations of the same bridge edge, but those bridges are rarely repeated, the vote system will be idle and won’t provide meaningful confirmation.”

### What changed

1. **Constraint-event logging (vote gating)**
   - Added a log that records when a CHANGE merge is blocked because votes are not yet sufficient.
   - Key goal: determine whether constrained pairs are:
     - Mostly **unique** (rare repetition), or
     - Frequently **repeated** (vote gating will work well).

2. **Uniqueness / repetition counters**
   - Track:
     - Total gating evaluations
     - Unique constrained pairs
     - Pairs that repeat at least once
   - This directly tests whether “repeat the exact same bridge” is a viable confirmation signal.

3. **Degree-based alternative guard (bridge-y change behavior proxy)**
   - Added an additional mechanism for cases where exact-pair repetition is rare:
   - For CHANGE merges only:
     - Track how many **distinct large anchors** a given change component attempts to attach to.
     - If the change component tries to attach to too many distinct large components, block further attachments.
   - Motivation:
     - A pathological change component that becomes a “hub” bridging multiple large entities is suspicious even if exact edge repetition is rare.

### Observed effect (2014 run)
- Constraint-event log showed **n=0** in at least one run:
  - This indicates the configured vote gating thresholds were not being triggered *in that run’s conditions*, or merges were not entering the big–big regime.
- This reinforced the earlier concern:
  - If constraints are not triggered (or if pairs are unique), “repeat-edge voting” cannot be relied upon as the primary confirmation signal.
- Degree-guard becomes the more meaningful control when repetition is scarce.

---

## Confidence Proxy Output — Address-level clustering likelihood proxy

### What changed
- Added `address_confidence_YYYY.parquet` output:
  - Columns:
    - `address`
    - `entity_id`
    - `p_clustered_proxy` (proxy score ∈ (0,1))
    - Optional: `cluster_size`, `evidence_bits`

### Evidence used
- Evidence bits (address-level):
  - Multi-input evidence
  - Change output evidence
  - Change anchor evidence
- Cluster size contributes a saturating bonus (log scale).

### Intended use
- Downstream weighting / filtering:
  - Give lower weight to singleton clusters or weakly evidenced links.
  - Highlight addresses/entities with stronger heuristic support.

### What it does *not* claim
- This is **not a calibrated probability**.
- It is a structured “confidence score” proxy.

---

## Performance / Engineering Preservations

The following design constraints were intentionally preserved:

1. **Node universe preservation**
   - Maintained: `create_nodes_for_all_resolved_inputs=True`
   - Ensures UF nodes exist for all resolved input addresses, preventing distortions due to node creation policy.

2. **Determinism safeguards**
   - Sorted address lists after `group_concat(DISTINCT ...)`.
   - Sorted unique address sets before node creation.

3. **Set-based prevout resolution**
   - Preserved and relied on:
     - `vinbuf` temp table
     - SQLite join aggregation
     - Polars transport
   - This produced very high DB hit-rates and stable performance.

4. **Heuristic conservatism**
   - Kept tight change detection and mixing-like skip logic.
   - Vote + degree controls apply only to **CHANGE**, not multi-input H1.

---

## Summary of Net Impact

### Main behavioral shifts
- The pipeline now **permits mega entities** (cap raised) but includes **stronger governance** for large merges:
  - ultra vote tiers (Patch A)
  - improved ratio guard applicability (Patch A)
  - visibility into constraint mechanisms (Patch B)
  - alternative guard when repetition is scarce (Patch B)

### Practical outcomes observed
- Largest cluster size increased meaningfully in the 2014 run.
- Constraint logging indicated the earlier “repeat-edge” assumption may not hold in practice (constraints often not triggered / pairs not repeated).
- Confidence proxy was successfully generated at full address scale (~33.4M rows).

---

## What to Watch Next (Suggested Diagnostic Questions)

1. **Is the largest cluster “real” or an artifact?**
   - Inspect whether its growth is driven mostly by CHANGE merges or H1 merges.
   - If mostly CHANGE: focus on ratio/degree/vote tuning.

2. **Do constrained pairs repeat?**
   - If repetition is truly rare, edge-repetition voting will remain low-value.
   - Prefer degree-based or other “structure” signals.

3. **Does the degree guard block too aggressively?**
   - Monitor how often degree-guard triggers and whether it blocks merges that appear legitimate.

4. **Does the confidence proxy correlate with downstream truth?**
   - Validate proxy with any available labels or with manual inspection of known services/exchanges.

---

# Patch Note 2:

# Experiment Change Log (What changed, impact, what we preserved)

This run consolidates multiple experimental patches into a single-year pipeline. Below is a **markdown-style change log** describing:

- **Main changes** (behavioral + instrumentation)
- **How they affect results** (what will change in outputs/metrics, and why)
- **What was preserved** (core invariants and safety constraints)

> Note: Where “impact” refers to quantitative changes, the code now **logs and writes the necessary artifacts** to verify them (instead of asserting numbers without seeing your actual run outputs).

---

## Baseline (implicit reference point)

### Core behavior
- Build an address-level Union-Find clustering with:
  - **H1** multi-input heuristic unions (inputs co-spent → cluster).
  - **CHANGE** unions (input anchor → inferred change output) gated by rules and a change model.
- Maintain an **outpoint database** (txid, n → address, value) to resolve inputs.
- Use **merge guards** to prevent pathological growth (caps, ratio guard, voting, degree guard).

### Key limitations that motivated the patches
- Change detection “precision” could not be audited cleanly (accepted vs rejected were not sampled symmetrically).
- Hard gating on script-type properties could be brittle across years (especially early years).
- Acceptance thresholds were effectively fixed/tuned, not year-adaptive.
- No “strong-ish” ground truth to evaluate change model quality beyond proxy labels.

---

## Patch 1 — Change precision audit (accepted vs rejected)

### What changed
- Added a **balanced reservoir sample** of change-scored transactions:
  - **Accepted samples**: up to `sample_n_each`
  - **Rejected samples**: up to `sample_n_each`
- Writes a Parquet audit dataset and generates **audit plots**.

### New artifacts
- `change_audit_<year>.parquet`
- Plots:
  - `audit_plots/change_audit_pbest_<year>.png`
  - `audit_plots/change_audit_fee_frac_<year>.png`
  - `audit_plots/change_audit_feature_rates_<year>.png`

### How it affects results
- **No behavioral change** to clustering.
- **Big change to observability**:
  - You now see whether rejected cases differ meaningfully from accepted cases (e.g., fee regime, value regime, “newness”, “min output” bias).
  - You can directly inspect whether the model is accepting “obviously wrong” candidates (e.g., high fee_frac, non-new outputs).

### What was preserved
- Same clustering logic.
- Same “change model” decision pipeline (this patch is instrumentation only).

---

## Patch 2 — Online quantile calibration for acceptance threshold (`p_accept`) (and optional `p_gap`)

### What changed
- Replaced static acceptance decisioning with a **rolling quantile threshold**:
  - `p_accept_cal`: maintains a running estimate of the chosen quantile of `p_best`
  - Optionally `p_gap_cal` (currently disabled in config, but supported)
- Threshold updates are:
  - **Windowed** (ring buffer)
  - **Periodic** (`update_every`)
  - **Warmup-gated** (`warmup_min_samples`)
  - **Clamped** (`min/max`) for safety

### How it affects results
- **Year-adaptive acceptance rate**:
  - In years where scores shift (feature distributions change, address types change), acceptance no longer depends on a fixed global cutoff.
- Expected behavioral impacts:
  - **More stable “easy acceptance rate” across days** (less sensitivity to early/late-year regime shifts).
  - Potentially **different count** of change-detected txs vs baseline:
    - If the score distribution is compressed: acceptance threshold drops (within clamps) → **more change unions**.
    - If the score distribution is inflated: threshold rises → **fewer change unions**.
- What you should look at:
  - Console prints:
    - `easy_seen`, `easy_accepted`, acceptance rate
    - final `p_accept_threshold`, `p_gap_threshold`
  - The audit Parquet + plots (Patch 1) to ensure “more accepted” doesn’t mean “worse precision”.

### What was preserved
- Same scoring function (logit) and features (modulo Patch 2–3 below).
- Merge guards still apply; adaptive acceptance does **not** bypass safety.

---

## Patch 2–3 — Convert hard gates to features (remove brittle candidate dropping)

This is the main *behavioral* change to the change heuristic.

### What changed
Previously, some script-type checks could effectively *discard* candidates / transactions. Now they become **model features** so the model can down-weight them instead of hard-rejecting.

#### 1) `in_type_uniform` becomes a feature (not a hard reject)
- Before: non-uniform input script types might be rejected early.
- Now: `in_type_uniform ∈ {0,1}` is fed into the model.

#### 2) Output-type matching is no longer used to drop candidates
- Before: if output type didn’t match “input type”, a candidate might be removed.
- Now: each output has:
  - `out_type_match_in` feature
  - `type_mismatch` penalty feature

#### 3) Add script-type entropy features
- New features:
  - `type_entropy_in` (normalized entropy of input types)
  - `type_entropy_out` (entropy of output types)
  - `type_entropy_delta = H_out - H_in`

### How it affects results
- **Higher recall / coverage** in years or regimes where script-type assumptions are weaker.
- Reduced brittle failures due to:
  - heterogeneous input types
  - mixed output types
  - evolving script adoption patterns
- Risks / tradeoffs:
  - Recall increases can reduce precision if weights are not well-tuned.
  - That’s why Patch 1 (audit) and Patch A (labels) are important.

### What was preserved
- Still retains key “sanity gates”:
  - output must not be one of the input addresses
  - spendable outputs must be distinct
  - fee constraints still enforced
  - 2-output constraint (if enabled) preserved
  - coinjoin/mixing-like filter preserved (if enabled)

---

## Patch A — “First-spend linkage” labeling (outpoint-level, stronger signal)

### What changed
Added a post-hoc labeling mechanism to approximate a stronger “change vs not-change” signal.

#### Key idea
For sampled accepted txs and near-threshold rejected txs:
- Track each output **by outpoint** `(txid, n)`
- Find that outpoint’s **first spend**
- Label output as “change” (`label=1`) if the first-spend transaction co-spends with an input that belongs to the original tx’s **H1-only input cluster** (to avoid leakage from CHANGE edges)

#### Important detail (preserved correctness)
- Uses **H1-only UF (`uf_h1`)** for anchor cluster membership, not the full UF.
  - This prevents circularity where your own change edges would “confirm themselves”.

### New artifacts
- `firstspend_labels_<year>.parquet`
  - includes `out0_label_fs`, `out1_label_fs` plus first-spend txid/day when observed

### How it affects results
- **No change to clustering decisions** (labels are evaluation-only).
- Big change to model evaluation quality:
  - You can now evaluate whether `p(output)` correlates with a plausibly-causal linkage signal.
  - You get both:
    - **accepted** cases
    - **near-threshold rejected** cases (critical for tuning)

### What was preserved
- No feedback loop into model during the run. Labels are computed *after*.
- Uses within-year scan; does not require external datasets.

---

## Additional preserved invariants (still true after all changes)

### Safety / anti-explosion controls
- Absolute cap on merged component size.
- Ratio guard for CHANGE merges.
- Vote-gated merges for large components.
- Degree guard for CHANGE to prevent “hub” explosions.
- Coinjoin/mixing-like filter preserved (if enabled).

### Determinism & repeatability
- Seeded reservoir sampling.
- Seeded numpy / random.
- Deterministic quantile estimation via `np.partition` on buffer copies.

### Output data contracts
- Still produces:
  - entity mapping (address → entity_id)
  - (optional) confidence proxy parquet

---

## What *should* differ in your results (expected directional changes)

These are the expected “result deltas” you should see when comparing to the prior baseline run:

1. **Count of change-detected transactions (`n_txs_with_change_detected`)**
   - Likely changes due to:
     - adaptive `p_accept`
     - removal of hard type-gates → more eligible txs

2. **Distribution of accepted `p_best`**
   - With quantile acceptance, accepted `p_best` will track the score distribution:
     - threshold ≈ target quantile (within clamps)

3. **Composition of accepted change edges**
   - More heterogenous script-type patterns should appear among accepted edges
   - Audit plots should show whether these new accepts look “reasonable” on fee/value/newness

4. **Model quality checks (evaluation)**
   - Proxy eval (min-output) still available
   - First-spend eval (Patch A) becomes the main sanity check for calibration/ROC

---

## “Eventually preserved” decisions (what we intentionally did *not* change)

- Kept the **core clustering approach**: UF + H1 + optional CHANGE.
- Kept the **coinjoin avoidance strategy** (mixing-like filter).
- Kept the **fee sanity constraints** (absolute and fractional caps).
- Kept **2-output-only change inference** (when `change_require_2_outputs=True`).
- Kept **merge guards** as hard constraints regardless of acceptance changes.
- Kept a **simple logistic model** (interpretable, cheap, stable) rather than switching to a black-box or re-training inline.

---

## Minimal checklist for interpreting “impact” from your run

Use these in order:

1. **Console diagnostics**
   - `easy_seen`, `easy_accepted`, rate
   - final `p_accept_threshold`, `p_gap_threshold`
   - `n_txs_with_change_detected`

2. **Patch 1 audit plots**
   - Check if rejected vs accepted separations look sane:
     - fee_frac: accepted should skew lower
     - cand_new / optimal_ok: accepted should skew higher

3. **Patch A evaluation plots**
   - First-spend ROC and calibration are the best “does it work?” signals.

---
