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

from multiprocessing import Event, Queue
from subprocess import PIPE, Popen
from queue import Empty
import threading
import math
import time
import sys
import logging
logger = logging.getLogger(__name__)
logger.level = logging.DEBUG
logger.addHandler(logging.StreamHandler(sys.stdout))


class Airtime(object):
    """ Calculates the Airtime / TXTIME of an given frame.
    It doesn't include idle times like DIFS, SIFS, Signal Extension - it's just the packet duration.
    All times are noted in us (microseconds) and rates are noted in MBit/s.

    FIXME: For 11n rates there is no differentiation between mixted mode and greenfield mode. So for greenfield
    frames the calculation is 4us too long.


    Code is inspired by FreeBSD's ath_hal and Linux mac80211 util.c See
    https://github.com/freebsd/freebsd/blob/master/sys/dev/ath/ath_hal/ah.c
    http://lxr.free-electrons.com/source/net/mac80211/util.c?v=3.19#L110
    ath9k/xmit.c:ath_buf_set_rate(), ath9k/xmit.c:ath_pkt_duration() ath9k/xmit.c:ath9k_hw_computetxtime()

    All references are referring to from:  IEEE Std 802.11-2012

    Also helpful:
    802.11n: A Survival Guide Wi-Fi Above 100 Mbps By Matthew S. Gast Publisher: O'Reilly Media (PDF, EBook)
    http://www.ni.com/tutorial/7131/en/
    http://blog.nettraptor.net/?p=51
    """

    PHY_UNKNOWN, PHY_CCK, PHY_OFDM, PHY_DSSSOFDM = range(4)  # missing: ODFM_HALF, OFDM_QUARTER, TURBO, PBCC
    phy_types = (PHY_UNKNOWN, PHY_CCK, PHY_OFDM, PHY_DSSSOFDM)
    rate_11bg_cck = (1, 2, 5.5, 11)  # ERP-DSSS: 1 and 2, ERP-CCK: 5.5 and 11
    # rate_11b_pbcc = (22)  # Not supported yet - ERP-PBCC: 5.5, 11, 22, and 33
    rate_11g_ofdm = (6, 9, 12, 18, 24, 36, 48, 54)  # ERP-OFDM: 6, 9, 12, 18, 24, 36, 48, and 54
                                                    # DSSS-OFDM: 6, 9, 12, 18, 24, 36, 48, and 54

    # 802.11b/g constants
    CCK_SIFS_TIME = 10
    DSSS_PREAMBLE_BITS = 144  # 128 Sync + 16 SFD
    DSSS_PLCP_BITS = 48  # 8 Signal + 8 Service + 16 Lenght + 16 CRC
    # 802.11a/g constants
    OFDM_PREAMBLE_TIME = 8+8  # See Table 18-4 and 18-5, section 18.3.2.3
    OFDM_PREAMBLE_SYNC_TIME = 8  # See 19.3.2.5
    OFDM_PREAMBLE_SIGNAL_TIME = 4  # See 19.3.2.5
    OFDM_SERVICE_BITS = 16  # See 19.3.2.5
    OFDM_PAD_BITS = 6  # See 19.3.2.5
    OFDM_SYMBOL_TIME_GI = 4  # [us]
    OFDM_SYMBOL_TIME_SGI = 3.6  # [us]

    # 802.11n constants
    HT_L_STF = 8  # Non-HT Legacy Short Training Field
    HT_L_LTF = 8  # Non-HT Legacy Long Training Field
    HT_L_SIG = 4  # Non-HT Legacy Signal Field
    HT_SIG = 8  # High Throughput Signal Field
    HT_STF = 4  # High Throughput Short Training Field
    HT_LTF = 4  # High Throughput Long Training Field

    ht20_Ndbps = [  # Table 20-30 MCS parameters for mandatory 20 MHz  N_SS=1-4 N_ES=1
        26, 52, 78, 104, 156, 208, 234, 260,
        52, 104, 156, 208, 312, 416, 468, 520,
        78, 156, 234, 312, 468, 624, 702, 780,
        104, 208, 312, 416, 624, 832, 936, 1040
    ]
    ht40_Ncbps = [  # Table 20-34 MCS parameters for optional 40 MHz N_SS=1-4, N_ES=1
        54, 108, 162, 216, 324, 432, 486, 540,
        108, 216, 324, 432, 648, 864, 972, 1080,
        162, 324, 486, 648, 972, 1296, 1458, 1620,
        216, 432, 648, 864, 1296, 1728, 1944, 2160
    ]

    @staticmethod
    def mcs_to_streams(mcs):
        if mcs < 8:
            return 1
        elif mcs < 16:
            return 2
        elif mcs < 24:
            return 3
        elif mcs < 32:
            return 4
        else:
            raise Exception("unsupported mcs: %d" % mcs)

    @staticmethod
    def streams_2_N_LTF(streams):  # See Table 20-13 and Table 20-14, section 20.3.9.4.6, p. 1704
        num_ltf = [1, 3, 6, 8]
        return num_ltf[streams]

    @staticmethod
    def computetxtime(frame_len, phy_type, rate, short_preamble, is_2GHz=True, include_SIFS=False): # FIXME kick unused
        if not(type(frame_len) is int
                and phy_type in Airtime.phy_types
                and type(rate) is float
                and (rate in Airtime.rate_11bg_cck or rate in Airtime.rate_11g_ofdm)
                and type(short_preamble) is bool
                and type(include_SIFS) is bool
                ):
            raise Exception("Invalid parameter type")

        frame_bits = frame_len * 8
        N_DBPS = 4 * rate
        # DSSS, CCK, etc. See IEEE802.11-2012 Section 17.3.4
        if phy_type == Airtime.PHY_CCK:
            tx_time = Airtime.DSSS_PREAMBLE_BITS + Airtime.DSSS_PLCP_BITS
            if short_preamble and rate != 1:
                tx_time /= 2
            tx_time += math.ceil(frame_bits / rate)
        # DSSS-OFDM / Wireshark calls this "Dynamic CCK-OFDM" ? - IEEE802.11-201219.8.3.4 DSSS-OFDM TXTIME calculations
        elif phy_type == Airtime.PHY_DSSSOFDM:
            tx_time = Airtime.DSSS_PREAMBLE_BITS + Airtime.DSSS_PLCP_BITS
            if short_preamble:
                tx_time /= 2
            tx_time += Airtime.OFDM_PREAMBLE_SYNC_TIME + Airtime.OFDM_PREAMBLE_SIGNAL_TIME
            tx_time += 4 * math.ceil(Airtime.OFDM_SERVICE_BITS + frame_bits + Airtime.OFDM_PAD_BITS / N_DBPS)
            #tx_time += 6  # omit OFDM SignalExtension
        # OFDM # See IEEE802.11-2012 formula 18-29
        elif phy_type == Airtime.PHY_OFDM:
            tx_time = Airtime.OFDM_PREAMBLE_TIME + Airtime.OFDM_PREAMBLE_SIGNAL_TIME
            tx_time += 4 * math.ceil((Airtime.OFDM_SERVICE_BITS + frame_bits + Airtime.OFDM_PAD_BITS) / N_DBPS)
        else:
            raise Exception("Unsupported phy_type: %d" % phy_type)

        return int(tx_time)

    @staticmethod
    def computedur_ht(frame_len, mcs_index, is_ht40, is_shortGI):
        if not(type(frame_len) is int
                and type(mcs_index) is int
                and type(is_ht40) is bool
                and type(is_shortGI) is bool
                ):
            raise Exception("Invalid parameter type")

        if is_ht40:
            N_DBPS = Airtime.ht40_Ncbps[mcs_index]
        else:
            N_DBPS = Airtime.ht20_Ndbps[mcs_index]

        frame_bits = frame_len * 8
        payload_bits = Airtime.OFDM_SERVICE_BITS + frame_bits + Airtime.OFDM_PAD_BITS  # this assumes ES=1
        num_payloadsymbols = math.ceil(payload_bits / N_DBPS)  # this assumes no STBC is used
        if is_shortGI:
            tx_time_payload = num_payloadsymbols * Airtime.OFDM_SYMBOL_TIME_SGI
        else:
            tx_time_payload = num_payloadsymbols * Airtime.OFDM_SYMBOL_TIME_GI
        ht_preamble_fix = Airtime.HT_L_STF + Airtime.HT_L_LTF + Airtime.HT_L_SIG + Airtime.HT_SIG
        ht_preamble_var = Airtime.HT_LTF * Airtime.streams_2_N_LTF(Airtime.mcs_to_streams(mcs_index))
        tx_time = ht_preamble_fix + ht_preamble_var + tx_time_payload
        return tx_time

    @staticmethod
    def tshark_output_parser(data_type, tshark_output):
        fields = tshark_output.split('\t')
        try:
            tsf = int(fields[0])
            frame_len = int(fields[1])
            mcs_info = fields[2]
            ant_pwrs = fields[3].split(",")
            ant_pwr = math.ceil((int(ant_pwrs[0]) + int(ant_pwrs[1])) / 2)  # FIXME: good idea?
            freq = fields[4]
        except Exception as e:
            #logger.warn("Parse error in line from tshark: %s on line '%s'" % (e, tshark_output))  # FIXME: very often no pwr info: 8305191	1475	1		57,8	0	1	0	0	1	0
            return

        if data_type == "b/g":
            # try to parse b/g information
            if mcs_info == "0,0" or mcs_info == "0":  # tshark gives both :(
                rate = float(fields[5].replace(",", "."))
                is_cck = int(fields[6])
                is_dynamic = int(fields[7])
                is_ofdm = int(fields[8])
                have_short_preamble = bool(int(fields[9]))
                is_2GHz = int(fields[10])
                is_5GHz = int(fields[11])
                is_fcs_bad = bool(int(fields[12]))
                # simple plausibility checks:
                if is_cck + is_dynamic + is_ofdm != 1:
                    logger.warning("Malformed line from tshark detected: %s" % tshark_output)
                    return
                if is_2GHz + is_5GHz != 1:
                    logger.warning("Malformed line from tshark detected: %s" % tshark_output)
                    return
                if bool(is_cck):
                    phy = Airtime.PHY_CCK
                elif bool(is_ofdm):
                    phy = Airtime.PHY_OFDM
                elif bool(is_dynamic):
                    phy = Airtime.PHY_DSSSOFDM
                else:
                    logger.warning("Packet at tsf=%d has unknown modulation type!" % tsf)
                    return

                tx_dur = Airtime.computetxtime(
                        frame_len=frame_len, phy_type=phy, rate=rate,
                        short_preamble=have_short_preamble, is_2GHz=bool(is_2GHz)
                    )
                return tsf, tx_dur, ant_pwr, freq, is_fcs_bad, "BG"
            else:
                pass  # ignore data from the wrong source type

        elif data_type == "n":
            if mcs_info == "1,0" or mcs_info == "1":  # tshark gives both :(
                mcs_index = int(fields[5])
                is_ht40 = bool(int(fields[6]))
                is_shortGI = bool(int(fields[7]))
                is_fcs_bad = bool(int(fields[8]))
                tx_dur = Airtime.computedur_ht(
                        frame_len=frame_len, mcs_index=mcs_index,
                        is_ht40=is_ht40, is_shortGI=is_shortGI)
                return tsf, tx_dur, ant_pwr, freq, is_fcs_bad, "N", is_shortGI
            else:
                pass  # ignore data from the wrong source type
        else:
            logger.warning("data type %s is unknown" % data_type)
        return


