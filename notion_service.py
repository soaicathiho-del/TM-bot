"""
TM-Bot v2
Notion Service

Quản lý toàn bộ giao tiếp với Notion.

⚠️ Không viết logic AI ở đây.
⚠️ Chỉ đọc / ghi dữ liệu.
"""

from datetime import datetime, timedelta, timezone

from notion_client import Client

from config import Config


VN = timezone(timedelta(hours=7))

notion = Client(auth=Config.NOTION_TOKEN)


# ==========================================================
# Utilities
# ==========================================================

def _today():
    return datetime.now(VN).strftime("%Y-%m-%d")


def _safe_title(page):
    try:
        return page["properties"]["Task"]["title"][0]["plain_text"]
    except Exception:
        return ""


# ==========================================================
# Daily Tasks
# ==========================================================

def get_today_tasks():
    """
    Lấy toàn bộ task hôm nay chưa hoàn thành.
    """

    try:

        response = notion.databases.query(
            database_id=Config.TM_DAILY_DATABASE_ID,
            filter={
                "and": [
                    {
                        "property": "Date",
                        "date": {
                            "equals": _today()
                        }
                    },
                    {
                        "property": "Done",
                        "checkbox": {
                            "equals": False
                        }
                    }
                ]
            },
            sorts=[
                {
                    "property": "Date",
                    "direction": "ascending"
                }
            ]
        )

        return response.get("results", [])

    except Exception as e:

        print(f"[Notion] get_today_tasks(): {e}")

        return []


# ==========================================================
# Find Task
# ==========================================================

def find_task_by_title(title):

    try:

        response = notion.databases.query(

            database_id=Config.TM_DAILY_DATABASE_ID,

            filter={
                "and": [

                    {
                        "property": "Date",
                        "date": {
                            "equals": _today()
                        }
                    },

                    {
                        "property": "Task",
                        "title": {
                            "contains": title
                        }
                    }

                ]
            }

        )

        results = response.get("results", [])

        if not results:
            return None

        return results[0]

    except Exception as e:

        print(f"[Notion] find_task_by_title(): {e}")

        return None


# ==========================================================
# Update Task
# ==========================================================

def update_task_status(page_id, done=True):

    try:

        notion.pages.update(

            page_id=page_id,

            properties={
                "Done": {
                    "checkbox": done
                }
            }

        )

        return True

    except Exception as e:

        print(f"[Notion] update_task_status(): {e}")

        return False


def update_status_note(page_id, note):

    try:

        notion.pages.update(

            page_id=page_id,

            properties={
                "Status Note": {
                    "rich_text": [
                        {
                            "text": {
                                "content": note
                            }
                        }
                    ]
                }
            }

        )

        return True

    except Exception as e:

        print(f"[Notion] update_status_note(): {e}")

        return False


# ==========================================================
# Helpers
# ==========================================================

def get_task_titles():

    tasks = get_today_tasks()

    return [
        _safe_title(task)
        for task in tasks
        if _safe_title(task)
    ]


def count_today_tasks():

    return len(get_today_tasks())


def count_completed_tasks():

    try:

        response = notion.databases.query(

            database_id=Config.TM_DAILY_DATABASE_ID,

            filter={
                "and": [

                    {
                        "property": "Date",
                        "date": {
                            "equals": _today()
                        }
                    },

                    {
                        "property": "Done",
                        "checkbox": {
                            "equals": True
                        }
                    }

                ]
            }

        )

        return len(response.get("results", []))

    except Exception:

        return 0


# ==========================================================
# Future APIs
# (Giữ để không lỗi import)
# ==========================================================

def get_goals():
    return []


def get_kpis():
    return []


def get_streaks():
    return []


def calculate_today_score():
    return 0


def calculate_week_score():
    return 0
