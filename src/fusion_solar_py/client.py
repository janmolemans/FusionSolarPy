"""Client library to the fusion solar API"""

import logging
import requests
from requests.exceptions import JSONDecodeError, HTTPError
import time
from functools import wraps
import pandas

from .exceptions import *

# global logger object
_LOGGER = logging.getLogger(__name__)


def logged_in(func):
    """
    Decorator to make sure user is logged in.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            result = func(self, *args, **kwargs)
        except (JSONDecodeError, HTTPError):
            _LOGGER.info("Logging in")
            self._login()
            result = func(self, *args, **kwargs)
        return result

    return wrapper


class FusionSolarClient:
    """The main client to interact with the Fusion Solar API
    """

    def __init__(
        self, username: str, password: str, huawei_subdomain: str = "region01eu5"
    ) -> None:
        """Initialiazes a new FusionSolarClient instance. This is the main
           class to interact with the FusionSolar API.
           The client tests the login credentials as soon as it is initialized
        :param username: The username for the system
        :type username: str
        :param password: The password
        :type password: str
        :param huawei_subdomain: The FusionSolar API uses different subdomains for different regions.
                                 Adapt this based on the first part of the URL when you access your system.
        :
        """
        self._user = username
        self._password = password
        self._huawei_subdomain = huawei_subdomain
        self._session = requests.session()
        # hierarchy: company <- plants <- devices <- subdevices
        self._company_id = None

        # login immediately to ensure that the credentials are correct
        # self._login()

    def log_out(self):
        """Log out from the FusionSolarAPI
        """
        self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/unisess/v1/logout",
            params={
                "service": f"https://{self._huawei_subdomain}.fusionsolar.huawei.com"
            },
        )

    def _login(self):
        """Logs into the Fusion Solar API. Raises an exception if the login fails.
        """
        # check the login credentials right away
        _LOGGER.debug("Logging into Huawei Fusion Solar API")

        url = f"https://{self._huawei_subdomain[8:]}.fusionsolar.huawei.com/unisso/v2/validateUser.action"
        params = {
            "decision": 1,
            "service": f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/unisess/v1/auth?service=/netecowebext/home/index.html#/LOGIN",
        }
        json_data = {
            "organizationName": "",
            "username": self._user,
            "password": self._password,
        }

        # send the request
        r = self._session.post(url=url, params=params, json=json_data)
        r.raise_for_status()

        # make sure that the login worked
        if r.json()["errorCode"]:
            _LOGGER.error(f"Login failed: {r.json()['errorMsg']}")
            raise AuthenticationException(
                f"Failed to login into FusionSolarAPI: { r.json()['errorMsg'] }"
            )

        # get the main id
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/organization/v2/company/current",
            params={"_": round(time.time() * 1000)},
        )
        r.raise_for_status()
        self._company_id = r.json()["data"]["moDn"]

        # get the roarand, which is needed for non-GET requests, thus to change device settings
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/unisess/v1/auth/session"
        )
        r.raise_for_status()
        self._session.headers["roarand"] = r.json()[
            "csrfToken"
        ]  # needed for post requests, otherwise it will return 401

    @logged_in
    def get_plant_ids(self) -> list:
        """Get the ids of all available plants linked
           to this account
        :return: A list of plant ids (strings)
        :rtype: list
        """
        # get the complete object tree
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/organization/v2/tree",
            params={
                "parentDn": self._company_id,
                "self": "true",
                "companyTree": "false",
                "cond": '{"BUSINESS_DEVICE":1,"DOMAIN":1}',
                "pageId": 1,
                "_": round(time.time() * 1000),
            },
        )
        r.raise_for_status()
        obj_tree = r.json()

        # get the ids
        plant_ids = [obj["elementDn"] for obj in obj_tree[0]["childList"]]
        # plant_name = [obj["nodeName"] for obj in obj_tree[0]["childList"]]

        return plant_ids

    @logged_in
    def get_device_ids(self) -> dict:
        """gets the devices associated to a given parent_id (can be a plant or a company/account)
        returns a dictionary mapping device_type to device_id"""
        url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/config/device/v1/device-list"
        params = {
            "conditionParams.parentDn": self._company_id,  # can be a plant or company id
            "conditionParams.mocTypes": "20814,20815,20816,20819,20822,50017,60066,60014,60015,23037",  # specifies the types of devices
            "_": round(time.time() * 1000),
        }
        r = self._session.get(url=url, params=params)
        r.raise_for_status()
        device_data = r.json()

        device_key = {}
        for device in device_data["data"]:
            device_key[device["mocTypeName"]] = device["dn"]
        return device_key

    @logged_in
    def active_power_control(self, power_setting) -> None:
        """apply active power control. 
        This can be usefull when electrity prices are
        negative (sunny summer holiday) and you want
        to limit the power that is exported into the grid"""
        power_setting_options = {
            "No limit": 0,
            "Zero Export Limitation": 5,
            "Limited Power Grid (kW)": 6,
            "Limited Power Grid (%)": 7,
        }
        if power_setting not in power_setting_options:
            raise ValueError("Unknown power setting")

        device_key = self.get_device_ids()

        url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/device/v1/deviceExt/set-config-signals"
        data = {
            "dn": device_key["Dongle"],  # power control needs to be done in the dongle
            "changeValues": f'[{{"id":"230190032","value":"{power_setting_options[power_setting]}"}}]',  # 230190032 stands for "Active Power Control"
        }

        r = self._session.post(url, data=data)
        r.raise_for_status()

    @logged_in
    def get_plant_flow(self, plant_id: str) -> dict:
        """Retrieves the data for the energy flow
        diagram displayed for each plant
        :param plant_id: The plant's id
        :type plant_id: str
        :return: The complete data structure as a dict
        """
        # https://region01eu5.fusionsolar.huawei.com/rest/pvms/web/station/v1/overview/energy-flow?stationDn=NE%3D33594051&_=1652469979488
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v1/overview/energy-flow",
            params={"stationDn": plant_id, "_": round(time.time() * 1000)},
        )

        r.raise_for_status()
        flow_data = r.json()

        if not flow_data["success"] or not "data" in flow_data:
            raise FusionSolarException(f"Failed to retrieve plant flow for {plant_id}")

        return flow_data

    @logged_in
    def get_plant_stats(
        self, plant_id: str, query_time=round(time.time() * 1000)
    ) -> dict:
        """Retrieves the complete plant usage statistics for the current day.
        :param plant_id: The plant's id
        :param query_time: should be the zeroth second of the day (otherwise data is missing for that day)
        :type plant_id: str
        :return: _description_
        """
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v1/overview/energy-balance",
            params={
                "stationDn": plant_id,
                "timeDim": 2,
                "queryTime": query_time,
                "timeZone": 2,  # 1 in no daylight
                "timeZoneStr": "Europe/Vienna",
                "_": round(time.time() * 1000),
            },
        )
        r.raise_for_status()
        plant_stats = r.json()

        if not plant_stats["success"] or not "data" in plant_stats:
            raise FusionSolarException(
                f"Failed to retrieve plant status for {plant_id}"
            )
        
        plant_data=plant_stats['data']

        # process the dict of list into a dataframe
        keys=list(plant_data.keys())
        for key in keys:
            if type(plant_data[key]) is not list:
                plant_data.pop(key)
        return (pandas.DataFrame
            .from_dict(plant_data)
            .set_index('xAxis')
            .replace({'--':None})
            .dropna(axis=0, how='all') # if we queried the current day, then the future timestamps should be dropped
            .replace({None:0})
            .astype('float')
            .drop(columns=['radiationDosePower']) 
            )

    def get_last_plant_stats(self, plant_id:str) -> dict:
        """returns the last known data point for the plant"""
        plant_data_df = self.get_plant_stats(plant_id=plant_id)
        if len(plant_data_df)>0:
            return plant_data_df.iloc[-1].to_dict() #get latest entry
        else:
            #no data available yet TODO
            return None


