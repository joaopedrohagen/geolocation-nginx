from typing import List
import re
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from psycopg2 import sql
import psycopg2
import os
from dotenv import load_dotenv
import requests
from pydantic import BaseModel

load_dotenv()

db_config = {
    "user": os.getenv('USER'),
    "password": os.getenv('PASS'),
    "host": os.getenv('HOST'),
    "port": os.getenv('PORT'),
    "dbname": os.getenv('DATA')
}

token = os.getenv('TOKEN')

ip_pattern = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[a-fA-F0-9]{1,4}:){7}[a-fA-F0-9]{1,4}\b')
time_pattern = re.compile(r'\[(\d{2}/[a-zA-Z]{3}/\d{4}:\d{2}:\d{2}:\d{2} \+\d{4})\]')

class Names(BaseModel):
    en: str

class Country(BaseModel):
    names: Names

class Subdivisions(Country): ...

class City(Country): ...

class Location(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    time_zone: str | None = None

class Traits(BaseModel):
    isp: str

class FindIpModel(BaseModel):
    country: Country | None = None
    subdivisions: List[Subdivisions] | None = None
    city: City | None = None
    location: Location | None = None
    traits: Traits | None = None

class LogHandler(FileSystemEventHandler):
    def __init__(self, log_file_path):
        self.log_file_path = log_file_path
        self.log_file = open(log_file_path, "r")
        self.log_file.seek(0, 2)
        self.conn = connect_db()
        self.cursor = self.conn.cursor()
        self.create_table()

    def create_table(self):
        self.cursor.execute(sql.SQL("SET search_path TO geoip"))

        query = sql.SQL("""
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                server_ip VARCHAR(15) NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                client_ip VARCHAR(100) NOT NULL,
                country_name VARCHAR(100),
                region VARCHAR(100),
                city VARCHAR(100),
                timezone VARCHAR(50),
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                org VARCHAR(100)
            )
        """)
        self.cursor.execute(query)
        self.conn.commit()

    def on_modified(self, event):
        if event.src_path == self.log_file_path:
            self.process_new_lines()

    def process_new_lines(self):
        while True:
            line = self.log_file.readline()
            if not line:
                if not self.is_log_file_rotated():
                    break
                self.log_file = open(self.log_file_path, "r")
                self.log_file.seek(0, 2)
            self.ensure_connection()
            self.extract_ip_and_time(line)

    def is_log_file_rotated(self):
        return os.stat(self.log_file_path).st_ino != os.fstat(self.log_file.fileno()).st_ino

    def ensure_connection(self):
        try:
            self.conn.poll()
        except psycopg2.OperationalError:
            self.conn = connect_db()
            self.cursor = self.conn.cursor()

    def extract_ip_and_time(self, line):
        ip_match = ip_pattern.findall(line)
        time_match = time_pattern.search(line)
        if len(ip_match) >= 2 and time_match:
            server_ip = ip_match[0]
            client_ip = ip_match[1]
            timestamp = time_match.group(1)

            if not self.record_exists(server_ip, timestamp, client_ip):
                api_info = self.query_api(client_ip)
                self.insert_record(server_ip, timestamp, client_ip, api_info)
                print(f"Inserindo {client_ip} no banco...")
                self.conn.commit()

    def query_api(self, client_ip) -> FindIpModel:
        response = requests.get(url=f'https://api.findip.net/{client_ip}/?token={token}')
        if response.status_code == 200:
            data = response.json()
            return FindIpModel.model_validate(data)
        elif response.status_code == 429:
            print("API rate limit exceeded. Retrying after a delay...")
            time.sleep(60)
            return self.query_api(client_ip)

        print(f"Error querying API for IP {client_ip}")
        return FindIpModel()

    def record_exists(self, server_ip, timestamp, client_ip):
        query = sql.SQL("""
            SELECT 1 FROM logs
            WHERE server_ip = %s AND timestamp = %s AND client_ip = %s
        """)
        self.cursor.execute(query, (server_ip, timestamp, client_ip))
        return self.cursor.fetchone() is not None

    def insert_record(self, server_ip, timestamp, client_ip, api_info: FindIpModel):
        query = sql.SQL("""
            INSERT INTO logs (server_ip, timestamp, client_ip, country_name, region, city, timezone, latitude, longitude, org)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """)

        region = api_info.subdivisions[0].names.en if api_info.subdivisions else None
        country = api_info.country.names.en if api_info.country else None
        city = api_info.city.names.en if api_info.city else None
        location = api_info.location if api_info.location else Location()
        traits = api_info.traits.isp if api_info.traits else None

        self.cursor.execute(query, (
            server_ip,
            timestamp,
            client_ip,
            country,
            region,
            city,
            location.time_zone,
            location.latitude,
            location.longitude,
            traits
        ))

def main():
    log_path = "/var/log/nginx/access.log"

    event_handler = LogHandler(log_path)
    observer = Observer()
    observer.schedule(event_handler, path=os.path.dirname(log_path), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

    event_handler.cursor.close()
    event_handler.conn.close()

def connect_db():
    conn = psycopg2.connect(**db_config)
    return conn

if __name__ == "__main__":
    main()
