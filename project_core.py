# ============================================================
# IMPORTS
# ============================================================
import pandas as pd
import duckdb
from pathlib import Path


# ============================================================
# PRINT OPTIONS
# ============================================================
def print_df():
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    pd.set_option("display.float_format", "{:,.3f}".format)


def title(title, width=112):
    print("\n", "=" * width, title.center(width), "=" * width, sep="\n")


# ============================================================
# PATHS FORMATTING
# ============================================================
def src(p) -> str:
    paths = [Path(p)] if isinstance(p, (str, Path)) else [Path(x) for x in p]
    globs = [
        (
            x.as_posix()
            if x.suffix == ".parquet"
            else f"{x.as_posix()}/**/*.parquet"
        )
        for x in paths
    ]
    return f"read_parquet({globs}, hive_partitioning=1, union_by_name=1)"


# ============================================================
# SCHEMA
# ============================================================
def schema(path) -> dict[str, str]:
    rel = duckdb.sql(f"SELECT * FROM {src(path)} LIMIT 0")
    return dict(zip(rel.columns, map(str, rel.types)))
  
