FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app server ./server
COPY --chown=app:app web ./web
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app legal ./legal
COPY --chown=app:app Kontracts-Preview.html ./Kontracts-Preview.html
RUN mkdir -p /app/data /app/legal && chown -R app:app /app/data /app/legal
USER app
RUN python scripts/update_zipcodes.py
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/health',timeout=3)"
CMD ["python", "scripts/serve.py"]
