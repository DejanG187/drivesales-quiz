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

questions_sheet = client.open_by_key(SHEET_ID).worksheet(QUESTIONS_TAB)
results_sheet = client.open_by_key(SHEET_ID).worksheet(RESULTS_TAB)

questions_data = pd.DataFrame(questions_sheet.get_all_records())

# TITLE
st.title("DriveSales Daily Quiz")

# LOGIN
email = st.text_input("Enter company email")

if email and not email.endswith(ALLOWED_DOMAIN):
    st.error("Only @drivesales.com emails allowed")
    st.stop()

# CHECK ATTEMPTS TODAY
today = datetime.now().strftime("%Y-%m-%d")

if email:

    results_data = pd.DataFrame(results_sheet.get_all_records())

    if not results_data.empty:

        results_data["date_only"] = results_data["date"].str[:10]

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
if st.session_state.quiz_started:

    score = 0
    total = len(st.session_state.quiz)

    with st.form("quiz_form"):

        user_answers = []

        for i, row in st.session_state.quiz.iterrows():

            options = {
                "A": row["A"],
                "B": row["B"],
                "C": row["C"],
                "D": row["D"]
            }

            selected = st.multiselect(
                f"Q{i+1}: {row['question']}",
                options=list(options.keys()),
                format_func=lambda x: options[x],
                key=i
            )

            user_answers.append(selected)

        submitted = st.form_submit_button("Submit")

        if submitted:

            for i, row in st.session_state.quiz.iterrows():

                correct_answers = row["correct"].split(",")

                if set(user_answers[i]) == set(correct_answers):
                    score += 1

            percentage = round(score / total * 100, 2)

            results_sheet.append_row([
                email,
                score,
                total,
                percentage,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
            
            st.success(f"Score: {score}/{total} ({percentage}%)")
            updated_results = pd.DataFrame(results_sheet.get_all_records())

            leaderboard_live = (
            updated_results.groupby("email")
            .agg(avg_score=("percentage", "mean"))
            .sort_values("avg_score", ascending=False)
            .reset_index()
            )

            rank = leaderboard_live.index[
                leaderboard_live["email"] == email
            ].tolist()[0] + 1

            st.info(f"Your current rank: #{rank}")   
    if st.session_state.quiz_started:
        if st.button("Finish Quiz"):
            st.session_state.quiz_started = False
            st.rerun()
# LEADERBOARD
results_data = pd.DataFrame(results_sheet.get_all_records())
st.header("Leaderboard")

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

    st.dataframe(leaderboard)
