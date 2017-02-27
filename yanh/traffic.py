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

from ostinato.core import ost_pb, DroneProxy
from ostinato.protocols.mac_pb2 import mac
import errno
import socket
import time
import os


class ConnectionRefusedException(Exception):
    pass


class TrafficGenerator(object):
    pass


class EthernetTrafficGenerator(TrafficGenerator):

    def __init__(self, drone_hostname, drone_ifname, src_mac, dest_mac):

        self.drone_ifname = drone_ifname
        self.src_mac = src_mac
        self.dest_mac = dest_mac
        self.stream_id = 0
        self.streams = []

        self.drone = DroneProxy(drone_hostname)
        try:
            self.drone.connect()
        except socket.error, e:
            if e[0] == errno.ECONNREFUSED:
                raise ConnectionRefusedException(
                    "Connection Refused - make sure that 'drone' is running at '%s' and the interface '%s' is up."
                % (drone_hostname, drone_ifname))
        time.sleep(1)
        self.tx_port = ost_pb.PortIdList()
        self.tx_port.port_id.add().id = self._get_portid_for_ifname(self.drone_ifname)

    def _get_portid_for_ifname(self, ifname):
        port_id_list = self.drone.getPortIdList()
        port_config_list = self.drone.getPortConfig(port_id_list)

        iface_found = False
        for port in port_config_list.port:
            # print('%2d %s (%s)' % (port.port_id.id, port.name, port.description))
            if port.name == self.drone_ifname:
                tx_port_id = port.port_id.id
                return tx_port_id
        if not iface_found:
            raise Exception("ERR: interface '%s' not found at '%s'" % (self.drone_ifname, self.drone.host))

    def _get_next_streamid(self):
        self.stream_id += 1
        return self.stream_id

    def add_stream(self, frame_len, num_packets, packets_per_sec=0):  # pps=0 -> max speed

        stream_id = ost_pb.StreamIdList()
        stream_id.port_id.CopyFrom(self.tx_port.port_id[0])
        stream_id.stream_id.add().id = self._get_next_streamid()
        self.drone.addStream(stream_id)
        self.streams.append(stream_id)

        stream_cfg = ost_pb.StreamConfigList()
        stream_cfg.port_id.CopyFrom(self.tx_port.port_id[0])

        s = stream_cfg.stream.add()
        # s.core.name = '1k_frames'
        s.stream_id.id = stream_id.stream_id[0].id
        s.control.num_packets = num_packets
        s.control.packets_per_sec = packets_per_sec
        s.control.next = ost_pb.StreamControl.e_nw_stop
        s.core.is_enabled = True
        s.core.frame_len = frame_len

        # setup stream protocols
        p = s.protocol.add()
        p.protocol_id.id = ost_pb.Protocol.kMacFieldNumber
        p.Extensions[mac].dst_mac = int(self.dest_mac.replace(":", ""), 16)
        p.Extensions[mac].src_mac = int(self.src_mac.replace(":", ""), 16)

        self.drone.modifyStream(stream_cfg)

    def start(self):
        self.drone.startTransmit(self.tx_port)

    def pause(self):
        self.drone.stopTransmit(self.tx_port)

    def stop(self):
        self.drone.stopTransmit(self.tx_port)
        for stream_id in self.streams:
            self.drone.deleteStream(stream_id)
        self.drone.disconnect()
