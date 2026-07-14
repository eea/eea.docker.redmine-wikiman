FROM python:3.11-slim

# Install git and other dependencies
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create audit user
RUN useradd -m -u 1000 audit && \
    mkdir -p /home/audit/.ssh && \
    chown -R audit:audit /home/audit

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src/ .

# Create logs directory
RUN mkdir -p /app/logs && chown -R audit:audit /app

# Switch to audit user
USER audit

# Expose port
EXPOSE 8080

# Health check
# The server only ever serves HTTPS (it exits at startup if webhook certs
# aren't mounted), so the probe must speak TLS too. Cert verification is
# skipped since the cert's CN/SANs are for the admission-webhook hostname,
# not "localhost".
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "\
import ssl, sys, urllib.request; \
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); \
ctx.check_hostname = False; \
ctx.verify_mode = ssl.CERT_NONE; \
resp = urllib.request.urlopen('https://localhost:8080/health', context=ctx, timeout=5); \
sys.exit(0 if resp.status == 200 else 1)"

# Default command
CMD ["python", "api.py"] 