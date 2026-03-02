import streamlit as st
import pandas as pd
import random
import uuid
from datetime import datetime
import gspread

from google.oauth2.service_account import Credentials
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Europe/Belgrade")
# ---------------- CONFIG ----------------
SHEET_ID = "1zDAsJD4uxw01eItCZ6Jeu6PjcQLQJHHkPczFFFnID7A"
QUESTIONS_TAB = "questions"
RESULTS_TAB = "results"  # must contain at least these headers: email,score,total,percentage,date
ALLOWED_DOMAIN = "@drivesales.com"
QUESTIONS_PER_QUIZ = 20
MAX_ATTEMPTS_PER_DAY = 3
ADMIN_EMAIL = "dejan.g@drivesales.com"
ADMIN_PASSWORD = "2026"  # better in st.secrets, but using what you requested

REQUIRED_RESULTS_COLS = ["email", "score", "total", "percentage", "date"]

# ---------------- GOOGLE AUTH ----------------
creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)
client = gspread.authorize(creds)

def get_worksheet(sheet_id, tab_name):
    try:
        return client.open_by_key(sheet_id).worksheet(tab_name)
    except gspread.exceptions.APIError as e:
        st.error(f"Google Sheets API error: {e}")
        st.stop()

results_sheet = get_worksheet(SHEET_ID, RESULTS_TAB)

# ---------------- HELPERS ----------------
def format_username(email: str) -> str:
    local = email.split("@")[0]
    if "." in local:
        parts = local.split(".")
        name = parts[0].capitalize()
        initial = parts[1][0].upper() + "."
        return f"{name} {initial}"
    return local.capitalize()

def clear_results_sheet_keep_header(ws, required_cols):
    """
    Clears all data rows but keeps header row.
    Safe for 1-row-per-quiz schema: email, score, total, percentage, date
    """
    try:
        values = ws.get_all_values()
        if not values:
            # Sheet is empty -> write header
            ws.update("A1", [required_cols])
            return

        # Ensure header exists and is correct-ish; if row1 is empty, write header
        header = [h.strip() for h in values[0]] if values else []
        if not any(header):
            ws.update("A1", [required_cols])

        # If there are data rows, clear A2:E (enough for your 5 cols)
        if len(values) > 1:
            ws.batch_clear(["A2:E"])
    except gspread.exceptions.APIError as e:
        st.error(f"Failed to clear results: {e}")
        st.stop()

