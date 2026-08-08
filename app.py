import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Data Q&A", layout="wide")
st.title("Data Q&A")
st.caption("Upload CSV or Excel files, then ask questions about them in plain English.")

MAX_SAMPLE_VALUES = 4


@st.cache_resource
def get_connection():
    return duckdb.connect(database=":memory:")


def to_table_name(filename: str) -> str:
    base = filename.rsplit(".", 1)[0].lower()
    safe = "".join(c if c.isalnum() else "_" for c in base)
    return safe.strip("_") or "uploaded_table"


def load_file(file, con):
    if file.name.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    name = to_table_name(file.name)
    con.register("_staging", df)
    con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _staging')
    con.unregister("_staging")
    return name, df


def columns_of(con, table):
    return [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]


def describe_table(con, table):
    """One table's name, size, columns, types and real sample values."""
    n_rows = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    lines = [f"Table: {table} ({n_rows} rows)"]

    for col, dtype in columns_of(con, table):
        try:
            vals = con.execute(
                f'SELECT DISTINCT "{col}" FROM "{table}" '
                f'WHERE "{col}" IS NOT NULL LIMIT {MAX_SAMPLE_VALUES}'
            ).fetchall()
            samples = ", ".join(str(v[0])[:25] for v in vals)
        except Exception:
            samples = ""
        lines.append(f"  - {col} ({dtype}) e.g. {samples}")

    return "\n".join(lines)


def find_join_keys(con, tables):
    """Columns that appear in more than one table are likely join keys."""
    seen = {}
    for t in tables:
        for col, _ in columns_of(con, t):
            seen.setdefault(col, []).append(t)
    return {c: ts for c, ts in seen.items() if len(ts) > 1}


def find_grain_warnings(con, tables, join_keys):
    """Flag tables holding several rows per key — averaging them naively gives wrong answers."""
    warnings = []
    for key, key_tables in join_keys.items():
        for t in key_tables:
            try:
                ratio = con.execute(
                    f'SELECT COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT "{key}"), 0) FROM "{t}"'
                ).fetchone()[0]
            except Exception:
                continue
            if ratio and ratio > 1.05:
                warnings.append(
                    f"  - {t} holds ~{ratio:.1f} rows per {key}. It is a history table, "
                    f"not one row per {key}. To get a current value, pick the latest row "
                    f"per {key} (e.g. with a window function) rather than averaging all rows."
                )
    return warnings


def build_schema_description(con, tables):
    parts = [describe_table(con, t) for t in tables]
    schema = "\n\n".join(parts)

    join_keys = find_join_keys(con, tables)
    if join_keys:
        schema += "\n\nRelationships (join on these columns):"
        for col, ts in join_keys.items():
            schema += f"\n  - {col} links: {', '.join(ts)}"

    warnings = find_grain_warnings(con, tables, join_keys)
    if warnings:
        schema += "\n\nImportant notes:\n" + "\n".join(warnings)

    return schema


con = get_connection()

uploaded_files = st.file_uploader(
    "Upload your data files",
    type=["csv", "xlsx", "xls"],
    accept_multiple_files=True,
)

if not uploaded_files:
    st.info("Upload one or more files to begin.")
    st.stop()

tables = {}
for file in uploaded_files:
    try:
        name, df = load_file(file, con)
        tables[name] = df
    except Exception as e:
        st.error(f"Could not read **{file.name}** — {e}")

if not tables:
    st.stop()

st.success(f"Loaded {len(tables)} table(s): {', '.join(tables)}")

for name, df in tables.items():
    with st.expander(f"{name} — {len(df):,} rows × {len(df.columns)} columns"):
        st.dataframe(df.head(10), width="stretch")

schema = build_schema_description(con, list(tables))

with st.expander("Schema description sent to the model", expanded=True):
    st.code(schema, language="text")
    st.caption(f"{len(schema):,} characters (~{len(schema) // 4:,} tokens)")
