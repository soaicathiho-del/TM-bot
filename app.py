from notion_service import get_today_tasks

tasks = get_today_tasks()

print(f"Found {len(tasks)} tasks")

for task in tasks:
    try:
        title = task["properties"]["Task"]["title"][0]["plain_text"]
    except:
        title = "No title"

    print(title)
