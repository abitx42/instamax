FROM python:3.10-slim

# Install system dependencies: ffmpeg, libimage-exiftool-perl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create non-root user (Hugging Face Spaces runs as user 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

# Copy requirements and install dependencies
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=user:user . .

# Expose web ports (7860 for HuggingFace, 10000 for Render, 8080 for Koyeb/CloudRun)
EXPOSE 7860 10000 8080

# Run InstaMax
CMD ["python", "app.py"]
