FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REVIEWER_LAB_BIND=0.0.0.0 \
    REVIEWER_LAB_PORT=8765 \
    REVIEWER_LAB_DEMO=1

WORKDIR /app
RUN useradd --create-home --uid 10001 lab
COPY --chown=lab:lab . .
USER lab
EXPOSE 8765
CMD ["python", "run.py", "ui", "--demo"]
