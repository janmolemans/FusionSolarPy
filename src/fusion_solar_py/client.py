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
LOCAL_TIMEZONE = "Europe/Brussels" #TODO infer automatically

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
            self.login()
            result = func(self, *args, **kwargs)
        return result

    return wrapper


class FusionSolarClient:
    """The main client to interact with the Fusion Solar API
    """

    def __init__(
        self, username: str, password: str, huawei_subdomain: str = "region01eu5"
    ) -> None:
        """Initializes a new FusionSolarClient instance. This is the main
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
        # self.login()

    def logout(self):
        """Log out from the FusionSolarAPI
        """
        self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/unisess/v1/logout",
            params={
                "service": f"https://{self._huawei_subdomain}.fusionsolar.huawei.com"
            },
        )

    def login(self):
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
    def get_plants(self) -> list:
        """Get the ids of all available plants linked
           to this account
        :return: A list of plant objects
        """
        # get the complete object tree
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/organization/v2/tree",
            params={
                "parentDn": self._company_id,
                "self": "true",
                "companyTree": "false",  # "false",
                "cond": '{"BUSINESS_DEVICE":1,"DOMAIN":1}',
                "pageId": 1,
                "_": round(time.time() * 1000),
            },
        )
        r.raise_for_status()
        obj_tree = r.json()

        plants = [
            Plant(client=self, parent=self, id=obj["elementDn"], name=obj["nodeName"])
            for obj in obj_tree[0]["childList"]
        ]

        return plants

    @logged_in
    def get_devices(self, parent=None) -> list:
        """gets the devices associated to a given parent_id (can be a plant or a company/account)
        returns a list of device objects"""
        if parent is None:
            parent = self._company_id
        url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/neteco/web/config/device/v1/device-list"
        params = {
            "conditionParams.parentDn": parent,  # can be a plant or company id
            "conditionParams.mocTypes": "20814,20815,20816,20819,20822,50017,60066,60014,60015,23037",  # specifies the types of devices
            "_": round(time.time() * 1000),
        }
        r = self._session.get(url=url, params=params)
        r.raise_for_status()
        device_data = r.json()

        devices = []
        for device in device_data["data"]:
            devices.append(
                Device(
                    client=self,
                    parent=parent,
                    id=device["dn"],
                    name=device["name"],
                    type=device["mocTypeName"],
                )
            )
        return devices
        # device_key = {}
        # for device in device_data["data"]:
        #     device_key[device["mocTypeName"]] = device["dn"]
        # return device_key

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

        # find the dongle as power control needs to be done in the dongle
        dongle_devices = [
            device for device in self.get_devices() if device.type == "Dongle"
        ]
        if len(dongle_devices) != 1:
            raise NotImplementedError(
                "Exaclty one dongle per account supported currently"
            )

        url = f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/device/v1/deviceExt/set-config-signals"
        data = {
            "dn": dongle_devices[0].id,
            "changeValues": f'[{{"id":"230190032","value":"{power_setting_options[power_setting]}"}}]',  # 230190032 stands for "Active Power Control"
        }

        r = self._session.post(url, data=data)
        r.raise_for_status()

    @logged_in
    def get_plant_flow(self, plant_id: str, return_resp=False) -> dict:
        """Retrieves the data for the energy flow
        diagram displayed for each plant
        This function does not return nicely formatted data
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
        if return_resp:
            return r
        flow_data = r.json()

        if not flow_data["success"] or not "data" in flow_data:
            raise FusionSolarException(f"Failed to retrieve plant flow for {plant_id}")

        return flow_data["data"]

    @logged_in
    def get_plant_report(
        self,
        plant_id: str,
        query_time=round(time.time() * 1000),
        return_resp=False,
    ) -> pandas.DataFrame:
        """Retrieves the complete plant report for the current day.
        :param plant_id: The plant's id
        :param query_time: should be the zeroth second of the day (otherwise data is missing for that day)
        """
        r = self._session.post(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/report/v1/station/station-kpi-list",
            json={
                "orderBy": "fmtCollectTimeStr",
                "page": 1,
                "pageSize": 24,
                "moList": [{"moType": 20801, "moString": plant_id}],
                "counterIDs": ["productPower", "inverterPower", "onGridPower",],
                "sort": "asc",
                "statDim": "2",
                "statTime": query_time,
                "statType": "1",
                # "station": "1",
                "timeZone": 2,
                "timeZoneStr": LOCAL_TIMEZONE,
            },
        )
        r.raise_for_status()
        if return_resp:
            return r
        report_data = r.json()
        df = pandas.DataFrame.from_records(report_data)
        df["fmtCollectTimeStr"] = pandas.to_datetime(
            df["fmtCollectTimeStr"]
        ).dt.tz_localize(LOCAL_TIMEZONE)
        return df

    @logged_in
    def get_plant_stats(
        self,
        plant_id: str,
        query_time=round(time.time() * 1000),
        return_resp=False,
        time_dim=2,
    ) -> pandas.DataFrame:
        """Retrieves the complete plant usage statistics for the current day.
        :param plant_id: The plant's id
        :param query_time: should be the zeroth second of the day (otherwise data is missing for that day)
        :param time_dim: aggregation level: 2=5min, 3=1hour (but gives error), 4=1day, 5=1month, 6=1year
        """
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/station/v1/overview/energy-balance",
            params={
                "stationDn": plant_id,
                "timeDim": time_dim,
                "queryTime": query_time,
                "timeZone": 2,  # 1 in no daylight
                "timeZoneStr": LOCAL_TIMEZONE,
                "_": round(time.time() * 1000),
            },
        )
        r.raise_for_status()
        if return_resp:
            return r
        plant_stats = r.json()

        # TODO this url also returns the total values

        if not plant_stats["success"] or not "data" in plant_stats:
            raise FusionSolarException(
                f"Failed to retrieve plant status for {plant_id}"
            )

        plant_data = plant_stats["data"]

        # process the dict of list into a dataframe
        keys = list(plant_data.keys())
        for key in keys:
            if type(plant_data[key]) is not list:
                plant_data.pop(key)
        df= (
            pandas.DataFrame.from_dict(plant_data)
            .set_index("xAxis")
            .replace({"--": None})
            .dropna(
                axis=0, how="all"
            )  # if we queried the current day, then the future timestamps should be dropped
            .replace({None: 0})
            .astype("float")
            .drop(columns=["radiationDosePower"])
        )
        df.index=pandas.to_datetime(df.index).tz_localize(LOCAL_TIMEZONE)
        return df

    @logged_in
    def get_last_plant_stats(self, plant_id: str) -> dict:
        """returns the last known data point for the plant"""
        plant_data_df = self.get_plant_stats(plant_id=plant_id)
        if len(plant_data_df) > 0:
            plant_data_dic= plant_data_df.iloc[-1].to_dict()  # get latest entry
            metrics = {}
            for key, value in plant_data_dic.items():
                metrics[key] = Metric(
                            parent=plant_id,
                            id=key,  # id is not unique accross devices
                            name=key,
                            unit="kW",
                            value=value,
                        )
            return metrics
        else:
            # no data available yet TODO
            return None

    @logged_in
    def get_device_stats(self, device_id: str, return_resp=False) -> dict:
        """Retrieves the all current metrics for a certain device.
        :param device_id: The device's id
        :type plant_id: str
        :return: _description_
        """
        r = self._session.get(
            url=f"https://{self._huawei_subdomain}.fusionsolar.huawei.com/rest/pvms/web/device/v1/device-signals",
            params={"deviceDn": device_id, "_": round(time.time() * 1000),},
        )
        r.raise_for_status()
        if return_resp:
            return r
        device_stats = r.json()

        if not device_stats["success"] or not "data" in device_stats:
            raise FusionSolarException(
                f"Failed to retrieve plant status for {device_id}"
            )

        metrics = {}
        for parameter in r.json()["data"]:
            if set(("name", "value")) <= parameter.keys():
                if (parameter["name"] != "") and (parameter["value"] != ""):
                    metrics[parameter["name"]] = Metric(
                        parent=device_id,
                        id=parameter["id"],  # id is not unique accross devices
                        name=parameter["name"],
                        unit=parameter["unit"],
                        value=parameter["value"],
                    )

        return metrics


class Plant:
    def __init__(self, client, parent, id, name) -> None:
        self.client = client
        self.parent = parent
        self.id = id
        self.name = name

    def get_plant_flow(self, **kwargs) -> dict:
        return self.client.get_plant_flow(self.id, **kwargs)

    def get_plant_report(self, **kwargs) -> pandas.DataFrame:
        return self.client.get_plant_stats(self.id, **kwargs)

    def get_plant_stats(self, **kwargs) -> pandas.DataFrame:
        return self.client.get_plant_stats(self.id, **kwargs)

    def get_last_plant_stats(self, **kwargs) -> dict:
        return self.client.get_last_plant_stats(self.id, **kwargs)

    def get_devices(self, **kwargs) -> list:
        return self.client.get_devices(self.id, **kwargs)


class Device:
    def __init__(self, client, parent, id, name, type) -> None:
        self.client = client
        self.parent = parent
        self.id = id
        self.name = name
        self.type = type

    def get_device_stats(self, **kwargs) -> dict:
        return self.client.get_device_stats(self.id, **kwargs)


class Metric:
    def __init__(self, parent, id, name, unit, value) -> None:
        self.parent = parent
        self.id = id
        self.name = name
        self.unit = unit
        self.value = value
