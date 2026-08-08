import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Data Q&A", layout="wide")
st.title("Data Q&A")
st.caption("Upload CSV or Excel files, then ask questions about them in plain English.")


@st.cache_resource
def get_connection():
    """One in-memory DuckDB database, kept alive across page reruns."""
    return duckdb.connect(database=":memory:")


def to_table_name(filename: str) -> str:
    """employees.csv -> employees   |   Q1 Sales!.xlsx -> q1_sales"""
    base = filename.rsplit(".", 1)[0].lower()
    safe = "".join(c if c.isalnum() else "_" for c in base)
    return safe.strip("_") or "uploaded_table"


def load_file(file, con):
    """Read one uploaded file into a DuckDB table. Returns (table_name, dataframe)."""
    if file.name.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    name = to_table_name(file.name)
    con.register("_staging", df)
    con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _staging')
    con.unregister("_staging")
    return name, df


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

if tables:
    st.success(f"Loaded {len(tables)} table(s): {', '.join(tables)}")

    for name, df in tables.items():
        with st.expander(f"{name} — {len(df):,} rows × {len(df.columns)} columns"):
            st.dataframe(df.head(10), use_container_width=True)
