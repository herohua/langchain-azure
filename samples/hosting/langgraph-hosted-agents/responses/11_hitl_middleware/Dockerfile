FROM python:3.12-slim

WORKDIR /app
COPY . user_agent/
WORKDIR /app/user_agent

RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

EXPOSE 8088
CMD ["python", "main.py"]
