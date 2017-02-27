# YANH - Yet Another (Python) Network Helper

## What is this?
This is a Python 2.7 module to simplify things related to Wi-Fi analysis setups. It helps to
setup APs (hostapd based), connect as STAtion, create traffic (using Ostinato) and create packet traces.

## Installation

Depencencies:

- hostapd
- ostinato <- only support python 2.7 :(
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

read http://stackoverflow.com/questions/25476648/how-to-read-cap-files-other-than-pyshark-that-is-faster-than-scapys-rdpcap