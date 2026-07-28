from datetime import datetime, timezone, timedelta
from notion_client import Client
from config import Config

notion = Client(auth=Config.NOTION_TOKEN)
VN = timezone(timedelta(hours=7))

def get_today_tasks():
    today = datetime.now(VN).strftime("%Y-%m-%d")
    response = notion.databases.query(
        database_id=Config.TM_DAILY_DATABASE_ID,
        filter={
            "and": [
                {"property": "Date", "date": {"equals": today}},
                {"property": "Done", "checkbox": {"equals": False}}
            ]
        }
    )
    return response["results"]

def find_task_by_title(title):
    today = datetime.now(VN).strftime("%Y-%m-%d")
    response = notion.databases.query(
        database_id=Config.TM_DAILY_DATABASE_ID,
        filter={
            "and": [
                {"property": "Date", "date": {"equals": today}},
                {"property": "Task", "title": {"contains": title}}
            ]
        }
    )
    return response["results"][0] if response["results"] else None

def update_task_status(page_id, done=True):
    notion.pages.update(page_id=page_id, properties={"Done": {"checkbox": done}})

def update_status_note(page_id, note):
    notion.pages.update(
        page_id=page_id, 
        properties={"Status Note": {"rich_text": [{"text": {"content": note}}]}}
    )

# Placeholder for other functions to avoid errors if called
def get_goals(): return []
def get_kpis(): return []
def get_streaks(): return []
def calculate_today_score(): return 0
def calculate_week_score(): return 0
