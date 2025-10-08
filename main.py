from pathlib import Path
import os, glob, json, requests
import polars as pl
import matplotlib.pyplot as plt

# ---- paths ----
PARQUET_DIR = Path("/home/vatereal/btc-node/parquet")  # <-- your node's parquet folder
OUTPUT_DIR  = Path("/home/vatereal/Projects/BlockChain/outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

assert PARQUET_DIR.exists(), f"Parquet path not found: {PARQUET_DIR}"
print("Using PARQUET_DIR =", PARQUET_DIR)

# ---- RPC to your dockerized node ----
RPC_URL  = "http://127.0.0.1:8332"
RPC_USER = "research"
RPC_PASS = "researchpass"

def rpc(method, params=None):
    r = requests.post(
        RPC_URL,
        json={"jsonrpc":"1.0","id":"nb","method":method,"params":params or []},
        auth=(RPC_USER, RPC_PASS),
        timeout=120
    )
    r.raise_for_status()
    out = r.json()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]

# quick sanity checks
info = rpc("getblockchaininfo")
print("RPC OK. chain:", info["chain"], "| node height:", info["blocks"])
try:
    print("txindex status:", rpc("getindexinfo").get("txindex"))
except Exception as e:
    print("getindexinfo not available (older nodes) or other issue:", e)

# Polars display prefs
pl.Config.set_tbl_rows(20)
pl.Config.set_fmt_str_lengths(80)
