import requests
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


# ---------------------------------------------------------------- SQL generation

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """You write DuckDB SQL. You are given a database schema and a question.

Rules:
- Reply with ONE SQL query and nothing else. No explanation, no markdown fences.
- Use only the tables and columns given in the schema.
- Date columns are stored as text in YYYY-MM-DD format. Compare as text or CAST to DATE.
- Read the "Important notes" section carefully and follow it exactly.
- If the question cannot be answered from this schema, reply with exactly: CANNOT_ANSWER
- Give result columns short, readable aliases."""


def generate_sql(question: str, schema: str) -> str:
    """Ask the model for a SQL query. Swap this one function to change LLM backend."""
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {st.secrets['GROQ_API_KEY']}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{schema}\n\nQuestion: {question}\n\nSQL:"},
            ],
            "max_tokens": 1500,
            "reasoning_effort": "low",
            "temperature": 0,
        },
        timeout=60,
    )
    response.raise_for_status()
    sql = response.json()["choices"][0]["message"]["content"].strip()

    # models sometimes wrap output in markdown fences despite instructions
    if sql.startswith("```"):
        sql = sql.split("```")[1]
        if sql.lower().startswith("sql"):
            sql = sql[3:]
    return sql.strip()


# ---------------------------------------------------------------- result display

def looks_like_date(series) -> bool:
    """True if a text column parses cleanly as dates."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if series.dtype == "object":
        try:
            parsed = pd.to_datetime(series, errors="coerce", format="mixed")
            return parsed.notna().mean() > 0.9
        except Exception:
            return False
    return False


def render_result(df):
    """Pick a display based on the shape of the result. No model call involved."""
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    other = [c for c in df.columns if c not in numeric]

    # a single value -> show it big
    if df.shape == (1, 1):
        value = df.iloc[0, 0]
        shown = f"{value:,}" if isinstance(value, (int, float)) else str(value)
        st.metric(df.columns[0].replace("_", " ").title(), shown)
        return

    # one label column + one number column -> chart it
    if len(numeric) == 1 and len(other) == 1 and 1 < len(df) <= 50:
        label, value = other[0], numeric[0]
        chart_data = df.set_index(label)[value]

        if looks_like_date(df[label]):
            st.line_chart(chart_data)
        else:
            st.bar_chart(chart_data)

        st.dataframe(df, width="stretch")
        return

    # anything else -> just the table
    st.dataframe(df, width="stretch")



# ---------------------------------------------------------------- execution

def run_sql(con, sql):
    """Execute SQL and return a dataframe. Raises on failure."""
    return con.execute(sql).df()


def fix_sql(question: str, schema: str, bad_sql: str, error: str) -> str:
    """Show the model its own failed query plus the database error, and ask for a fix."""
    repair_request = (
        f"{schema}\n\n"
        f"Question: {question}\n\n"
        f"This SQL failed:\n{bad_sql}\n\n"
        f"The database returned this error:\n{error}\n\n"
        f"Return a corrected SQL query. SQL only, nothing else."
    )
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {st.secrets['GROQ_API_KEY']}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": repair_request},
            ],
            "max_tokens": 1500,
            "reasoning_effort": "low",
            "temperature": 0,
        },
        timeout=60,
    )
    response.raise_for_status()
    sql = response.json()["choices"][0]["message"]["content"].strip()
    if sql.startswith("```"):
        sql = sql.split("```")[1]
        if sql.lower().startswith("sql"):
            sql = sql[3:]
    return sql.strip()


st.divider()
question = st.text_input("Ask a question about your data")

if question:
    with st.spinner("Writing SQL..."):
        try:
            sql = generate_sql(question, schema)
        except Exception as e:
            st.error(f"Could not reach the model — {e}")
            st.stop()

    if sql == "CANNOT_ANSWER":
        st.warning("I can't answer that from the uploaded data.")
        st.stop()

    result = None
    try:
        result = run_sql(con, sql)
    except Exception as first_error:
        st.info("First query failed — asking the model to correct it.")
        with st.expander("Failed query"):
            st.code(sql, language="sql")
            st.caption(str(first_error))

        try:
            sql = fix_sql(question, schema, sql, str(first_error))
            result = run_sql(con, sql)
        except Exception as second_error:
            st.error("Couldn't produce a working query for that question.")
            st.code(sql, language="sql")
            st.caption(str(second_error))
            st.stop()

    if result is None or result.empty:
        st.warning("That query ran, but returned no rows.")
        st.stop()

    render_result(result)

    with st.expander("Show the SQL"):
        st.code(sql, language="sql")
