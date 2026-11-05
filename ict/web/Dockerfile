# Bouwinstructies voor de container "webapp"
FROM python:3.12-slim

# Laat Python logregels direct tonen (niet bufferen)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Eerst de benodigde pakketten installeren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Daarna de rest van de applicatie kopiëren
COPY . .

EXPOSE 5000

# "exec"-vorm: Python krijgt zo zelf het stopsignaal van "docker stop"
CMD ["python", "app.py"]
