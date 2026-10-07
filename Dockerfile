FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY rubika_group_math_bot.py .

CMD ["python", "rubika_group_math_bot.py"]