def ensure_results_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Make results_df safe:
    - remove duplicate column labels
    - keep only REQUIRED_RESULTS_COLS (create missing ones)
    - coerce types
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_RESULTS_COLS)

    # Drop duplicate column names (keep first)
    if not df.columns.is_unique:
        df = df.loc[:, ~df.columns.duplicated()].copy()

    # Normalize headers (strip)
    df.columns = [str(c).strip() for c in df.columns]

    # Keep required columns; add missing
    for c in REQUIRED_RESULTS_COLS:
        if c not in df.columns:
            df[c] = pd.NA

    df = df[REQUIRED_RESULTS_COLS].copy()

    # Convert types
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce")
    df["percentage"] = pd.to_numeric(df["percentage"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df

def get_attempts_today(results_df: pd.DataFrame, user_email: str, today_str: str) -> int:
    if results_df.empty:
        return 0
    if "email" not in results_df.columns or "date" not in results_df.columns:
        return 0

    today_rows = results_df[
        (results_df["email"] == user_email) &
        (results_df["date"].astype(str).str[:10] == today_str)
    ]
    return len(today_rows)

def calculate_streak(results_df: pd.DataFrame, user_email: str, threshold=70) -> int:
    if results_df.empty:
        return 0

    user_data = results_df[results_df["email"] == user_email].copy()
    if user_data.empty:
        return 0

    user_data = user_data.dropna(subset=["date", "percentage"])
    user_data = user_data[user_data["percentage"] >= threshold].copy()

    if user_data.empty:
        return 0

    user_data["day"] = user_data["date"].dt.date
    unique_days = list(dict.fromkeys(user_data.sort_values("day", ascending=False)["day"].tolist()))

    streak = 0
    cursor = datetime.now().date()

    for d in unique_days:
        if d == cursor:
            streak += 1
            cursor = cursor - pd.Timedelta(days=1)
        elif d == cursor - pd.Timedelta(days=1):
            streak += 1
            cursor = cursor - pd.Timedelta(days=1)
        else:
            break

    return streak

# ---------------- LOAD FUNCTIONS ----------------
@st.cache_data(ttl=300)
def load_questions(limit=90) -> pd.DataFrame:
    sheet = get_worksheet(SHEET_ID, QUESTIONS_TAB)
    all_rows = sheet.get_all_records()

    filtered_rows = []
    for row in all_rows[:limit]:
        if not row.get("question"):
            continue

        options = [row.get(opt) for opt in ["A", "B", "C", "D"]]
        if any(str(opt).strip() for opt in options):
            for opt in ["A", "B", "C", "D"]:
                row[opt] = str(row.get(opt, "")).strip()
            filtered_rows.append(row)

    return pd.DataFrame(filtered_rows)

@st.cache_data(ttl=15)  # longer to reduce read quota
def load_results_raw() -> pd.DataFrame:
    sheet = get_worksheet(SHEET_ID, RESULTS_TAB)
    values = sheet.get_all_values()

    if not values or len(values) < 2:
        return pd.DataFrame(columns=REQUIRED_RESULTS_COLS)

    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    rows = [r for r in rows if any(str(cell).strip() for cell in r)]

    # Pad rows to header length
    max_cols = len(headers)
    padded = [r + [""] * (max_cols - len(r)) if len(r) < max_cols else r[:max_cols] for r in rows]

    df = pd.DataFrame(padded, columns=headers)
    return df

def refresh_results_data():
    load_results_raw.clear()
    st.session_state.results_data = ensure_results_schema(load_results_raw())

# ---------------- SESSION STATE ----------------
if "quiz_started" not in st.session_state:
    st.session_state.quiz_started = False
if "quiz_finished" not in st.session_state:
    st.session_state.quiz_finished = False

# ---------------- TITLE ----------------
st.title("DriveSales Daily Quiz")

questions_data = load_questions()

# ---------------- LOGIN ----------------
email = st.text_input("Enter company email")
if not email:
    st.stop()

if not email.endswith(ALLOWED_DOMAIN):
    st.error("Only @drivesales.com emails allowed")
    st.stop()

now = datetime.now(TZ)
today = now.strftime("%Y-%m-%d")

# ---------------- LOAD RESULTS ONCE (SESSION) ----------------
if "results_data" not in st.session_state:
    st.session_state.results_data = ensure_results_schema(load_results_raw())

# Optional refresh requested by actions
if st.session_state.get("needs_refresh", False):
    refresh_results_data()
    st.session_state.needs_refresh = False

results_data = st.session_state.results_data

# Show persisted message
if st.session_state.get("last_save_msg"):
    st.success(st.session_state.last_save_msg)
    st.session_state.last_save_msg = None

# ---------------- CHECK ATTEMPTS ----------------
attempts_today = get_attempts_today(results_data, email, today)
blocked_today = attempts_today >= MAX_ATTEMPTS_PER_DAY

if blocked_today:
    st.error("You reached max attempts today. Come back tomorrow!")
else:
    st.info(f"Attempts today: {attempts_today}/{MAX_ATTEMPTS_PER_DAY}")

# ---------------- START QUIZ ----------------
if (not blocked_today) and (not st.session_state.quiz_started) and (not st.session_state.quiz_finished):
    if st.button("Start Quiz"):
        st.session_state.quiz_id = str(uuid.uuid4())
        st.session_state.quiz = questions_data.sample(
            min(QUESTIONS_PER_QUIZ, len(questions_data))
        ).reset_index(drop=True)
        st.session_state.quiz_started = True
        st.rerun()

# ---------------- QUIZ FORM ----------------
if (not blocked_today) and st.session_state.quiz_started:
    total = len(st.session_state.quiz)
    st.subheader("Quiz In Progress")
    user_answers = []

    for i, row in st.session_state.quiz.iterrows():
        options = [("A", row["A"]), ("B", row["B"]), ("C", row["C"]), ("D", row["D"])]
        random.shuffle(options)
        option_dict = dict(options)

        selected = st.multiselect(
            f"Q{i+1}: {row['question']}",
            options=[key for key, _ in options],
            format_func=lambda x: option_dict[x],
            key=f"question_{i}",
        )
        user_answers.append(selected)

    answered = sum(1 for ans in user_answers if len(ans) > 0)
    st.progress(answered / total)
    st.write(f"Answered {answered} of {total} questions")

    all_answered = answered == total
    submit = st.button("Submit Quiz", disabled=not all_answered)
    if not all_answered:
        st.warning("Please answer all questions before submitting.")

    if submit:
        now = datetime.now(TZ)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        # score calc
        score = 0
        for i, row in st.session_state.quiz.iterrows():
            correct_answers = [x.strip() for x in str(row["correct"]).split(",") if x.strip()]
            user_selected = [x.strip() for x in user_answers[i] if str(x).strip()]
            if set(user_selected) == set(correct_answers):
                score += 1

        percentage = round(score / total * 100, 2)

        # 1-row schema
        row_to_append = [email, score, total, percentage, timestamp]

        try:
            # ONE write call per quiz -> drastically reduces quota usage
            results_sheet.append_row(row_to_append, value_input_option="RAW")
        except gspread.exceptions.APIError as e:
            st.error(f"Failed to save result: {e}")
            st.stop()

        # ✅ Update in-memory results immediately (no read)
        new_row = pd.DataFrame([{
            "email": email,
            "score": score,
            "total": total,
            "percentage": percentage,
            "date": pd.to_datetime(timestamp, errors="coerce"),
        }])
        new_row = ensure_results_schema(new_row)

        st.session_state.results_data = ensure_results_schema(
            pd.concat([st.session_state.results_data, new_row], ignore_index=True)
        )

        st.session_state.last_save_msg = f"✅ Saved quiz attempt at {timestamp}"

        # store review data
        st.session_state.user_answers = user_answers
        st.session_state.quiz_snapshot = st.session_state.quiz.copy()

        st.session_state.quiz_started = False
        st.session_state.quiz_finished = True
        st.session_state.last_score = (score, total, percentage)
        st.session_state.pop("quiz_id", None)

        st.rerun()

# ---------------- RESULT SCREEN ----------------
if st.session_state.quiz_finished:
    score, total, percentage = st.session_state.last_score
    st.success(f"Final Score: {score}/{total} ({percentage}%)")

    # ✅ attempts update immediately (from in-memory appended results)
    attempts_now = get_attempts_today(st.session_state.results_data, email, today)
    remaining_now = MAX_ATTEMPTS_PER_DAY - attempts_now
    if remaining_now > 0:
        st.info(f"You have {remaining_now} attempt(s) left today.")
    else:
        st.info("You reached the maximum attempts for today. Come back tomorrow!")

    st.divider()
    st.subheader("Review Answers")

    quiz = st.session_state.quiz_snapshot
    user_answers = st.session_state.user_answers

    for i, row in quiz.iterrows():
        correct_answers = [x.strip() for x in str(row["correct"]).split(",") if x.strip()]
        user_selected = user_answers[i]

        st.markdown(f"**Q{i+1}: {row['question']}**")

        for option_key in ["A", "B", "C", "D"]:
            option_text = row.get(option_key, "")
            if not str(option_text).strip():
                continue

            label = f"{option_key}: {option_text}"

            if option_key in correct_answers and option_key in user_selected:
                st.success(label + " ✅ (Correct)")
            elif option_key in correct_answers:
                st.info(label + " ✔️ (Correct Answer)")
            elif option_key in user_selected:
                st.error(label + " ❌ (Your Answer)")
            else:
                st.write(label)

        st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Try Again"):
            st.session_state.quiz_started = False
            st.session_state.quiz_finished = False
            st.session_state.pop("quiz", None)
            st.session_state.pop("quiz_snapshot", None)
            st.session_state.pop("user_answers", None)
            st.session_state.pop("last_score", None)
            st.rerun()

    with col2:
        if st.button("View Leaderboard"):
            st.session_state.quiz_finished = False
            st.session_state.pop("quiz", None)
            st.session_state.pop("quiz_snapshot", None)
            st.session_state.pop("user_answers", None)
            st.session_state.pop("last_score", None)
            st.rerun()

# ---------------- LEADERBOARD ----------------
if not st.session_state.quiz_started:
    st.subheader("Leaderboard")

    results_data = ensure_results_schema(st.session_state.results_data.copy())

    if not results_data.empty:
        results_data = results_data.dropna(subset=["date", "percentage"])

        results_data["week"] = results_data["date"].dt.to_period("W-MON")
        results_data["month"] = results_data["date"].dt.to_period("M")

        leaderboard_type = st.selectbox("Select leaderboard type", ["All Time", "Weekly", "Monthly"])

        if leaderboard_type == "Weekly":
            current_week = pd.Timestamp.now().to_period("W-MON")
            filtered = results_data[results_data["week"] == current_week]
        elif leaderboard_type == "Monthly":
            current_month = pd.Timestamp.now().to_period("M")
            filtered = results_data[results_data["month"] == current_month]
        else:
            filtered = results_data

        leaderboard = (
            filtered.groupby("email")
            .agg(
                avg_score=("percentage", "mean"),
                attempts=("percentage", "count"),
                best_score=("percentage", "max"),
            )
            .sort_values("avg_score", ascending=False)
            .reset_index()
        )

        leaderboard["avg_score"] = leaderboard["avg_score"].round(2)
        leaderboard["best_score"] = leaderboard["best_score"].round(2)
        leaderboard["username"] = leaderboard["email"].apply(format_username)
        leaderboard["rank"] = leaderboard["avg_score"].rank(method="min", ascending=False).astype(int)

        def medal(rank: int) -> str:
            if rank == 1:
                return "🥇"
            if rank == 2:
                return "🥈"
            if rank == 3:
                return "🥉"
            return ""

        leaderboard["medal"] = leaderboard["rank"].apply(medal)

        view = leaderboard[["rank", "medal", "username", "avg_score", "attempts", "best_score"]]

        current_user_display = format_username(email)

        def highlight_user(row):
            if row.get("username") == current_user_display:
                return ["background-color: #1f6f3d; color: white"] * len(row)
            return [""] * len(row)

        st.dataframe(view.style.apply(highlight_user, axis=1), use_container_width=True)

    streak = calculate_streak(st.session_state.results_data, email)
    st.info(f"🔥 Current streak: {streak} day(s) (70%+ required)")

# ---------------- ATTEMPTS LEFT INFO ----------------
attempts_today = get_attempts_today(st.session_state.results_data, email, today)
remaining_attempts = MAX_ATTEMPTS_PER_DAY - attempts_today
if remaining_attempts > 0:
    st.info(f"You have {remaining_attempts} attempt(s) left today.")
else:
    st.info("You reached the maximum attempts for today. Come back tomorrow!")

# --- Clean leaderboard ---

st.divider()
with st.expander("Admin (Clear leaderboard)", expanded=False):
    admin_email = st.text_input("Admin email", key="admin_email")
    admin_password = st.text_input("Admin password", type="password", key="admin_password")

    if admin_email == ADMIN_EMAIL and admin_password == ADMIN_PASSWORD:
        st.warning("This will delete ALL results (except the header).")

        confirm = st.checkbox("I understand — permanently clear leaderboard", key="admin_confirm")

        if st.button("Clear Leaderboard", disabled=not confirm, key="admin_clear_btn"):
            clear_results_sheet_keep_header(results_sheet, REQUIRED_RESULTS_COLS)

            # refresh in-memory + cached results immediately (so UI updates now)
            if "results_data" in st.session_state:
                st.session_state.results_data = pd.DataFrame(columns=REQUIRED_RESULTS_COLS)
            try:
                load_results_raw.clear()
            except Exception:
                pass

            st.success("Leaderboard cleared.")
            st.rerun()
    else:
        st.info("Enter admin credentials to unlock.")