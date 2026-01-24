FROM python:3.13

# Set timezone (change to your local timezone)
ENV TZ=Europe/Madrid

# Install tzdata so the timezone is recognized by Python and the OS
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=src
WORKDIR /app

COPY src /app/src
COPY Makefile pyproject.toml /app/

RUN pip install uv

CMD ["make", "run"]
