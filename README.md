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

This metadata should give you enough context to safely use the `blocks`, `txs`, and `io` folders for downstream analytics, UTXO tracking, and fee/weight studies.
