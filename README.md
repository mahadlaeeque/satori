# Satori Attendance Intelligence Chatbot

A web-based chatbot that lets you ask natural language questions about employee attendance data stored in BigQuery, powered by Google Gemini.

## Setup (5 minutes)

### Step 1: Install Python dependencies
```bash
cd satori-chatbot
pip install -r requirements.txt
```

### Step 2: Authenticate with Google Cloud
```bash
gcloud auth application-default login
gcloud config set project ai-vertex-mahad
```

### Step 3: Set your BigQuery dataset name
Open `app.py` and replace `YOUR_DATASET_NAME` on **line 30** with your actual BigQuery dataset name.

To find it: Go to BigQuery Console > click on your Attendance_Satori table > the dataset name is the middle part of the full path shown at the top (e.g., `ai-vertex-mahad.my_dataset.Attendance_Satori` — the dataset is `my_dataset`).

### Step 4: Run the app
```bash
python app.py
```

### Step 5: Open the chatbot
Go to: **http://localhost:8080**

## Example Questions
- "Who was late today?"
- "Show me attendance for this week"
- "Which employees are absent the most?"
- "What's Mahad's attendance this month?"
- "Who is working remotely today?"
- "Show employees with missing punches this week"
- "What's the average check-in time for the team?"
- "List all employees who were on leave yesterday"

## Project Structure
```
satori-chatbot/
  app.py              # Flask backend + Gemini + BigQuery logic
  requirements.txt    # Python dependencies
  README.md           # This file
  templates/
    index.html        # Chat UI frontend
```

## How It Works
1. You type a question in the chat
2. Gemini AI converts your question to a BigQuery SQL query
3. The SQL runs against your live attendance data
4. Gemini formats the results into a friendly natural language answer
5. The answer appears in the chat
