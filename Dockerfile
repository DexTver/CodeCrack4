FROM python:3.12-slim


RUN adduser --disabled-password --gecos '' bot
USER bot
WORKDIR /app

COPY --chown=bot:bot requirements.txt* .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=bot:bot . .

CMD ["python", "main.py"]
