FROM python:3.11-slim  
  
RUN apt-get update && apt-get install -y \  
    wget curl ca-certificates \  
    && rm -rf /var/lib/apt/lists/*  
  
WORKDIR /app  
COPY requirements.txt .  
RUN pip install --no-cache-dir -r requirements.txt  
RUN playwright install chromium  
RUN playwright install-deps chromium  
  
COPY scraper.py .  
  
EXPOSE 8000  
CMD ["python", "scraper.py"]  
