"""住まいコンパス — Streamlit版 v05（表示地域名 + HOME'S / SUUMO取得・Supabase保存）
実行: streamlit run app.py
依存: streamlit>=1.50,<2 / folium>=0.18,<1 / streamlit-folium>=0.24,<1 / requests>=2.32,<3
実物件の家賃メッシュ表示。通勤は概算。公開情報取得はサイトの仕様・接続状況に依存。
"""
from __future__ import annotations
import csv
import os
import base64
import hashlib
import heapq
import io
import math
import uuid
import json
import re
import statistics
import time
import concurrent.futures
import threading
import urllib.robotparser
import unicodedata
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from html import escape
from urllib.parse import urlencode, urlparse, urljoin, parse_qsl, urlunparse

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

STATIONS = [{'name': '東京', 'lat': 35.6812, 'lng': 139.7671, 'region': '東京'},
 {'name': '品川', 'lat': 35.6285, 'lng': 139.7388, 'region': '東京'},
 {'name': '大崎', 'lat': 35.6197, 'lng': 139.7286, 'region': '東京'},
 {'name': '五反田', 'lat': 35.6264, 'lng': 139.7235, 'region': '東京'},
 {'name': '目黒', 'lat': 35.6339, 'lng': 139.7158, 'region': '東京'},
 {'name': '恵比寿', 'lat': 35.6467, 'lng': 139.7101, 'region': '東京'},
 {'name': '渋谷', 'lat': 35.658, 'lng': 139.7016, 'region': '東京'},
 {'name': '新宿', 'lat': 35.6909, 'lng': 139.7003, 'region': '東京'},
 {'name': '池袋', 'lat': 35.7295, 'lng': 139.7109, 'region': '東京'},
 {'name': '上野', 'lat': 35.7138, 'lng': 139.777, 'region': '東京'},
 {'name': '秋葉原', 'lat': 35.6984, 'lng': 139.7731, 'region': '東京'},
 {'name': '赤羽', 'lat': 35.778, 'lng': 139.7209, 'region': '東京'},
 {'name': '川口', 'lat': 35.8019, 'lng': 139.7175, 'region': '埼玉'},
 {'name': '浦和', 'lat': 35.8585, 'lng': 139.6571, 'region': '埼玉'},
 {'name': '大宮', 'lat': 35.9063, 'lng': 139.6238, 'region': '埼玉'},
 {'name': '大井町', 'lat': 35.6063, 'lng': 139.7346, 'region': '東京'},
 {'name': '蒲田', 'lat': 35.5625, 'lng': 139.716, 'region': '東京'},
 {'name': '川崎', 'lat': 35.5313, 'lng': 139.697, 'region': '神奈川'},
 {'name': '横浜', 'lat': 35.4662, 'lng': 139.622, 'region': '神奈川'},
 {'name': '武蔵小杉', 'lat': 35.575, 'lng': 139.6595, 'region': '神奈川'},
 {'name': '日吉', 'lat': 35.553, 'lng': 139.6468, 'region': '神奈川'},
 {'name': '自由が丘', 'lat': 35.6074, 'lng': 139.6685, 'region': '東京'},
 {'name': '中目黒', 'lat': 35.6443, 'lng': 139.699, 'region': '東京'},
 {'name': '中野', 'lat': 35.7058, 'lng': 139.6658, 'region': '東京'},
 {'name': '荻窪', 'lat': 35.7045, 'lng': 139.6201, 'region': '東京'},
 {'name': '吉祥寺', 'lat': 35.7032, 'lng': 139.5797, 'region': '東京'},
 {'name': '三鷹', 'lat': 35.7027, 'lng': 139.5603, 'region': '東京'},
 {'name': '国分寺', 'lat': 35.7001, 'lng': 139.4808, 'region': '東京'},
 {'name': '立川', 'lat': 35.6982, 'lng': 139.4137, 'region': '東京'},
 {'name': '錦糸町', 'lat': 35.696, 'lng': 139.814, 'region': '東京'},
 {'name': '新小岩', 'lat': 35.7169, 'lng': 139.8586, 'region': '東京'},
 {'name': '市川', 'lat': 35.7289, 'lng': 139.9084, 'region': '千葉'},
 {'name': '船橋', 'lat': 35.7017, 'lng': 139.985, 'region': '千葉'},
 {'name': '津田沼', 'lat': 35.6907, 'lng': 140.0207, 'region': '千葉'},
 {'name': '千葉', 'lat': 35.6134, 'lng': 140.1133, 'region': '千葉'},
 {'name': '北千住', 'lat': 35.7494, 'lng': 139.805, 'region': '東京'},
 {'name': '松戸', 'lat': 35.7847, 'lng': 139.9007, 'region': '千葉'},
 {'name': '柏', 'lat': 35.8622, 'lng': 139.9711, 'region': '千葉'},
 {'name': '二子玉川', 'lat': 35.6117, 'lng': 139.6267, 'region': '東京'},
 {'name': '溝の口', 'lat': 35.5998, 'lng': 139.6115, 'region': '神奈川'},
 {'name': '南浦和', 'lat': 35.8476, 'lng': 139.669, 'region': '埼玉'}]
LINES = [{'name': '山手線',
  'seq': ['東京', '品川', '大崎', '五反田', '目黒', '恵比寿', '渋谷', '新宿', '池袋', '上野', '秋葉原', '東京'],
  'rate': 136,
  'section': '新大久保 → 新宿',
  'period': '7:44〜8:44'},
 {'name': '京浜東北線',
  'seq': ['大宮', '浦和', '南浦和', '川口', '赤羽', '上野', '秋葉原', '東京', '品川', '大井町', '蒲田', '川崎', '横浜'],
  'rate': 158,
  'section': '川口 → 赤羽',
  'period': '7:20〜8:20'},
 {'name': '埼京線',
  'seq': ['大宮', '赤羽', '池袋', '新宿', '渋谷', '恵比寿', '大崎'],
  'rate': 167,
  'section': '板橋 → 池袋',
  'period': '7:51〜8:51'},
 {'name': '中央線快速',
  'seq': ['東京', '新宿', '中野', '荻窪', '吉祥寺', '三鷹', '国分寺', '立川'],
  'rate': 158,
  'section': '中野 → 新宿',
  'period': '7:35〜8:35'},
 {'name': '東海道線', 'seq': ['東京', '品川', '川崎', '横浜'], 'rate': 159, 'section': '川崎 → 品川', 'period': '7:39〜8:39'},
 {'name': '横須賀線',
  'seq': ['東京', '品川', '武蔵小杉', '横浜'],
  'rate': 138,
  'section': '武蔵小杉 → 西大井',
  'period': '7:26〜8:26'},
 {'name': '東横線',
  'seq': ['渋谷', '中目黒', '自由が丘', '武蔵小杉', '日吉', '横浜'],
  'rate': 124,
  'section': '祐天寺 → 中目黒',
  'period': '7:50〜8:50'},
 {'name': '総武線快速',
  'seq': ['東京', '錦糸町', '新小岩', '市川', '船橋', '津田沼', '千葉'],
  'rate': 160,
  'section': '新小岩 → 錦糸町',
  'period': '7:31〜8:31'},
 {'name': '常磐線快速',
  'seq': ['上野', '北千住', '松戸', '柏'],
  'rate': 142,
  'section': '三河島 → 日暮里',
  'period': '7:26〜8:26'},
 {'name': '南武線',
  'seq': ['川崎', '武蔵小杉', '溝の口', '立川'],
  'rate': 156,
  'section': '武蔵中原 → 武蔵小杉',
  'period': '7:30〜8:30'},
 {'name': '田園都市線', 'seq': ['渋谷', '二子玉川', '溝の口'], 'rate': 138, 'section': '池尻大橋 → 渋谷', 'period': '7:50〜8:50'}]
BY_NAME = {s['name']: s for s in STATIONS}
LAYOUTS = ('1LDK', '2LDK', '3LDK', '4LDK')
CROWD_SOURCE = 'https://www.mlit.go.jp/report/press/content/002015142.pdf'
CSV_FIELDS = ['id', 'name', 'station', 'layout', 'rent', 'management', 'common', 'url']
FACILITY_FILTERS = {'スーパー': '[shop=supermarket]', 'コンビニ': '[shop=convenience]',
                    '公園': '[leisure=park]', '病院': '[amenity~"hospital|clinic"]'}


def distance(a, b):
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat, dlng = lat2-lat1, math.radians(b['lng']-a['lng'])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
    return 6371*2*math.asin(math.sqrt(min(1, max(0, h))))


def build_graph():
    graph = {s['name']: [] for s in STATIONS}
    for i, line in enumerate(LINES):
        for a, b in zip(line['seq'], line['seq'][1:]):
            km = distance(BY_NAME[a], BY_NAME[b])*1.15
            graph[a].append((b, i, km, km/40*60))
            graph[b].append((a, i, km, km/40*60))
    return graph


GRAPH = build_graph()


@st.cache_data(show_spinner=False)
def route(origin, destination):
    """Line-aware Dijkstra: 5 min for each line change, no timetable or through services."""
    if origin not in GRAPH or destination not in GRAPH:
        raise ValueError('登録されていない駅です。')
    queue = [(0.0, 0, origin, -1, 0.0, 0, (), (origin,))]
    visited, serial = set(), 0
    while queue:
        minutes, _, node, last_line, km, transfers, legs, path = heapq.heappop(queue)
        if (node, last_line) in visited:
            continue
        visited.add((node, last_line))
        if node == destination:
            return dict(time=minutes, km=km, transfers=transfers, legs=legs, path=path)
        for next_node, line, edge_km, edge_time in GRAPH[node]:
            change = int(last_line != -1 and last_line != line)
            serial += 1
            heapq.heappush(queue, (minutes+edge_time+5*change, serial, next_node, line,
                                   km+edge_km, transfers+change, legs+((node, next_node, line),),
                                   path+(next_node,)))
    raise ValueError('登録路線で到達できません。')


