import streamlit as st
import pandas as pd
import random
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

# Helper function to safely get a worksheet
def get_worksheet(sheet_id, tab_name):
    try:
        return client.open_by_key(sheet_id).worksheet(tab_name)
    except gspread.exceptions.APIError as e:
        st.error(f"Google Sheets API error: {e}")
        st.stop()

# Safe worksheet access
results_sheet = get_worksheet(SHEET_ID, RESULTS_TAB)

# ---------------- HELPERS ----------------
def format_username(email):
    local = email.split("@")[0]
    if "." in local:
        parts = local.split(".")
        name = parts[0].capitalize()
        initial = parts[1][0].upper() + "."
        return f"{name} {initial}"
    else:
        return local.capitalize()
    
def calculate_streak(results_df, user_email, threshold=70):
    user_data = results_df[results_df["email"] == user_email].copy()
    if user_data.empty:
        return 0

    # ✅ Reduce to quiz-level rows (1 row per quiz)
    user_data = user_data.drop_duplicates(subset=["quiz_id"])

    user_data["date"] = pd.to_datetime(user_data["date"]).dt.date
    user_data = user_data[user_data["percentage"] >= threshold]
    user_data = user_data.sort_values("date", ascending=False)

    streak = 0
    day_cursor = datetime.now().date()

    # Use unique days only (in case multiple quizzes/day)
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

# ---------------- CACHED LOAD FUNCTIONS ----------------
@st.cache_data(ttl=60)
def load_questions(limit=90):
    sheet = get_worksheet(SHEET_ID, QUESTIONS_TAB)
    all_rows = sheet.get_all_records()
    filtered_rows = []
    for row in all_rows[:limit]:
        if not row.get("question"):
            continue
        options = [row.get(opt) for opt in ["A","B","C","D"]]
        if any(str(opt).strip() for opt in options):
            for opt in ["A","B","C","D"]:
                row[opt] = str(row.get(opt,"")).strip()
            filtered_rows.append(row)
    return pd.DataFrame(filtered_rows)

@st.cache_data(ttl=30)
def load_results():
    sheet = get_worksheet(SHEET_ID, RESULTS_TAB)
    return pd.DataFrame(sheet.get_all_records())

questions_data = load_questions()

# ---------------- SESSION STATE ----------------
if "quiz_started" not in st.session_state:
    st.session_state.quiz_started = False
if "quiz_finished" not in st.session_state:
    st.session_state.quiz_finished = False

# ---------------- TITLE ----------------
st.title("DriveSales Daily Quiz")

# ---------------- LOGIN ----------------
email = st.text_input("Enter company email")
if email and not email.endswith(ALLOWED_DOMAIN):
    st.error("Only @drivesales.com emails allowed")
    st.stop()
# ✅ LOAD RESULTS HERE
results_data = load_results()
# ---------------- CHECK ATTEMPTS ----------------
today = datetime.now().strftime("%Y-%m-%d")
attempts_today = 0
if email and not results_data.empty and "date" in results_data.columns:
    today_rows = results_data[
        (results_data["email"] == email) &
        (results_data["date"].astype(str).str[:10] == today)
    ]
    attempts_today = today_rows["quiz_id"].nunique()

    if attempts_today >= MAX_ATTEMPTS_PER_DAY:
        st.error("You reached max attempts today. Come back tomorrow!")
        st.stop()
    else:
        st.info(f"Attempts today: {attempts_today}/{MAX_ATTEMPTS_PER_DAY}")

# ---------------- START QUIZ ----------------
import uuid

