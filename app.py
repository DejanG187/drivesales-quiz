import streamlit as st
import pandas as pd
import random
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials


# CONFIG
SHEET_ID = "1zDAsJD4uxw01eItCZ6Jeu6PjcQLQJHHkPczFFFnID7A"
QUESTIONS_TAB = "questions"
RESULTS_TAB = "results"
ALLOWED_DOMAIN = "@drivesales.com"
QUESTIONS_PER_QUIZ = 20
MAX_ATTEMPTS_PER_DAY = 3

# GOOGLE AUTH
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)

client = gspread.authorize(creds)

# ------------------ HELPER FUNCTIONS ------------------
def format_username(email):
    """
    Convert email to friendly username:
    - dejan@drivesales.com -> Dejan
    - dejan.g@drivesales.com -> Dejan G.
    """
    local = email.split("@")[0]
    if "." in local:
        parts = local.split(".")
        name = parts[0].capitalize()
        initial = parts[1][0].upper() + "."
        return f"{name} {initial}"
    else:
        return local.capitalize()

# ---------- CACHED LOAD FUNCTIONS ----------
@st.cache_data(ttl=60)
def load_questions(limit=500):
    sheet = client.open_by_key(SHEET_ID).worksheet(QUESTIONS_TAB)
    all_rows = sheet.get_all_records()

    filtered_rows = []
    for row in all_rows[:limit]:
        # Only keep rows with a non-empty question
        if not row.get("question"):
            continue
        # Keep only rows where at least one option is not empty
        options = [row.get(opt) for opt in ["A","B","C","D"]]
        if any(opt for opt in options if str(opt).strip()):
            # Convert all options to strings to avoid None
            row["A"] = str(row.get("A", "")).strip()
            row["B"] = str(row.get("B", "")).strip()
            row["C"] = str(row.get("C", "")).strip()
            row["D"] = str(row.get("D", "")).strip()
            filtered_rows.append(row)

    return pd.DataFrame(filtered_rows)

@st.cache_data(ttl=30)
def load_results():
    sheet = client.open_by_key(SHEET_ID).worksheet(RESULTS_TAB)
    return pd.DataFrame(sheet.get_all_records())

questions_data = load_questions()
results_data = load_results()

# Keep results_sheet reference to append rows later
results_sheet = client.open_by_key(SHEET_ID).worksheet(RESULTS_TAB)

# ------------------- SESSION STATE -------------------
if "quiz_started" not in st.session_state:
    st.session_state.quiz_started = False

if "view_leaderboard" not in st.session_state:
    st.session_state.view_leaderboard = False

# TITLE
st.title("DriveSales Daily Quiz")

# LOGIN
email = st.text_input("Enter company email")

if email and not email.endswith(ALLOWED_DOMAIN):
    st.error("Only @drivesales.com emails allowed")
    st.stop()

# CHECK ATTEMPTS TODAY

today = datetime.now().strftime("%Y-%m-%d")

if email and not results_data.empty and "date" in results_data.columns:
    
    results_data["date_only"] = results_data["date"].astype(str).str[:10]

    attempts_today = len(
        results_data[
            (results_data["email"] == email)
            & (results_data["date_only"] == today)
        ]
    )

    if attempts_today >= MAX_ATTEMPTS_PER_DAY:
        st.error("You reached max 3 attempts today")
        st.stop()

    st.info(f"Attempts today: {attempts_today}/3")

# START QUIZ
if "quiz_started" not in st.session_state:
    st.session_state.quiz_started = False

if email and not st.session_state.quiz_started:

    if st.button("Start Quiz"):

        quiz = questions_data.sample(
            min(QUESTIONS_PER_QUIZ, len(questions_data))
        ).reset_index(drop=True)

        st.session_state.quiz = quiz
        st.session_state.quiz_started = True

# QUIZ FORM
# QUIZ SECTION (Optimized UX)
if st.session_state.quiz_started:

    total = len(st.session_state.quiz)

    st.subheader("Quiz In Progress")

    user_answers = []

    # ---------- QUESTIONS ----------
    for i, row in st.session_state.quiz.iterrows():

        options = [
            ("A", row["A"]),
            ("B", row["B"]),
            ("C", row["C"]),
            ("D", row["D"])
        ]

        random.shuffle(options)
        option_dict = dict(options)

        selected = st.multiselect(
            f"Q{i+1}: {row['question']}",
            options=[key for key, _ in options],
            format_func=lambda x: option_dict[x],
            key=f"question_{i}"
        )

        user_answers.append(selected)

    # ---------- PROGRESS ----------
    answered = sum(1 for ans in user_answers if len(ans) > 0)
    progress = answered / total

    st.progress(progress)
    st.write(f"Answered {answered} of {total} questions")

    # ---------- SUBMIT BUTTON ----------
    all_answered = answered == total

    submit = st.button(
        "Submit Quiz",
        disabled=not all_answered
    )

    if not all_answered:
        st.warning("Please answer all questions before submitting.")

    # ---------- SCORING ----------
    if submit:

        score = 0

        for i, row in st.session_state.quiz.iterrows():
            correct_answers = row["correct"].split(",")

            if set(user_answers[i]) == set(correct_answers):
                score += 1

        percentage = round(score / total * 100, 2)

        # Save result
        results_sheet.append_row([
            email,
            score,
            total,
            percentage,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ])

        st.success(f"Final Score: {score}/{total} ({percentage}%)")

        # Refresh leaderboard data ONCE
        results_data = pd.DataFrame(results_sheet.get_all_records())

        leaderboard_live = (
            results_data.groupby("email")
            .agg(avg_score=("percentage", "mean"))
            .sort_values("avg_score", ascending=False)
            .reset_index()
        )

        rank = leaderboard_live.index[
            leaderboard_live["email"] == email
        ].tolist()[0] + 1

        st.info(f"Your current rank: #{rank}")

        # ---------- OPTIONS ----------
        col1, col2 = st.columns(2)

        with col1:
            if st.button("Try Again"):
                st.session_state.quiz_started = False
                st.rerun()  # resets quiz to start a new one

        with col2:
            if st.button("View Leaderboard"):
                st.session_state.quiz_started = False
                st.session_state.view_leaderboard = True
                st.rerun()

        # Reset quiz state automatically
        st.session_state.quiz_started = False

        # Optional small delay feeling
        st.balloons()


# ------------------- LEADERBOARD -------------------
if st.session_state.view_leaderboard or not st.session_state.quiz_started:
    st.header("Leaderboard")

    results_data = load_results()
    if not results_data.empty:
        leaderboard = (
            results_data.groupby("email")
            .agg(
                avg_score=("percentage", "mean"),
                quizzes=("percentage", "count"),
                best_score=("percentage", "max")
            )
            .sort_values("avg_score", ascending=False)
            .reset_index()
        )
        leaderboard["avg_score"] = leaderboard["avg_score"].round(2)
                # Add a 'username' column formatted from email
        leaderboard["username"] = leaderboard["email"].apply(format_username)

        # Show only username and scores
        st.dataframe(
            leaderboard[["username", "avg_score", "quizzes", "best_score"]]
        )

        # Button to go back to quiz
        if email and st.button("Back to Quiz"):
            st.session_state.view_leaderboard = False
            st.session_state.quiz_started = False
            st.rerun()