SNAPSHOT = [{'id': 'ed2f2657ad567ec6eef5294b1dc707bd3db4f4b0',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 303000,
  'fees': 15000,
  'area': 48.86,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/ed2f2657ad567ec6eef5294b1dc707bd3db4f4b0/',
  'fetched_at': '2026-10-05T12:01:37+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': 'cf6ffd6b47d7aeb1f775761626a1c81b01a03a74',
  'name': '間取り',
  'address': '東京都豊島区南池袋1丁目15-22',
  'lat': 35.725097889978,
  'lng': 139.70893961185,
  'layout': '2LDK',
  'rent': 314000,
  'fees': 12000,
  'area': 47.55,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/cf6ffd6b47d7aeb1f775761626a1c81b01a03a74/',
  'fetched_at': '2026-10-05T12:01:37+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '3af182f6ce9c8047f773abe1b1367ebbd1f0be90',
  'name': '間取り',
  'address': '東京都豊島区上池袋2丁目',
  'lat': 35.734975073851,
  'lng': 139.71796066979,
  'layout': '2LDK',
  'rent': 200000,
  'fees': 15000,
  'area': 55.78,
  'floor': '2階',
  'url': 'https://www.homes.co.jp/chintai/room/3af182f6ce9c8047f773abe1b1367ebbd1f0be90/',
  'fetched_at': '2026-10-05T12:01:38+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '8d4d970320957a705f14615a9176ac00cb710651',
  'name': '間取り',
  'address': '東京都豊島区上池袋２丁目14-13',
  'lat': 35.734973685126,
  'lng': 139.7179609476,
  'layout': '2LDK',
  'rent': 200000,
  'fees': 20000,
  'area': 55.78,
  'floor': '2階',
  'url': 'https://www.homes.co.jp/chintai/room/8d4d970320957a705f14615a9176ac00cb710651/',
  'fetched_at': '2026-10-05T12:01:38+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '79fc3f8ca5cd9b430a1cbd873f1212bdcdf65eb5',
  'name': '間取り',
  'address': '東京都豊島区上池袋２丁目14-13',
  'lat': 35.734973685126,
  'lng': 139.7179609476,
  'layout': '2LDK',
  'rent': 200000,
  'fees': 15000,
  'area': 55.78,
  'floor': '2階',
  'url': 'https://www.homes.co.jp/chintai/room/79fc3f8ca5cd9b430a1cbd873f1212bdcdf65eb5/',
  'fetched_at': '2026-10-05T12:01:38+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '9bf5d45a8fa5a32d02f7b550232baa2f3e2a20a0',
  'name': '間取り',
  'address': '東京都豊島区上池袋2丁目14-13',
  'lat': 35.734975073851,
  'lng': 139.71796066979,
  'layout': '2LDK',
  'rent': 200000,
  'fees': 20000,
  'area': 55.78,
  'floor': '2階',
  'url': 'https://www.homes.co.jp/chintai/room/9bf5d45a8fa5a32d02f7b550232baa2f3e2a20a0/',
  'fetched_at': '2026-10-05T12:01:38+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'afca076cc1e90b13286ff9acf1e861d384178ea1',
  'name': '間取り',
  'address': '東京都豊島区南池袋２丁目7',
  'lat': 35.724895309222,
  'lng': 139.71545489936,
  'layout': '2LDK',
  'rent': 320000,
  'fees': 30000,
  'area': 72.16,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/afca076cc1e90b13286ff9acf1e861d384178ea1/',
  'fetched_at': '2026-10-05T12:01:39+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '3ef9af441308ac0487b6fae49c62b4c2cc5b5064',
  'name': '間取り',
  'address': '東京都豊島区南池袋2丁目',
  'lat': 35.724906973928,
  'lng': 139.71543073428,
  'layout': '2LDK',
  'rent': 320000,
  'fees': 30000,
  'area': 72.16,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/3ef9af441308ac0487b6fae49c62b4c2cc5b5064/',
  'fetched_at': '2026-10-05T12:01:40+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': '1ee9c245b549499f153553935b7a3f0cdc061be1',
  'name': '間取り',
  'address': '東京都豊島区南池袋２丁目',
  'lat': 35.724778374751,
  'lng': 139.71533519202,
  'layout': '2LDK',
  'rent': 320000,
  'fees': 30000,
  'area': 72.16,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/1ee9c245b549499f153553935b7a3f0cdc061be1/',
  'fetched_at': '2026-10-05T12:01:40+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '20db92ed5ab10c08526443eb5217fd10bfdee3ab',
  'name': '間取り',
  'address': '東京都豊島区南池袋２丁目',
  'lat': 35.72451736607,
  'lng': 139.71806608144,
  'layout': '2LDK',
  'rent': 612000,
  'fees': 50000,
  'area': 70.44,
  'floor': '41階',
  'url': 'https://www.homes.co.jp/chintai/room/20db92ed5ab10c08526443eb5217fd10bfdee3ab/',
  'fetched_at': '2026-10-05T12:01:41+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '120604cd5d435e33b84d3cea9469397381fe2f2b',
  'name': '間取り',
  'address': '東京都豊島区南池袋２丁目',
  'lat': 35.72451736607,
  'lng': 139.71806608144,
  'layout': '2LDK',
  'rent': 410000,
  'fees': 40000,
  'area': 55.44,
  'floor': '41階',
  'url': 'https://www.homes.co.jp/chintai/room/120604cd5d435e33b84d3cea9469397381fe2f2b/',
  'fetched_at': '2026-10-05T12:01:41+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '1d242f41b0e3b9e543cb61b352804b8b44adf43f',
  'name': '間取図',
  'address': '東京都豊島区南池袋２丁目1-2',
  'lat': 35.724299885395,
  'lng': 139.71787249553,
  'layout': '2LDK',
  'rent': 410000,
  'fees': 40000,
  'area': 55.44,
  'floor': '41階',
  'url': 'https://www.homes.co.jp/chintai/room/1d242f41b0e3b9e543cb61b352804b8b44adf43f/',
  'fetched_at': '2026-10-05T12:01:41+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '74455ab55cddfbf3af90b4aea54ed86882979030',
  'name': '間取り',
  'address': '東京都豊島区南池袋２丁目',
  'lat': 35.72451736607,
  'lng': 139.71806608144,
  'layout': '2LDK',
  'rent': 662000,
  'fees': 50000,
  'area': 70.44,
  'floor': '40階',
  'url': 'https://www.homes.co.jp/chintai/room/74455ab55cddfbf3af90b4aea54ed86882979030/',
  'fetched_at': '2026-10-05T12:01:42+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '0a2cc97856e96b5db8c8e4aebd4674e15eb08d44',
  'name': '間取図',
  'address': '東京都北区滝野川６丁目21-7',
  'lat': 35.745039419723,
  'lng': 139.72551823321,
  'layout': '2LDK',
  'rent': 350000,
  'fees': 15000,
  'area': 64.68,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/0a2cc97856e96b5db8c8e4aebd4674e15eb08d44/',
  'fetched_at': '2026-10-05T12:01:43+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '63d4c2bb92ba3181f6bc5ee561b2d55570cacf06',
  'name': '間取図',
  'address': '東京都北区滝野川６丁目21-7',
  'lat': 35.745039419723,
  'lng': 139.72551823321,
  'layout': '2LDK',
  'rent': 385000,
  'fees': 15000,
  'area': 70.92,
  'floor': '14階',
  'url': 'https://www.homes.co.jp/chintai/room/63d4c2bb92ba3181f6bc5ee561b2d55570cacf06/',
  'fetched_at': '2026-10-05T12:01:44+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '92f984df0ef3928dd4aa61a18c4c6a7dc0609de9',
  'name': '間取り',
  'address': '東京都文京区音羽1丁目',
  'lat': 35.716697133088,
  'lng': 139.72834663304,
  'layout': '2LDK',
  'rent': 184000,
  'fees': 15000,
  'area': 53.33,
  'floor': '7階',
  'url': 'https://www.homes.co.jp/chintai/room/92f984df0ef3928dd4aa61a18c4c6a7dc0609de9/',
  'fetched_at': '2026-10-05T12:01:45+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': 'b52405c0f51ebd271471b7cb81641eff4cef8e31',
  'name': '間取り',
  'address': '東京都豊島区上池袋１丁目',
  'lat': 35.737608202349,
  'lng': 139.72139499404,
  'layout': '2LDK',
  'rent': 245000,
  'fees': 15000,
  'area': 62.87,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/b52405c0f51ebd271471b7cb81641eff4cef8e31/',
  'fetched_at': '2026-10-05T12:01:45+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '2f8b0c1f9646348944f9852e469c1e8a850a8a59',
  'name': '間取り',
  'address': '東京都豊島区南池袋１丁目15-22',
  'lat': 35.725096778999,
  'lng': 139.70893988965,
  'layout': '2LDK',
  'rent': 265000,
  'fees': 12000,
  'area': 48.56,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/2f8b0c1f9646348944f9852e469c1e8a850a8a59/',
  'fetched_at': '2026-10-05T12:01:45+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '518c470b3d7f81c223c0bf7bee88594023f28674',
  'name': '間取り',
  'address': '東京都豊島区南池袋1丁目15-22',
  'lat': 35.725097889978,
  'lng': 139.70893961185,
  'layout': '2LDK',
  'rent': 272000,
  'fees': 12000,
  'area': 48.56,
  'floor': '7階',
  'url': 'https://www.homes.co.jp/chintai/room/518c470b3d7f81c223c0bf7bee88594023f28674/',
  'fetched_at': '2026-10-05T12:01:46+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'ca9b4a813f8d0cdacdabb2cd6b87897e924f6b0b',
  'name': '間取り',
  'address': '東京都豊島区南池袋１丁目15-22',
  'lat': 35.725096778999,
  'lng': 139.70893988965,
  'layout': '2LDK',
  'rent': 269000,
  'fees': 12000,
  'area': 48.56,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/ca9b4a813f8d0cdacdabb2cd6b87897e924f6b0b/',
  'fetched_at': '2026-10-05T12:01:46+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '943cb5ce7c3c36ee26c1e84f867e98cebb48b652',
  'name': '間取り',
  'address': '東京都豊島区南池袋1丁目',
  'lat': 35.725097889978,
  'lng': 139.70893961185,
  'layout': '2LDK',
  'rent': 265000,
  'fees': 12000,
  'area': 48.56,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/943cb5ce7c3c36ee26c1e84f867e98cebb48b652/',
  'fetched_at': '2026-10-05T12:01:47+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '4a714180174c1318412f85ee48ca260c6eef6b02',
  'name': '間取り',
  'address': '東京都豊島区南池袋1丁目',
  'lat': 35.725097889978,
  'lng': 139.70893961185,
  'layout': '2LDK',
  'rent': 263000,
  'fees': 12000,
  'area': 48.56,
  'floor': '2階',
  'url': 'https://www.homes.co.jp/chintai/room/4a714180174c1318412f85ee48ca260c6eef6b02/',
  'fetched_at': '2026-10-05T12:01:49+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': 'c0bc0dee17531e5f98f2efd2cae2765d37f6a3ab',
  'name': '間取り',
  'address': '東京都豊島区南池袋１丁目',
  'lat': 35.725096778999,
  'lng': 139.70893988965,
  'layout': '2LDK',
  'rent': 258000,
  'fees': 12000,
  'area': 48.56,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/c0bc0dee17531e5f98f2efd2cae2765d37f6a3ab/',
  'fetched_at': '2026-10-05T12:01:50+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '2432c65ee8e0e5f206a5bd734ce956e584299f3f',
  'name': '間取り',
  'address': '東京都豊島区南池袋１丁目15-22',
  'lat': 35.725077061672,
  'lng': 139.70903988194,
  'layout': '2LDK',
  'rent': 258000,
  'fees': 12000,
  'area': 48.56,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/2432c65ee8e0e5f206a5bd734ce956e584299f3f/',
  'fetched_at': '2026-10-05T12:01:52+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'f86e14e29b39ee76041cc49e28f48ced497cba02',
  'name': '間取り',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.729726482917,
  'lng': 139.70698709179,
  'layout': '2LDK',
  'rent': 590000,
  'fees': 20000,
  'area': 91.82,
  'floor': '32階',
  'url': 'https://www.homes.co.jp/chintai/room/f86e14e29b39ee76041cc49e28f48ced497cba02/',
  'fetched_at': '2026-10-05T12:01:52+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '81a7b2b1e4d930a9e1192f4f33a1ea76db857a35',
  'name': '間取り',
  'address': '東京都豊島区西池袋3丁目21-13',
  'lat': 35.729606219092,
  'lng': 139.70700209535,
  'layout': '2LDK',
  'rent': 490000,
  'fees': 20000,
  'area': 91.82,
  'floor': '32階',
  'url': 'https://www.homes.co.jp/chintai/room/81a7b2b1e4d930a9e1192f4f33a1ea76db857a35/',
  'fetched_at': '2026-10-05T12:01:53+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'b1c3bf574ed569e2a804895ad9b0c15fa698a8c0',
  'name': '間取り',
  'address': '東京都豊島区西池袋3丁目21-13',
  'lat': 35.729727871642,
  'lng': 139.70698681398,
  'layout': '2LDK',
  'rent': 490000,
  'fees': 20000,
  'area': 91.82,
  'floor': '32階',
  'url': 'https://www.homes.co.jp/chintai/room/b1c3bf574ed569e2a804895ad9b0c15fa698a8c0/',
  'fetched_at': '2026-10-05T12:01:54+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '452e3c94b9bfaa850d193a3683d8e57260e4d1c2',
  'name': '間取り',
  'address': '東京都豊島区西池袋３丁目',
  'lat': 35.729716762429,
  'lng': 139.70701097903,
  'layout': '2LDK',
  'rent': 450000,
  'fees': 20000,
  'area': 81.87,
  'floor': '30階',
  'url': 'https://www.homes.co.jp/chintai/room/452e3c94b9bfaa850d193a3683d8e57260e4d1c2/',
  'fetched_at': '2026-10-05T12:01:54+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'ff84c2b89b676509a2e8789ad173773282e9f329',
  'name': '間取り',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.729574001361,
  'lng': 139.70703431612,
  'layout': '2LDK',
  'rent': 450000,
  'fees': 20000,
  'area': 81.9,
  'floor': '30階',
  'url': 'https://www.homes.co.jp/chintai/room/ff84c2b89b676509a2e8789ad173773282e9f329/',
  'fetched_at': '2026-10-05T12:01:55+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '1014754633452e627b000d3dd4afa0349a7e3770',
  'name': '間取図',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.72957650108,
  'lng': 139.70703431602,
  'layout': '2LDK',
  'rent': 450000,
  'fees': 20000,
  'area': 81.87,
  'floor': '30階',
  'url': 'https://www.homes.co.jp/chintai/room/1014754633452e627b000d3dd4afa0349a7e3770/',
  'fetched_at': '2026-10-05T12:01:55+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'cc6ae2bdaecff98aa1eceb41776c047bbe1449e5',
  'name': '間取り',
  'address': '東京都豊島区西池袋3丁目21-13',
  'lat': 35.729727871642,
  'lng': 139.70698681398,
  'layout': '2LDK',
  'rent': 337000,
  'fees': 20000,
  'area': 66.69,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/cc6ae2bdaecff98aa1eceb41776c047bbe1449e5/',
  'fetched_at': '2026-10-05T12:01:55+00:00',
  'modified': '2026-09-29',
  'source': "LIFULL HOME'S"},
 {'id': 'c9fcaf38dbb4bbbf72f71b5cd05b60a0c900b8a5',
  'name': '間取り',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.729447076506,
  'lng': 139.70723208219,
  'layout': '2LDK',
  'rent': 450000,
  'fees': 20000,
  'area': 81.87,
  'floor': '30階',
  'url': 'https://www.homes.co.jp/chintai/room/c9fcaf38dbb4bbbf72f71b5cd05b60a0c900b8a5/',
  'fetched_at': '2026-10-05T12:01:57+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '50c4fd3347cb2b46c3582e6e86f953be0db88e96',
  'name': 'ウエストパークタワー池袋 3207',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.729447076506,
  'lng': 139.70723208219,
  'layout': '2LDK',
  'rent': 490000,
  'fees': 20000,
  'area': 91.82,
  'floor': '32階',
  'url': 'https://www.homes.co.jp/chintai/room/50c4fd3347cb2b46c3582e6e86f953be0db88e96/',
  'fetched_at': '2026-10-05T12:01:57+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '4282c80d10aca7d97c1f48792fea440302a58ec3',
  'name': 'ウエストパークタワー池袋',
  'address': '東京都豊島区西池袋３丁目21-13',
  'lat': 35.729447076506,
  'lng': 139.70723208219,
  'layout': '2LDK',
  'rent': 337000,
  'fees': 20000,
  'area': 66.69,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/4282c80d10aca7d97c1f48792fea440302a58ec3/',
  'fetched_at': '2026-10-05T12:01:58+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '287968dd587f833d1f92dbb771fab31807bfae1a',
  'name': '間取り',
  'address': '東京都新宿区西早稲田2丁目',
  'lat': 35.710900076679,
  'lng': 139.71012258187,
  'layout': '2LDK',
  'rent': 260000,
  'fees': 10000,
  'area': 61.71,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/287968dd587f833d1f92dbb771fab31807bfae1a/',
  'fetched_at': '2026-10-05T12:02:00+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': '349e1e0b5a284ad779db2cbe04419e8cd1fd4360',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目13-3',
  'lat': 35.73185097621,
  'lng': 139.71776581203,
  'layout': '2LDK',
  'rent': 246000,
  'fees': 12000,
  'area': 42.17,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/349e1e0b5a284ad779db2cbe04419e8cd1fd4360/',
  'fetched_at': '2026-10-05T12:02:00+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '715d7f293e73fdc0ac37e3079e2db97ced3800ba',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-3',
  'lat': 35.731865703332,
  'lng': 139.71801106836,
  'layout': '2LDK',
  'rent': 355000,
  'fees': 15000,
  'area': 54.51,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/715d7f293e73fdc0ac37e3079e2db97ced3800ba/',
  'fetched_at': '2026-10-05T12:02:00+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '4a1199bd1f80652988bc1dad0d8a235bde294737',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-3',
  'lat': 35.731865703332,
  'lng': 139.71801106836,
  'layout': '2LDK',
  'rent': 248000,
  'fees': 12000,
  'area': 42.05,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/4a1199bd1f80652988bc1dad0d8a235bde294737/',
  'fetched_at': '2026-10-05T12:02:00+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': 'c6f070438eb5cbbb95bbb18f373d5837b6d1db00',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-3',
  'lat': 35.731849032371,
  'lng': 139.71778025533,
  'layout': '2LDK',
  'rent': 248000,
  'fees': 12000,
  'area': 42.05,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/c6f070438eb5cbbb95bbb18f373d5837b6d1db00/',
  'fetched_at': '2026-10-05T12:02:01+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '8b82dba4a4ddec5576d3da26e864b74fa77843f9',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-3',
  'lat': 35.731865703332,
  'lng': 139.71801106836,
  'layout': '2LDK',
  'rent': 246000,
  'fees': 12000,
  'area': 42.17,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/8b82dba4a4ddec5576d3da26e864b74fa77843f9/',
  'fetched_at': '2026-10-05T12:02:01+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '279c0334caf0fb231fbaae9d5f067aa3d01a24f6',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 310000,
  'fees': 15000,
  'area': 48.86,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/279c0334caf0fb231fbaae9d5f067aa3d01a24f6/',
  'fetched_at': '2026-10-05T12:02:01+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '95e63c546532d07193a00ceac66e6e25257af16c',
  'name': '間取り',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731996235964,
  'lng': 139.71770386701,
  'layout': '2LDK',
  'rent': 312000,
  'fees': 15000,
  'area': 48.86,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/95e63c546532d07193a00ceac66e6e25257af16c/',
  'fetched_at': '2026-10-05T12:02:02+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '58493f3a7fd0998ef84c1a6443d8981e597280cc',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731995958254,
  'lng': 139.71770525579,
  'layout': '2LDK',
  'rent': 310000,
  'fees': 15000,
  'area': 48.86,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/58493f3a7fd0998ef84c1a6443d8981e597280cc/',
  'fetched_at': '2026-10-05T12:02:02+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '07a56931f8b21cc3d0f3cf5c231c4aa2f9bbef33',
  'name': '間取り',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731996235964,
  'lng': 139.71770386701,
  'layout': '2LDK',
  'rent': 343000,
  'fees': 15000,
  'area': 53.41,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/07a56931f8b21cc3d0f3cf5c231c4aa2f9bbef33/',
  'fetched_at': '2026-10-05T12:02:02+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '31a6e9fc71ca7c154430097a2aeff488a9766b4b',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731995958254,
  'lng': 139.71770525579,
  'layout': '2LDK',
  'rent': 337000,
  'fees': 15000,
  'area': 53.41,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/31a6e9fc71ca7c154430097a2aeff488a9766b4b/',
  'fetched_at': '2026-10-05T12:02:03+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '05401a21e61a4a831bb0ac415b1053e433eef0d6',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 337000,
  'fees': 15000,
  'area': 53.41,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/05401a21e61a4a831bb0ac415b1053e433eef0d6/',
  'fetched_at': '2026-10-05T12:02:03+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': 'b8ce21338f610fd55f35a771286dd0e8a4435f99',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 307000,
  'fees': 15000,
  'area': 48.86,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/b8ce21338f610fd55f35a771286dd0e8a4435f99/',
  'fetched_at': '2026-10-05T12:02:04+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '00ecfac724c390d6cc863dbe1677a4a63f1062fb',
  'name': '間取り',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731996235964,
  'lng': 139.71770386701,
  'layout': '2LDK',
  'rent': 307000,
  'fees': 15000,
  'area': 48.86,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/00ecfac724c390d6cc863dbe1677a4a63f1062fb/',
  'fetched_at': '2026-10-05T12:02:04+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'be49b41bfe4eec944a7e4af587a4660f10504b7a',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731995958254,
  'lng': 139.71770525579,
  'layout': '2LDK',
  'rent': 303000,
  'fees': 15000,
  'area': 48.86,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/be49b41bfe4eec944a7e4af587a4660f10504b7a/',
  'fetched_at': '2026-10-05T12:02:05+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'b1b25b43ee8d605e0aae76951493275143238589',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731995958254,
  'lng': 139.71770525579,
  'layout': '2LDK',
  'rent': 331000,
  'fees': 15000,
  'area': 53.41,
  'floor': '9階',
  'url': 'https://www.homes.co.jp/chintai/room/b1b25b43ee8d605e0aae76951493275143238589/',
  'fetched_at': '2026-10-05T12:02:05+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'b6eb663196a52613600e3e21d4542bd8e0004010',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 331000,
  'fees': 15000,
  'area': 53.41,
  'floor': '9階',
  'url': 'https://www.homes.co.jp/chintai/room/b6eb663196a52613600e3e21d4542bd8e0004010/',
  'fetched_at': '2026-10-05T12:02:05+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': 'b1a7e45f060b38c011a2fb99228c8fb5b666fb62',
  'name': '間取り',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731996235964,
  'lng': 139.71770386701,
  'layout': '2LDK',
  'rent': 302000,
  'fees': 15000,
  'area': 48.86,
  'floor': '9階',
  'url': 'https://www.homes.co.jp/chintai/room/b1a7e45f060b38c011a2fb99228c8fb5b666fb62/',
  'fetched_at': '2026-10-05T12:02:06+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '6b04eb76616cbe6bdfbc5f8f493f849931996810',
  'name': '間取図',
  'address': '東京都豊島区東池袋３丁目13-5',
  'lat': 35.731995958254,
  'lng': 139.71770525579,
  'layout': '2LDK',
  'rent': 300000,
  'fees': 15000,
  'area': 48.86,
  'floor': '9階',
  'url': 'https://www.homes.co.jp/chintai/room/6b04eb76616cbe6bdfbc5f8f493f849931996810/',
  'fetched_at': '2026-10-05T12:02:06+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'ed7844182ee3e7fe1925ebc63250298ae36756e3',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目',
  'lat': 35.731997902093,
  'lng': 139.7176908125,
  'layout': '2LDK',
  'rent': 300000,
  'fees': 15000,
  'area': 48.86,
  'floor': '9階',
  'url': 'https://www.homes.co.jp/chintai/room/ed7844182ee3e7fe1925ebc63250298ae36756e3/',
  'fetched_at': '2026-10-05T12:02:07+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '5aee3d21c518591d5fc8a393ec3f276f3c6dd1d0',
  'name': '間取り',
  'address': '東京都豊島区南大塚1丁目',
  'lat': 35.725716311859,
  'lng': 139.73562870685,
  'layout': '2LDK',
  'rent': 243000,
  'fees': 12000,
  'area': 53.25,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/5aee3d21c518591d5fc8a393ec3f276f3c6dd1d0/',
  'fetched_at': '2026-10-05T12:02:09+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': 'de684a3d69ca2a0bb5c468a635b0cd9204860cb9',
  'name': '間取り',
  'address': '東京都板橋区大山町',
  'lat': 35.748550825908,
  'lng': 139.69942281127,
  'layout': '2LDK',
  'rent': 240000,
  'fees': 30000,
  'area': 53.85,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/de684a3d69ca2a0bb5c468a635b0cd9204860cb9/',
  'fetched_at': '2026-10-05T12:02:09+00:00',
  'modified': '2026-09-29',
  'source': "LIFULL HOME'S"},
 {'id': '63ef30f10299196e10685ebf3e51a7271c28efad',
  'name': '間取り',
  'address': '東京都板橋区板橋4丁目',
  'lat': 35.748781959183,
  'lng': 139.72199282677,
  'layout': '2LDK',
  'rent': 215000,
  'fees': 10000,
  'area': 54.99,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/63ef30f10299196e10685ebf3e51a7271c28efad/',
  'fetched_at': '2026-10-05T12:02:11+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '21a555102c817ad9ecedfd535dd23f46a6a3ae99',
  'name': '間取り',
  'address': '東京都板橋区板橋4丁目',
  'lat': 35.748781959183,
  'lng': 139.72199282677,
  'layout': '2LDK',
  'rent': 219000,
  'fees': 10000,
  'area': 54.99,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/21a555102c817ad9ecedfd535dd23f46a6a3ae99/',
  'fetched_at': '2026-10-05T12:02:11+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '680fb7dfa36929768c16ebf379604cd8d7964c52',
  'name': '間取り',
  'address': '東京都板橋区板橋４丁目1-1',
  'lat': 35.748672532749,
  'lng': 139.72220503535,
  'layout': '2LDK',
  'rent': 219000,
  'fees': 10000,
  'area': 54.99,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/680fb7dfa36929768c16ebf379604cd8d7964c52/',
  'fetched_at': '2026-10-05T12:02:11+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '038ca715e3c67c1df6fd54f4c2a51f90067f5ab1',
  'name': '間取り',
  'address': '東京都豊島区雑司が谷３丁目15-25',
  'lat': 35.720925171137,
  'lng': 139.71433959825,
  'layout': '2LDK',
  'rent': 299000,
  'fees': 25000,
  'area': 50.31,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/038ca715e3c67c1df6fd54f4c2a51f90067f5ab1/',
  'fetched_at': '2026-10-05T12:02:12+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'ed3ba1a26af61bfe59587031aa2faa9a9e176be2',
  'name': '間取り',
  'address': '東京都豊島区雑司が谷3丁目',
  'lat': 35.721560096315,
  'lng': 139.71421652763,
  'layout': '2LDK',
  'rent': 299000,
  'fees': 20000,
  'area': 50.31,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/ed3ba1a26af61bfe59587031aa2faa9a9e176be2/',
  'fetched_at': '2026-10-05T12:02:13+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'ffe5d1429e9279f7d760b269e5bac2b2877a1b7f',
  'name': '間取り',
  'address': '東京都豊島区雑司が谷３丁目15-25',
  'lat': 35.720925171137,
  'lng': 139.71433959825,
  'layout': '2LDK',
  'rent': 285000,
  'fees': 20000,
  'area': 50.31,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/ffe5d1429e9279f7d760b269e5bac2b2877a1b7f/',
  'fetched_at': '2026-10-05T12:02:13+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'e71d0f2987d950e17a648aebda482487dbe5ff7a',
  'name': '間取り',
  'address': '東京都豊島区雑司が谷3丁目15-25',
  'lat': 35.721560096315,
  'lng': 139.71421652763,
  'layout': '2LDK',
  'rent': 285000,
  'fees': 20000,
  'area': 50.31,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/e71d0f2987d950e17a648aebda482487dbe5ff7a/',
  'fetched_at': '2026-10-05T12:02:13+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '75e56ecbd443fb513619e742cd2efb6822a0251f',
  'name': '間取り',
  'address': '東京都文京区大塚5丁目',
  'lat': 35.720069172626,
  'lng': 139.72533258699,
  'layout': '2LDK',
  'rent': 236000,
  'fees': 25000,
  'area': 51.05,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/75e56ecbd443fb513619e742cd2efb6822a0251f/',
  'fetched_at': '2026-10-05T12:02:14+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '3350e7e8c38a2d7b1a37f8b532b8830eb02aeed9',
  'name': '間取り',
  'address': '東京都文京区大塚5丁目',
  'lat': 35.720069172626,
  'lng': 139.72533258699,
  'layout': '2LDK',
  'rent': 259000,
  'fees': 25000,
  'area': 59.31,
  'floor': '7階',
  'url': 'https://www.homes.co.jp/chintai/room/3350e7e8c38a2d7b1a37f8b532b8830eb02aeed9/',
  'fetched_at': '2026-10-05T12:02:14+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '4c09ca28474241345b1b90cff40fc88a63c7ffe9',
  'name': '間取図',
  'address': '東京都文京区大塚５丁目40-17',
  'lat': 35.720200546574,
  'lng': 139.72532730438,
  'layout': '2LDK',
  'rent': 236000,
  'fees': 25000,
  'area': 51.05,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/4c09ca28474241345b1b90cff40fc88a63c7ffe9/',
  'fetched_at': '2026-10-05T12:02:14+00:00',
  'modified': '2026-09-30',
  'source': "LIFULL HOME'S"},
 {'id': '0bd7fc57066fa98e83afb55dd9200458c692b6fb',
  'name': '間取り',
  'address': '東京都新宿区中落合2丁目',
  'lat': 35.722245836969,
  'lng': 139.69285470538,
  'layout': '2LDK',
  'rent': 158000,
  'fees': 12000,
  'area': 52.21,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/0bd7fc57066fa98e83afb55dd9200458c692b6fb/',
  'fetched_at': '2026-10-05T12:02:15+00:00',
  'modified': '2026-09-30',
  'source': "LIFULL HOME'S"},
 {'id': '9f9b446470617120541299e82371d60a100c9452',
  'name': '間取り',
  'address': '東京都新宿区中落合２丁目27-18',
  'lat': 35.722284166251,
  'lng': 139.69286470299,
  'layout': '2LDK',
  'rent': 158000,
  'fees': 12000,
  'area': 52.21,
  'floor': '1階',
  'url': 'https://www.homes.co.jp/chintai/room/9f9b446470617120541299e82371d60a100c9452/',
  'fetched_at': '2026-10-05T12:02:15+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '1f32a61112ae68c905749bf20cd17b37d73a7b5c',
  'name': '間取り',
  'address': '東京都新宿区中落合3丁目',
  'lat': 35.721072844778,
  'lng': 139.69027969372,
  'layout': '2LDK',
  'rent': 298000,
  'fees': 8000,
  'area': 88.81,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/1f32a61112ae68c905749bf20cd17b37d73a7b5c/',
  'fetched_at': '2026-10-05T12:02:16+00:00',
  'modified': '2026-09-30',
  'source': "LIFULL HOME'S"},
 {'id': '36889372e4683f32c0ddc9f529778c6bffa9d566',
  'name': '間取り',
  'address': '東京都豊島区目白2丁目',
  'lat': 35.720224930732,
  'lng': 139.71278670287,
  'layout': '2LDK',
  'rent': 350000,
  'fees': 10000,
  'area': 75.63,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/36889372e4683f32c0ddc9f529778c6bffa9d566/',
  'fetched_at': '2026-10-05T12:02:17+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '74a1a19b25a4f10080971f6ed800ece1d489e552',
  'name': '間取り',
  'address': '東京都豊島区南大塚2丁目',
  'lat': 35.728079009771,
  'lng': 139.73220973577,
  'layout': '2LDK',
  'rent': 220000,
  'fees': 18000,
  'area': 41.31,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/74a1a19b25a4f10080971f6ed800ece1d489e552/',
  'fetched_at': '2026-10-05T12:02:18+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': 'db863147c90cb14a9b306a404c11c7fcd63c1b8c',
  'name': '間取り',
  'address': '東京都北区滝野川6丁目',
  'lat': 35.748027046051,
  'lng': 139.72205979592,
  'layout': '2LDK',
  'rent': 230000,
  'fees': 20000,
  'area': 60.65,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/db863147c90cb14a9b306a404c11c7fcd63c1b8c/',
  'fetched_at': '2026-10-05T12:02:18+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': 'b5559897d9dfc9131a0675884626dd49492cdea7',
  'name': '★間取り★',
  'address': '東京都北区滝野川６丁目44-12',
  'lat': 35.746074023853,
  'lng': 139.72546208522,
  'layout': '2LDK',
  'rent': 239000,
  'fees': 15000,
  'area': 55.21,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/b5559897d9dfc9131a0675884626dd49492cdea7/',
  'fetched_at': '2026-10-05T12:02:19+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '5f8c012cadc8eb871452f4a5f8a935235677df24',
  'name': '間取り',
  'address': '東京都北区滝野川6丁目',
  'lat': 35.746268989187,
  'lng': 139.72498767327,
  'layout': '2LDK',
  'rent': 238000,
  'fees': 15000,
  'area': 55.23,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/5f8c012cadc8eb871452f4a5f8a935235677df24/',
  'fetched_at': '2026-10-05T12:02:19+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': '8dcdc3f2970e4cb62217460ce4299ec34cb41406',
  'name': '間取り',
  'address': '東京都北区滝野川６丁目44-12',
  'lat': 35.746234272148,
  'lng': 139.72503517063,
  'layout': '2LDK',
  'rent': 236000,
  'fees': 15000,
  'area': 55.21,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/8dcdc3f2970e4cb62217460ce4299ec34cb41406/',
  'fetched_at': '2026-10-05T12:02:19+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'b8529a3b39e5652288058d2068f76f244ff900dd',
  'name': '間取り',
  'address': '東京都北区滝野川６丁目44-12',
  'lat': 35.746218996508,
  'lng': 139.72505072548,
  'layout': '2LDK',
  'rent': 233000,
  'fees': 15000,
  'area': 55.21,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/b8529a3b39e5652288058d2068f76f244ff900dd/',
  'fetched_at': '2026-10-05T12:02:20+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '63e23f5b850ea3969c67ee5cf50c756a6607f378',
  'name': '間取図',
  'address': '東京都北区滝野川６丁目',
  'lat': 35.746267045333,
  'lng': 139.72500156106,
  'layout': '2LDK',
  'rent': 232000,
  'fees': 15000,
  'area': 55.21,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/63e23f5b850ea3969c67ee5cf50c756a6607f378/',
  'fetched_at': '2026-10-05T12:02:20+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '431d7f01b48e8065f1098ea6dd5e93de383f0387',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目22-21',
  'lat': 35.730828959031,
  'lng': 139.72112667849,
  'layout': '2LDK',
  'rent': 312000,
  'fees': 14000,
  'area': 68.93,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/431d7f01b48e8065f1098ea6dd5e93de383f0387/',
  'fetched_at': '2026-10-05T12:02:21+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '69e3a12755df1307d88714afb3690b90dd9c59e6',
  'name': '間取り',
  'address': '東京都豊島区長崎1丁目',
  'lat': 35.728752711635,
  'lng': 139.69689076731,
  'layout': '2LDK',
  'rent': 240000,
  'fees': 10000,
  'area': 55.58,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/69e3a12755df1307d88714afb3690b90dd9c59e6/',
  'fetched_at': '2026-10-05T12:02:21+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': 'f2988fee0b5c956e1deaebc2a85da48cf76952c1',
  'name': '間取り',
  'address': '東京都板橋区氷川町47-9',
  'lat': 35.751197642797,
  'lng': 139.7058077172,
  'layout': '2LDK',
  'rent': 150000,
  'fees': 10000,
  'area': 48.42,
  'floor': '5階',
  'url': 'https://www.homes.co.jp/chintai/room/f2988fee0b5c956e1deaebc2a85da48cf76952c1/',
  'fetched_at': '2026-10-05T12:02:22+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'b7514d5c146450cc8788e07a59b0abfc225eb49e',
  'name': '間取り',
  'address': '東京都文京区大塚5丁目',
  'lat': 35.720094169223,
  'lng': 139.72531064341,
  'layout': '2LDK',
  'rent': 255000,
  'fees': 25000,
  'area': 59.57,
  'floor': '3階',
  'url': 'https://www.homes.co.jp/chintai/room/b7514d5c146450cc8788e07a59b0abfc225eb49e/',
  'fetched_at': '2026-10-05T12:02:23+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '5a4827819b63d7391789737c7c6d8679265454f9',
  'name': '間取り',
  'address': '東京都豊島区上池袋2丁目15-17',
  'lat': 35.73572195187,
  'lng': 139.71862363892,
  'layout': '2LDK',
  'rent': 290000,
  'fees': 15000,
  'area': 69.57,
  'floor': '6階',
  'url': 'https://www.homes.co.jp/chintai/room/5a4827819b63d7391789737c7c6d8679265454f9/',
  'fetched_at': '2026-10-05T12:02:23+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': '3b7da3793723877c49dd1f5458d37a586ea1f083',
  'name': '間取り',
  'address': '東京都北区滝野川７丁目48-2',
  'lat': 35.741272883742,
  'lng': 139.72492121338,
  'layout': '2LDK',
  'rent': 249000,
  'fees': 12000,
  'area': 55.76,
  'floor': '15階',
  'url': 'https://www.homes.co.jp/chintai/room/3b7da3793723877c49dd1f5458d37a586ea1f083/',
  'fetched_at': '2026-10-05T12:02:24+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'ac093b64721f80570db1610b1a3ff356db14504b',
  'name': '間取り',
  'address': '東京都北区滝野川7丁目48-2',
  'lat': 35.741207889632,
  'lng': 139.72486760944,
  'layout': '2LDK',
  'rent': 247000,
  'fees': 12000,
  'area': 55.86,
  'floor': '12階',
  'url': 'https://www.homes.co.jp/chintai/room/ac093b64721f80570db1610b1a3ff356db14504b/',
  'fetched_at': '2026-10-05T12:02:24+00:00',
  'modified': '2026-09-29',
  'source': "LIFULL HOME'S"},
 {'id': 'dd7c146e36956356781fdf5069543d1ba6846f2b',
  'name': '間取図',
  'address': '東京都北区滝野川７丁目48-2',
  'lat': 35.741299823479,
  'lng': 139.72485871761,
  'layout': '2LDK',
  'rent': 249000,
  'fees': 12000,
  'area': 55.76,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/dd7c146e36956356781fdf5069543d1ba6846f2b/',
  'fetched_at': '2026-10-05T12:02:25+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': 'aeac2fe866e20475832132416af3b8594a64dce2',
  'name': '間取り',
  'address': '東京都北区滝野川7丁目',
  'lat': 35.741207889632,
  'lng': 139.72486760944,
  'layout': '2LDK',
  'rent': 249000,
  'fees': 12000,
  'area': 55.76,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/aeac2fe866e20475832132416af3b8594a64dce2/',
  'fetched_at': '2026-10-05T12:02:25+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'fea8079485d43be37d81fb344538705149326361',
  'name': '間取図',
  'address': '東京都北区滝野川７丁目48-2',
  'lat': 35.741299823479,
  'lng': 139.72485871761,
  'layout': '2LDK',
  'rent': 247000,
  'fees': 12000,
  'area': 55.76,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/fea8079485d43be37d81fb344538705149326361/',
  'fetched_at': '2026-10-05T12:02:26+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '7a8ce3bacdcd3b893fc4dfb8209b490a3ce2aec1',
  'name': '間取図',
  'address': '東京都北区滝野川７丁目48-2',
  'lat': 35.741206501286,
  'lng': 139.72488205272,
  'layout': '2LDK',
  'rent': 247000,
  'fees': 12000,
  'area': 55.76,
  'floor': '11階',
  'url': 'https://www.homes.co.jp/chintai/room/7a8ce3bacdcd3b893fc4dfb8209b490a3ce2aec1/',
  'fetched_at': '2026-10-05T12:02:26+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '87810cc818bf023b126d0cb588572b2f309e8beb',
  'name': '間取り',
  'address': '東京都北区滝野川７丁目48-2',
  'lat': 35.741206500922,
  'lng': 139.72486844276,
  'layout': '2LDK',
  'rent': 245000,
  'fees': 12000,
  'area': 56.33,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/87810cc818bf023b126d0cb588572b2f309e8beb/',
  'fetched_at': '2026-10-05T12:02:26+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '90a26a50ebc8e143ef9b36850f863568f5380514',
  'name': '間取り',
  'address': '東京都北区滝野川7丁目',
  'lat': 35.743227027324,
  'lng': 139.72190666884,
  'layout': '2LDK',
  'rent': 245000,
  'fees': 12000,
  'area': 55.86,
  'floor': '10階',
  'url': 'https://www.homes.co.jp/chintai/room/90a26a50ebc8e143ef9b36850f863568f5380514/',
  'fetched_at': '2026-10-05T12:02:27+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'dda2b2e05900d2672b6cf6b98bc1683c94d702ec',
  'name': '間取り',
  'address': '東京都豊島区東池袋3丁目23-22',
  'lat': 35.730365152488,
  'lng': 139.72225160151,
  'layout': '2LDK',
  'rent': 325000,
  'fees': 25000,
  'area': 64.3,
  'floor': '29階',
  'url': 'https://www.homes.co.jp/chintai/room/dda2b2e05900d2672b6cf6b98bc1683c94d702ec/',
  'fetched_at': '2026-10-05T12:02:27+00:00',
  'modified': '2026-09-29',
  'source': "LIFULL HOME'S"},
 {'id': '0c22e5e4bcde2178d82c24d41a89da318ad20edf',
  'name': '間取り',
  'address': '東京都豊島区東池袋３丁目',
  'lat': 35.73027016787,
  'lng': 139.72242659045,
  'layout': '2LDK',
  'rent': 325000,
  'fees': 25000,
  'area': 64.3,
  'floor': '29階',
  'url': 'https://www.homes.co.jp/chintai/room/0c22e5e4bcde2178d82c24d41a89da318ad20edf/',
  'fetched_at': '2026-10-05T12:02:28+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '0a4c9a0aa3a55301f162f04a751f54a6cb1b0380',
  'name': '間取り',
  'address': '東京都新宿区高田馬場2丁目',
  'lat': 35.711714146162,
  'lng': 139.70991756659,
  'layout': '2LDK',
  'rent': 240500,
  'fees': 10500,
  'area': 59.39,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/0a4c9a0aa3a55301f162f04a751f54a6cb1b0380/',
  'fetched_at': '2026-10-05T12:02:28+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': 'cf8db61c49c9ca9a0152ad7cbcaed4669bd7ed5a',
  'name': '間取り',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711712201951,
  'lng': 139.70991812218,
  'layout': '2LDK',
  'rent': 275500,
  'fees': 10500,
  'area': 56.46,
  'floor': '19階',
  'url': 'https://www.homes.co.jp/chintai/room/cf8db61c49c9ca9a0152ad7cbcaed4669bd7ed5a/',
  'fetched_at': '2026-10-05T12:02:28+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': '126b9b8b4fcb3126d7b3fcf3ca76cfd6a8cba4c4',
  'name': '当社は仲介手数料無料！\u3000リノベーション2LDK\u3000NURO光無料\u3000WEB申込受付中♪',
  'address': '東京都新宿区高田馬場２丁目',
  'lat': 35.711748583995,
  'lng': 139.70981535167,
  'layout': '2LDK',
  'rent': 254500,
  'fees': 10500,
  'area': 56.46,
  'floor': '19階',
  'url': 'https://www.homes.co.jp/chintai/room/126b9b8b4fcb3126d7b3fcf3ca76cfd6a8cba4c4/',
  'fetched_at': '2026-10-05T12:02:28+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'be80ab3766bbb15369afa65ff3bc00f61ebdb1f6',
  'name': '間取り',
  'address': '東京都新宿区高田馬場2丁目',
  'lat': 35.711714146162,
  'lng': 139.70991756659,
  'layout': '2LDK',
  'rent': 254500,
  'fees': 10500,
  'area': 56.46,
  'floor': '19階',
  'url': 'https://www.homes.co.jp/chintai/room/be80ab3766bbb15369afa65ff3bc00f61ebdb1f6/',
  'fetched_at': '2026-10-05T12:02:29+00:00',
  'modified': '2026-09-30',
  'source': "LIFULL HOME'S"},
 {'id': 'fc8662a1f1caf7dfb5aa0287750cc66b6c05868f',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711982725466,
  'lng': 139.70985950518,
  'layout': '2LDK',
  'rent': 271500,
  'fees': 10500,
  'area': 56.46,
  'floor': '15階',
  'url': 'https://www.homes.co.jp/chintai/room/fc8662a1f1caf7dfb5aa0287750cc66b6c05868f/',
  'fetched_at': '2026-10-05T12:02:29+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': 'b42ed5fed749ed1027ca86ed9a16d73516582ef1',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711712202315,
  'lng': 139.70993173213,
  'layout': '2LDK',
  'rent': 271500,
  'fees': 10500,
  'area': 56.46,
  'floor': '15階',
  'url': 'https://www.homes.co.jp/chintai/room/b42ed5fed749ed1027ca86ed9a16d73516582ef1/',
  'fetched_at': '2026-10-05T12:02:30+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': 'c34bab469b6726c48e899904df97b1bccdb1f61c',
  'name': '間取り',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.712030499925,
  'lng': 139.70993671892,
  'layout': '2LDK',
  'rent': 265500,
  'fees': 10500,
  'area': 56.46,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/c34bab469b6726c48e899904df97b1bccdb1f61c/',
  'fetched_at': '2026-10-05T12:02:30+00:00',
  'modified': '2026-10-05',
  'source': "LIFULL HOME'S"},
 {'id': 'fff01047e83ff67dc70b32986aa5355a588e9b78',
  'name': '間取り',
  'address': '東京都新宿区高田馬場2丁目',
  'lat': 35.711714146162,
  'lng': 139.70991756659,
  'layout': '2LDK',
  'rent': 265500,
  'fees': 10500,
  'area': 56.46,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/fff01047e83ff67dc70b32986aa5355a588e9b78/',
  'fetched_at': '2026-10-05T12:02:31+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '5cf4f65f2db8ad626fe0237c44146e89e3209fce',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711712202315,
  'lng': 139.70993173213,
  'layout': '2LDK',
  'rent': 249500,
  'fees': 10500,
  'area': 56.46,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/5cf4f65f2db8ad626fe0237c44146e89e3209fce/',
  'fetched_at': '2026-10-05T12:02:31+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"},
 {'id': 'b3ad5578dd94b349038979739c0a49ee1f19131e',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711982725466,
  'lng': 139.70985950518,
  'layout': '2LDK',
  'rent': 249500,
  'fees': 10500,
  'area': 56.46,
  'floor': '13階',
  'url': 'https://www.homes.co.jp/chintai/room/b3ad5578dd94b349038979739c0a49ee1f19131e/',
  'fetched_at': '2026-10-05T12:02:31+00:00',
  'modified': '2026-10-04',
  'source': "LIFULL HOME'S"},
 {'id': '8d50deedeba7838b78858fb108ad4b19fa641c8f',
  'name': '間取り',
  'address': '東京都新宿区高田馬場2丁目',
  'lat': 35.711714146162,
  'lng': 139.70991756659,
  'layout': '2LDK',
  'rent': 254500,
  'fees': 10500,
  'area': 60.15,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/8d50deedeba7838b78858fb108ad4b19fa641c8f/',
  'fetched_at': '2026-10-05T12:02:32+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '99051c9763bd065fa536cc15de3408807857af02',
  'name': '間取り',
  'address': '東京都新宿区高田馬場2丁目',
  'lat': 35.711714146162,
  'lng': 139.70991756659,
  'layout': '2LDK',
  'rent': 254500,
  'fees': 10500,
  'area': 60.15,
  'floor': '7階',
  'url': 'https://www.homes.co.jp/chintai/room/99051c9763bd065fa536cc15de3408807857af02/',
  'fetched_at': '2026-10-05T12:02:33+00:00',
  'modified': '2026-10-03',
  'source': "LIFULL HOME'S"},
 {'id': '4cca4077fb2ad52541edc78b0ef968e0ffadb425',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711712202315,
  'lng': 139.70993173213,
  'layout': '2LDK',
  'rent': 244500,
  'fees': 10500,
  'area': 60.15,
  'floor': '8階',
  'url': 'https://www.homes.co.jp/chintai/room/4cca4077fb2ad52541edc78b0ef968e0ffadb425/',
  'fetched_at': '2026-10-05T12:02:33+00:00',
  'modified': '2026-10-02',
  'source': "LIFULL HOME'S"},
 {'id': '5367f730f3d176a4c3bebefeba98980298cea98a',
  'name': '間取図',
  'address': '東京都新宿区高田馬場２丁目1-1',
  'lat': 35.711712202315,
  'lng': 139.70993173213,
  'layout': '2LDK',
  'rent': 256500,
  'fees': 10500,
  'area': 59.39,
  'floor': '4階',
  'url': 'https://www.homes.co.jp/chintai/room/5367f730f3d176a4c3bebefeba98980298cea98a/',
  'fetched_at': '2026-10-05T12:02:33+00:00',
  'modified': '2026-10-01',
  'source': "LIFULL HOME'S"}]
SNAPSHOT_INFO = {'layout': '2LDK',
 'requested': 150,
 'received': 106,
 'warnings': ['16ページは位置・金額・間取りを確認できず除外しました。'],
 'source_url': 'https://www.homes.co.jp/chintai/tokyo/ikebukuro_00488-st/list/',
 'fetched_at': '2026-10-05T12:02:34+00:00'}
MD_CODES = {'1LDK': '15', '2LDK': '25', '3LDK': '35', '4LDK': '45-'}
DEFAULT_URL = 'https://www.homes.co.jp/chintai/tokyo/ikebukuro_00488-st/list/'
HEADERS = {'User-Agent': 'SumaiCompass/4.0 (personal rental map)'}
_HTTP = threading.local()

def http_session():
    if not hasattr(_HTTP, 'session'):
        _HTTP.session = requests.Session()
    return _HTTP.session

REAL_FIELDS = ['id', 'name', 'address', 'lat', 'lng', 'layout', 'rent', 'fees', 'area', 'floor', 'url', 'fetched_at', 'modified', 'source']
COLORS = ['#2166ac', '#1d8fa7', '#48aa96', '#87b85c', '#d2be42', '#e9a248', '#df7245', '#c94049']
BANDS = [15, 20, 22.5, 25, 27.5, 30, 35]


def safe_source_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname == 'www.homes.co.jp' and not p.username
                and not p.password and p.port in (None, 443) and p.path.startswith('/chintai/'))
    except (ValueError, TypeError):
        return False


