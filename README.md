# YANH - Yet Another (Python) Network Helper

## What is this?
This is a Python 3 module to simplify things related to Wi-Fi analysis setups. It helps to
setup APs (hostapd based), connect as STAtion, create traffic (using Ostinato) and create packet traces.

Also it provides to caluclate the TXTIME / airtime via the Airtime class.

## Installation

Depencencies:

- hostapd - $ sudo apt-get install hostapd
- ostinato <- only support python 2.7 :( - use precompiled drone + 
$ sudo apt-get install python-minimal python-setuptools python-pip
$ sudo pip install python-ostinato
- tshark - $ sudo apt-get install tshark 
- tshark v2.0+ is needed due to fieldnames like 'radiotap.channel.flags.5ghz',  https://www.wireshark.org/docs/dfref/r/radiotap.html
- $ sudo usermod -a -G wireshark $USER (need to  relogin afterwards)
- $ sudo setcap 'CAP_NET_RAW+eip CAP_NET_ADMIN+eip' /usr/bin/dumpcap (https://wiki.wireshark.org/CaptureSetup/CapturePrivileges )
- $ sudo chmod +s /usr/bin/dumpcap
- sudo 
uncommend:
```bash
$ sudo visudo
```
At the bottom of the file add:
```bash
<your username> ALL=(ALL) NOPASSWD: ALL
```

## Usage
- user needs to be have SUODers rigtjs
- manupulate ap.config[] before ap.start()
- run tests with arguments: (need to provide interfaces)
- "Connection Refused - make sure that 'drone' is running at '%s' and the interface '%s' is up."

```bash
$ tests/test_ap.py ap=wlxf4f26d0ec262 sta=wlx10feed1465e3
```
