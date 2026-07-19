from datetime import datetime

from notion_client import Client

from config import Config

notion = Client(auth=Config.NOTION_TOKEN)


def get_today_tasks():
    today = datetime.today().strftime("%Y-%m-%d")

    response = notion.databases.query(
        database_id=Config.TM_DAILY_DATABASE_ID,
        filter={
            "and": [
                {
                    "property": "Date",
                    "date": {
                        "equals": today
                    }
                },
                {
                    "property": "Done",
                    "checkbox": {
                        "equals": False
                    }
                }
            ]
        }
    )

    return response["results"]
