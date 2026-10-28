FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir \
    "anthropic==1.8.0" \
    "fastapi==0.137.1" \
    "pydantic==2.13.4" \
    "python-dotenv==1.2.2" \
    "uvicorn[standard]==0.49.0"
COPY reconciliation ./reconciliation

EXPOSE 5000
CMD ["uvicorn", "reconciliation.standalone_api:app", "--host", "0.0.0.0", "--port", "5000"]
