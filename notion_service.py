from notion_client import Client

from config import Config


notion = Client(auth=Config.NOTION_TOKEN)


def get_today_tasks():

    response = notion.databases.query(
        database_id=Config.TM_DAILY_DATABASE_ID
    )

    return response["results"]
