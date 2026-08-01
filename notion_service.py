"""
TM-Bot v2
Notion Service
"""

from datetime import datetime, timedelta, timezone

from notion_client import Client

from config import Config


VN = timezone(timedelta(hours=7))
notion = Client(auth=Config.NOTION_TOKEN)


def _today():
    return datetime.now(VN).strftime("%Y-%m-%d")


def _safe_title(page):
    try:
        return page["properties"]["Task"]["title"][0]["plain_text"]
    except Exception:
        return ""


def get_task_type(page):
    try:
        return page["properties"]["Type"]["select"]["name"]
    except Exception:
        return None


# ==========================================================
# Daily Tasks
# ==========================================================
def get_today_tasks():
    try:
        response = notion.databases.query(
            database_id=Config.TM_DAILY_DATABASE_ID,
            filter={"and": [
                {"property": "Date", "date": {"equals": _today()}},
                {"property": "Done", "checkbox": {"equals": False}},
            ]},
            sorts=[{"property": "Date", "direction": "ascending"}],
        )
        return response.get("results", [])
    except Exception as e:
        print(f"[Notion] get_today_tasks(): {e}")
        return []


def get_tasks_by_date_range(start_date: str, end_date: str):
    """start_date/end_date dạng 'YYYY-MM-DD'. Lấy TẤT CẢ task (kể cả đã Done)."""
    try:
        response = notion.databases.query(
            database_id=Config.TM_DAILY_DATABASE_ID,
            filter={"and": [
                {"property": "Date", "date": {"on_or_after": start_date}},
                {"property": "Date", "date": {"on_or_before": end_date}},
            ]},
        )
        return response.get("results", [])
    except Exception as e:
        print(f"[Notion] get_tasks_by_date_range(): {e}")
        return []


def find_task_by_title(title):
    try:
        response = notion.databases.query(
            database_id=Config.TM_DAILY_DATABASE_ID,
            filter={"and": [
                {"property": "Date", "date": {"equals": _today()}},
                {"property": "Task", "title": {"contains": title}},
            ]},
        )
        results = response.get("results", [])
        return results[0] if results else None
    except Exception as e:
        print(f"[Notion] find_task_by_title(): {e}")
        return None


def create_task(title: str, task_type: str = None, date: str = None):
    try:
        properties = {
            "Task": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": date or _today()}},
            "Done": {"checkbox": False},
        }
        if task_type:
            properties["Type"] = {"select": {"name": task_type}}
        return notion.pages.create(
            parent={"database_id": Config.TM_DAILY_DATABASE_ID},
            properties=properties,
        )
    except Exception as e:
        print(f"[Notion] create_task(): {e}")
        return None


def update_task_status(page_id, done=True):
    try:
        notion.pages.update(page_id=page_id, properties={"Done": {"checkbox": done}})
        return True
    except Exception as e:
        print(f"[Notion] update_task_status(): {e}")
        return False


def update_status_note(page_id, note):
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"Status Note": {"rich_text": [{"text": {"content": note}}]}},
        )
        return True
    except Exception as e:
        print(f"[Notion] update_status_note(): {e}")
        return False


# ==========================================================
# Rules Point (điểm số)
# ==========================================================
def get_rules_point_map():
    """Trả về {type_name: {"point": int, "priority": int}}"""
    try:
        response = notion.databases.query(database_id=Config.RULES_POINT_DATABASE_ID)
        result = {}
        for page in response.get("results", []):
            try:
                type_name = page["properties"]["Type"]["title"][0]["plain_text"]
                point = page["properties"]["Point"]["number"] or 0
                priority = page["properties"]["Priority"]["number"]
                result[type_name] = {"point": point, "priority": priority}
            except Exception:
                continue
        return result
    except Exception as e:
        print(f"[Notion] get_rules_point_map(): {e}")
        return {}


# ==========================================================
# Helpers
# ==========================================================
def get_task_titles():
    return [_safe_title(t) for t in get_today_tasks() if _safe_title(t)]


def count_today_tasks():
    return len(get_today_tasks())


def count_completed_tasks():
    try:
        response = notion.databases.query(
            database_id=Config.TM_DAILY_DATABASE_ID,
            filter={"and": [
                {"property": "Date", "date": {"equals": _today()}},
                {"property": "Done", "checkbox": {"equals": True}},
            ]},
        )
        return len(response.get("results", []))
    except Exception:
        return 0


# ==========================================================
# Future APIs (Giai đoạn 2 — chưa dùng, giữ để không lỗi import)
# ==========================================================
def get_goals():
    return []


def get_kpis():
    return []


def get_streaks():
    return []


def calculate_today_score():
    return 0
