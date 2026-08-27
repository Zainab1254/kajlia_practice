import streamlit as st
import sqlite3
import pandas as pd
import requests
import re

FORBIDDEN_SQL_WORDS = ["insert", "update", "delete", "drop", "alter", "create", "replace"]
FORBIDDEN_SQL_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_SQL_WORDS) + r")\b")

def check_sql(sql):
    """Query mehfooz hai ya nahi. Poora lafz dekhta hai, kisi lafz ke andar chhupa tukra nahi."""
    s = sql.strip().lower()
    if not s.startswith("select"):
        return False, "not_select"
    if FORBIDDEN_SQL_RE.search(s):
        return False, "forbidden"
    return True, ""

KEY = st.secrets["ANTHROPIC_API_KEY"]

conn = sqlite3.connect("kajlia_demo.db")
payments = pd.read_sql("SELECT * FROM payments", conn)
flats = pd.read_sql("SELECT * FROM flats", conn)


cleared = payments[~payments["cheque_status"].isin(["pending", "returned"])]
total_recovery = cleared["amount"].sum()

flat_sale = flats["sale"].sum()
outstanding = flat_sale - total_recovery

pending = payments.loc[payments["cheque_status"] == "pending", "amount"].sum()
unsecured = outstanding - pending
total_recovery_cr = total_recovery / 10_000_000
outstanding_cr = outstanding / 10_000_000
unsecured_cr = unsecured / 10_000_000
pending_cr = pending / 10_000_000
st.title("Kajlia Recovery")

col1, col2, col3 = st.columns(3)
col1.metric("Recovered", f"{total_recovery_cr:.2f} cr")
col2.metric("Outstanding", f"{outstanding_cr:.1f} cr")
col3.metric("Unsecured", f"{unsecured_cr:.1f} cr")
def llm_call(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 800,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    return r.json()["content"][0]["text"]

def get_schema():
    lines = []
    for t in ["payments", "flats"]:
        cols = pd.read_sql(f"PRAGMA table_info({t})", conn)
        col_list = ", ".join(f"{r['name']} ({r['type']})" for _, r in cols.iterrows())
        lines.append(f"Table {t}: {col_list}")

    lines.append("Note: payments.date is stored as TEXT in YYYY-MM-DD format.")
    lines.append("Note: cheque_status has values: n/a, pending, realized_bank, realized_cash, returned. A payment counts as cleared if cheque_status is NOT 'pending' and NOT 'returned'.")
    lines.append("Note: ignore the is_returned column, it is unused and always 0.")
    lines.append("Business rules:")
    lines.append("- total_recovery = SUM(payments.amount) where cheque_status is NOT 'pending' and NOT 'returned'")
    lines.append("- outstanding = SUM(flats.sale) - total_recovery")
    lines.append("- pending = SUM(payments.amount) where cheque_status = 'pending'")
    lines.append("- unsecured = outstanding - pending")
    lines.append("IMPORTANT: never JOIN flats and payments to sum both tables. A flat has many payments, so joining multiplies flats.sale. Use separate subqueries instead, e.g. (SELECT SUM(sale) FROM flats) - (SELECT SUM(amount) FROM payments WHERE ...)")

    return "\n".join(lines)

tools = [
    {
        "name": "run_sql",
        "description": "Run a read-only SQL query against the Kajlia SQLite database and get the result back as a table. Use this whenever you need actual numbers. You may call it more than once if a question needs several steps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single SQLite SELECT query. Never INSERT, UPDATE, DELETE, DROP, or ALTER."
                }
            },
            "required": ["query"]
        }
    }
]
def llm_call_full(messages, tools=None):
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 2000,
        "temperature": 0,
        "messages": messages
    }
    if tools:
        body["tools"] = tools

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json=body
    )
    return r.json()

st.divider()
st.subheader("Agent")

agent_q = st.text_input("Agent se poochhein")

if agent_q:
    messages = [{"role": "user", "content": f"""You are a data analyst for a real estate recovery database.

{get_schema()}

Use the run_sql tool to get real numbers. Never guess a number.
You may call the tool several times if the question needs multiple steps.
Amounts in the database are in rupees. Convert to crore only in your final answer by stating the rupee figure divided by 10,000,000 — but do not do arithmetic beyond that conversion.
No recommendations, no advice. State only what the data shows.
Before comparing time periods, first check the actual date range of the data (MIN and MAX of payments.date). Never compare a period that falls outside the available data — say so instead.
Reply in the same script the question was written in. If the question is in Roman Urdu (Urdu written in English letters), reply in Roman Urdu — never in Devanagari or Arabic script.
        

Question: {agent_q}"""}]

    for step in range(6):
        response = llm_call_full(messages, tools)

        for block in response["content"]:
            if block["type"] == "text":
                st.write(block["text"])

        if response["stop_reason"] != "tool_use":
            break

        messages.append({"role": "assistant", "content": response["content"]})

        tool_results = []
        for block in response["content"]:
            if block["type"] == "tool_use":
                q = block["input"]["query"]

                with st.expander(f"Step {step+1}: SQL"):
                    st.code(q, language="sql")

                ok, reason = check_sql(q)

                if not ok and reason == "not_select":
                    out = "ERROR: only SELECT queries are allowed."
                elif not ok:
                    out = "ERROR: this query is not allowed. Use a plain SELECT."
                else:
                    try:
                        df = pd.read_sql(q, conn)
                        out = df.to_string()
                        with st.expander(f"Step {step+1}: result"):
                            st.write(df)
                    except Exception as e:
                        out = f"ERROR: {e}"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": out
                })

        messages.append({"role": "user", "content": tool_results})
         

question = st.text_input("Apna sawal likhein")

if question:
    sql_prompt = f"""You are a SQLite expert. Write ONE SQL query to answer the question.

{get_schema()}

Rules:
- Return ONLY the SQL query. No explanation, no markdown, no backticks.
- Use SELECT only. Never use INSERT, UPDATE, DELETE, DROP, or ALTER.
- Amounts are in rupees, not crore.

Question: {question}"""

    sql = llm_call(sql_prompt).strip()
    with st.expander("SQL dekhein"):
        st.code(sql, language="sql")
    ok, reason = check_sql(sql)

    if not ok and reason == "not_select":
        st.error("Sirf SELECT queries chal sakti hain.")
    elif not ok:
        st.error("Ye query mehfooz nahi hai.")
    else:
        try:
            result = pd.read_sql(sql, conn)
        except Exception as e:
            st.error("Ye query chal nahi saki. Sawal thora alag tareeqe se likh kar dekhein.")
            with st.expander("Technical details"):
                st.write(str(e))
            st.stop()

        with st.expander("Raw result"):
            st.write(result)

        result_cr = result.copy()
        money_words = ["amount", "sale", "value", "recovery", "outstanding", "unsecured"]
        for c in result_cr.columns:
            if result_cr[c].dtype.kind in "if" and any(w in c.lower() for w in money_words):
                result_cr[c] = (result_cr[c] / 10_000_000).round(2)

        answer_prompt = f"""Answer the question in one or two sentences based only on this result.

Question: {question}
SQL result: {result_cr.to_string()}

Rules:
- Columns whose name contains amount, sale, value, recovery, outstanding, or unsecured are in crore.
- All other numbers are plain counts, not crore.
- Do NOT calculate anything yourself.
- Numbers are already in crore. Use them exactly as given.
- No recommendations, no advice.
- If the result is empty, say no data was found."""

        st.write(llm_call(answer_prompt))




