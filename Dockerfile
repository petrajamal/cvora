FROM python:3.11-slim

# LaTeX — texlive-latex-extra pulls in pdflatex + common packages (~400 MB)
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached unless requirements.txt changes)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy source
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# Ensure data directories exist inside the container
RUN mkdir -p uploads generated_tex

EXPOSE 8000

# Run the background worker + the API server together
CMD ["sh", "-c", "python worker.py & uvicorn main:app --host 0.0.0.0 --port 8000"]
