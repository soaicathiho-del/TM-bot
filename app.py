name: TM Daily

on:
  workflow_dispatch:

  schedule:
    # Sáng
    - cron: "40 7 * * *"

    # Chiều
    - cron: "00 19 * * *"

    # Tối
    - cron: "00 3 * * *"

jobs:
  send-message:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run application
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          CHAT_ID: ${{ secrets.CHAT_ID }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python app.py
