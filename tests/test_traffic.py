#!/usr/bin/env python2
# -*- coding: utf-8 -*-

#   This file is part of the yanh project.
#
#   Copyright (C) 2017 Robert Felten - https://github.com/rfelten/
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA

import subprocess
import unittest
import time
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'yanh'))
from wifinode import AP, STA
from traffic import EthernetTrafficGenerator, ConnectionRefusedException

INTERFACE_STA = "wlx10feed1465e3"
INTERFACE_AP = "wlxf4f26d0ec262"
DRONE_HOST = "127.0.0.1"


class TestEthernetTrafficGenerator(unittest.TestCase):

    def setUp(self):
        # create connection between wifi nodes
        self.ap = AP(interface=INTERFACE_AP)
        self.ap.start()
        self.ap.start_dump()
        self.sta = STA(interface=INTERFACE_STA)
        self.sta.start_dump()
        self.sta.connect_to(ssid=self.ap.config['ssid'])

        # create traffic STA->AP
        try:
            self.gen = EthernetTrafficGenerator(
                drone_hostname=DRONE_HOST,
                drone_ifname=self.sta.get_interface(),
                src_mac=self.sta.get_mac_addr(),
                dest_mac=self.ap.get_mac_addr(),
            )
        except ConnectionRefusedException:
            self.sta.stop()
            self.ap.stop()
            raise ConnectionRefusedException

    def tearDown(self):
        self.gen.stop()
        self.sta.stop()
        self.ap.stop()

    def test_traffic_gen(self):
        self.gen.add_stream(frame_len=1500, num_packets=1000, packets_per_sec=0)
        self.gen.start()
        time.sleep(50)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            key, val = arg.split("=")
            if key is "ap":
                INTERFACE_AP = val
            if key is "sta":
                INTERFACE_STA = val
            if key is "host":
                DRONE_HOST = val

    sys.argv = [sys.argv[0]]  # otherwise we confuse unittests
    unittest.main()
