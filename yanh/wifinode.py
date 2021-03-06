#!/usr/bin/env python3
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

import os
import sys
import time
import signal
import subprocess
from airtime import AirtimeCalculator
import logging
logger = logging.getLogger(__name__)
logger.level = logging.DEBUG
logger.addHandler(logging.StreamHandler(sys.stdout))


class Node(object):

    def __init__(self):
        self.interface = None
        self.monitor_ifname = None
        self.dump_filename = ""
        self._reader_process = None
        self._fn_cap_template = "/tmp/%s.pcapng"  # % self.monitor_ifname
        self._airtime_reader = None

    def attach_monitor(self):
        mon_if = self.interface[:10] + "mon"  # wlxf4f26d0ec262mon fails -> max 16 chars
        logger.info("attach monitor '%s' to interface '%s'" % (mon_if, self.interface))
        cmd = "sudo iw dev %s interface add %s type monitor flags fcsfail" % (self.interface, mon_if)
        logger.debug(cmd)
        os.system(cmd)
        cmd = "sudo ifconfig %s up" % mon_if
        os.system(cmd)
        self.monitor_ifname = mon_if
        time.sleep(0.5)

    def remove_monitor(self):
        if self.monitor_ifname is None:
            return
        logger.info("remove monitor '%s' from interface '%s'" % (self.monitor_ifname, self.interface))
        cmd = "sudo ifconfig %s down" % self.monitor_ifname
        os.system(cmd)
        cmd = "sudo iw dev %s del" % self.monitor_ifname
        logger.debug(cmd)
        os.system(cmd)
        self.monitor_ifname = None

    def start_dump(self, filename=None):
        if self._reader_process is not None:
            return
        if self.monitor_ifname is None:
            self.attach_monitor()
        if filename is None:
            output_file = self._fn_cap_template % self.monitor_ifname
        else:
            output_file = filename

        self.dump_filename = output_file
        # see http://stackoverflow.com/questions/4789837/how-to-terminate-a-python-subprocess-launched-with-shell-true
        tshark_cmd = "/usr/bin/tshark -s 100 -n -w %s -i %s" % (output_file, self.monitor_ifname)
        logger.debug(tshark_cmd)
        self._reader_process = subprocess.Popen(tshark_cmd, stdout=subprocess.PIPE,
                               shell=True, preexec_fn=os.setsid)
        time.sleep(0.5)

    def stop_dump(self):
        if self._reader_process is None:
            return
        # see http://stackoverflow.com/questions/4789837/how-to-terminate-a-python-subprocess-launched-with-shell-true
        os.killpg(os.getpgid(self._reader_process.pid), signal.SIGTERM)  # Send the signal to all the process groups
        os.killpg(os.getpgid(self._reader_process.pid), signal.SIGTERM)  # Send the signal to all the process groups

    def delete_dump(self):
        os.remove(self.dump_filename)

    def start_airtime_calculation(self, output_queue):
        if self._airtime_reader is not None:  # ready running
            return
        if self.monitor_ifname is None:
            self.attach_monitor()
        self._airtime_reader = AirtimeCalculator(monitor_interface=self.monitor_ifname, output_queue=output_queue)
        self._airtime_reader.start()

    def stop_airtime_calculation(self):
        if self._airtime_reader is not None:
            self._airtime_reader.stop()
            self._airtime_reader = None

    def get_mac_addr(self):
        ifconfig_stdout = subprocess.check_output(["ifconfig", "-a"]).decode('UTF-8')
        for line in ifconfig_stdout.split('\n'):
            if line.startswith(self.interface):
                return line.split("HWaddr ")[-1]
        return None

    def get_interface(self):
        return self.interface

    def stop(self):
        self.stop_dump()
        self.stop_airtime_calculation()
        self.remove_monitor()


class AP(Node):
    """ Wrapper for an hostapd AP"""

    hostapd_default_conf = """driver=nl80211
    ssid=unittest
    country_code=DE
    channel=6
    auth_algs=1
    hw_mode=g
    ieee80211n=1
    wmm_enabled=1
    ht_capab=[HT20]
    """

    def __init__(self, interface, hostapd_conf=None):
        super(AP, self).__init__()
        if hostapd_conf is None:
            hostapd_conf = self.hostapd_default_conf
        self.config = dict([(x[0], x[1]) for x in [line.strip().split("=") for line in hostapd_conf.split('\n')] if len(x) > 1])
        self.interface = self.config['interface'] = interface
        self._pid = -1
        self._fn_hostapdconf = "/tmp/yanh_hostapd.conf"
        self._fn_hosapdpid = "/tmp/yanh_hostapd.pid"
        self._fn_hosapdlog = "/tmp/yanh_hostapd.log"

    def start(self):
        # FIXME: test if there is a running hostapd instance (on this interface)?
        logger.info("start hostapd at interface '%s'" % self.interface)
        os.system("sudo nmcli radio wifi off")  # only needed if networkmanager is installed (default on ubuntu-desktop)
        os.system("sudo rfkill unblock wlan")
        os.system("sudo ifconfig %s up" % self.interface)  # need to be up
        time.sleep(0.5)  # wait for interface coming up
        with open(self._fn_hostapdconf, 'wt') as f:
            f.write('\n'.join(['%s=%s' % (key, value) for (key, value) in self.config.items()]) + '\n')
        f.close()
        cmd = "sudo hostapd -B -t -P %s -f %s %s " % (self._fn_hosapdpid, self._fn_hosapdlog, self._fn_hostapdconf)
        os.system(cmd)
        logger.debug(cmd)
        time.sleep(10)  # wait for hostapd coming up
        try:
            with open(self._fn_hosapdpid) as f:
                self._pid = int(f.read())
                logger.info("started hostapd with pid=%d. logfile: %s" % (self._pid, self._fn_hosapdlog))
            f.close()
        except OSError: # FileNotFoundError
            logger.error("failed to start hostapd. see logfile '%s' for more info" % self._fn_hosapdlog)

    def stop(self):
        if self._pid == -1:
            super(AP, self).stop()
            return
        logger.info("stop hostapd at interface '%s'" % self.interface)
        #os.kill(self._pid, signal.SIGTERM)  # PermissionError: [Errno 1] Operation not permitted
        os.system("sudo kill %d" % self._pid)
        # os.system("sudo ifconfig %s down" % self.interface) # bad idea if other stuff is running on this interface too
        # os.system("sudo nmcli radio wifi on")  # annoying
        self._pid = -1
        super(AP, self).stop()


class STA(Node):

    def __init__(self, interface):
        super(STA, self).__init__()
        self.interface = interface
        logger.info("setup station at interface '%s'" % self.interface)

    def __del__(self):
        self.disconnect()
        super(STA, self).stop()

    def connect_to(self, ssid):
        logger.info("connect interface '%s' to ssid '%s'" % (self.interface, ssid))
        cmd = "sudo ifconfig %s up" % self.interface
        os.system(cmd)
        time.sleep(0.5)
        cmd = "sudo iw dev %s connect %s" % (self.interface, ssid)
        os.system(cmd)
        logger.debug(cmd)
        time.sleep(2)  # need up to 2s

    def disconnect(self):
        logger.info("disconnect interface '%s' from ap" % self.interface)
        cmd = "sudo iw dev %s disconnect" % self.interface
        os.system(cmd)

    def get_station_dump(self):
        cmd = "iw dev %s station dump" % self.interface
        return subprocess.check_output(cmd.split(" ")).decode('UTF-8')
