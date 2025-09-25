FROM python:3.11-slim

WORKDIR /app

COPY .. /app

RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir -r dashboard/requirements.txt

ENV PYTHONPATH=/app \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "dashboard/app.py"]