if email and not st.session_state.quiz_started and not st.session_state.quiz_finished:
    if st.button("Start Quiz"):
        st.session_state.quiz_id = str(uuid.uuid4())
        quiz = questions_data.sample(min(QUESTIONS_PER_QUIZ, len(questions_data))).reset_index(drop=True)
        st.session_state.quiz = quiz
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
            key=f"question_{i}"
        )
        user_answers.append(selected)

    # Progress bar
    answered = sum(1 for ans in user_answers if len(ans) > 0)
    st.progress(answered / total)
    st.write(f"Answered {answered} of {total} questions")

    # Submit
    all_answered = answered == total
    submit = st.button("Submit Quiz", disabled=not all_answered)
    if not all_answered:
        st.warning("Please answer all questions before submitting.")

    if submit:
        import uuid

        # Ensure quiz_id exists (best is to set this on Start Quiz)
        if "quiz_id" not in st.session_state:
            st.session_state.quiz_id = str(uuid.uuid4())

        quiz_id = st.session_state.quiz_id
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # --- Calculate score ---
        score = 0
        per_question_rows = []

        for i, row in st.session_state.quiz.iterrows():
            correct_answers = [x.strip() for x in str(row["correct"]).split(",") if x.strip()]
            user_selected = [x.strip() for x in user_answers[i] if str(x).strip()]

            is_correct = set(user_selected) == set(correct_answers)
            if is_correct:
                score += 1

            per_question_rows.append([
                email,                          # email
                quiz_id,                         # quiz_id
                score,                           # score (temporary, will overwrite after loop below if you want)
                total,                           # total
                None,                            # percentage (temporary)
                timestamp,                       # date
                i + 1,                           # question_number
                row["question"],                 # question_text
                ",".join(user_selected),         # user_answer
                ",".join(correct_answers),       # correct_answer
                is_correct                       # is_correct
            ])

        percentage = round(score / total * 100, 2)

        # Now that we know final score/percentage, update those fields in each row
        for r in per_question_rows:
            r[2] = score        # score
            r[4] = percentage   # percentage

        # --- Save results (bulk append) ---
        try:
            # Append all question rows in one API call
            results_sheet.append_rows(per_question_rows, value_input_option="RAW")

            # Clear cached results so attempts/leaderboard update immediately
            load_results.clear()
        except gspread.exceptions.APIError as e:
            st.error(f"Failed to save result: {e}")
            st.stop()

        # --- Store for review screen (correct answers display) ---
        st.session_state.user_answers = user_answers
        st.session_state.quiz_snapshot = st.session_state.quiz.copy()

        # --- Move to result screen ---
        st.session_state.quiz_started = False
        st.session_state.quiz_finished = True
        st.session_state.last_score = (score, total, percentage)

        # Optional: clear timer if you use it
        # st.session_state.pop("quiz_start_time", None)
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
        correct_answers = row["correct"].split(",")
        user_selected = user_answers[i]

        st.markdown(f"**Q{i+1}: {row['question']}**")

        for option_key in ["A", "B", "C", "D"]:
            option_text = row[option_key]
            if not option_text:
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
            st.session_state.pop("quiz_id", None)
            st.session_state.quiz_started = False
            st.session_state.quiz_finished = False
            st.rerun()

    with col2:
        if st.button("View Leaderboard"):
            st.session_state.quiz_finished = False
            st.rerun()

# ---------------- LEADERBOARD ----------------
st.subheader("Leaderboard")

results_data = load_results()

if not results_data.empty:

    results_data["date"] = pd.to_datetime(results_data["date"], errors="coerce")
    results_data = results_data.dropna(subset=["date"])  # remove bad dates safely

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

    # ✅ DEDUPE SAFELY
    if "quiz_id" in filtered.columns:
        quiz_level = filtered.drop_duplicates(subset=["quiz_id"])
        attempts_col = ("quiz_id", "count")
    else:
        quiz_level = filtered.copy()
        attempts_col = ("percentage", "count")  # old format fallback

    leaderboard = (
        quiz_level.groupby("email")
        .agg(
            avg_score=("percentage", "mean"),
            attempts=attempts_col,
            best_score=("percentage", "max")
        )
        .sort_values("avg_score", ascending=False)
        .reset_index()
    )

    leaderboard["avg_score"] = leaderboard["avg_score"].round(2)
    leaderboard["username"] = leaderboard["email"].apply(format_username)

    st.dataframe(
        leaderboard[["username", "avg_score", "attempts", "best_score"]],
        use_container_width=True
    )

# --- Attempts left info ---
# --- Attempts left info ---
if email:
    results_data = load_results()

    if not results_data.empty and "date" in results_data.columns:
        today_rows = results_data[
            (results_data["email"] == email) &
            (results_data["date"].astype(str).str[:10] == today)
        ]

        # New format (quiz_id exists) vs old format fallback
        if "quiz_id" in today_rows.columns:
            attempts_today = today_rows["quiz_id"].nunique()
        else:
            attempts_today = len(today_rows)

    else:
        attempts_today = 0

    remaining_attempts = MAX_ATTEMPTS_PER_DAY - attempts_today

    if remaining_attempts > 0:
        st.info(f"You have {remaining_attempts} attempt(s) left today.")
    else:
        st.info("You reached the maximum attempts for today. Come back tomorrow!")