def fetch_html(url):
    if not safe_source_url(url):
        raise ValueError('LIFULL HOME’Sの賃貸ページURLを指定してください。')
    response = http_session().get(url, headers=HEADERS, timeout=(20, 20))
    response.raise_for_status()
    if not safe_source_url(response.url):
        raise ValueError('物件ページ以外に移動しました。')
    if len(response.content) > 4_000_000:
        raise ValueError('ページが大きすぎます。')
    response.encoding = 'utf-8'
    return response.text


@st.cache_data(ttl=3600, show_spinner=False)
def check_robots():
    response = http_session().get('https://www.homes.co.jp/robots.txt', headers=HEADERS, timeout=(20, 20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return rp.can_fetch('SumaiCompass', DEFAULT_URL) and rp.can_fetch('SumaiCompass', 'https://www.homes.co.jp/chintai/room/test/')


def search_url(base, layout, page):
    p = urlparse(base)
    if not safe_source_url(base) or not p.path.endswith('/list/'):
        raise ValueError('駅や地域の「物件一覧」ページのURLを指定してください。')
    args = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith('cond[madori]') and k != 'page']
    args += [(f'cond[madori][{MD_CODES[layout]}]', MD_CODES[layout]), ('page', str(page))]
    return urlunparse(p._replace(query=urlencode(args), fragment=''))


def room_links(html):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.select('a[href]'):
        url = urljoin('https://www.homes.co.jp', a['href']).split('?')[0]
        if safe_source_url(url) and re.fullmatch(r'/chintai/(room/[a-zA-Z0-9]+|b-[0-9]+)/', urlparse(url).path) and url not in links:
            links.append(url)
    # Room URLs and agency URLs can represent the same unit; canonical URL is used later.
    return links


def money(value):
    if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0:
        return int(value)
    text = str(value).replace(',', '').replace(' ', '').strip()
    if text in ('無', 'なし', '-', '0', '0円'):
        return 0
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(万円|円)?(?:/月)?', text)
    if not match:
        raise ValueError('家賃または管理費等が読み取れません。')
    return round(float(match[1])*(10000 if match[2] == '万円' else 1))


def json_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from json_nodes(item)
    elif isinstance(value, dict):
        yield value
        if '@graph' in value:
            yield from json_nodes(value['@graph'])


def parse_listing(html, url, fetched_at):
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            parsed = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        for data in json_nodes(parsed):
            if data.get('@type') != 'RealEstateListing':
                continue
            offer, entity = data.get('offers', {}), data.get('mainEntity', {})
            if not isinstance(offer, dict) or not isinstance(entity, dict):
                continue
            if offer.get('availability') not in (None, 'https://schema.org/InStock', 'http://schema.org/InStock'):
                continue
            if offer.get('priceCurrency') != 'JPY':
                continue
            geo = entity.get('geo', {})
            try:
                lat, lng = float(geo['latitude']), float(geo['longitude'])
                rent = money(offer['price'])
                size = float(entity.get('floorSize', {}).get('value', 0))
            except (KeyError, ValueError, TypeError):
                continue
            if not (34 <= lat <= 37 and 138 <= lng <= 141 and 0 < rent <= 10_000_000 and math.isfinite(size) and size > 0):
                continue
            attrs = {a.get('name'): a.get('value') for a in entity.get('additionalProperty', []) if isinstance(a, dict)}
            layout = str(attrs.get('間取り', '')).strip()
            if layout not in LAYOUTS:
                continue
            costs = {a.get('name'): a.get('value') for a in offer.get('additionalProperty', []) if isinstance(a, dict)}
            if '管理費等' not in costs:
                continue  # Unknown fees are not silently treated as zero.
            try:
                fees = money(costs['管理費等'])
                if fees > 10_000_000:
                    continue
            except ValueError:
                continue
            address = entity.get('address', {})
            if not isinstance(address, dict):
                continue
            addr = ''.join(str(address.get(k, '')) for k in ('addressRegion', 'addressLocality', 'streetAddress'))
            image = entity.get('image', [])
            caption = image[0].get('caption') if isinstance(image, list) and image and isinstance(image[0], dict) else None
            canonical = data.get('url', url)
            if not safe_source_url(canonical):
                canonical = url
            return dict(id=canonical.rstrip('/').split('/')[-1], name=caption or entity.get('name', '物件'),
                        address=addr, lat=lat, lng=lng, layout=layout, rent=rent, fees=fees,
                        area=size, floor=str(entity.get('floorLevel', '')), url=canonical,
                        fetched_at=fetched_at, modified=str(data.get('dateModified', '')), source="LIFULL HOME'S")
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_listing(url):
    return parse_listing(fetch_html(url), url, datetime.now(timezone.utc).isoformat(timespec='seconds'))


def unit_key(p):
    # Multiple agencies listing the same room are counted once when observable fields match.
    return (round(p['lat'], 5), round(p['lng'], 5), p['layout'], p['floor'], round(p['area'], 1), p['rent'], p['fees'])


def deduplicate(properties):
    result, seen = [], set()
    for p in properties:
        key = unit_key(p)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def collect(base, layout, limit=120, pages=3, progress=None):
    if not check_robots():
        raise ValueError('現在、取得元が自動取得を許可していません。CSV読み込みを利用してください。')
    links, warnings, seen_links = [], [], set()
    for page in range(1, pages+1):
        try:
            found = room_links(fetch_html(search_url(base, layout, page)))
        except (requests.RequestException, ValueError) as error:
            warnings.append(f'一覧{page}ページ目を取得できませんでした（{type(error).__name__}）。')
            break
        fresh = [u for u in found if u not in seen_links]
        if not fresh:
            break
        seen_links.update(fresh)
        links.extend(fresh)
        if len(links) >= limit:
            break
    links = links[:limit]
    records, failed = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_listing, url): url for url in links}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                p = future.result()
                if p and p['layout'] == layout:
                    records.append(p)
                else:
                    failed += 1
            except (requests.RequestException, ValueError, KeyError, TypeError):
                failed += 1
            if progress:
                progress(number, len(links), len(records))
    records = deduplicate(records)
    if failed:
        warnings.append(f'{failed}ページは位置・金額・間取りを確認できず除外しました。')
    return records, dict(layout=layout, requested=len(links), received=len(records), warnings=warnings,
                         source_url=base, fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def project(lat, lng, origin):
    return ((lng-origin['lng'])*111320*math.cos(math.radians(origin['lat'])), (lat-origin['lat'])*111320)


def unproject(x, y, origin):
    return [origin['lat']+y/111320, origin['lng']+x/(111320*math.cos(math.radians(origin['lat'])))]


def color_for(yen):
    amount = yen/10000
    return COLORS[sum(amount > cut for cut in BANDS)]


def mesh(properties, origin, cell=100, radius=1500, interpolate=True, reach=400, minimum=3):
    buildings = {}
    for p in properties:
        x, y = project(p['lat'], p['lng'], origin)
        if math.hypot(x, y) > radius:
            continue
        key = (round(p['lat'], 5), round(p['lng'], 5))
        buildings.setdefault(key, []).append(p)
    observed = {}
    points = []
    for ps in buildings.values():
        x, y = project(ps[0]['lat'], ps[0]['lng'], origin)
        point = dict(x=x, y=y, price=statistics.median(p['rent']+p['fees'] for p in ps), count=len(ps))
        points.append(point)
        observed.setdefault((math.floor(x/cell), math.floor(y/cell)), []).append(point)
    cells = []
    extent = math.ceil(radius/cell)
    for ix in range(-extent, extent):
        for iy in range(-extent, extent):
            cx, cy = (ix+.5)*cell, (iy+.5)*cell
            if math.hypot(cx, cy) > radius:
                continue
            own = observed.get((ix, iy), [])
            if own:
                price = statistics.median(p['price'] for p in own)
                kind, support, farthest = '掲載物件の集計', own, 0
            elif interpolate:
                support = [p for p in points if math.hypot(p['x']-cx, p['y']-cy) <= reach]
                if len(support) < minimum:
                    continue
                weighted = [(p['price'], 1/max(50, math.hypot(p['x']-cx, p['y']-cy))**2) for p in support]
                price = sum(value*w for value, w in weighted)/sum(w for _, w in weighted)
                kind = '近隣からの推定'
                farthest = round(max(math.hypot(p['x']-cx, p['y']-cy) for p in support))
            else:
                continue
            cells.append(dict(bounds=[unproject(ix*cell, iy*cell, origin), unproject((ix+1)*cell, (iy+1)*cell, origin)],
                              price=price, kind=kind, buildings=len(support), rooms=sum(p['count'] for p in support), farthest=farthest))
    return cells


def validate_records(raw, maximum=2000):
    if len(raw) > max(5_000_000, maximum*2500):
        raise ValueError('CSVは5MB以内にしてください。')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    required = {'name', 'lat', 'lng', 'layout', 'rent', 'fees', 'area'}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError('name, lat, lng, layout, rent, fees, area列が必要です。金額は円、座標は掲載位置を指定します。')
    records = []
    for n, row in enumerate(reader, 2):
        if n > maximum+1:
            raise ValueError(f'CSVは{maximum:,}件以内にしてください。')
        try:
            p = {key: row.get(key, '') for key in REAL_FIELDS}
            for key in ('lat', 'lng', 'area'):
                p[key] = float(row[key])
            for key in ('rent', 'fees'):
                p[key] = int(row[key])
            if not (34 <= p['lat'] <= 37 and 138 <= p['lng'] <= 141 and math.isfinite(p['area']) and p['area'] > 0
                    and 0 < p['rent'] <= 10_000_000 and 0 <= p['fees'] <= 10_000_000 and p['layout'] in LAYOUTS):
                raise ValueError()
            for field in ('name', 'id', 'address', 'floor'):
                p[field] = str(p[field])[:200]
                if p[field].startswith("'") and p[field][1:].startswith(('=', '+', '-', '@')):
                    p[field] = p[field][1:]
            p['name'] = p['name'].strip()
            if not p['name']:
                raise ValueError()
            if p['url'] and (urlparse(p['url']).scheme not in ('http', 'https') or not urlparse(p['url']).netloc):
                raise ValueError()
            p['id'] = p['id'] or str(uuid.uuid4())
            p['source'] = p['source'] or 'CSV入力'
            records.append(p)
        except (ValueError, TypeError, KeyError):
            raise ValueError(f'{n}行目の金額・位置・間取りを確認してください。') from None
    return deduplicate(records)


def export_records(records):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=REAL_FIELDS)
    writer.writeheader()
    for p in records:
        row = {k: p.get(k, '') for k in REAL_FIELDS}
        for k, value in row.items():
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                row[k] = "'"+value
        writer.writerow(row)
    return output.getvalue().encode('utf-8-sig')


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_facilities_at(lat, lng, category):
    query = f'[out:json][timeout:15];nwr{FACILITY_FILTERS[category]}(around:1000,{lat},{lng});out center tags;'
    r = requests.post('https://overpass-api.de/api/interpreter', data={'data': query}, timeout=(5, 20))
    r.raise_for_status()
    facilities = []
    for e in r.json().get('elements', []):
        loc = e.get('center', {})
        x, y = e.get('lat', loc.get('lat')), e.get('lon', loc.get('lon'))
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            facilities.append(dict(lat=x, lng=y, name=e.get('tags', {}).get('name', '名称未登録')))
    return facilities[:100]


