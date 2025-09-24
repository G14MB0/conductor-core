FROM python:3.11-slim

WORKDIR /app
COPY . /app

# Optional: install PyYAML for YAML configuration support
RUN pip install --no-cache-dir pyyaml || true

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "conductor.container_entrypoint"]
CMD ["run", "--help"]
