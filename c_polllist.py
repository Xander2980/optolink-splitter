'''
   Copyright 2025 philippoo66
   
   Licensed under the GNU GENERAL PUBLIC LICENSE, Version 3 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       https://www.gnu.org/licenses/gpl-3.0.html

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
'''

import os
import sys
import importlib
from typing import Any, Optional

from c_settings_adapter import settings
from logger_util import logger
import utils


class cPollList:
    def __init__(self):
        """
        Initialisiert die Klasse
        """
        self.items = []
        self.cycle_groups = {}
        self.num_items = 0
        self.module_date = "0"
        # datapoint metadata cache for /set topics
        self.datapoint_metadata = {}


    def make_list(self, reload: bool = False) -> bool:
        previous = self.__dict__.copy()
        self.items = []
        self.cycle_groups = {}
        self.num_items = 0
        self.module_date = "0"
        self.datapoint_metadata = {}
        try:
            # import module where poll list is taken from
            if(os.path.isfile("poll_list.py")):
                listmodule = importlib.import_module('poll_list')
            elif(os.path.isfile("homeassistant_poll_list.py")):
                listmodule = importlib.import_module('homeassistant_adapter')
            else:
                listmodule = importlib.import_module('settings_ini')
            
            # if reload is requested
            if reload:
                if(listmodule.__name__ == 'homeassistant_adapter'):
                    # the adapter imports homeassistant_poll_list, which stays cached and would ignore changes
                    ha_list = sys.modules.get('homeassistant_poll_list')
                    if ha_list is not None:
                        importlib.reload(ha_list)
                listmodule = importlib.reload(listmodule)
            
            # apply poll groups (if exists)
            foreign_groups = getattr(listmodule, "poll_groups", None)
            if foreign_groups is not None:
                self.cycle_groups = dict(foreign_groups)
            if any(type(cycle) is not int for cycle in self.cycle_groups.values()):
                raise ValueError("poll group cycles must be integers")
            # make 1-cycle group
            self.cycle_groups["*#1"] = 1

            # apply poll list
            #self.items = listmodule.poll_items
            for item in listmodule.poll_items:
                new_item = []
                if item[0] in self.cycle_groups:
                    # is cycle-grouped item
                    new_item.append(item[0])
                    new_item.extend(item[1:])
                elif isinstance(item[0], int):
                    # has numeric cycle
                    key = f"*#{item[0]}"
                    self.cycle_groups[key] = item[0]
                    new_item.append(key)
                    new_item.extend(item[1:])
                else:
                    # no cycle given - set 1
                    new_item.append("*#1")
                    new_item.extend(item[0:])
                
                # make tuple
                new_tuple = tuple(new_item)
                # append to poll items
                if (len(new_tuple) < 4 or not isinstance(new_tuple[1], str) or not new_tuple[1]
                        or type(new_tuple[2]) is not int or not 0 <= new_tuple[2] <= 0xFFFF
                        or type(new_tuple[3]) is not int or not 1 <= new_tuple[3] <= 255):
                    raise ValueError(f"Invalid poll item: {new_tuple!r}")
                self.items.append(new_tuple)

            self.num_items = len(self.items)
            self.module_date = utils.get_module_modified_datetime(listmodule)

            self.build_index()

            # apply poll interval if given
            foreign_interval = getattr(listmodule, 'poll_interval', None)
            if foreign_interval is not None:
                if type(foreign_interval) is not int or foreign_interval < -1:
                    raise ValueError("poll_interval must be an integer >= -1")
                settings.poll_interval = foreign_interval

            # infos
            logger.info(f"poll_list made, {self.num_items} items")
            return True
        except Exception:
            self.__dict__.update(previous)
            logger.exception("make_list")
            return False


    def set_pollcycle(self, group_key:str, value) -> bool:
        if group_key not in self.cycle_groups:
            return False
        try:
            cyc = int(value)
        except (TypeError, ValueError):
            return False
        self.cycle_groups[group_key] = cyc
        return True


    def build_index(self) -> None:
        index = {}
        for idx, item in enumerate(self.items):
            name, addr, length = item[1:4]
            if name in index:
                logger.warning(f"Duplicate poll datapoint name: {name}")
                continue
            bbfilter = item[4] if len(item) > 4 and isinstance(item[4], str) and item[4].lower().startswith("b:") else None
            offset = 1 if bbfilter else 0
            index[name] = {
                "addr": addr, "len": length, "bbfilter": bbfilter,
                "scale_type": item[4 + offset] if len(item) > 4 + offset else None,
                "signed": item[5 + offset] if len(item) > 5 + offset else False,
                "list_index": idx,
            }
        self.datapoint_metadata = index

    def find_datapoint_by_name(self, dpname: str) -> Optional[dict[str, Any]]:
        return self.datapoint_metadata.get(dpname)


# === for global use ================
poll_list = cPollList()

# poll_list.make_list()
# print(poll_list.num_items)
# for itm in poll_list.items:
#     print(itm)