def make_map(origin, cells, records, facilities):
    m = folium.Map(location=[origin['lat'], origin['lng']], zoom_start=st.session_state.get('zoom03', 15), tiles='OpenStreetMap', control_scale=True)
    for c in cells:
        estimated = c['kind'] == '近隣からの推定'
        tip = (f'{c["price"]/10000:.1f}万円/月 · {c["kind"]}<br>'
               f'根拠：{c["buildings"]}建物 / {c["rooms"]}募集住戸'
               +(f'<br>最遠の参照建物：{c["farthest"]}m' if estimated else ''))
        folium.Rectangle(c['bounds'], color=color_for(c['price']), weight=.5, fill=True,
                         fill_color=color_for(c['price']), fill_opacity=.36 if estimated else .65,
                         dash_array='3,3' if estimated else None, tooltip=tip).add_to(m)
    for p in records:
        url = p['url']
        safe = urlparse(url).scheme in ('http', 'https') and bool(urlparse(url).netloc)
        detail = (f'<b>{escape(p["name"])}</b><br>{escape(p["layout"])} / {p["area"]:g}㎡ / {escape(p["floor"])}<br>'
                  f'総額 {(p["rent"]+p["fees"])/10000:g}万円（月額）<br>'
                  f'家賃 {p["rent"]:,}円 ＋ 管理費等 {p["fees"]:,}円<br>{escape(p["address"])}<br>'
                  +(f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">募集ページを確認</a>' if safe else 'CSV入力'))
        folium.CircleMarker([p['lat'], p['lng']], radius=5, color='white', weight=1,
                            fill=True, fill_color=color_for(p['rent']+p['fees']), fill_opacity=1,
                            tooltip=f'{escape(p["name"])} · {(p["rent"]+p["fees"])/10000:g}万',
                            popup=folium.Popup(detail, max_width=300)).add_to(m)

    for f in facilities:
        folium.CircleMarker([f['lat'], f['lng']], radius=6, fill=True, fill_color='#173a5e', fill_opacity=1,
                            color='white', weight=1, tooltip=escape(f['name'])).add_to(m)
    return m

def normalize_bounds(raw):
    try:
        if isinstance(raw, dict):
            a, b = raw['_southWest'], raw['_northEast']
            values = [float(a['lat']), float(a['lng']), float(b['lat']), float(b['lng'])]
        else:
            values = list(map(float, raw))
        south, west, north, east = values
        if not all(math.isfinite(x) for x in values) or not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            return None
        return values
    except (TypeError, KeyError, ValueError):
        return None


def inside(p, bounds):
    return bool(bounds and bounds[0] <= p['lat'] <= bounds[2] and bounds[1] <= p['lng'] <= bounds[3])


def cell_intersects(c, b):
    sw, ne = c['bounds']
    return sw[0] <= b[2] and ne[0] >= b[0] and sw[1] <= b[3] and ne[1] >= b[1]


def bounds_radius(b, origin):
    return max(distance(origin, dict(lat=lat,lng=lng))*1000 for lat in (b[0],b[2]) for lng in (b[1],b[3]))


class StorageError(Exception):
    """Safe, actionable messages; never expose provider response bodies or credentials."""


def secret_value(name, default=''):
    value = os.environ.get(name)
    if value is None:
        try:
            value = st.secrets.get(name, default)
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            value = default
    return str(value or '').strip()


def supabase_settings():
    url = secret_value('SUPABASE_URL').rstrip('/')
    key = secret_value('SUPABASE_SECRET_KEY') or secret_value('SUPABASE_SERVICE_ROLE_KEY')
    namespace = secret_value('SUPABASE_NAMESPACE', 'sumai-compass')
    if not url or not key:
        raise StorageError('StreamlitのSecretsにSUPABASE_URLとSUPABASE_SECRET_KEYを設定してください。')
    try:
        parsed = urlparse(url)
        valid_url = (parsed.scheme == 'https' and parsed.hostname and parsed.hostname.endswith('.supabase.co')
                     and not parsed.username and not parsed.password and parsed.port in (None,443)
                     and parsed.path in ('','/') and not parsed.query and not parsed.fragment)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise StorageError('SUPABASE_URLには https://プロジェクトID.supabase.co 形式のProject URLを設定してください。')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', namespace):
        raise StorageError('SUPABASE_NAMESPACEは英数字・ハイフン・アンダースコアで80文字以内にしてください。')
    headers = {'apikey':key, 'Content-Type':'application/json', 'Accept':'application/json'}
    if key.startswith('sb_secret_'):
        pass  # New secret keys use apikey, not Authorization: Bearer.
    else:
        try:
            encoded = key.split('.')[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '='*(-len(encoded)%4)))
            if len(key.split('.')) != 3 or payload.get('role') != 'service_role':
                raise ValueError()
        except (ValueError, IndexError, UnicodeError, TypeError, AttributeError):
            raise StorageError('Secret key（sb_secret_…）または旧service_roleキーを使用してください。anon/publishableキーでは保存できません。') from None
        headers['Authorization'] = 'Bearer '+key
    return url, headers, namespace


def supabase_request(method, route, *, params=None, payload=None):
    url, headers, _ = supabase_settings()
    allowed = ('sumai_properties','sumai_fetch_queries','sumai_metadata','rpc/sumai_save_properties')
    if route not in allowed:
        raise StorageError('保存先の指定が不正です。')
    try:
        # Use a separate one-shot connection: do not share the HOME'S scraping session or headers.
        response = requests.request(method, url+'/rest/v1/'+route, headers=headers,
                                    params=params, json=payload, timeout=(10,45), allow_redirects=False)
    except requests.RequestException:
        raise StorageError('Supabaseに接続できませんでした。Projectの稼働状態・接続設定を確認して再試行してください。') from None
    if not 200 <= response.status_code < 300:
        if response.status_code in (401,403):
            message = 'Supabaseのキーまたはアクセス権が不正です。Secret keyとセットアップSQLを確認してください。'
        elif response.status_code == 404:
            message = 'Supabaseのテーブルまたは保存用関数がありません。supabase_setup_ver.04.sqlをSQL Editorで実行してください。'
        else:
            message = f'Supabaseの読み込み・保存に失敗しました（HTTP {response.status_code}）。保存済みデータは削除していません。'
        raise StorageError(message)
    try:
        return response.json()
    except ValueError:
        raise StorageError('Supabaseの応答を読み取れませんでした。') from None


def load_store():
    _, _, namespace = supabase_settings()
    records, offset = [], 0
    # PostgREST caps one response; read every page with a deterministic primary-key order.
    while True:
        rows = supabase_request('GET','sumai_properties',params={
            'namespace':'eq.'+namespace, 'select':'identity,payload', 'order':'identity.asc', 'limit':500, 'offset':offset})
        if not isinstance(rows,list) or any(not isinstance(r,dict) or not isinstance(r.get('payload'),dict) for r in rows):
            raise StorageError('Supabaseの物件データ形式が不正です。')
        if not rows:
            break
        records.extend(row['payload'] for row in rows)
        offset += len(rows)  # Continue even if the server's configured cap is less than 500.
    meta = supabase_request('GET','sumai_metadata',params={
        'namespace':'eq.'+namespace,'select':'payload','identity':'eq.last_fetch','limit':1})
    if not isinstance(meta,list) or (meta and (not isinstance(meta[0],dict) or not isinstance(meta[0].get('payload'),dict))):
        raise StorageError('Supabaseの取得履歴の形式が不正です。')
    try:
        valid = validate_records(export_records(records),maximum=max(2000,len(records))) if records else []
    except (ValueError, KeyError, TypeError, csv.Error):
        raise StorageError('保存済み物件の座標・家賃・間取りを確認できません。Supabaseのデータを確認してください。') from None
    return deduplicate(valid), meta[0]['payload'] if meta else {}


def query_key(bounds, layout, limit):
    # v05 adds visible-region detection plus HOME'S and SUUMO. Include the version so v04 caches are not reused.
    value = json.dumps(['v05-regions-homes-suumo', list(map(lambda x: round(x,5), bounds)), layout, limit],
                       ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def cached_query(bounds, layout, limit):
    _, _, namespace = supabase_settings()
    rows = supabase_request('GET','sumai_fetch_queries',params={
        'namespace':'eq.'+namespace,'identity':'eq.'+query_key(bounds,layout,limit),'select':'payload','limit':1})
    if not isinstance(rows,list) or (rows and (not isinstance(rows[0],dict) or not isinstance(rows[0].get('payload'),dict))):
        raise StorageError('Supabaseの範囲キャッシュの形式が不正です。')
    return rows[0]['payload'] if rows else None


def save_store(records, info, bounds=None, layout=None, limit=None, reload_after=True):
    _, _, namespace = supabase_settings()
    try:
        records = validate_records(export_records(records),maximum=5000)
    except (ValueError, KeyError, TypeError, csv.Error) as error:
        raise StorageError('保存する物件データの形式を確認してください。') from None
    if not records:
        raise StorageError('保存する物件がありません。')
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    unique = {}
    for p in records:
        stamp = p.get('fetched_at') or now
        try:
            parsed = datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            p['fetched_at'] = parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
        except (ValueError, TypeError, AttributeError):
            raise StorageError('物件の取得日時を確認してください。') from None
        identity = p.get('url') or str(p['id'])
        if identity not in unique or unique[identity]['fetched_at'] <= p['fetched_at']:
            unique[identity] = p
    result = supabase_request('POST','rpc/sumai_save_properties',payload={
        'p_namespace':namespace,'p_records':list(unique.values()),'p_info':info,
        'p_query_key':query_key(bounds,layout,limit) if bounds is not None else None})
    if not isinstance(result,dict) or result.get('ok') is not True:
        raise StorageError('Supabaseへの保存完了を確認できませんでした。再読み込みして確認してください。')
    if not reload_after:
        return list(unique.values())
    try:
        return load_store()[0]
    except StorageError:
        raise StorageError('Supabaseへの保存は完了しましたが、再読み込みに失敗しました。再接続して確認してください。') from None


def show_storage_setup():
    st.info('保存・読み込み先はSupabaseです。接続設定が完了すると地図を表示します。')
    st.markdown('1. SupabaseのSQL Editorで **supabase_setup_ver.04.sql** を実行。\n'
                '2. Streamlitの **Settings → Secrets** に下の接続情報を登録。\n'
                '3. アプリを再起動、または下のボタンで再接続。')
    st.code('SUPABASE_URL = "https://プロジェクトID.supabase.co"\n'
            'SUPABASE_SECRET_KEY = "sb_secret_から始まるSecret key"\n'
            'SUPABASE_NAMESPACE = "sumai-compass"',language='toml')
    st.caption('キーはStreamlitのサーバーからのみ使用します。GitHubやチャットには貼り付けないでください。旧service_roleキーはSUPABASE_SERVICE_ROLE_KEYでも指定できます。')
    if st.button('Supabaseに再接続',key='reconnect04'):
        st.rerun()


MAP_TILE_URL = 'https://www.homes.co.jp/_ajax/map/realestate_article/tile/'
MAP_INFO_URL = 'https://www.homes.co.jp/_ajax/map/realestate_article/info_view/'
SUUMO_SEARCH_URL = 'https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
SUUMO_MD_CODES = {'1LDK': '04', '2LDK': '07', '3LDK': '10', '4LDK': '13'}
GSI_REVERSE_URL = 'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress'
GSI_SEARCH_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch'
GSI_MUNI_URL = 'https://maps.gsi.go.jp/js/muni.js'
SUUMO_HEADERS = {'User-Agent': 'SumaiCompass/5.0 (personal rental map)'}
GSI_HEADERS = {'User-Agent': 'SumaiCompass/5.0 (personal rental map)'}
_SUUMO_HTTP = threading.local()


def suumo_session():
    if not hasattr(_SUUMO_HTTP, 'session'):
        _SUUMO_HTTP.session = requests.Session()
    return _SUUMO_HTTP.session


@st.cache_data(ttl=86400, show_spinner=False)
def gsi_municipalities():
    try:
        r = requests.get(GSI_MUNI_URL, headers=GSI_HEADERS, timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            return {}
        table = {}
        for code, value in re.findall(r'GSI\.MUNI_ARRAY\["(\d+)"\]\s*=\s*[\'\"]([^\'\"]+)', r.text):
            parts = value.split(',')
            if len(parts) >= 4:
                table[code] = {'pref_code': parts[0], 'prefecture': parts[1], 'municipality': parts[3]}
        return table
    except (requests.RequestException, ValueError, TypeError):
        return {}


@st.cache_data(ttl=86400, show_spinner=False)
def gsi_reverse_region(lat, lng):
    try:
        r = requests.get(GSI_REVERSE_URL, params={'lat': round(float(lat), 7), 'lon': round(float(lng), 7)},
                         headers=GSI_HEADERS, timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 500_000:
            return None
        data = r.json().get('results', {})
        code, town = str(data.get('muniCd', '')).strip(), str(data.get('lv01Nm', '')).strip()
        if not code or not town:
            return None
        muni = gsi_municipalities().get(code, {})
        # Search at town-name granularity; remove only the trailing chome number.
        town_base = re.sub(r'[0-9０-９一二三四五六七八九十百]+丁目$', '', town).strip() or town
        prefecture = muni.get('prefecture', '')
        municipality = muni.get('municipality', '')
        label = ''.join(x for x in (prefecture, municipality, town_base) if x)
        return {'muni_code': code, 'pref_code': muni.get('pref_code', ''), 'prefecture': prefecture,
                'municipality': municipality, 'town': town, 'town_base': town_base,
                'label': label or town_base, 'lat': float(lat), 'lng': float(lng)}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return None


def visible_regions(bounds):
    bounds = normalize_bounds(bounds)
    if not bounds:
        raise ValueError('地図の表示範囲を確認できません。')
    south, west, north, east = bounds
    # 3x3 samples keep the mechanism small while covering more than only the map center.
    fractions = (0.18, 0.50, 0.82)
    regions = []
    seen = set()
    for fy in fractions:
        for fx in fractions:
            lat = south + (north-south)*fy
            lng = west + (east-west)*fx
            region = gsi_reverse_region(lat, lng)
            if not region:
                continue
            key = (region['muni_code'], region['town_base'])
            if key not in seen:
                seen.add(key)
                regions.append(region)
    if not regions:
        raise ValueError('表示範囲の地域名を取得できませんでした。地図を少し動かして再試行してください。')
    return regions[:9]


@st.cache_data(ttl=604800, show_spinner=False)
def gsi_geocode_address(address):
    address = str(address).strip()
    if not address:
        return None
    try:
        r = requests.get(GSI_SEARCH_URL, params={'q': address}, headers=GSI_HEADERS,
                         timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 2_000_000:
            return None
        rows = r.json()
        if not isinstance(rows, list):
            return None
        normalized = unicodedata.normalize('NFKC', address).replace(' ', '')
        candidates = []
        for item in rows[:8]:
            try:
                coords = item['geometry']['coordinates']
                title = str(item.get('properties', {}).get('title', ''))
                lng, lat = float(coords[0]), float(coords[1])
                if 34 <= lat <= 37 and 138 <= lng <= 141:
                    nt = unicodedata.normalize('NFKC', title).replace(' ', '')
                    score = len(os.path.commonprefix([normalized, nt]))
                    candidates.append((score, lat, lng, title))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        if not candidates:
            return None
        _, lat, lng, title = max(candidates, key=lambda x: x[0])
        return {'lat': lat, 'lng': lng, 'title': title}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def map_robots_allowed():
    response = http_session().get('https://www.homes.co.jp/robots.txt',headers=HEADERS,timeout=(20,20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return all(rp.can_fetch('SumaiCompass', u) for u in (MAP_TILE_URL, MAP_INFO_URL, DEFAULT_URL))


@st.cache_data(ttl=3600, show_spinner=False)
def suumo_robots_allowed():
    response = suumo_session().get('https://suumo.jp/robots.txt', headers=SUUMO_HEADERS, timeout=(10,20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return rp.can_fetch('SumaiCompass', SUUMO_SEARCH_URL)


def tile_xy(lat, lng, zoom=15):
    n = 2**zoom
    return int((lng/360+.5)*n), int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)


def map_request(url, data):
    if url not in (MAP_TILE_URL, MAP_INFO_URL):
        raise ValueError('取得先が不正です。')
    r = http_session().post(url,data=data,headers=HEADERS,timeout=(20,20),allow_redirects=False)
    if 300 <= r.status_code < 400:
        raise ValueError('取得元が別ページへ移動しました。')
    r.raise_for_status()
    if len(r.content) > 4_000_000:
        raise ValueError('応答が大きすぎます。')
    r.encoding = 'utf-8'
    return r


def map_condition(layout, freeword=''):
    return {'cond[mbg][3001]':'3001','cond[mbg][3002]':'3002','cond[mbg][3003]':'3003',
            'cond[monthmoneyroom]':'0','cond[monthmoneyroomh]':'0','cond[housearea]':'0',
            'cond[houseareah]':'0','cond[walkminutesh]':'0','cond[houseageh]':'0',
            'cond[newdate]':'0','cond[freeword]':freeword,'cond[fwtype]':'1','cond[exfreeword]':'',
            f'cond[madori][{MD_CODES[layout]}]':MD_CODES[layout]}


def viewport_tiles(bounds, zoom=15):
    xmin,ymin = tile_xy(bounds[2],bounds[1],zoom)
    xmax,ymax = tile_xy(bounds[0],bounds[3],zoom)
    tiles = [(x,y) for x in range(xmin,xmax+1) for y in range(ymin,ymax+1)]
    if len(tiles) > 160:
        raise ValueError('検索区画が多すぎます。地図を拡大してください。')
    return tiles


def homes_candidates(bounds, layout, freeword):
    warnings, candidates, zoom = [], {}, 15
    for offset in range(0, len(viewport_tiles(bounds, zoom)), 6):
        part = viewport_tiles(bounds, zoom)[offset:offset+6]
        data = map_condition(layout, freeword)
        data['zoom'] = zoom
        for i,(x,y) in enumerate(part):
            data[f'tiles[{i}][x]'], data[f'tiles[{i}][y]'] = x,y
        try:
            payload = map_request(MAP_TILE_URL,data).json()
            if not isinstance(payload, dict):
                continue
            for group in payload.values():
                if not isinstance(group, dict):
                    continue
                for row in group.get('row_set',[]):
                    try:
                        if inside(dict(lat=float(row['lat']),lng=float(row['lon'])),bounds):
                            key = row.get('tykey')
                            if key and re.fullmatch(r'[a-zA-Z0-9]+',str(key)):
                                candidates[str(key)] = row
                    except (KeyError, TypeError, ValueError):
                        continue
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
            warnings.append(f'HOME’Sの検索区画の一部を取得できませんでした（{type(error).__name__}）。')
    return candidates, warnings


def collect_homes_region(bounds, layout, region, limit=12):
    if not map_robots_allowed():
        raise ValueError('LIFULL HOME’Sは現在、自動取得を許可していません。')
    town = region['town_base']
    candidates, warnings = homes_candidates(bounds, layout, town)
    if not candidates:
        # Small fallback: query only the visible tiles, then keep detail pages whose address matches the detected town.
        candidates, more = homes_candidates(bounds, layout, '')
        warnings.extend(more)
        if candidates:
            warnings.append(f'HOME’Sは「{town}」の直接候補が0件だったため、表示範囲候補を地域名で再確認しました。')
    keys = sorted(candidates, key=lambda k: distance(
        {'lat':float(candidates[k]['lat']),'lng':float(candidates[k]['lon'])}, region))
    keys = keys[:max(1, limit)]
    links = []
    for start in range(0,len(keys),8):
        data = map_condition(layout, town)
        data['cond[tykey]'] = ','.join(keys[start:start+8])
        try:
            links.extend(room_links(map_request(MAP_INFO_URL,data).text))
        except (requests.RequestException, ValueError) as error:
            warnings.append(f'HOME’Sの候補一覧を一部取得できませんでした（{type(error).__name__}）。')
    if not links and keys:
        # Some HOME'S configurations reject a freeword together with tykey; retry the same small key set without freeword.
        for start in range(0,len(keys),8):
            data = map_condition(layout, '')
            data['cond[tykey]'] = ','.join(keys[start:start+8])
            try:
                links.extend(room_links(map_request(MAP_INFO_URL,data).text))
            except (requests.RequestException, ValueError):
                pass
    links = list(dict.fromkeys(links))[:max(1, limit)]
    records, failed = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(fetch_listing,u) for u in links]
        for job in concurrent.futures.as_completed(jobs):
            try:
                p = job.result()
                if p and p['layout'] == layout and inside(p,bounds) and town in p.get('address',''):
                    records.append(p)
                else:
                    failed += 1
            except (requests.RequestException, ValueError, KeyError, TypeError):
                failed += 1
    if failed:
        warnings.append(f'HOME’S「{town}」で{failed}ページを範囲・間取り・位置・費用の確認後に除外しました。')
    return deduplicate(records), warnings


def safe_suumo_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname == 'suumo.jp' and not p.username and not p.password
                and p.port in (None,443) and p.path.startswith('/chintai/'))
    except (ValueError, TypeError):
        return False


def suumo_search_url(region, layout, page=1):
    params = {'ar':'030','bs':'040','pc':'50','md':SUUMO_MD_CODES[layout],
              'sc':region['muni_code'],'fw2':region['town_base'],'page':str(page)}
    if region.get('pref_code'):
        params['ta'] = region['pref_code']
    return SUUMO_SEARCH_URL + '?' + urlencode(params)


def suumo_fetch_html(url):
    p = urlparse(url)
    if not (p.scheme == 'https' and p.hostname == 'suumo.jp' and p.path == urlparse(SUUMO_SEARCH_URL).path):
        raise ValueError('SUUMOの検索URLが不正です。')
    r = suumo_session().get(url,headers=SUUMO_HEADERS,timeout=(10,25),allow_redirects=False)
    if 300 <= r.status_code < 400:
        raise ValueError('SUUMOの検索ページが移動しました。')
    r.raise_for_status()
    if len(r.content) > 5_000_000:
        raise ValueError('SUUMOの検索結果が大きすぎます。')
    r.encoding = r.apparent_encoding or 'utf-8'
    return r.text


def parse_suumo_money(text):
    value = unicodedata.normalize('NFKC', str(text or '')).strip().replace('－','-')
    if value in ('', '-', '―', 'なし', '無'):
        return 0
    return money(value)


def parse_suumo_list(html, layout, region, limit):
    soup = BeautifulSoup(html,'html.parser')
    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    town = region['town_base']
    for building in soup.select('div.cassetteitem'):
        title = building.select_one('.cassetteitem_content-title')
        address_el = building.select_one('.cassetteitem_detail-col1')
        name = title.get_text(' ',strip=True) if title else 'SUUMO掲載物件'
        address = address_el.get_text(' ',strip=True) if address_el else ''
        if not address or town not in address:
            continue
        for room in building.select('tr.js-cassette_link'):
            madori = room.select_one('.cassetteitem_madori')
            menseki = room.select_one('.cassetteitem_menseki')
            rent_el = room.select_one('.cassetteitem_price--rent')
            admin_el = room.select_one('.cassetteitem_price--administration')
            link = room.select_one('a.js-cassette_link_href[href]') or room.select_one('a[href*="/chintai/"]')
            room_layout = madori.get_text(strip=True) if madori else ''
            if room_layout != layout or not menseki or not rent_el or not link:
                continue
            url = urljoin('https://suumo.jp',link.get('href','')).split('?')[0]
            if not safe_suumo_url(url):
                continue
            try:
                area_match = re.search(r'(\d+(?:\.\d+)?)',unicodedata.normalize('NFKC',menseki.get_text(strip=True)))
                area = float(area_match.group(1)) if area_match else 0
                rent = parse_suumo_money(rent_el.get_text(strip=True))
                fees = parse_suumo_money(admin_el.get_text(strip=True) if admin_el else '0')
                if not (0 < area <= 1000 and 0 < rent <= 10_000_000 and 0 <= fees <= 10_000_000):
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
            cells = room.select('td')
            floor = cells[2].get_text(' ',strip=True)[:80] if len(cells) > 2 else ''
            rows.append(dict(id=url.rstrip('/').split('/')[-1],name=name[:200],address=address[:200],layout=layout,
                             rent=rent,fees=fees,area=area,floor=floor,url=url,fetched_at=now,
                             modified='',source='SUUMO'))
            if len(rows) >= limit:
                return rows
    return rows


def collect_suumo_region(bounds, layout, region, limit=12):
    if not suumo_robots_allowed():
        raise ValueError('SUUMOは現在、自動取得を許可していません。')
    warnings, parsed = [], []
    # One compact results page per detected town. This intentionally avoids a large crawler.
    url = suumo_search_url(region,layout,1)
    try:
        parsed = parse_suumo_list(suumo_fetch_html(url),layout,region,max(limit*2,limit))
    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        raise ValueError(f'SUUMO「{region["town_base"]}」を取得できませんでした（{type(error).__name__}）。') from None
    records, geocode_failed = [], 0
    for p in parsed:
        geo = gsi_geocode_address(p['address'])
        if not geo:
            geocode_failed += 1
            continue
        p['lat'],p['lng'] = geo['lat'],geo['lng']
        if inside(p,bounds):
            records.append(p)
        if len(records) >= limit:
            break
    if geocode_failed:
        warnings.append(f'SUUMO「{region["town_base"]}」で{geocode_failed}件は住所位置を確認できず除外しました。')
    return deduplicate(records), warnings


def collect_visible_regions(bounds, layout, limit=120, progress=None, save_chunk=None):
    bounds = normalize_bounds(bounds)
    if not bounds:
        raise ValueError('地図の表示範囲を確認できません。')
    center = dict(lat=(bounds[0]+bounds[2])/2,lng=(bounds[1]+bounds[3])/2)
    if bounds_radius(bounds,center) > 6000:
        raise ValueError('地図を拡大してください。')
    if not (34.8 <= bounds[0] < bounds[2] <= 36.5 and 138.5 <= bounds[1] < bounds[3] <= 140.9):
        raise ValueError('首都圏の範囲で取得してください。')
    regions = visible_regions(bounds)
    per_source_region = max(4, min(24, math.ceil(limit/max(1,len(regions)*2))))
    total_steps = len(regions)*2
    step, warnings, all_records = 0, [], []
    source_counts = {"LIFULL HOME'S":0,'SUUMO':0}
    for region in regions:
        for source_name, collector in (("LIFULL HOME'S",collect_homes_region),('SUUMO',collect_suumo_region)):
            step += 1
            if progress:
                progress(step,total_steps,f'{region["label"]}｜{source_name}を確認中')
            try:
                records, local_warnings = collector(bounds,layout,region,per_source_region)
                warnings.extend(local_warnings)
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                records = []
                warnings.append(str(error))
            if records:
                source_counts[source_name] += len(records)
                all_records.extend(records)
                if save_chunk:
                    chunk_info = dict(layout=layout,bounds=bounds,region=region['label'],source=source_name,
                                      received=len(records),warnings=[],source_url=(
                                          'https://www.homes.co.jp/chintai/map/' if source_name.startswith('LIFULL') else SUUMO_SEARCH_URL),
                                      fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
                    save_chunk(records,chunk_info)
    combined = deduplicate(all_records)
    info = dict(layout=layout,bounds=bounds,regions=[r['label'] for r in regions],requested_limit=limit,
                received=len(combined),source_counts=source_counts,warnings=warnings,
                source_url="LIFULL HOME'S + SUUMO",
                fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return combined,info

def main():
    st.set_page_config(page_title='住まいコンパス', page_icon='🏠', layout='wide', initial_sidebar_state='collapsed')
    st.markdown('''<style>
    .stApp{background:white;color:#173a5e;color-scheme:light}
    .block-container{padding-top:1rem;padding-bottom:2rem}
    h1,h2,h3,p,label{color:#173a5e}
    [data-testid="stMetric"]{background:#f1f5f9;padding:8px;border-radius:10px}
    @media(max-width:760px){.block-container{padding-left:.7rem;padding-right:.7rem}h1{font-size:1.5rem}}
    </style>''', unsafe_allow_html=True)
    st.title('住まいコンパス')
    st.caption('掲載物件の総額家賃を、街の中で比較')
    state = st.session_state
    if 'records02' not in state:
        try:
            records, info = load_store()
            if not records:
                # First connection only: persist the existing, dated real Ikebukuro snapshot.
                records = save_store(list(SNAPSHOT), dict(SNAPSHOT_INFO))
                _, info = load_store()
            state.records02, state.info02 = records, info
        except StorageError as error:
            st.error(str(error))
            show_storage_setup()
            st.stop()
        state.facilities02 = []
        state.facility_origin02 = None
    if 'flash02' in state:
        st.success(state.pop('flash02'))
    layout = st.radio('間取り', LAYOUTS, index=1, horizontal=True, key='layout02')
    with st.expander('地域・色分け・物件取得の設定', expanded=False):
        station_name = st.selectbox('地図の中心駅', list(BY_NAME), index=list(BY_NAME).index('池袋'), key='center02')
        c1, c2 = st.columns(2)
        cell = c1.selectbox('メッシュの大きさ', [100, 200, 300], format_func=lambda x: f'{x}m', key='cell02')
        radius = c2.selectbox('駅からの表示範囲', [1000, 1500, 2000, 3000], index=1, format_func=lambda x: f'{x/1000:g}km', key='radius02')
        c1, c2 = st.columns(2)
        interpolate = c1.checkbox('近隣データから未掲載メッシュも推定', value=True, key='interpolate02')
        reach = c2.slider('推定に使う建物の範囲（m）', 200, 700, 400, 50, key='reach02')
        budget = st.number_input('物件一覧・ピンの月額上限（万円）', 1.0, 1000.0, 50.0, 1.0, key='budget02')
        st.caption('家賃の高低を比較できるよう、面の色分けは予算で絞る前の全取得物件を使います。推定は3建物以上ある範囲だけです。')
        limit = st.selectbox('取得上限（物件ページ数）', [60, 120, 180, 240], index=1, key='limit02')
        st.caption('取得はボタンを押したときだけ行います。間取り・表示範囲で検索し、掲載座標を確認した物件を保存します。')
    origin = BY_NAME[station_name]
    if state.get('map_station03') != station_name:
        state.map_station03 = station_name
        state.bounds03 = None
        state.view_center03 = [origin['lat'], origin['lng']]
        state.zoom03 = 15
    bounds = state.get('bounds03')
    view = dict(lat=state.view_center03[0], lng=state.view_center03[1], name=station_name)
    render_radius = bounds_radius(bounds, view) if bounds else radius
    all_layout = [p for p in state.records02 if p['layout'] == layout]
    nearby = [p for p in all_layout if inside(p, bounds)] if bounds else [p for p in all_layout if distance(p, origin)*1000 <= radius]
    support = [p for p in all_layout if distance(p, view)*1000 <= render_radius+reach]
    displayed = [p for p in nearby if p['rent']+p['fees'] <= budget*10000]
    cells = mesh(support, view, cell, render_radius, interpolate, reach) if render_radius <= 6000 else []
    if bounds:
        cells = [c for c in cells if cell_intersects(c, bounds)]
    if state.facility_origin02 != station_name:
        state.facilities02 = []
        state.facility_origin02 = station_name
    def remember_view():
        data = state.get(f'map03_{station_name}', {})
        if isinstance(data, dict):
            valid = normalize_bounds(data.get('bounds'))
            if valid:
                state.bounds03 = valid
                state.view_center03 = [(valid[0]+valid[2])/2, (valid[1]+valid[3])/2]
                zoom = data.get('zoom')
                if isinstance(zoom, (int, float)) and 1 <= zoom <= 20:
                    state.zoom03 = int(zoom)
    st_folium(make_map(view, cells, displayed, state.facilities02), height=560, use_container_width=True,
              key=f'map03_{station_name}', returned_objects=['bounds', 'zoom'],
              center=tuple(state.view_center03), zoom=state.zoom03, on_change=remember_view)
    st.caption('地図を移動・拡大してから取得ボタンを押してください。表示中の長方形から地域名を細かく判定し、HOME’S・SUUMOを地域ごとに取得してSupabaseへ順次保存します。')
    refresh = st.checkbox('保存済み範囲も再取得する（通常は保存データを利用）', key='refresh03')
    if st.button('表示範囲の家賃帯一覧を取得・保存', type='primary', disabled=bounds is None, key='fetch03'):
        if bounds_radius(bounds, view) > 6000:
            st.warning('取得対象が広すぎます。対角線が約12km以内になるまで拡大してください。')
        else:
            progress = st.progress(0.0, text='表示範囲の物件を検索しています…')
            try:
                cached = cached_query(bounds, layout, limit) if not refresh else None
                if cached:
                    loaded_records, _ = load_store()
                    state.records02, state.info02 = loaded_records, cached
                    state.flash02 = 'この範囲の保存データを読み込みました。最新情報は再取得を選んでください。'
                    st.rerun()
                if refresh:
                    fetch_listing.clear()
                records, info = collect_visible_regions(
                    bounds, layout, limit,
                    progress=lambda n,total,message: progress.progress(n/max(1,total), text=f'{n}/{total} · {message}'),
                    save_chunk=lambda chunk,chunk_info: save_store(chunk,chunk_info,reload_after=False))
                if records:
                    state.records02 = save_store(records, info, bounds, layout, limit)
                    state.info02 = info
                    counts = info.get('source_counts', {})
                    regions_text = '、'.join(info.get('regions', []))
                    state.flash02 = (f'表示地域を細分化して{len(records)}物件を取得・保存しました。'
                                     f' HOME’S {counts.get("LIFULL HOME'S",0)}件 / SUUMO {counts.get("SUUMO",0)}件。'
                                     +(f' 対象：{regions_text}' if regions_text else ''))
                    st.rerun()
                else:
                    st.warning('表示範囲内の物件を確認できませんでした。保存済みデータは保持しています。')
                    for warning in info.get('warnings', []):
                        st.caption(warning)
            except (requests.RequestException, StorageError, OSError, ValueError, KeyError, TypeError) as error:
                st.error(str(error) if isinstance(error, StorageError) else f'取得できませんでした（{type(error).__name__}）。保存済みデータは保持しています。')
            finally:
                progress.empty()
    if render_radius > 6000:
        st.caption('広域表示ではピン・一覧のみ表示します。拡大すると100m単位の色分けと取得が利用できます。')
    if bounds is None:
        st.caption('地図の表示範囲を読み込み中です。地図を少し動かすと取得ボタンが有効になります。')
    st.caption(f'Supabaseから読み込み済み：{len(state.records02)}物件。取得後は自動保存し、次回起動時もSupabaseから読み込みます。')
    labels = ['15万円以下', '15〜20万円', '20〜22.5万円', '22.5〜25万円', '25〜27.5万円', '27.5〜30万円', '30〜35万円', '35万円超']
    st.markdown('<div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0">'+''.join(
        f'<span style="font-size:13px;color:#173a5e"><i style="display:inline-block;width:13px;height:13px;background:{color};margin-right:4px"></i>{label}</span>'
        for color, label in zip(COLORS, labels))+'</div>', unsafe_allow_html=True)
    st.caption('家賃＋管理費等の月額総額｜濃い面：そのメッシュの掲載額集計 / 薄い破線の面：近隣建物からの推定 / 無色：データ不足｜ドットは掲載座標')
    observed_count = sum(c['kind'] == '掲載物件の集計' for c in cells)
    c1, c2, c3 = st.columns(3)
    c1.metric('範囲内の募集住戸', f'{len(nearby)}件')
    c2.metric('掲載建物の位置数', f'{len({(round(p["lat"],5),round(p["lng"],5)) for p in nearby})}箇所')
    c3.metric('色分けメッシュ', f'{len(cells)}区画')
    st.caption(f'直接集計 {observed_count}区画 / 近隣推定 {len(cells)-observed_count}区画。募集住戸は同じ位置・間取り・階・面積・金額の重複を除いています。')
    with st.expander('表示範囲の家賃帯一覧', expanded=True):
        counts = [0]*len(COLORS)
        for p in nearby:
            counts[sum((p['rent']+p['fees'])/10000 > cut for cut in BANDS)] += 1
        st.dataframe([{'家賃帯（管理費等込み）':label, '募集住戸数':count}
                      for label,count in zip(labels,counts)], hide_index=True, width='stretch')
        st.caption('選択した間取り・表示範囲の保存済み募集物件を集計。予算上限で絞る前の件数です。')
    info = state.info02
    if info:
        stamp = info.get('fetched_at', '')
        try:
            from zoneinfo import ZoneInfo
            stamp = datetime.fromisoformat(stamp).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y/%m/%d %H:%M（日本時間）')
        except (ValueError, TypeError):
            pass
        st.caption(f'取得日時：{stamp}｜取得元：{info.get("source_url", "CSV")}')
        if info.get('regions'):
            st.caption('表示範囲から判定した地域：'+'、'.join(info['regions']))
        if info.get('source_counts'):
            counts = info['source_counts']
            st.caption(f'今回取得：HOME’S {counts.get("LIFULL HOME'S",0)}件 / SUUMO {counts.get("SUUMO",0)}件')
        for warning in info.get('warnings', []):
            st.caption(warning)
    if not nearby:
        st.info('この地域・間取りの取得データがありません。地図下のボタンで表示範囲の物件を取得するか、座標付きCSVを読み込んでください。架空の家賃で塗り分けは行いません。')
    elif len({(round(p['lat'],5),round(p['lng'],5)) for p in nearby}) < 3:
        st.info('建物の位置数が少ないため、近隣メッシュの推定範囲が限られます。')
    st.caption('掲載位置には誤差がある場合があります。建物ごとの募集総額中央値を集計・推定するため、築年数や面積の違いも色に影響します。各物件の募集ページで現在の条件を確認してください。')
    if displayed:
        with st.expander('地図内の物件一覧・通勤を確認', expanded=False):
            st.dataframe([{'物件名': p['name'], '総額（万円）': (p['rent']+p['fees'])/10000,
                           '家賃（円）': p['rent'], '管理費等（円）': p['fees'], '面積（㎡）': p['area'],
                           '所在階': p['floor'], '住所': p['address'], '掲載更新日': p['modified'],
                           '募集ページ': p['url']} for p in sorted(displayed, key=lambda x: x['rent']+x['fees'])],
                         hide_index=True, width='stretch', column_config={'募集ページ': st.column_config.LinkColumn('募集ページ')})
            options = [f'{i+1}. {p["name"]} / {(p["rent"]+p["fees"])/10000:g}万 / {p["area"]:g}㎡' for i,p in enumerate(displayed)]
            chosen = st.selectbox('通勤を確認する物件', options, key='property02')
            property_data = displayed[options.index(chosen)]
            destination = st.selectbox('勤務先の最寄り駅', list(BY_NAME), key='destination02')
            work_walk = st.number_input('駅 → 勤務先（徒歩・分）', 0, 60, 8, key='work_walk02')
            closest = min(STATIONS, key=lambda s: distance(property_data,s))
            walk_distance = distance(property_data,closest)*1.25
            home_walk = math.ceil(walk_distance/.08)
            r = route(closest['name'], destination)
            total = math.ceil(home_walk+r['time']+work_walk+(3 if r['legs'] else 0))
            c1,c2 = st.columns(2)
            c1.metric('ドアツードア概算', f'約{total}分')
            c2.metric('乗り換え（登録路線）', f'{r["transfers"]}回')
            st.caption(f'登録41駅のうち最も近い {closest["name"]}駅へ徒歩約{home_walk}分（直線距離×1.25で推定）。実際には未登録駅を使う方が便利な場合があります。')
            st.caption(f'総移動距離の概算：{walk_distance+r["km"]+work_walk*.08:.1f}km。時刻表・急行・直通運転・住所単位の徒歩経路は未対応。')
            route_url = 'https://www.google.com/maps/dir/?'+urlencode({'api':1,'origin':f'{property_data["lat"]},{property_data["lng"]}',
                          'destination':f'{BY_NAME[destination]["lat"]},{BY_NAME[destination]["lng"]}','travelmode':'transit'})
            st.link_button('実際の通勤経路・時刻を確認', route_url)
            used = list(dict.fromkeys(l[2] for l in r['legs']))
            if used:
                st.dataframe([{'利用路線':LINES[i]['name'],'混雑率':f'{LINES[i]["rate"]}%',
                               '公表区間':LINES[i]['section'],'調査時間帯':LINES[i]['period']} for i in used],hide_index=True,width='stretch')
            st.caption('混雑率は国交省2025年度・代表区間の朝1時間平均。選択した経路・方向・時刻の混雑ではありません。')
            st.link_button('混雑率の原資料', CROWD_SOURCE)
    with st.expander('スーパーなど周辺施設を表示'):
        category = st.selectbox('施設の種類', list(FACILITY_FILTERS), key='facility02')
        if st.button('中心駅から1kmの施設を取得',key='facility_fetch02'):
            try:
                state.facilities02 = fetch_facilities_at(origin['lat'],origin['lng'],category)
                state.flash02 = f'{len(state.facilities02)}施設を取得しました。'
                st.rerun()
            except (requests.RequestException,ValueError,TypeError):
                st.error('施設を取得できませんでした。Google マップで確認してください。')
        st.link_button('Google マップで周辺施設を確認','https://www.google.com/maps/search/?'+urlencode({'api':1,'query':station_name+' '+category}))
        st.caption('OpenStreetMap登録施設を使用。登録漏れ・閉店・位置誤差がある場合があります。')
    with st.expander('Supabase保存・CSVバックアップ・読み込み'):
        st.caption('物件・取得範囲・間取り・取得日時をSupabaseに保存します。再デプロイ後も同じSupabaseから読み込みます。v03で取得したCSVは、ここからSupabaseへ追加保存できます。v01の駅単位CSVには物件位置がないため使用できません。')
        if st.button('Supabaseの保存データを再読み込み',key='reload04'):
            try:
                records, info = load_store()
                state.records02, state.info02 = records, info
                state.flash02 = f'Supabaseから{len(records)}物件を読み込みました。'
                st.rerun()
            except StorageError as error:
                st.error(str(error))
        st.download_button('取得した物件データを保存',export_records(state.records02),'sumai_geo_properties.csv','text/csv')
        upload = st.file_uploader('座標付き物件CSV',type=['csv'])
        if st.button('CSVの物件データを追加・保存',disabled=upload is None):
            try:
                imported = validate_records(upload.getvalue())
                info = dict(source_url='CSV',fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),warnings=[])
                loaded = save_store(imported, info)
                state.records02, state.info02 = loaded, info
                state.flash02 = f'{len(imported)}物件を読み込みました。'
                st.rerun()
            except (ValueError,UnicodeError,csv.Error,StorageError,OSError) as error:
                st.error(str(error))
    with st.expander('集計・推定・データ取得の方法'):
        st.write('家賃は実際の募集額＋管理費等。敷金・礼金・駐車場・その他の月額サービス料は含みません。管理費等が確認できない物件、掲載座標がない物件は除外します。')
        st.write('直接集計：各建物位置の募集住戸総額の中央値を求め、そのメッシュ内の建物中央値をさらに集計します。掲載位置が同一の建物は同じ位置群として扱います。')
        st.write('近隣推定：メッシュ中心から指定距離内に3建物位置以上ある場合だけ、距離の逆二乗で加重平均します。これは近隣の募集額を使った推定であり、その場所に実在する募集中の物件価格ではありません。駅の一つの家賃を全域に広げる計算ではありません。')
        st.write('取得時は表示範囲を3×3の小区画で確認し、町名ごとにHOME’SとSUUMOを少量ずつ取得してSupabaseへ順次保存します。HOME’Sは掲載座標、SUUMOは一覧に出る住所を住所検索で座標化し、表示範囲外は除外します。')
        st.write('募集状況は取得日時の公開ページ情報。取得できる候補・件数上限で偏りがあり、地域全体の相場推計ではありません。自動取得は公開ページの構造・接続状況で停止することがあります。認証やアクセス制限の回避は行いません。')
        st.write('背景地図：© OpenStreetMap contributors。初期データは池袋駅の2LDKの取得結果。間取りや地域を変更した場合は、新しいデータの取得が必要です。')


if __name__ == '__main__':
    main()
