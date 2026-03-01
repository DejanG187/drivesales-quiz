import streamlit as st
import pandas as pd
import random
import uuid
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ---------------- CONFIG ----------------
SHEET_ID = "1zDAsJD4uxw01eItCZ6Jeu6PjcQLQJHHkPczFFFnID7A"
QUESTIONS_TAB = "questions"
RESULTS_TAB = "results"
ALLOWED_DOMAIN = "@drivesales.com"
QUESTIONS_PER_QUIZ = 20
MAX_ATTEMPTS_PER_DAY = 3

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

def get_attempts_today(results_df: pd.DataFrame, user_email: str, today_str: str) -> int:
    if results_df.empty or "email" not in results_df.columns or "date" not in results_df.columns:
        return 0

    today_rows = results_df[
        (results_df["email"] == user_email) &
        (results_df["date"].astype(str).str[:10] == today_str)
    ]

    if "quiz_id" in today_rows.columns:
        return today_rows["quiz_id"].nunique()
    return len(today_rows)

def calculate_streak(results_df: pd.DataFrame, user_email: str, threshold=70) -> int:
    if results_df.empty or "email" not in results_df.columns:
        return 0

    user_data = results_df[results_df["email"] == user_email].copy()
    if user_data.empty:
        return 0

    # 1 row per quiz attempt
    if "quiz_id" in user_data.columns:
        user_data = user_data.drop_duplicates(subset=["quiz_id"])

    if "date" not in user_data.columns or "percentage" not in user_data.columns:
        return 0

    user_data["date"] = pd.to_datetime(user_data["date"], errors="coerce").dt.date
    user_data["percentage"] = pd.to_numeric(user_data["percentage"], errors="coerce")

    user_data = user_data.dropna(subset=["date", "percentage"])
    user_data = user_data[user_data["percentage"] >= threshold].sort_values("date", ascending=False)

    if user_data.empty:
        return 0

    streak = 0
    day_cursor = datetime.now().date()

    # unique days only (multiple quizzes/day still counts as 1 day)
    unique_days = list(dict.fromkeys(user_data["date"].tolist()))

    for d in unique_days:
        if d == day_cursor:
            streak += 1
            day_cursor = day_cursor - pd.Timedelta(days=1)
        elif d == day_cursor - pd.Timedelta(days=1):
            streak += 1
            day_cursor = day_cursor - pd.Timedelta(days=1)
        else:
            break

    return streak

# ---------------- LOAD FUNCTIONS ----------------
@st.cache_data(ttl=300)  # 5 minutes
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

@st.cache_data(ttl=180)  # 3 minutes
def load_results_raw() -> pd.DataFrame:
    sheet = get_worksheet(SHEET_ID, RESULTS_TAB)
    values = sheet.get_all_values()

    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    rows = [r for r in rows if any(str(cell).strip() for cell in r)]

    # pad short rows
    max_cols = len(headers)
    padded = [r + [""] * (max_cols - len(r)) if len(r) < max_cols else r[:max_cols] for r in rows]

    return pd.DataFrame(padded, columns=headers)

def convert_results_types(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    for col in ["score", "total", "percentage", "question_number"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df

def refresh_results_data():
    # Force a fresh read from Sheets (only when we explicitly call it)
    load_results_raw.clear()
    st.session_state.results_data = convert_results_types(load_results_raw())
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

# ---------------- LOAD RESULTS ONCE PER RERUN ----------------
if "results_data" not in st.session_state:
    st.session_state.results_data = convert_results_types(load_results_raw())
results_data = st.session_state.results_data

today = datetime.now().strftime("%Y-%m-%d")

# ✅ Recompute attempts every rerun (fixes Try Again / View Leaderboard refresh)
results_data = st.session_state.results_data
# If a previous action requested a refresh, do it now (before attempts/leaderboard)
if st.session_state.get("needs_refresh", False):
    refresh_results_data()
    st.session_state.needs_refresh = False

results_data = st.session_state.results_data  # re-bind after refresh
attempts_today = get_attempts_today(st.session_state.results_data, email, today)

# ---------------- CHECK ATTEMPTS ----------------
if attempts_today >= MAX_ATTEMPTS_PER_DAY:
    st.error("You reached max attempts today. Come back tomorrow!")
    st.stop()
else:
    st.info(f"Attempts today: {attempts_today}/{MAX_ATTEMPTS_PER_DAY}")

# ---------------- START QUIZ ----------------
if not st.session_state.quiz_started and not st.session_state.quiz_finished:
    if st.button("Start Quiz"):
        st.session_state.quiz_id = str(uuid.uuid4())
        st.session_state.quiz = questions_data.sample(
            min(QUESTIONS_PER_QUIZ, len(questions_data))
        ).reset_index(drop=True)
        st.session_state.quiz_started = True
        st.rerun()

# ---------------- QUIZ FORM ----------------
if st.session_state.quiz_started:
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
        quiz_id = st.session_state.get("quiz_id", str(uuid.uuid4()))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        score = 0
        per_question_rows = []

        for i, row in st.session_state.quiz.iterrows():
            correct_answers = [x.strip() for x in str(row["correct"]).split(",") if x.strip()]
            user_selected = [x.strip() for x in user_answers[i] if str(x).strip()]

            is_correct = set(user_selected) == set(correct_answers)
            if is_correct:
                score += 1

            per_question_rows.append([
                email,                       # email
                quiz_id,                      # quiz_id
                None,                         # score (fill after)
                total,                        # total
                None,                         # percentage (fill after)
                timestamp,                    # date
                i + 1,                        # question_number
                row["question"],              # question_text
                ",".join(user_selected),      # user_answer
                ",".join(correct_answers),    # correct_answer
                is_correct                    # is_correct
            ])

        percentage = round(score / total * 100, 2)

        for r in per_question_rows:
            r[2] = score
            r[4] = percentage

        try:
            results_sheet.append_rows(per_question_rows, value_input_option="RAW")

            # ✅ request refresh on next run (reliable)
            st.session_state.needs_refresh = True

            # optional: keep a toast message for next run
            st.session_state.last_save_msg = f"✅ Saved quiz attempt {quiz_id} at {timestamp}"
        except gspread.exceptions.APIError as e:
            st.error(f"Failed to save result: {e}")
            st.stop()

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
            st.session_state.needs_refresh = True
            st.session_state.quiz_started = False
            st.session_state.quiz_finished = False
            st.session_state.pop("quiz", None)
            st.session_state.pop("quiz_snapshot", None)
            st.session_state.pop("user_answers", None)
            st.session_state.pop("last_score", None)
            st.rerun()

    with col2:
        if st.button("View Leaderboard"):
            st.session_state.needs_refresh = True
            st.session_state.quiz_finished = False
            st.session_state.pop("quiz", None)
            st.session_state.pop("quiz_snapshot", None)
            st.session_state.pop("user_answers", None)
            st.session_state.pop("last_score", None)
            st.rerun()

# ---------------- LEADERBOARD ----------------
if not st.session_state.quiz_started:
    st.subheader("Leaderboard")

    results_data = st.session_state.results_data

    if not results_data.empty and "date" in results_data.columns:
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

        if "quiz_id" in filtered.columns:
            quiz_level = filtered.drop_duplicates(subset=["quiz_id"])
            attempts_series = ("quiz_id", "nunique")
        else:
            quiz_level = filtered.copy()
            attempts_series = ("percentage", "count")

        leaderboard = (
            quiz_level.groupby("email")
            .agg(
                avg_score=("percentage", "mean"),
                attempts=attempts_series,
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