import streamlit as st
import sqlite3
import pandas as pd
import requests
import re
import datetime

FORBIDDEN_SQL_WORDS = ["insert", "update", "delete", "drop", "alter", "create", "replace"]
FORBIDDEN_SQL_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_SQL_WORDS) + r")\b")

MONEY_COL_RE = re.compile(r"\b(amount|sale|principal|dr|cr|rate|received|refund|forfeit|discount)\b")

def needs_rupees_alias(sql):
    """SQL paison ka column le rahi hai magar kisi output column ka naam _rupees par khatam nahi hota."""
    s = sql.lower()
    return MONEY_COL_RE.search(s) is not None and "_rupees" not in s

def _num(v):
    """Number ko parhne layak banata hai: comma lagata hai, bekaar sifar hatata hai."""
    if pd.isna(v):
        return ""
    s = f"{v:,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return s

def to_readable(df):
    """UNIT KA FAISLA SIRF YAHAN HOTA HAI. Aur kahin nahi.

    Har _rupees column ko ek hi unit mein badalta hai (poore column ki sab se bari
    raqam dekh kar, har row ki alag nahi), aur naam ke aakhir mein unit likh deta hai:
      1 crore ya us se zyada -> _crore
      1 lakh se 1 crore      -> _lakh
      1 lakh se kam          -> _rupees (waise hi, comma ke saath)
    """
    out = df.copy()
    renames = {}
    for c in list(out.columns):
        if not c.lower().endswith("_rupees") or out[c].dtype.kind not in "if":
            continue
        biggest = out[c].abs().max()
        if pd.isna(biggest):
            continue
        if biggest >= 10_000_000:
            div, suffix = 10_000_000, "_crore"
        elif biggest >= 100_000:
            div, suffix = 100_000, "_lakh"
        else:
            div, suffix = 1, "_rupees"
        out[c] = out[c].map(lambda v: _num(v / div))
        renames[c] = c[:-len("_rupees")] + suffix
    return out.rename(columns=renames)

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

# ---------------------------------------------------------------- Khamosh flats
def pretty_columns(df):
    """Sirf label khoobsurat banata hai. Unit ka faisla to_readable() karta hai, yahan nahi."""
    LABEL = {
        "flat_num": "Flat number", "customer": "Customer", "last_payment": "Aakhri payment",
        "days_silent": "Kitne din khamosh", "payment_count": "Kitni payments",
        "baqi": "Baqi", "pending": "Pending cheques",
    }
    out = {}
    for c in df.columns:
        base, unit = c, None
        for suffix in ("_crore", "_lakh", "_rupees"):
            if c.endswith(suffix):
                base, unit = c[:-len(suffix)], suffix[1:]
                break
        name = LABEL.get(base, base)
        out[c] = f"{name} ({unit})" if unit else name
    return df.rename(columns=out)


def khamosh_flats():
    """Kaunsa flat chal raha hai aur kaunsa ruk gaya. Sab hisaab yahan Python karta hai."""
    today = pd.Timestamp(datetime.date.today())
    cleared_p = payments[~payments["cheque_status"].isin(["pending", "returned"])]

    rec = cleared_p.groupby("flat_id")["amount"].sum()
    last = cleared_p.groupby("flat_id")["date"].max()
    cnt = cleared_p.groupby("flat_id")["id"].count()
    pend = payments[payments["cheque_status"] == "pending"].groupby("flat_id")["amount"].sum()

    s = flats[["id", "num", "customer", "sale"]].copy()
    s["rec"] = s["id"].map(rec).fillna(0.0)
    s["baqi_rupees"] = s["sale"] - s["rec"]
    s["last"] = s["id"].map(last)
    s["payment_count"] = s["id"].map(cnt).fillna(0).astype(int)
    s["pending_rupees"] = s["id"].map(pend).fillna(0.0)
    s["days_silent"] = (today - pd.to_datetime(s["last"])).dt.days
    return s


def khamosh_table(rows, never=False):
    d = pd.DataFrame({
        "flat_num": rows["num"],
        "customer": rows["customer"],
        "baqi_rupees": rows["baqi_rupees"],
        "last_payment": "Kabhi nahi" if never else rows["last"],
        "days_silent": "—" if never else rows["days_silent"].astype(int).astype(str),
        "payment_count": rows["payment_count"],
        "pending_rupees": rows["pending_rupees"],
    })
    return pretty_columns(to_readable(d))


with st.expander("Khamosh flats — wasooli ruki hui", expanded=False):
    _s = khamosh_flats()
    _zero = _s[_s["rec"] == 0].sort_values("baqi_rupees", ascending=False)
    _alive = _s[_s["rec"] > 0].sort_values("days_silent", ascending=False)
    _silent = _alive[_alive["days_silent"] > 60]
    _stuck = _silent["baqi_rupees"].sum() + _zero["baqi_rupees"].sum()

    st.warning(
        f"**{len(_silent) + len(_zero)} flats** 2 mahine (60 din) se zyada khamosh hain — "
        f"in mein **{_stuck / 10_000_000:,.2f} crore** phansi hui hai. "
        f"(Hisaab {datetime.date.today():%d %B %Y} tak)"
    )

    st.markdown(f"**Ek rupya bhi wasool nahi hua — {len(_zero)} flats**")
    st.dataframe(khamosh_table(_zero, never=True), hide_index=True)

    st.markdown(
        f"**Wasooli shuru hui phir ruk gayi — {len(_alive)} flats** "
        "(sab se zyada arse se khamosh sab se upar)"
    )
    st.dataframe(khamosh_table(_alive), hide_index=True)

    st.caption(
        "'Aakhri payment' se murad wo aakhri payment hai jo CLEAR ho chuki ho — pending "
        "cheque ko wasooli nahi mana jata. Isi liye upar wale flats ki koi aakhri payment "
        "nahi hai, halanke un par cheques pending ho sakte hain."
    )

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

Column naming — this is required:
- Every column that holds a rupee amount MUST be aliased with a name ending in _rupees.
  Example: SELECT SUM(amount) AS total_recovery_rupees FROM payments
- Never use the _rupees suffix for counts, dates, names, or IDs.

Units — the numbers are converted for you before you see them:
- The unit is written at the end of each column name (_crore, _lakh, _rupees) and that
  IS the unit of that number.
- Use every number exactly as given. Never divide, multiply, or calculate anything yourself.
- State the unit that the column name says (crore, lakh, or rupees).

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
                elif needs_rupees_alias(q):
                    out = ("ERROR: this query returns a rupee amount but no output column "
                           "name ends in _rupees. Rewrite it, changing only the column "
                           "aliases so every rupee column ends in _rupees.")
                else:
                    try:
                        df = to_readable(pd.read_sql(q, conn))
                        out = df.to_string(index=False)
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
