from unittest import TestCase
import logging
import json
import os
import sys
import requests

currentdir = os.path.dirname(__file__)
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, os.path.join(parentdir, "src")) 

from fusion_solar_py.client import FusionSolarClient
from fusion_solar_py.exceptions import *


class FusionSolarClientTest(TestCase):
    def setUp(self) -> None:
        logging.basicConfig(level=logging.DEBUG)

        # load the credentials
        cred_filename = os.path.join(os.path.dirname(__file__), "credentials.json")

        if not os.path.exists(cred_filename):
            raise Exception("Tests require a 'credentials.json' file in the 'tests' directory. "
                            "This file must contain a dict with a 'username' and 'password' which "
                            "which are used to test the HuaweiFusionSolar API.")

        # load the file
        with open(cred_filename, "r") as reader:
            cred_data = json.load(reader)

            if "username" not in cred_data or "password" not in cred_data:
                raise Exception("Invalid 'credentials.json' file. Must contain 'username' and 'password.")

            self.user = cred_data["username"]
            self.password = cred_data["password"]
            self.subdomain = cred_data.get('subdomain', "region01eu5")

    def test_login(self):
        # create a new client instance
        client = FusionSolarClient(self.user, self.password, self.subdomain)
        client.login()

    def test_failed_login(self):
        self.assertRaises(AuthenticationException, FusionSolarClient, "asda", "asda")

    def test_status(self):
        client = FusionSolarClient(self.user, self.password, self.subdomain)

        devices = client.get_devices()

        self.assertIsInstance(devices, list)
        self.assertTrue(len(devices) > 0)

    def test_get_plant_stats(self):
        client = FusionSolarClient(self.user, self.password, self.subdomain)

        plants = client.get_plants()

        self.assertIsInstance(plants, list)
        self.assertTrue(len(plants) > 0)

        self.assertRaises(requests.exceptions.HTTPError, client.get_plant_stats, "1234")

        plant_stats = client.get_plant_stats(plants[0])

        self.assertIsNotNone(plant_stats)

        with open("/tmp/plant_data.json", "w") as writer:
            json.dump(plant_stats, writer, indent=3)

        # get the last measurements
        last_data = client.get_last_plant_stats(plants[0])

        self.assertIsNotNone(last_data["productPower"])

        # get the energy flow data structure
        energy_flow = client.get_plant_flow(plant_id=plants[0])

        self.assertIsNotNone(energy_flow)

        with open("/tmp/plant_flow.json", "w") as writer:
            json.dump(energy_flow, writer, indent=3)
