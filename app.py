from notion_service import get_today_tasks

tasks = get_today_tasks()

print(f"Today's Tasks: {len(tasks)}")

for task in tasks:
    props = task["properties"]

    title = props["Task"]["title"][0]["plain_text"]
    task_type = props["Type"]["select"]["name"]

    print(f"{title} | {task_type}")
