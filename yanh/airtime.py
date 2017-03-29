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
    """ Calculates the Airtime / TX Time of an given frame.
    It doesn't include slot, ACK, RTS, etc timing - it's just the packet duration.

    Code based on FreeBSD's ath_hal and Linux mac80211 util.c See
    https://github.com/freebsd/freebsd/blob/master/sys/dev/ath/ath_hal/ah.c
    http://lxr.free-electrons.com/source/net/mac80211/util.c?v=3.19#L110
    ath9k/xmit.c:ath_buf_set_rate(), ath9k/xmit.c:ath_pkt_duration() ath9k/xmit.c:ath9k_hw_computetxtime()

    Useful resources:
    IEEE Std 802.11-2007 - Part 11: Wireless LAN Medium Access Control (MAC) and Physical Layer (PHY) Specifications
    " 20.3.6 Timing-related parameters ff
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
    CCK_PREAMBLE_BITS = 144  # 128 Sync + 16 SFD
    CCK_PLCP_BITS = 48  # 8 Signal + 8 Service + 16 Lenght + 16 CRC

    OFDM_SIFS_TIME = 16  # Table 18-17 - OFDM PHY characteristics
    OFDM_PREAMBLE_TIME = 20  #[us] 16 Preamble + 4 Signal 18.3.2.4 Timing related parameters
    OFDM_PLCP_BITS = 22  # 11g: ? 24 =4 Rate + 1 Res. + 12 Lenght + 1 Parity + 6 Tail  FIXME: name is wrong, is 16 ServiceBits +6 PadBits =22
    # ODFM_PLCP_BITS_11a # ? 40 =  4 Rate + 1 Reserved + 12 Length + 1 Parity + 6 Tail + 16 Sertcide
    OFDM_SYMBOL_TIME = 4  # [us]

    # 802.11n constants
    HT_L_STF = 8  # Non-HT Legacy Short Training Field
    HT_L_LTF = 8  # Non-HT Legacy Long Training Field
    HT_L_SIG = 4  # Non-HT Legacy Signal Field
    HT_SIG = 8  # High Throughput Signal Field
    HT_STF = 4  # High Throughput Short Training Field

    @staticmethod
    def HT_LTF(n):  # High Throughput Long Training Field # FIXME, worng:  see Table 20-13—Number of HT-DLTFs required for data space-time streams
        return n * 4

    ht20_bps = [
        26, 52, 78, 104, 156, 208, 234, 260,
        52, 104, 156, 208, 312, 416, 468, 520,
        78, 156, 234, 312, 468, 624, 702, 780,
        104, 208, 312, 416, 624, 832, 936, 1040
    ]
    ht40_bps = [
        54, 108, 162, 216, 324, 432, 486, 540,
        108, 216, 324, 432, 648, 864, 972, 1080,
        162, 324, 486, 648, 972, 1296, 1458, 1620,
        216, 432, 648, 864, 1296, 1728, 1944, 2160
    ]

    @staticmethod
    def div_and_ceil(numerator, denominator):
        """C hacker style integer division with ceil, from macro:
        #define	 howmany(x, y)	(((x)+((y)-1))/(y))"""
        return (numerator + (denominator - 1)) // denominator

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
            raise Exception("unknown mcs: %d" % mcs)

    @staticmethod
    def computetxtime(frame_len, phy_type, rate, short_preamble, is_2GHz=True, include_SIFS=False):
        if not(type(frame_len) is int
                and phy_type in Airtime.phy_types
                and type(rate) is float
                and (rate in Airtime.rate_11bg_cck or rate in Airtime.rate_11g_ofdm)
                and type(short_preamble) is bool
                and type(include_SIFS) is bool
                ):
            raise Exception("Invalid parameter type")

        frame_bits = frame_len * 8
        tx_time = 0

        if phy_type == Airtime.PHY_CCK:
            phy_time = Airtime.CCK_PREAMBLE_BITS + Airtime.CCK_PLCP_BITS

            if short_preamble and rate != 1:  # short preamble not possible with rate = 1Mbit/s
                phy_time /= 2  # Preamble 144bits->72bits@1Mbit/s + PLCP 48bits: @1Mbit/s->2@Mbit/s == divide by 2
            num_bits = frame_bits
            tx_time = phy_time + (num_bits / rate)  # FIXME: ceil here
            if include_SIFS:
                tx_time += Airtime.CCK_SIFS_TIME

        elif phy_type == Airtime.PHY_DSSSOFDM:  # DSSS-OFDM / Wireshark calls this "Dynamic CCK-OFDM" ?
                # See 19.8.3.4 DSSS-OFDM TXTIME calculations
                # TXTIME = PreambleLengthDSSS (144/72us) + PLCPHeaderTimeDSSS (48/24us)
                #   + PreambleLengthOFDM (8us) + PLCPSignalOFDM (4us)
                #   + 4 * Ceiling((PLCPServiceBits (16) + 8 * (NumberOfOctets[Framelen]) + PadBits (6bits) / N DBPS ) + SignalExtension (6us)
                phy_time = 144 + 42  # FIXME: typo 42 -> 48
                if short_preamble:
                    phy_time /= 2
                phy_time += 8 + 4
                phy_time += 4 * math.ceil(16 + frame_bits + 6) / (4*rate)  # N_DBPS, see Table 18-4 Modulation-dependent parameters
                phy_time += 6  # FIXME: do not add OFDM Signal Extension (its a idle time)
                tx_time = phy_time

        elif phy_type == Airtime.PHY_OFDM:  # ERP-OFDM, DSSS-OFDM, ?
            # 19.3.3.4.2 Overview of the DSSS-OFDM PLCP PSDU encoding process
            # 19.8.3 TXTIME / 19.3.2.6 DSSS-OFDM PLCP length field calculation (2012)
            if is_2GHz:
                # 802.11g-only (ERP-OFDM TXTIME calculations)

                # FIXME: Doublecheck in IEEE801.11-2007 + clarify  # FIXME: use -2012
                # 18.4.3 OFDM TXTIME calculation
                # TXTIME = T_PREAMBLE + T_SIGNAL + T_SYM * N_SYM
                # Equ. 18-11
                # N_SYM = Ceiling ((16 + 8 * LENGTH + 6)/N DBPS )
                ## No "Signal Extension" ?
                ## v.s.
                # 19.8.3.2 ERP-OFDM TXTIME calculations
                # TXTIME = T PREAMBLE + T SIGNAL + T SYM * Ceiling ((16 + 8 * LENGTH + 6)/ N DBPS ) + Signal Extension
                ## v.s
                # 17.3.4 High Rate TXTIME calculation
                # TXTIME = PreambleLength + PLCPHeaderTime + Ceiling(((LENGTH+PBCC) * 8) / DATARATE)
                # TXTIME = TPREAMBLE + TSIGNAL + TSYM * Ceiling ((16 + 8 * LENGTH + 6)/NDBPS)

                bits_per_symbol = Airtime.OFDM_SYMBOL_TIME * rate
                num_bits = Airtime.OFDM_PLCP_BITS + frame_bits
                num_symbols = Airtime.div_and_ceil(num_bits, bits_per_symbol)
                tx_time = Airtime.OFDM_PREAMBLE_TIME + num_symbols * Airtime.OFDM_SYMBOL_TIME

                # Alternative calculation, closer to formula in IEEE802.11-2007
                # tx_time_std = 16 + 4 + 4* math.ceil( (16 + frame_len * 8 + 6) / (4 * rate)) #4x rate = N_DBPS
                # if tx_time_std != tx_time:
                #    raise Exception("tx_time_std != tx_time:", tx_time_std, tx_time)  # not occurred yet

                if include_SIFS:
                    tx_time += Airtime.OFDM_SIFS_TIME
            else:
                raise Exception("Sry, 5GHz 11g PHY not supported yet...")  # FIXME
        else:
            logger.warn("Can't compute airtime for frame_len=%d, phy_type=%d, rate=%d, short_preamble=%s" %
                        (frame_len, phy_type, rate, short_preamble))
        return int(math.ceil(tx_time))

    @staticmethod
    def computedur_ht(frame_len, mcs_index, num_streams, is_ht40, is_shortGI):
        if not(type(frame_len) is int
                and type(mcs_index) is int
                and type(num_streams) is int
                and type(is_ht40) is bool
                and type(is_shortGI) is bool
                ):
            raise Exception("Invalid parameter type")
        if is_ht40:
            bits_per_symbol = Airtime.ht40_bps[mcs_index & 0x1f]
        else:
            bits_per_symbol = Airtime.ht20_bps[mcs_index & 0x1f]
        num_bits = Airtime.OFDM_PLCP_BITS + (frame_len * 8)
        num_symbols = Airtime.div_and_ceil(num_bits, bits_per_symbol)
        if is_shortGI: # Short Guard Interval (SGI)
            tx_time = ((num_symbols * 18) + 4) // 5  # 3.6us (OFDM Data Symbol [3.2us] + short GI [0.4us])
            # tx_time = math.ceil(num_symbols * 3.6)
        else:
            tx_time = num_symbols * Airtime.OFDM_SYMBOL_TIME  # 4 us (OFDM Data Symbol [3.2us] + long GI [0.8us])
        return tx_time + Airtime.HT_L_STF + Airtime.HT_L_LTF + \
            Airtime.HT_L_SIG + Airtime.HT_SIG + Airtime.HT_STF + Airtime.HT_LTF(num_streams)
            # FIXME: signal extnsions? see 20.4.3 TXTIME calculation
            # FIXME Table 20-12—Determining the number of space-time streams

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
                    logger.warn("Malformed line from tshark detected: %s" % tshark_output)
                    return
                if is_2GHz + is_5GHz != 1:
                    logger.warn("Malformed line from tshark detected: %s" % tshark_output)
                    return
                if bool(is_cck):
                    phy = Airtime.PHY_CCK
                elif bool(is_ofdm):
                    phy = Airtime.PHY_OFDM
                elif bool(is_dynamic):
                    phy = Airtime.PHY_DSSSOFDM
                else:
                    logger.warn("Packet at tsf=%d has unknown modulation type!" % tsf)
                    return

                tx_dur = Airtime.computetxtime(
                        frame_len=frame_len, phy_type=phy, rate=rate,
                        short_preamble=have_short_preamble, is_2GHz=bool(is_2GHz)
                    )
                return tsf, tx_dur, ant_pwr, freq, is_fcs_bad, "BG"
            else:
                pass  # ignore data from the wrong source type

        elif data_type == "n":
            """airtime_N_cmd = "tshark -i %s -T fields -e radiotap.mactime -e frame.len -e radiotap.present.mcs " \
                        "-e radiotap.dbm_antsignal -e radiotap.channel.freq -e radiotap.mcs.index -e radiotap.mcs.bw " \
                        "-e radiotap.mcs.gi -e radiotap.flags.badfcs" % monitor_interface
            Example: '1318337149\t128\t0,0\t-58,-58\t2412\t\t\t\t0\n' """
            if mcs_info == "1,0" or mcs_info == "1":  # tshark gives both :(
                mcs_index = int(fields[5])
                streams = Airtime.mcs_to_streams(mcs_index)
                is_ht40 = bool(int(fields[6]))
                is_shortGI = bool(int(fields[7]))
                is_fcs_bad = bool(int(fields[8]))
                tx_dur = Airtime.computedur_ht(
                        frame_len=frame_len, num_streams=streams, mcs_index=mcs_index,
                        is_ht40=is_ht40, is_shortGI=is_shortGI)
                return tsf, tx_dur, ant_pwr, freq, is_fcs_bad, "N"
            else:
                pass  # ignore data from the wrong source type
        else:
            logger.warn("data type %s is unknown" % data_type)
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
