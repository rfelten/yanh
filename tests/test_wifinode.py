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
from multiprocessing import Queue
from Queue import Empty
import unittest
import time
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'yanh'))
from wifinode import AP, STA

INTERFACE_STA = "wlx10feed1465e3"
INTERFACE_AP = "wlxf4f26d0ec262"


class TestAP(unittest.TestCase):

    def setUp(self):
        self.ap = AP(interface=INTERFACE_AP)

    def tearDown(self):
        self.ap.stop()

    def test_start_stop(self):
        self.assertEqual(self.ap._pid, -1)
        self.ap.start()

        self.assertNotEqual(self.ap._pid, -1)
        time.sleep(1)

        self.ap.stop()
        self.assertEqual(self.ap._pid, -1)


class TestStation(unittest.TestCase):

    def setUp(self):
        self.ap = AP(interface=INTERFACE_AP)
        self.ap.start()

    def tearDown(self):
        self.sta.disconnect()
        self.ap.stop()

    def test_connection(self):
        self.sta = STA(interface=INTERFACE_STA)
        self.sta.connect_to(ssid=self.ap.config['ssid'])
        station_dump = self.sta.get_station_dump()
        self.assertNotEqual(station_dump, '')


class TestMonitor(unittest.TestCase):

    def setUp(self):
        self.ap = AP(interface=INTERFACE_AP)
        self.ap.start()
        self.sta = STA(interface=INTERFACE_STA)

    def tearDown(self):
        self.sta.stop()
        self.ap.stop()

    def test_monitors(self):
        def cnt_monitors(moni_names):
            monitors_found = 0
            ifconfig_stdout = subprocess.check_output(["ifconfig", "-a"]).decode('UTF-8')
            for line in ifconfig_stdout.split('\n'):
                for ifname in moni_names:
                    if ifname in line:
                        monitors_found += 1
            return monitors_found

        self.ap.attach_monitor()
        self.sta.attach_monitor()
        moni_names = [self.ap.monitor_ifname, self.sta.monitor_ifname]
        self.assertEqual(cnt_monitors(moni_names), 2)

        self.ap.remove_monitor()
        self.sta.remove_monitor()
        self.assertEqual(cnt_monitors(moni_names), 0)


class TestPacketCapture(unittest.TestCase):

    def setUp(self):
        self.ap = AP(interface=INTERFACE_AP)
        self.ap.start()
        self.sta = STA(interface=INTERFACE_STA)

    def tearDown(self):
        self.sta.stop()
        self.ap.stop()

    def test_capture(self):
        self.ap.start_dump()
        self.sta.start_dump()
        self.sta.connect_to(ssid=self.ap.config['ssid'])
        time.sleep(10)  # capture packets for 10s
        self.assertTrue(os.path.isfile(self.ap.dump_filename))
        self.assertTrue(os.path.isfile(self.sta.dump_filename))
        self.ap.delete_dump()
        self.sta.delete_dump()


class TestAirtime(unittest.TestCase):

    def setUp(self):
        self.sta = STA(interface=INTERFACE_STA)

    def test_airtime(self):
        output = Queue()
        self.sta.start_airtime_calculation(output_queue=output)
        time.sleep(5)
        self.assertTrue(output.qsize() > 0)  # FIXME: any better idea?
        #while True:
        #    try:
        #        print(output.get(block=False))
        #    except Empty:
        #        break

    def tearDown(self):
        self.sta.stop()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        for arg in sys.argv[1:3]:
            print (arg)
            node_type, interface = arg.split("=")
            if node_type is "ap":
                INTERFACE_AP = interface
            if node_type is "sta":
                INTERFACE_STA = interface

    sys.argv = [sys.argv[0]]  # otherwise we confuse unittests
    unittest.main()
