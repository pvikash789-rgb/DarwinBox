# Data Q&A — ask questions about your spreadsheets in plain English

Upload one or more CSV or Excel files, ask a question in ordinary English, and get an
answer — with a chart when the shape of the answer calls for one.

Built for the Darwinbox Product Management take-home task.

**Every answer shows the SQL that produced it.** That is the central design decision:
a data tool that returns a confidently wrong number is worse than no tool at all,
because someone will act on it. Showing the query makes answers checkable rather than
something you have to trust.

## What it does

- **Multi-file upload** — several CSV/Excel files in one session, each becomes a table
- **Cross-file analysis** — questions spanning multiple files are answered with a JOIN;
  the app detects shared columns and tells the model which ones link the files
- **Visual insights** — a chart is chosen from the shape of the result, not by a second
  model call, so it cannot produce a nonsensical chart
- **Declines when it cannot answer** — questions the data does not support return a
  clear refusal rather than an invented number

## Running it locally

Requires **Python 3.12** and a free Groq API key from console.groq.com
(no payment method needed).

```bash
git clone https://github.com/pvikash789-rgb/DarwinBox.git
cd DarwinBox

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Add your Groq key:

```bash
mkdir -p .streamlit
printf 'GROQ_API_KEY = "gsk_your_key_here"\n' > .streamlit/secrets.toml
```

Run it:

```bash
streamlit run app.py
```

Opens at http://localhost:8501. Sample files are in `data/` — upload all four
together to see cross-file questions work.

## Try these

| Question | What it shows |
|---|---|
| How many employees are currently active? | Single value |
| What is the median base salary by department? | History-table handling + chart |
| How many approved leave days did each department take? | Cross-file join |
| Which location has the highest median salary? | Grouping and comparison |
| Which employee is most likely to resign next quarter? | Graceful refusal |

## How it works

1. **Load.** Each uploaded file becomes a DuckDB table in memory.

2. **Describe.** The app builds a text description of the data: table names and row
counts, column names and types, up to four example values per column, and which columns
appear in more than one table. The model never receives the full dataset — the
description scales with the number of *columns*, not rows, so a large file costs no
more than a small one.

3. **Detect table grain.** For each shared key, the app measures rows-per-key. A table
holding several rows per key is flagged as history, with guidance to take the latest row
per key for current values and to aggregate normally for totals across time.

This matters. The sample `salaries.csv` holds one row per employee *per year*. A naive
"average salary by department" averages three years of history and returns a number that
is wrong and entirely plausible. With the flag present, the model produces a
`ROW_NUMBER() OVER (PARTITION BY employee_id ORDER BY effective_date DESC)` with
`WHERE rn = 1`, so each employee contributes only their current salary.

The rule is generic, not hardcoded to these files — it works on any upload.

4. **Generate and run.** Question plus description go to the model, which returns SQL or
`CANNOT_ANSWER`. Only SELECT queries are executed. If the query errors, the error and
the failed SQL go back to the model for one repair attempt; the retry is shown, not
hidden.

5. **Display.** Result shape picks the view — one value renders as a metric, a label
column plus a numeric column renders as a chart, anything else renders as a table. The
table is always shown alongside the chart.

## Tech stack

| Layer | Choice |
|---|---|
| UI | Streamlit |
| Query engine | DuckDB (in-memory) |
| File reading | pandas, openpyxl |
| Model | openai/gpt-oss-120b via Groq free tier |
| HTTP | requests |

**Why SQL rather than generated Python:** cross-file analysis becomes a JOIN instead of
bespoke merge logic; SQL is a narrower output space so smaller models get it right more
often; execution can be restricted to reads; and a query is showable to the user in a
way a block of pandas is not.

**Why requests instead of the official groq SDK:** the SDK depends on pydantic-core, a
Rust-compiled package that would not install on the macOS 12 Intel machine this was
built on. Groq's API is plain HTTPS, so requests does the same job with no compiled
dependencies — which also makes deployment more reliable.

**Model settings:** temperature=0 so the same question gives the same query, and
reasoning_effort="low" because gpt-oss-120b is a reasoning model whose thinking tokens
count against the output budget — writing SQL does not need deliberation.

## Known limitations

- **Each question is independent.** No memory of the previous answer, so follow-ups like
  "and what about Sales?" do not work.
- **Sample values include real data.** Up to four values per column are sent to the
  model. This is what makes it reliable about matching values like Active vs active,
  but personal columns would need masking in a real deployment.
- **Sample values are not representative.** They are whichever rows the database returns
  first, not the most frequent or a random sample.
- **Dates are text.** Files load with dates as strings; the model is told the format and
  handles it, but mixed or malformed date formats are not yet described.
- **Charts use a simple rule.** A year column returned as a number gets a bar chart
  rather than a line chart. Readable, but not always ideal.

## Repository

- app.py — the whole application
- requirements.txt — dependencies
- data/ — four sample HR files (CSV and one Excel)
- .streamlit/ — secrets, not committed
