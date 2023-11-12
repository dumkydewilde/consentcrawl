import requests
import logging
import os
import re
import yaml
from time import time
from pathlib import Path
import sqlite3

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


class Blocklists:
    BLOCKLISTS_FILE = f"{MODULE_DIR}/assets/blocklists.yml"
    DB_FILE = f"{MODULE_DIR}/data/blocklists.db"
    TABLE_NAME = "blocklists"

    def __init__(
        self, db_file=None, source_file=None, max_age_days=7, force_bootstrap=False
    ):
        self.has_blocklists = False
        self.max_age_days = max_age_days
        self.force_bootstrap = force_bootstrap
        self._data = {}
        if db_file:
            self.DB_FILE = db_file
        if source_file:
            self.BLOCKLISTS_FILE = source_file

        self.connection = self.get_connection()

        # create table if not exists
        c = self.connection.cursor()
        c.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                url TEXT,
                ids TEXT,
                last_fetch_time TEXT
            )"""
        )
        self.connection.commit()

        # Get last fetch timestamp and check staleness
        self.last_fetch_timestamp = self.get_last_fetch_timestamp()
        logging.debug(f"Last fetch timestamp: {self.last_fetch_timestamp}")

        if self.last_fetch_timestamp > 0:
            self.has_blocklists = True

        if self.blocklists_older_than(self.max_age_days):
            logging.debug(
                f"Blocklists older than {self.max_age_days} days, fetching new blocklist data"
            )
            self.bootstrap()

        if not self.has_blocklists or self.force_bootstrap:
            logging.debug("Fetching new blocklist data...")
            self.bootstrap()

        self.get_blocklist_data_from_db()

    def bootstrap(self):
        """
        Bootstrap blocklists data from a YAML file.
        """
        self.get_blocklists_file()
        self.get_blocklists_data()
        self.generate_master_list()
        self.last_fetch_timestamp = int(time())
        self.store_blocklists_data()
        self.has_blocklists = True

    def get_domains(self):
        if not self.has_blocklists:
            raise Exception("No blocklists data available.")
        return self._data.keys()

    def get_connection(self):
        """
        Create a connection to a SQLite database.
        """

        db_file = self.DB_FILE

        Path.mkdir(Path(db_file).parent, exist_ok=True)
        logging.debug(f"Connecting to database: {db_file}")
        conn = sqlite3.connect(db_file)

        return conn

    def blocklists_older_than(self, days: int = 7):
        age_in_seconds = int(time()) - self.last_fetch_timestamp
        age_in_days = int(age_in_seconds / (60 * 60 * 24))
        logging.debug(
            f"Bootstrap data age in days: {age_in_days} ( > {days} = {age_in_days > days})"
        )
        return age_in_days > days

    def get_blocklists_file(self):
        """
        Retrieve blocklists and hostfiles from a YAML file.
        """

        with open(self.BLOCKLISTS_FILE, "r") as f:
            self.blocklists = yaml.safe_load(f)

    def get_blocklists_data(self):
        for blocklist in self.blocklists:
            logging.info(
                f"Retrieving blocklist: '{blocklist['name']}' from: {blocklist['url']}"
            )

            if blocklist["type"] == "hostfile":
                data = requests.get(blocklist["url"]).text
                blocklist["data"] = self.parse_hostfile(data)

            elif blocklist["type"] == "blocklist":
                data = requests.get(blocklist["url"]).text.split("\n")
                blocklist["data"] = self.parse_blocklist(data)

            elif blocklist["type"] == "domains":
                blocklist["data"] = requests.get(blocklist["url"]).text.split("\n")

            else:
                raise Exception(f"Unknown blocklist type {blocklist['type']}")

    def parse_hostfile(self, data):
        """
        Retrieve a unique list of domain names from a hostfile with the format similar to:
            '127.0.0.1   domain-name.com'
        """
        domains = []
        for line in data.splitlines():
            if not line.startswith("#"):
                match = re.search(r"^\S+\s+(\S+)$", line)
                if match:
                    domains.append(match.group(1))

        return set(domains)

    def parse_blocklist(self, data):
        """
        Retrieve a unique list of domain names from a blocklist with the format similar to:
            '||domain-name.com^'
        """
        domains = []
        for line in data:
            if line.startswith("||"):
                match = re.search(r"^\|\|([^\/]+)\^$", line)
                if match:
                    domains.append(match.group(1))

        return set(domains)

    def generate_master_list(self):
        """
        Generate a master list of domains from all blocklists.
        """

        for l in self.blocklists:
            for item in l["data"]:
                if item in self._data:
                    self._data[item].append(l["id"])
                else:
                    self._data[item] = [l["id"]]

    def store_blocklists_data(self, table_name="blocklists"):
        """
        Store blocklist data in a SQLite database.
        """
        c = self.connection.cursor()
        c.execute(f"DROP TABLE IF EXISTS {table_name}")
        c.execute(
            f"""
            CREATE TABLE {table_name} (
                url TEXT,
                ids TEXT,
                last_fetch_time TEXT
            )"""
        )
        self.connection.commit()

        c.executemany(
            f"INSERT OR REPLACE INTO {table_name} VALUES (?, ?, ?)",
            (
                [
                    (k, ",".join(self._data[k]), self.last_fetch_timestamp)
                    for k in self._data.keys()
                ]
            ),
        )
        self.connection.commit()

    def get_blocklist_data_from_db(self, table_name="blocklists"):
        """
        Retrieve blocklist data from a SQLite database.
        """
        c = self.connection.cursor()
        c.execute(f"SELECT * FROM {table_name}")
        self._data = dict([(row[0], row[1]) for row in c.fetchall()])

    def get_last_fetch_timestamp(self, table_name="blocklists"):
        """
        Retrieve the last fetch timestamp from a SQLite database.
        """
        c = self.connection.cursor()
        try:
            c.execute(f"SELECT MAX(last_fetch_time) FROM {table_name}")
            return int(c.fetchone()[0])
        except:
            return 0