class ReaderThread(object):

    def __init__(self, cmd, output_queue):
        self._cmd = cmd
        self.output_queue = output_queue
        self._process_to_read = None
        self._reader_thread = None
        self._exit_flag = Event()

    def start(self):
        if self._reader_thread is None:
            self._exit_flag.clear()
            logger.debug("start external process: '%s'" % self._cmd)
            self._process_to_read = Popen(self._cmd, stdout=PIPE, bufsize=1, close_fds=True, shell=True)
            self._reader_thread = threading.Thread(target=self._read, args=())
            self._reader_thread.start()

    def stop(self):
        if self._reader_thread is not None:
            self._exit_flag.set()
            self._reader_thread.join()
            self._reader_thread = None

    def _read(self):
        # reading non-blocking from pipe is is tricky, see
        # http://stackoverflow.com/questions/375427/non-blocking-read-on-a-subprocess-pipe-in-python
        line = None
        buf = ''
        while not self._exit_flag.is_set():
            # read char-wise
            ch = str(self._process_to_read.stdout.read(1), encoding="utf-8")
            if ch == '' and self._process_to_read.poll() is not None:
                time.sleep(0.1)
                continue
            if ch != '':
                buf += ch
            if ch == '\n':
                line = buf
                buf = ''
            if not line:
                continue
            self.output_queue.put((line,))
            line = None
        # tear down
        self._process_to_read.stdout.close()


