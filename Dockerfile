FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# fuzzy (used by the rule_based algorithm) has no wheels, so gcc is installed to build it and
# removed again in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && pip install --no-cache-dir \
        "anthropic==1.8.0" \
        "fastapi==0.137.1" \
        "fuzzy==1.2.2" \
        "openai==3.48.0" \
        "pydantic==2.13.4" \
        "python-dotenv==1.2.2" \
        "requests==2.34.2" \
        "textdistance==4.6.3" \
        "tqdm==4.70.1" \
        "uvicorn[standard]==0.49.0" \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY reconciliation ./reconciliation

EXPOSE 5000
CMD ["uvicorn", "reconciliation.standalone_api:app", "--host", "0.0.0.0", "--port", "5000"]
