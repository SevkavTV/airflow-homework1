from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.operators.python import PythonOperator
from airflow.models import Variable
import json
import sqlite3

# Constants
CITIES = ["Lviv", "Kyiv", "Kharkiv", "Odesa", "Zhmerynka"]
DATABASE_PATH = 'weather_data.db'

# Function to create table
def create_table():
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS measures (
            city TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            temp FLOAT NOT NULL,
            humidity INT NOT NULL,
            cloudiness INT NOT NULL,
            wind_speed FLOAT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

# Function to process weather data
def _process_weather(ti, city):
    info = ti.xcom_pull(task_ids=f'extract_data_{city}')
    timestamp = info["current"]["dt"]
    temp = info["current"]["temp"]
    humidity = info["current"]["humidity"]
    cloudiness = info["current"]["clouds"]
    wind_speed = info["current"]["wind_speed"]
    return {"timestamp": timestamp, "temp": temp, "humidity": humidity, "cloudiness": cloudiness, "wind_speed": wind_speed}

# Function to inject data into SQLite
def inject_data(**kwargs):
    ti = kwargs['ti']
    city = kwargs['city']
    weather_data = ti.xcom_pull(task_ids=f'process_data_{city}')
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO measures (city, timestamp, temp, humidity, cloudiness, wind_speed) VALUES (?, ?, ?, ?, ?, ?);
    """, (city, weather_data['timestamp'], weather_data['temp'], weather_data['humidity'], weather_data['cloudiness'], weather_data['wind_speed']))
    conn.commit()
    conn.close()

default_args = {
    "owner": "airflow",
    "start_date": datetime(2023, 11, 30),
    "catchup": False
}

# Create the DAG
with DAG(
    "weather_data_pipeline",
    default_args=default_args,
    description="A DAG to extract weather data on an hourly basis",
    schedule_interval=timedelta(hours=1),
) as dag:

    # Task to create table
    create_table_task = PythonOperator(
        task_id="create_table",
        python_callable=create_table,
    )

    for city in CITIES:
        # Task to check API availability
        check_api = HttpSensor(
            task_id=f"check_api_{city}",
            http_conn_id="openweathermap_conn",
            endpoint="data/3.0/onecall",  # Adjusted for API 3.0
            request_params={
                "appid": Variable.get("WEATHER_API_KEY"),
                "q": city
            },
            poke_interval=5,
            timeout=20
        )

        # Task to extract data
        extract_data = SimpleHttpOperator(
            task_id=f"extract_data_{city}",
            http_conn_id="openweathermap_conn",
            endpoint="data/3.0/onecall",
            data={
                "appid": Variable.get("WEATHER_API_KEY"),
                "q": city,
                "exclude": "minutely,daily,alerts",
                "units": "metric"
            },
            method="GET",
            response_filter=lambda response: json.loads(response.text),
            log_response=True
        )

        # Task to process data
        process_data = PythonOperator(
            task_id=f"process_data_{city}",
            python_callable=_process_weather,
            op_kwargs={"city": city},
        )

        # Task to inject data
        inject_data_task = PythonOperator(
            task_id=f"inject_data_{city}",
            python_callable=inject_data,
            provide_context=True,
            op_kwargs={"city": city}
        )

        # Define DAG task sequence
        create_table_task >> check_api >> extract_data >> process_data >> inject_data_task
