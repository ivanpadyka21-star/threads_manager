# Runner image for the mobile E2E framework.
# It connects to an external Appium server (see docker-compose.yml); it does not
# bundle the Android SDK/emulator, which belong in a dedicated appium service.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY mobile_e2e/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mobile_e2e ./mobile_e2e

# Point the framework at the appium service defined in docker-compose.
ENV E2E_SERVER_URL=http://appium:4723

CMD ["python", "-m", "pytest", "mobile_e2e/tests", "-v"]