class AirtimeCalculator(object):

    def __init__(self, monitor_interface, output_queue):
        self.output_queue = output_queue
        # Unfortunately tshark stops packet parsing, if field unknown/not set.
        # Therefore we need to setup 2 reader, one for 11n signals and one for 11b/g signals :(
        airtime_N_cmd = "tshark -l -i %s -T fields -e radiotap.mactime -e frame.len -e radiotap.present.mcs " \
                        "-e radiotap.dbm_antsignal -e radiotap.channel.freq -e radiotap.mcs.index -e radiotap.mcs.bw " \
                        "-e radiotap.mcs.gi -e radiotap.flags.badfcs" % monitor_interface

        airtime_BG_cmd = "tshark -l -i %s -T fields -e radiotap.mactime -e frame.len -e radiotap.present.mcs " \
                         "-e radiotap.dbm_antsignal -e radiotap.channel.freq -e radiotap.datarate " \
                         "-e radiotap.channel.flags.cck -e radiotap.channel.flags.dynamic -e radiotap.channel.flags.ofdm " \
                         "-e radiotap.flags.preamble -e radiotap.channel.flags.2ghz -e radiotap.channel.flags.5ghz " \
                         "-e radiotap.flags.badfcs" % monitor_interface
        self._queue_N = Queue()
        self._queue_BG = Queue()
        self._reader_N = ReaderThread(cmd=airtime_N_cmd, output_queue=self._queue_N)
        self._reader_BG = ReaderThread(cmd=airtime_BG_cmd, output_queue=self._queue_BG)
        self._calculate_thread_exit = Event()
        self._calculate_thread = threading.Thread(target=self.calculate_airtime, args=())

    def start(self):
        self._calculate_thread_exit.clear()
        self._calculate_thread.start()
        self._reader_N.start()
        self._reader_BG.start()

    def stop(self):
        self._reader_N.stop()
        self._reader_BG.stop()
        self._calculate_thread_exit.set()
        # drain own queues
        for q in [self._queue_N, self._queue_BG]:
            while True:
                try:
                    q.get(block=False)
                except Empty:
                    break
        self._calculate_thread.join()

    def calculate_airtime(self):
        while not self._calculate_thread_exit.is_set():
            if self._queue_N.qsize() < 10 or self._queue_BG.qsize() < 10:
                time.sleep(0.2)
            for (q, t) in [(self._queue_N, "n"), (self._queue_BG, "b/g")]:  # (queue, type)
                try:
                    line = q.get(block=False)[0]
                    airtime = Airtime.tshark_output_parser(data_type=t, tshark_output=line)
                    if airtime is not None:
                        self.output_queue.put(airtime)
                except Empty:
                    pass
