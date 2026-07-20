FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir flask>=3.0
WORKDIR /app
COPY app.py ./
COPY templates/ ./templates/
EXPOSE 8080
CMD ["python3", "app.py"]
