"""住まいコンパス / REBUILD 01 — complete independent implementation.
Run: streamlit run app.py
Dependencies: streamlit>=1.50,<2, requests>=2.32,<3, beautifulsoup4>=4.13,<5, folium>=0.18,<1, streamlit-folium==0.24.0, numpy>=1.26,<3, Pillow>=10,<12.
Persistent storage: Supabase housing_units_v1, housing_searches_v1, housing_places_v1.
Search and available-field storage run on server worker threads; UI polls snapshots.
New flexible listings are JSON records in housing_searches_v1; old housing_units_v1 records remain readable.
"""
from __future__ import annotations

import base64
import concurrent.futures as futures
import csv
import hashlib
import heapq
import html
import io
import json
import math
import os
import calendar
import queue
import re
import shutil
import statistics
import tempfile
import sqlite3
import traceback
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, quote

# The active town map is Leaflet-based and does not require branca or folium.
# Keep branca optional: Streamlit Cloud can run this app without the legacy
# point-map dependencies installed in its requirements.txt.
try:
    from branca.element import MacroElement, Template
except ModuleNotFoundError as exc:
    if exc.name not in ('branca', 'branca.element'):
        raise
    # Only the unused legacy point-map classes refer to these names at import
    # time. Folium (which requires branca) is imported inside that legacy path.
    MacroElement = object
    def Template(source):
        return source
# folium is loaded only by the legacy point-map fallback, not the active town map.
import requests
import streamlit as st
# streamlit_folium is deferred for the same legacy path.
from bs4 import BeautifulSoup

BUILD = "REBUILD-01-v228"
_V127_INSPECTION_ONLY = True  # Do not reclaim cache until the actual file inventory is reviewed.

# ============================================================================
# SUUMO MAP-GRANULARITY POLICY (revised at the user's request, v150)
# ============================================================================
# 1) When the detail page's explicitly labelled 所在地 identifies a town/chome
#    that can be used on the town rent map, accept it at precisely that level.
# 2) Verify Tokyo ward and requested town; never synthesize building numbers.
# 3) If the detail address is too coarse, examine the listing's own map marker,
#    then use its GIS reverse result only to identify map-relevant town/chome.
# 4) A map center, neighborhood centroid or nearest unverified street number
#    must NEVER be presented as a building's actual position or street address.
# 5) Keep old rental_listing and acquisition records unchanged. Version-150
#    observations use isolated IDs; old/new records share read-time map keys
#    and deduplicate by SUUMO property ID in the town rent aggregator.
# 6) Continue searching age<=15-year target-layout mansions and age<=40-year,
#    >=50sqm rental houses. Actual save means Supabase POST+GET field readback.
SUUMO_TEXT_ADDRESS_FORBIDDEN = False
SUUMO_MAP_ONLY_ADDRESS_POLICY = 'published_town_first_unique_property_map_fallback_v150'

# ============================================================================
# ============================================================================
# NON-NEGOTIABLE HOMES ADDRESS / REGION-SEARCH POLICY -- DO NOT DELETE OR WEAKEN
# ============================================================================
# 1) HOME'S list/detail-page textual address MUST NOT be used to create, geocode,
#    complete, correct, compare, or validate the saved property address.
# 2) HOME'S regional search starts from the internally derived ward/town list,
#    opens the ward page, selects the matching 町域, then applies mansion / <=15y
#    and the two layout groups in order.
# 3) Every result is opened as an individual detail page. Rent/layout are read
#    from that detail page; list-page 所在地 is ignored.
# 4) Position/address acquisition starts from that listing's own 地図を見る map.
#    Prefer the map image pin; when the map is an embedded Google map, published
#    ll/center/@ coordinates are a fallback. Detailed address is inferred from
#    map-derived coordinates and independent address/building data only.
# 5) A listing must not be saved unless rent, target layout, and a detailed
#    map-derived address are all available.
# 6) In addition to the existing mansion searches, HOME'S must also search
#    detached houses (kodate) with age <= 40 years and area >= 50 sqm.
# These rules are part of the application specification. Future edits must keep them.
# The detached-house branch is additive: do not weaken/remove the existing mansion flow.
HOMES_TEXT_ADDRESS_FORBIDDEN = True
HOMES_MAP_ONLY_ADDRESS_POLICY = 'map_image_or_published_center_plus_identity_v1'
AUTOMATIC_REGION_PROVIDERS = ['SUUMO']

PROVIDER_OPTIONS = ('SUUMO',)
PROVIDER_CANONICAL = {"HOMES":"HOME’S","HOME’S":"HOME’S","スマイティ":"スマイティ","SUUMO":"SUUMO","カナリー":"カナリー",
                      "アットホーム":"アットホーム","CHINTAI":"CHINTAI","Comfy":"Comfy","アパマンショップ":"アパマンショップ"}
PROVIDER_DISPLAY = {"HOME’S":"HOMES",**{x:x for x in PROVIDER_OPTIONS if x!="HOMES"}}
PROVIDER_HOST = {
    "スマイティ":"sumaity.com","HOME’S":"www.homes.co.jp","SUUMO":"suumo.jp","カナリー":"web.canary-app.jp",
    "アットホーム":"www.athome.co.jp","CHINTAI":"www.chintai.net","Comfy":"comfy.maison","アパマンショップ":"www.apamanshop.com",
}
RENTAL_HOSTS = frozenset(PROVIDER_HOST.values())
# Concurrency is increased across independent tasks/hosts only. Per-host request
# spacing and in-flight limits remain conservative, so faster collection comes
# from overlap, caching and fewer duplicate requests rather than hitting one site harder.
# Bound concurrent BeautifulSoup trees and HTTP responses on Streamlit Cloud.
# Peak memory matters more than simultaneous SUUMO requests.
SEARCH_TASK_WORKERS = 2
DETAIL_WORKERS = 2
DETAIL_PAGE_WORKERS = 2
PER_HOST_SLOTS = 2
PER_HOST_MIN_INTERVAL = 1.0
ADDRESS_NEGATIVE_CACHE_SECONDS = 900
PREF_ALIAS = {"11":"saitama","12":"chiba","13":"tokyo","14":"kanagawa"}

# HOME'S Tokyo 23 wards: direct city-list URLs.
# Do not fetch /chintai/mansion/tokyo/ or /chintai/kodate/tokyo/ first for these wards.
# Those prefecture entry pages have returned CloudFront 403 from the app server, while
# the public city-list URLs are independently addressable. Keep this mapping explicit.
HOMES_TOKYO23_CITY_SLUGS = {
    '13101':'chiyoda-city','13102':'chuo-city','13103':'minato-city',
    '13104':'shinjuku-city','13105':'bunkyo-city','13106':'taito-city',
    '13107':'sumida-city','13108':'koto-city','13109':'shinagawa-city',
    '13110':'meguro-city','13111':'ota-city','13112':'setagaya-city',
    '13113':'shibuya-city','13114':'nakano-city','13115':'suginami-city',
    '13116':'toshima-city','13117':'kita-city','13118':'arakawa-city',
    '13119':'itabashi-city','13120':'nerima-city','13121':'adachi-city',
    '13122':'katsushika-city','13123':'edogawa-city',
}

def homes_direct_city_list_url(region, category='mansion'):
    """Return HOME'S city list directly for Tokyo 23 wards; never use text address."""
    code=str(region.get('code',''))
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    slug=HOMES_TOKYO23_CITY_SLUGS.get(code)
    if alias=='tokyo' and slug and category in ('mansion','kodate'):
        return f'https://www.homes.co.jp/chintai/{category}/tokyo/{slug}/list/'
    return None

# HOME'S transport policy (v93):
# - First use the existing lightweight HTTP client.
# - If HOME'S itself returns HTTP 403, retry that same public URL with an actual
#   headless Chromium browser when Chromium/Selenium are installed.  This is a
#   normal browser navigation path: no stealth plugin, CAPTCHA bypass, fingerprint
#   spoofing, proxy rotation, or access-control circumvention is implemented.
# - Keep one browser session/cookie jar and one HOME'S navigation at a time.
# - If HOME'S also refuses the real browser, stop HOME'S for that run and continue
#   with SUUMO exactly as before.
# The application-level robotparser check is not an HTTP rejection. For the
# user-requested first-stage direct search only, use a consistent navigation
# header profile. This cannot force remote servers to return a usable page.
# No OS proxy/VPN settings are modified, and no CAPTCHA solving is attempted.
_STAGE1_DIRECT_NAV_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.6',
    'Upgrade-Insecure-Requests': '1',
}
_STAGE1_DIRECT_MIN_INTERVAL = 5.0

HOMES_BROWSER_FALLBACK = True
HOMES_BROWSER_PAGE_TIMEOUT = 20
HOMES_BROWSER_RENDER_WAIT = 0.8
HOMES_BROWSER_MIN_INTERVAL = 2.0

# HOME'S availability policy:
# - A server-side HTTP 403 from HOME'S is treated as provider unavailability, not
#   as a reason to stop the whole collection.
# - We do not try to defeat the access control.  HOME'S is disabled for the current
#   run/controller and collection continues with SUUMO.
# - A new user-initiated collection may test HOME'S again, because the site's policy
#   or CloudFront routing may have changed meanwhile.
def is_homes_http403_error(exc):
    if not isinstance(exc,AppError):return False
    diagnostic=getattr(exc,'diagnostic',{}) or {}
    try:status=int(diagnostic.get('status')) if diagnostic.get('status') is not None else None
    except (TypeError,ValueError):status=None
    return status==403 or bool(re.search(r'(?:^|\D)HTTP\s*403(?:\D|$)',str(exc),re.I))


def homes_runtime_disabled(web):
    state=getattr(web,'provider_runtime_disabled',{}) or {}
    item=state.get('HOME’S') or state.get('HOMES')
    return dict(item) if isinstance(item,dict) else None


def disable_homes_for_runtime(web,exc,url=''):
    """Open a per-runtime circuit breaker after a genuine HOME'S HTTP 403."""
    if not is_homes_http403_error(exc):return None
    diagnostic=getattr(exc,'diagnostic',{}) or {}
    item={'provider':'HOME’S','reason':'HTTP 403','message':'HOME’SがHTTP 403を返したため、この実行ではHOME’Sを停止しSUUMOのみで続行します。',
          'url':url or normal(diagnostic.get('url')),'status':403,'detected_at':utc_now()}
    lock=getattr(web,'cache_lock',None)
    if lock:
        with lock:
            state=getattr(web,'provider_runtime_disabled',None)
            if not isinstance(state,dict):state={};web.provider_runtime_disabled=state
            state['HOME’S']=dict(item)
    else:
        state=getattr(web,'provider_runtime_disabled',None)
        if not isinstance(state,dict):state={};web.provider_runtime_disabled=state
        state['HOME’S']=dict(item)
    trace(web,'homes_http403_fallback',item,'WARNING','provider_fallback')
    try:web.close_homes_browser()
    except Exception:pass
    return item


def homes_preflight_or_fallback(web,regions):
    """Probe one direct ward URL once; HTTP 403 opens the HOME'S circuit breaker."""
    disabled=homes_runtime_disabled(web)
    if disabled:return False,disabled
    probe_region=next((r for r in (regions or []) if homes_direct_city_list_url(r,'mansion')),None)
    if not probe_region:return True,None
    url=homes_direct_city_list_url(probe_region,'mansion')
    try:
        if not web.permitted(url):raise AppError('HOMESの区別一覧ページの自動取得が許可されていません。')
        web.fetch(url)
        trace(web,'homes_preflight_ok',{'url':url,'decision':'HOME’Sを利用'},stage='provider_fallback')
        return True,None
    except AppError as exc:
        if is_homes_http403_error(exc):
            return False,disable_homes_for_runtime(web,exc,url)
        raise
DEFAULT_CENTER = (35.7303,139.711)
# Restore the specified home view from app.py_ver.201.
_V202_HOME_CENTER = (35.669,139.757)
_V202_HOME_ZOOM = 11
_V202_HOME_WINDOW = (35.50,139.57,35.84,139.94)
UNIT_TABLE = "housing_units_v1"
SEARCH_TABLE = "housing_searches_v1"
PLACE_TABLE = "housing_places_v1"
ADDRESS_POINT_KIND = "saved_address_point_v1"
GROUPS = {"ワンルーム・1K・1DK": ("1R", "1K", "1DK"), "1LDK・2DK": ("1LDK", "2DK"),
          "2LDK・3DK": ("2LDK", "3DK"), "3LDK": ("3LDK",)}
LAYOUTS = tuple(x for group in GROUPS.values() for x in group)
SUUMO_LAYOUT_GROUPS = (("1R", "1K", "1DK"), ("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
SUUMO_LAYOUTS = tuple(x for group in SUUMO_LAYOUT_GROUPS for x in group)
SUUMO_LAYOUT_CODES = {"1R":"01","1K":"02","1DK":"03","1LDK":"04","2K":"05","2DK":"06","2LDK":"07","3K":"08","3DK":"09"}
HOMES_LAYOUT_GROUPS = (("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
DISPLAY_LAYOUT_GROUPS = {"group0": ("1R", "1K", "1DK"), "group1": ("1LDK", "2K", "2DK"), "group2": ("2LDK", "3K", "3DK")}
HOMES_LAYOUTS = tuple(x for group in HOMES_LAYOUT_GROUPS for x in group)
HOUSE_MAX_AGE = 40
HOUSE_MIN_AREA = 50.0
# SUUMO building type filter: mansion=1, apartment=2, house/other=3.
# Search 1 and 3 in separate passes so each has its own age/layout rules.
SUUMO_BUILDING_TYPE_CODES = {'マンション':'1','アパート':'2','一戸建て・その他':'3'}
SUUMO_TARGET_BUILDING_TYPES = ('マンション','一戸建て・その他')
# SUUMO's public age dropdown may not offer a 40-year value. Do not submit
# an unconfirmed cn=40 code which could produce an apparent zero-result page.
# Keep the >=50 sqm public search filter and verify <=40 years in each detail.
SUUMO_HOUSE_PARAMS = {'ts':SUUMO_BUILDING_TYPE_CODES['一戸建て・その他'],'mb':'50','pc':'50'}
ALL_TARGET_LAYOUTS = tuple(dict.fromkeys((*LAYOUTS,*SUUMO_LAYOUTS,*HOMES_LAYOUTS,'1SLDK','2SLDK','3SLDK','1SDK','2SDK','3SDK','1SK')))
WARD_LABELS = {
    "千代田区":(35.6938,139.7535), "中央区":(35.6707,139.7727), "港区":(35.6581,139.7516),
    "新宿区":(35.6938,139.7034), "文京区":(35.7081,139.7522), "台東区":(35.7126,139.7800),
    "墨田区":(35.7107,139.8015), "江東区":(35.6728,139.8174), "品川区":(35.6092,139.7302),
    "目黒区":(35.6415,139.6982), "大田区":(35.5613,139.7161), "世田谷区":(35.6466,139.6532),
    "渋谷区":(35.6640,139.6982), "中野区":(35.7075,139.6637), "杉並区":(35.6995,139.6364),
    "豊島区":(35.7263,139.7167), "北区":(35.7528,139.7336), "荒川区":(35.7361,139.7833),
    "板橋区":(35.7512,139.7093), "練馬区":(35.7356,139.6517), "足立区":(35.7753,139.8047),
    "葛飾区":(35.7436,139.8476), "江戸川区":(35.7066,139.8682),
}
MAJOR_STATION_LABELS = ("東京","新宿","渋谷","池袋","品川","上野","秋葉原","大崎","目黒","恵比寿","中野","北千住","錦糸町","蒲田")
# v158: Additional transport hubs for zoomed-out maps.  These names are
# already in the offline reference table.  Rank below the 14 anchor hubs;
# no new HTTP/API requests or extra map-layer data fetches.
REGIONAL_HUB_STATION_LABELS = (
    "赤羽", "高田馬場", "巣鴨", "大塚", "日暮里", "新橋", "御茶ノ水", "飯田橋",
    "四ツ谷", "原宿", "代々木", "浅草", "押上", "両国", "大井町", "五反田",
    "中目黒", "下北沢", "三軒茶屋", "自由が丘", "荻窪", "吉祥寺", "武蔵小杉",
    "川崎", "横浜", "豊洲", "王子", "駒込", "練馬", "田町", "浜松町",
    "西日暮里", "西新井", "武蔵小山", "神田", "浅草橋", "新小岩",
    "有楽町", "大手町", "光が丘", "成増", "立川", "南千住",
    "石神井公園", "西荻窪",
)

STATIONS = {
    "東京": (35.6812,139.7671), "品川":(35.6285,139.7388), "大崎":(35.6197,139.7286),
    "五反田":(35.6264,139.7235), "目黒":(35.6339,139.7158), "恵比寿":(35.6467,139.7101),
    "渋谷":(35.6580,139.7016), "新宿":(35.6909,139.7003), "池袋":(35.7295,139.7109),
    "上野":(35.7138,139.7770), "秋葉原":(35.6984,139.7731), "赤羽":(35.7780,139.7209),
    "川口":(35.8019,139.7175), "浦和":(35.8585,139.6571), "大宮":(35.9063,139.6238),
    "大井町":(35.6063,139.7346), "蒲田":(35.5625,139.7160), "川崎":(35.5313,139.6970),
    "横浜":(35.4662,139.6220), "武蔵小杉":(35.5750,139.6595), "日吉":(35.5530,139.6468),
    "自由が丘":(35.6074,139.6685), "中目黒":(35.6443,139.6990), "中野":(35.7058,139.6658),
    "荻窪":(35.7045,139.6201), "吉祥寺":(35.7032,139.5797), "三鷹":(35.7027,139.5603),
    "国分寺":(35.7001,139.4808), "立川":(35.6982,139.4137), "錦糸町":(35.6960,139.8140),
    "新小岩":(35.7169,139.8586), "市川":(35.7289,139.9084), "船橋":(35.7017,139.9850),
    "津田沼":(35.6907,140.0207), "千葉":(35.6134,140.1133), "北千住":(35.7494,139.8050),
    "松戸":(35.7847,139.9007), "柏":(35.8622,139.9711), "二子玉川":(35.6117,139.6267),
    "溝の口":(35.5998,139.6115), "南浦和":(35.8476,139.6690),
}

# Expanded, offline station label reference set (not used for travel-time estimates).
# Source: select766/tokyo-train-time-map/data/station_locations.csv (MIT License).
# https://github.com/select766/tokyo-train-time-map
# Open-but-unbuilt planned stations are intentionally excluded. No runtime HTTP/DB read.
STATION_MAP_POINTS = {
    '一之江': (35.685940, 139.882890),
    '三ノ輪': (35.729285, 139.791165),
    '三田': (35.647875, 139.749480),
    '三越前': (35.684930, 139.773080),
    '上野': (35.711835, 139.775625),
    '上野広小路': (35.707770, 139.773000),
    '上野御徒町': (35.707960, 139.773065),
    '両国': (35.695755, 139.793350),
    '中井': (35.713865, 139.686310),
    '中延': (35.605175, 139.713590),
    '中目黒': (35.644105, 139.698835),
    '中野': (35.705555, 139.666085),
    '中野坂上': (35.697085, 139.682205),
    '中野富士見町': (35.690820, 139.668155),
    '中野新橋': (35.691715, 139.673980),
    '乃木坂': (35.666615, 139.726210),
    '九段下': (35.695510, 139.751115),
    '亀戸': (35.697340, 139.826610),
    '二重橋前': (35.680505, 139.761780),
    '五反田': (35.627110, 139.724185),
    '京橋': (35.676700, 139.770115),
    '人形町': (35.686350, 139.782170),
    '代々木': (35.683795, 139.702210),
    '代々木上原': (35.669015, 139.679885),
    '代々木公園': (35.669100, 139.689790),
    '仲御徒町': (35.706655, 139.776190),
    '住吉': (35.688705, 139.815670),
    '信濃町': (35.679965, 139.721100),
    '光が丘': (35.758470, 139.629240),
    '入谷': (35.720570, 139.784460),
    '八丁堀': (35.675365, 139.777340),
    '六本木': (35.664065, 139.731275),
    '六本木一丁目': (35.665075, 139.738900),
    '内幸町': (35.669175, 139.755340),
    '勝どき': (35.658945, 139.777155),
    '北千住': (35.748845, 139.804640),
    '北参道': (35.678485, 139.705435),
    '北綾瀬': (35.776890, 139.832095),
    '千川': (35.738175, 139.689400),
    '千石': (35.727965, 139.744745),
    '千駄ヶ谷': (35.681195, 139.711530),
    '千駄木': (35.725745, 139.763305),
    '半蔵門': (35.685380, 139.741695),
    '南千住': (35.732310, 139.798780),
    '南砂町': (35.668415, 139.831695),
    '南阿佐ヶ谷': (35.699455, 139.635575),
    '原宿': (35.671310, 139.702730),
    '吉祥寺': (35.702290, 139.580310),
    '品川': (35.629640, 139.740130),
    '四ツ谷': (35.685650, 139.730540),
    '四谷三丁目': (35.687835, 139.719325),
    '国会議事堂前': (35.675005, 139.745410),
    '国立競技場': (35.679855, 139.714875),
    '地下鉄成増': (35.776740, 139.631170),
    '地下鉄赤塚': (35.769980, 139.644000),
    '外苑前': (35.670375, 139.717825),
    '大久保': (35.700635, 139.697445),
    '大塚': (35.731785, 139.727940),
    '大島': (35.689765, 139.834360),
    '大崎': (35.619945, 139.728245),
    '大手町': (35.684695, 139.766030),
    '大門': (35.656680, 139.755705),
    '宝町': (35.675670, 139.772115),
    '小伝馬町': (35.690425, 139.778760),
    '小岩': (35.733330, 139.881850),
    '小川町': (35.695215, 139.766860),
    '小竹向原': (35.743395, 139.679520),
    '岩本町': (35.695575, 139.775185),
    '巣鴨': (35.733720, 139.740350),
    '市ヶ谷': (35.691455, 139.737385),
    '平井': (35.706450, 139.842385),
    '平和台': (35.757555, 139.654300),
    '広尾': (35.651500, 139.722210),
    '後楽園': (35.707310, 139.750860),
    '御徒町': (35.707195, 139.774715),
    '御成門': (35.660985, 139.751485),
    '御茶ノ水': (35.699605, 139.765030),
    '志村三丁目': (35.777465, 139.685860),
    '志村坂上': (35.776340, 139.694730),
    '志茂': (35.778025, 139.732500),
    '恵比寿': (35.646680, 139.710125),
    '戸越': (35.614425, 139.716375),
    '押上': (35.708470, 139.813750),
    '新中野': (35.697475, 139.669510),
    '新大久保': (35.700930, 139.700255),
    '新大塚': (35.726130, 139.729355),
    '新宿': (35.689210, 139.701220),
    '新宿三丁目': (35.690840, 139.706535),
    '新宿御苑前': (35.688525, 139.710915),
    '新宿西口': (35.693160, 139.699160),
    '新富町': (35.670530, 139.773595),
    '新小岩': (35.716785, 139.857650),
    '新御徒町': (35.707005, 139.781910),
    '新御茶ノ水': (35.696925, 139.765450),
    '新日本橋': (35.689040, 139.774295),
    '新木場': (35.645830, 139.826600),
    '新板橋': (35.748840, 139.719365),
    '新橋': (35.667375, 139.758510),
    '新江古田': (35.732490, 139.670565),
    '新高円寺': (35.697860, 139.648510),
    '新高島平': (35.790220, 139.654330),
    '方南町': (35.683515, 139.657935),
    '日暮里': (35.727330, 139.771015),
    '日本橋': (35.681880, 139.773335),
    '日比谷': (35.676620, 139.760145),
    '早稲田': (35.705680, 139.722190),
    '明治神宮前': (35.668205, 139.705240),
    '春日': (35.708495, 139.752170),
    '曙橋': (35.692330, 139.722580),
    '月島': (35.664710, 139.784405),
    '有楽町': (35.675040, 139.763260),
    '木場': (35.669350, 139.807115),
    '末広町': (35.702780, 139.771765),
    '本所吾妻橋': (35.708595, 139.804560),
    '本蓮沼': (35.768985, 139.702110),
    '本郷三丁目': (35.706705, 139.760135),
    '本駒込': (35.724365, 139.753815),
    '東中野': (35.706250, 139.685080),
    '東京': (35.681195, 139.767440),
    '東大前': (35.717985, 139.757820),
    '東大島': (35.689790, 139.847265),
    '東新宿': (35.697895, 139.707690),
    '東日本橋': (35.691780, 139.784800),
    '東池袋': (35.725950, 139.718865),
    '東銀座': (35.669655, 139.767125),
    '東陽町': (35.669590, 139.817680),
    '東高円寺': (35.697965, 139.658295),
    '板橋区役所前': (35.751305, 139.710055),
    '板橋本町': (35.761100, 139.705605),
    '根津': (35.717325, 139.765750),
    '桜田門': (35.677465, 139.751885),
    '森下': (35.688005, 139.798200),
    '水天宮前': (35.682995, 139.785165),
    '水道橋': (35.702000, 139.753745),
    '氷川台': (35.749620, 139.665470),
    '永田町': (35.678010, 139.741815),
    '汐留': (35.664915, 139.761270),
    '江戸川橋': (35.709295, 139.734130),
    '池袋': (35.727660, 139.710830),
    '泉岳寺': (35.638130, 139.739730),
    '浅草': (35.708990, 139.796595),
    '浅草橋': (35.697390, 139.784490),
    '浜松町': (35.655410, 139.757125),
    '浜町': (35.688590, 139.788125),
    '淡路町': (35.694870, 139.767455),
    '清澄白河': (35.682945, 139.798665),
    '渋谷': (35.659095, 139.702674),
    '湯島': (35.706805, 139.769980),
    '溜池山王': (35.673090, 139.741295),
    '牛込柳町': (35.699460, 139.725190),
    '牛込神楽坂': (35.700915, 139.736040),
    '王子': (35.752325, 139.738210),
    '王子神谷': (35.765295, 139.735650),
    '瑞江': (35.693390, 139.897810),
    '田原町': (35.709910, 139.790305),
    '田町': (35.645740, 139.747605),
    '田端': (35.737475, 139.761575),
    '町屋': (35.742070, 139.780045),
    '白山': (35.721335, 139.752210),
    '白金台': (35.637770, 139.725860),
    '白金高輪': (35.643145, 139.734285),
    '目白': (35.720390, 139.706280),
    '目黒': (35.632940, 139.715900),
    '神保町': (35.694930, 139.758430),
    '神楽坂': (35.703865, 139.734520),
    '神田': (35.691770, 139.770875),
    '神谷町': (35.662625, 139.744725),
    '秋葉原': (35.698075, 139.773240),
    '稲荷町': (35.711385, 139.782210),
    '竹橋': (35.690440, 139.757670),
    '築地': (35.667925, 139.772475),
    '築地市場': (35.664960, 139.766750),
    '篠崎': (35.706120, 139.903885),
    '綾瀬': (35.762165, 139.824935),
    '練馬': (35.737315, 139.654570),
    '練馬春日町': (35.751360, 139.640760),
    '船堀': (35.683785, 139.863855),
    '芝公園': (35.653455, 139.749720),
    '若松河田': (35.699165, 139.718185),
    '茅場町': (35.679075, 139.779625),
    '茗荷谷': (35.717200, 139.736895),
    '荻窪': (35.704430, 139.620245),
    '菊川': (35.688410, 139.806125),
    '落合': (35.710635, 139.685965),
    '落合南長崎': (35.723385, 139.683510),
    '葛西': (35.663620, 139.872530),
    '蓮根': (35.784055, 139.679060),
    '蔵前': (35.703145, 139.790855),
    '虎ノ門': (35.670160, 139.750120),
    '虎ノ門ヒルズ': (35.667444, 139.747778),
    '表参道': (35.665145, 139.712380),
    '西ヶ原': (35.745945, 139.742260),
    '西台': (35.787080, 139.672680),
    '西大島': (35.689345, 139.826320),
    '西巣鴨': (35.743415, 139.728725),
    '西新宿': (35.694515, 139.692560),
    '西新宿五丁目': (35.689900, 139.684665),
    '西日暮里': (35.732080, 139.766775),
    '西早稲田': (35.707800, 139.709150),
    '西荻窪': (35.703785, 139.599450),
    '西葛西': (35.664560, 139.859600),
    '西馬込': (35.587305, 139.706320),
    '西高島平': (35.791895, 139.645685),
    '要町': (35.733215, 139.698480),
    '護国寺': (35.719180, 139.727450),
    '豊島園': (35.741610, 139.649205),
    '豊洲': (35.655155, 139.795995),
    '赤坂': (35.672080, 139.736435),
    '赤坂見附': (35.676865, 139.737385),
    '赤羽岩淵': (35.783355, 139.722110),
    '赤羽橋': (35.654970, 139.744195),
    '辰巳': (35.645775, 139.810330),
    '都庁前': (35.690590, 139.692625),
    '銀座': (35.673235, 139.763830),
    '銀座一丁目': (35.674510, 139.766780),
    '錦糸町': (35.696720, 139.814320),
    '門前仲町': (35.672770, 139.795105),
    '阿佐ヶ谷': (35.704880, 139.635855),
    '雑司が谷': (35.720200, 139.714715),
    '霞ヶ関': (35.674235, 139.752680),
    '青山一丁目': (35.672955, 139.724075),
    '飯田橋': (35.701420, 139.746530),
    '馬喰横山': (35.692010, 139.782925),
    '馬喰町': (35.693370, 139.782435),
    '馬込': (35.596445, 139.711850),
    '駒込': (35.736860, 139.748055),
    '高円寺': (35.705325, 139.649700),
    '高島平': (35.788935, 139.661295),
    '高田馬場': (35.713280, 139.705045),
    '高輪ゲートウェイ': (35.635500, 139.740700),
    '高輪台': (35.631680, 139.730365),
    '鶯谷': (35.721455, 139.778030),
    '麹町': (35.684785, 139.737350),
    '麻布十番': (35.656490, 139.736105),
    '大井町': (35.607417, 139.734583),
    '品川シーサイド': (35.609720, 139.749720),
    '天王洲アイル': (35.620560, 139.750830),
    '東京テレポート': (35.627500, 139.778890),
    '国際展示場': (35.634463, 139.791792),
    '東雲': (35.640639, 139.803389),
    '竹芝': (35.653972, 139.761917),
    '日の出': (35.649194, 139.759083),
    '芝浦ふ頭': (35.641750, 139.757833),
    'お台場海浜公園': (35.629889, 139.778778),
    '台場': (35.625889, 139.771417),
    '東京国際クルーズターミナル': (35.621333, 139.773083),
    'テレコムセンター': (35.617556, 139.779389),
    '青海': (35.624750, 139.781306),
    '東京ビッグサイト': (35.630222, 139.791361),
    '有明(ゆりかもめ)': (35.634700, 139.793300),
    '有明テニスの森': (35.639944, 139.788890),
    '市場前': (35.645667, 139.785639),
    '新豊洲': (35.648667, 139.790056),
    '赤土小学校前': (35.742861, 139.769000),
    '足立小台': (35.754667, 139.770389),
    '扇大橋': (35.763944, 139.770806),
    '高野': (35.768417, 139.770694),
    '江北': (35.773890, 139.770306),
    '西新井大師西': (35.781472, 139.770056),
    '谷在家': (35.788778, 139.770056),
    '舎人公園': (35.796390, 139.770167),
    '舎人': (35.805667, 139.770083),
    '見沼代親水公園': (35.814528, 139.770694),
    '面影橋': (35.712917, 139.714528),
    '学習院下': (35.716222, 139.712500),
    '鬼子母神前': (35.720333, 139.715000),
    '都電雑司ヶ谷': (35.724250, 139.718000),
    '東池袋四丁目': (35.725361, 139.720444),
    '向原': (35.728889, 139.724889),
    '大塚駅前': (35.731583, 139.729333),
    '巣鴨新田': (35.735444, 139.727778),
    '庚申塚': (35.739528, 139.729639),
    '新庚申塚': (35.741333, 139.730444),
    '西ヶ原四丁目': (35.744444, 139.732778),
    '滝野川一丁目': (35.747361, 139.735389),
    '飛鳥山': (35.750167, 139.737417),
    '王子駅前': (35.752722, 139.738278),
    '栄町': (35.750917, 139.742194),
    '梶原': (35.751139, 139.747472),
    '荒川車庫前': (35.750861, 139.752750),
    '荒川遊園地前': (35.750667, 139.757722),
    '小台': (35.750528, 139.761639),
    '宮ノ前': (35.750083, 139.765000),
    '熊野前': (35.749167, 139.769222),
    '東尾久三丁目': (35.745389, 139.774389),
    '町屋二丁目': (35.743694, 139.776944),
    '町屋駅前': (35.742778, 139.780833),
    '荒川七丁目': (35.741944, 139.784167),
    '荒川二丁目': (35.738583, 139.784722),
    '荒川区役所前': (35.734972, 139.786389),
    '荒川一中前': (35.733722, 139.788889),
    '三ノ輪橋': (35.732139, 139.791528),
    '越中島': (35.667944, 139.792694),
    '潮見': (35.658722, 139.817333),
    '葛西臨海公園': (35.644389, 139.861583),
    '浮間舟渡': (35.791222, 139.691390),
    '北赤羽': (35.786694, 139.706194),
    '赤羽': (35.778028, 139.720830),
    '十条': (35.760280, 139.722220),
    '板橋': (35.746028, 139.719528),
    '東十条': (35.762944, 139.727694),
    '上中里': (35.746556, 139.746940),
    '大森': (35.588444, 139.727917),
    '蒲田': (35.562472, 139.716056),
    '尾久': (35.746778, 139.754639),
    '三河島': (35.733472, 139.776417),
    '亀有': (35.766670, 139.847780),
    '金町': (35.769556, 139.870472),
    '西大井': (35.601806, 139.721806),
}
# Keep pre-existing wider-area stations; reference points remain separate from STATIONS.
STATION_MAP_POINTS.update({name:point for name,point in STATIONS.items() if name not in STATION_MAP_POINTS})
# Additional commuter/private-rail station reference coordinates (MLIT N02, 2024).
# https://github.com/antfu/mlit-json (derived from Ministry of Land railway data).
# Offline, does not increase map-render requests, SUUMO requests, or Supabase usage.
STATION_MAP_POINTS.update({'お花茶屋': (35.74762, 139.83989), 'ときわ台': (35.7589, 139.68876), '芦花公園': (35.67055, 139.6084), '井荻': (35.72466, 139.61555), '永福町': (35.67623, 139.64267), '荏原中延': (35.6099, 139.71203), '荏原町': (35.60381, 139.70755), '奥沢': (35.60395, 139.67241), '下井草': (35.72387, 139.62473), '下丸子': (35.57133, 139.68549), '下高井戸': (35.66599, 139.6418), '下赤塚': (35.77049, 139.64485), '下板橋': (35.74507, 139.71562), '下北沢': (35.66153, 139.66696), '下落合': (35.71569, 139.69529), '学芸大学': (35.62877, 139.68524), '旗の台': (35.60503, 139.70276), '久我山': (35.68814, 139.59932), '経堂': (35.65131, 139.63708), '京成曳舟': (35.71864, 139.82028), '京成立石': (35.73819, 139.84816), '京成小岩': (35.74222, 139.88361), '京成上野': (35.71131, 139.77351), '駒場東大前': (35.65867, 139.68408), '駒沢大学': (35.63303, 139.66097), '高井戸': (35.68326, 139.61523), '鷺ノ宮': (35.72262, 139.63985), '桜上水': (35.66764, 139.63146), '桜新町': (35.6317, 139.64537), '桜台': (35.73875, 139.6624), '笹塚': (35.67366, 139.66717), '三軒茶屋': (35.64373, 139.67017), '参宮橋': (35.6786, 139.69359), '自由が丘': (35.60734, 139.66884), '初台': (35.68142, 139.68827), '小村井': (35.71032, 139.82761), '松陰神社前': (35.64388, 139.65499), '沼袋': (35.71916, 139.66484), '上井草': (35.72525, 139.60296), '上石神井': (35.72619, 139.59229), '上板橋': (35.76362, 139.67621), '上北沢': (35.66884, 139.62341), '新井薬師前': (35.7158, 139.6725), '新桜台': (35.74098, 139.66857), '新代田': (35.66249, 139.66139), '神泉': (35.65718, 139.69325), '成城学園前': (35.64014, 139.59908), '成増': (35.77759, 139.6329), '西永福': (35.67887, 139.63515), '西小山': (35.61543, 139.69871), '西新井': (35.77728, 139.79037), '西太子堂': (35.64452, 139.66635), '西武新宿': (35.69613, 139.70007), '青砥': (35.74587, 139.85627), '石神井公園': (35.74364, 139.60655), '千歳烏山': (35.66783, 139.60038), '千歳船橋': (35.64767, 139.62453), '千住大橋': (35.74241, 139.79705), '代々木八幡': (35.66972, 139.68904), '代官山': (35.64825, 139.70349), '代田橋': (35.67108, 139.65939), '大崎広小路': (35.6223, 139.72235), '大山': (35.74841, 139.70262), '大泉学園': (35.74941, 139.58665), '池ノ上': (35.66037, 139.67345), '池尻大橋': (35.65071, 139.68471), '中村橋': (35.73667, 139.63717), '中板橋': (35.75628, 139.69439), '椎名町': (35.72649, 139.69431), '都立家政': (35.72219, 139.64543), '都立大学': (35.61785, 139.67644), '東松原': (35.66264, 139.65576), '東長崎': (35.72997, 139.68319), '東武練馬': (35.76865, 139.66254), '東北沢': (35.66544, 139.67304), '幡ヶ谷': (35.67676, 139.67772), '浜田山': (35.68158, 139.62758), '不動前': (35.62542, 139.71315), '富士見ヶ丘': (35.6848, 139.60723), '富士見台': (35.73585, 139.62982), '武蔵関': (35.72762, 139.57637), '武蔵小山': (35.62051, 139.70447), '武蔵新田': (35.56775, 139.69243), '豊島園': (35.74183, 139.64861), '北池袋': (35.74034, 139.7164), '堀切菖蒲園': (35.74765, 139.82754), '明大前': (35.66872, 139.65046), '野方': (35.71959, 139.65349), '祐天寺': (35.63717, 139.69075), '用賀': (35.62658, 139.63425), '練馬高野台': (35.74061, 139.61684), '竹ノ塚': (35.79437, 139.79072), '下神明': (35.60871, 139.72631), '戸越銀座': (35.61601, 139.715), '戸越公園': (35.60893, 139.71839), '大井競馬場前': (35.59503, 139.7472), '糀谷': (35.55445, 139.72959), '西荻窪': (35.7038, 139.59971)})

LINES = {
    "山手線": "東京 品川 大崎 五反田 目黒 恵比寿 渋谷 新宿 池袋 上野 秋葉原 東京".split(),
    "京浜東北線": "大宮 浦和 南浦和 川口 赤羽 上野 秋葉原 東京 品川 大井町 蒲田 川崎 横浜".split(),
    "埼京線": "大宮 赤羽 池袋 新宿 渋谷 恵比寿 大崎".split(),
    "中央線快速": "東京 新宿 中野 荻窪 吉祥寺 三鷹 国分寺 立川".split(),
    "東海道線": "東京 品川 川崎 横浜".split(), "横須賀線": "東京 品川 武蔵小杉 横浜".split(),
    "東横線": "渋谷 中目黒 自由が丘 武蔵小杉 日吉 横浜".split(),
    "総武線快速": "東京 錦糸町 新小岩 市川 船橋 津田沼 千葉".split(),
    "常磐線快速": "上野 北千住 松戸 柏".split(), "南武線": "川崎 武蔵小杉 溝の口 立川".split(),
    "田園都市線": "渋谷 二子玉川 溝の口".split(),
}
CROWD_SOURCE = "https://www.mlit.go.jp/report/press/content/002015142.pdf"
# 国土交通省・2025年度。公表区間の最混雑1時間、経路全体の予測ではない。
CROWD = {
    '山手線':(136,'新大久保 → 新宿','7:44〜8:44'),
    '京浜東北線':(158,'川口 → 赤羽','7:20〜8:20'),
    '埼京線':(167,'板橋 → 池袋','7:51〜8:51'),
    '中央線快速':(158,'中野 → 新宿','7:35〜8:35'),
    '東海道線':(159,'川崎 → 品川','7:39〜8:39'),
    '横須賀線':(138,'武蔵小杉 → 西大井','7:26〜8:26'),
    '東横線':(124,'祐天寺 → 中目黒','7:50〜8:50'),
    '総武線快速':(160,'新小岩 → 錦糸町','7:31〜8:31'),
    '常磐線快速':(142,'三河島 → 日暮里','7:26〜8:26'),
    '南武線':(156,'武蔵中原 → 武蔵小杉','7:30〜8:30'),
    '田園都市線':(138,'池尻大橋 → 渋谷','7:50〜8:50'),
}
# Legacy point-map scale; the active町丁目 map uses the continuous TOWN_GRADIENT below.
# Include compact 1R/1K/1DK rents without changing rent or storage data.
BANDS = (30000,50000,70000,90000,120000,150000,175000,200000,250000,300000,350000,400000,450000)
COLORS = ("#d0efff","#b0dffa","#69bfe9","#398bdc","#2283ba","#1b998a",
          "#4eac5b","#b9bf2b","#eb9824","#c73731","#98232b","#641820","#330a1a","#330a1a")
RENT_BAND_LABELS = (f'{BANDS[0]/10000:g}万円以下',) + tuple(
    f'{lower/10000:g}〜{upper/10000:g}万円' for lower,upper in zip(BANDS,BANDS[1:])
) + (f'{BANDS[-1]/10000:g}万円超',)
DB_MAINTENANCE_SQL = '-- v105: one-time removal of OLD error and diagnostic records only.\n-- Stop automatic collection before running. Change the namespace only if\n-- SUPABASE_NAMESPACE in Streamlit Secrets is not "sumai-compass".\n-- 1) Inspect counts first:\nSELECT status, count(*) AS stored_rows\nFROM public.housing_searches_v1\nWHERE namespace = \'sumai-compass\'\nGROUP BY status\nORDER BY stored_rows DESC;\n\n-- 2) Discard only historical diagnostic/error records.\n--    No rental_listing, rental_acquisition or automatic_collection_settings rows are removed.\nDELETE FROM public.housing_searches_v1\nWHERE namespace = \'sumai-compass\'\n  AND status IN (\'diagnostic_error\', \'diagnostic_log\');\n\n-- 3) Refresh planner statistics; physical disk space is normally reclaimed\n--    for reuse by autovacuum, not immediately returned to the operating system.\nANALYZE public.housing_searches_v1;\n\n-- 4) Verify no diagnostic records remain:\nSELECT status, count(*) AS remaining_rows\nFROM public.housing_searches_v1\nWHERE namespace = \'sumai-compass\'\nGROUP BY status\nORDER BY remaining_rows DESC;\n'

SQL = '''-- 新しいアプリ専用。既存の物件・ジョブ・テーブルは削除しません。
create table if not exists public.housing_units_v1 (
 namespace text not null, key text not null, title text not null,
 address text not null default '', latitude double precision not null,
 longitude double precision not null, layout text not null,
 rent integer not null check (rent > 0), fees integer not null default 0 check (fees >= 0),
 area double precision not null check (area > 0), floor text not null default '',
 structure text not null check (structure = 'SRC'), age integer not null check (age between 0 and 20),
 built_ym text, location_method text not null, map_address text not null default '',
 address_match text not null default '', provider text not null,
 listing_url text not null, fetched_at timestamptz not null,
 primary key(namespace, key),
 check(latitude between 34 and 37), check(longitude between 138 and 141)
);
create index if not exists housing_units_v1_range on public.housing_units_v1(namespace,latitude,longitude);
create table if not exists public.housing_searches_v1 (
 namespace text not null, id text not null, status text not null,
 started_at timestamptz not null, finished_at timestamptz,
 conditions jsonb not null, summary jsonb not null default '{}'::jsonb,
 primary key(namespace,id)
);
-- Optional for existing databases: run these non-destructive indexes in SQL Editor
-- if housing_searches_v1 GET (the acquisition ledger) returns HTTP 500/timeout.
create index if not exists housing_searches_v1_status_id
    on public.housing_searches_v1(namespace,status,id);
create index if not exists housing_searches_v1_acquisition_recent
    on public.housing_searches_v1(namespace,status,finished_at,id);
create table if not exists public.housing_places_v1 (
 namespace text not null, key text not null, title text not null, kind text not null,
 latitude double precision not null, longitude double precision not null,
 fetched_at timestamptz not null, primary key(namespace,key),
 check(latitude between 34 and 37), check(longitude between 138 and 141)
);
create index if not exists housing_places_v1_range on public.housing_places_v1(namespace,latitude,longitude);
alter table public.housing_places_v1 enable row level security;
alter table public.housing_units_v1 enable row level security;
alter table public.housing_searches_v1 enable row level security;
grant usage on schema public to service_role;
grant all on public.housing_units_v1, public.housing_searches_v1, public.housing_places_v1 to service_role;
notify pgrst, 'reload schema';
'''
CSS = '''<style>
.stApp,[data-testid="stAppViewContainer"]{background:#f5f3ed!important;color:#203b38!important;color-scheme:light}
[data-testid="stHeader"]{background:#f5f3ed!important}
.block-container{max-width:1150px;padding:1.4rem 1rem 3rem}
h1,h2,h3,label,p{color:#203b38}
.hero{background:#203f39;border-radius:22px;padding:24px;margin-bottom:20px}
.hero h1,.hero p,.hero span{color:#fff!important}
.hero h1{font-size:30px;margin:10px 0}.hero p{font-size:14px;line-height:1.8}
.badge{font-size:11px;letter-spacing:2px;background:#41635a;border-radius:12px;padding:5px 9px}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button,[data-testid="stFormSubmitButton"] button{
 background:#fff!important;color:#203f39!important;border:1px solid #a7b8ac!important;border-radius:12px!important;min-height:44px}
[data-testid="stButton"] button *,[data-testid="stDownloadButton"] button *,[data-testid="stFormSubmitButton"] button *{color:inherit!important}
button[kind="primary"],button[kind="primaryFormSubmit"]{background:#c55c42!important;color:#fff!important;border-color:#c55c42!important}
button:disabled{background:#e3e9e3!important;color:#607369!important;opacity:1!important}
[data-baseweb="select"]>div,[data-baseweb="input"],input,[data-testid="stNumberInput"] button{background:#fff!important;color:#203f39!important}
[data-baseweb="select"] span,[role="option"],[role="listbox"]{color:#203f39!important;background:#fff!important}
[data-testid="stMetric"]{background:#fff;border:1px solid #e0e4da;border-radius:16px;padding:15px}
[data-testid="stCode"] pre,[data-testid="stCode"] code{background:#edf0e8!important;color:#203f39!important}
[data-testid="stForm"]{border:1px solid #d1dccf;border-radius:18px;background:#fff;padding:18px}
[data-testid="stSidebar"]{background:#e8ece3!important}
[data-baseweb="tab-list"],[data-baseweb="tab"]{background:#f5f3ed!important;color:#203f39!important}
/* Keep Streamlit theme defaults from painting dark summary/portal surfaces. */
[data-testid="stExpander"] details,[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary:hover,[data-testid="stExpander"] summary:focus,
[data-testid="stExpander"] [data-testid="stExpanderDetails"]{
 background:#fff!important;color:#203f39!important;color-scheme:light}
[data-testid="stExpander"] summary *{color:#203f39!important}
[data-testid="stExpander"] summary svg{fill:currentColor!important;color:#203f39!important}
[data-testid="stExpander"] summary:focus-visible{outline:3px solid #174a6a;outline-offset:-3px}
[data-baseweb="popover"],[data-baseweb="menu"],[role="dialog"],[role="tooltip"]{
 background:#fff!important;color:#203f39!important;color-scheme:light}
[data-testid="stTextArea"] textarea{background:#fff!important;color:#203f39!important}
[data-testid="stCaptionContainer"]{color:#52655f!important}
[data-baseweb="select"] svg{fill:#203f39!important}
[data-testid="stFileUploader"] section{background:#fff!important;color:#203f39!important}
.unit{background:#fff;border-radius:16px;padding:18px;margin-bottom:12px;border:1px solid #e0e4da}
.unit h3{font-size:18px;margin:0 0 8px}.unit a{color:#286b5d}
@media(max-width:600px){.hero{padding:18px}.hero h1{font-size:26px}.block-container{padding-top:1rem}}

/* v210: immediate pointer/touch feedback and retained keyboard/click focus. */
.stApp button:not(:disabled),.stApp a[data-testid="stBaseLinkButton"]{
 transition:background-color .08s ease,color .08s ease,border-color .08s ease}
.stApp button:not(:disabled):is(:active,:focus),
.stApp [data-testid="stLinkButton"] a:is(:active,:focus),
.stApp a[data-testid="stBaseLinkButton"]:is(:active,:focus){
 background:#174a6a!important;color:#fff!important;border-color:#174a6a!important;
 box-shadow:0 0 0 3px rgba(23,74,106,.23)!important}
.stApp button:not(:disabled):is(:active,:focus) *,
.stApp [data-testid="stLinkButton"] a:is(:active,:focus) *{color:inherit!important}
.stApp button:not(:disabled):focus-visible,.stApp [data-testid="stLinkButton"] a:focus-visible{
 outline:3px solid #b35636!important;outline-offset:3px}
@media(prefers-reduced-motion:reduce){.stApp button,.stApp a{transition:none!important}}
</style>'''


class SearchCancelled(BaseException):
    """Cooperative stop; collector error recovery must not swallow it."""


class AppError(Exception):
    """User-safe connection or validation failure."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normal(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def address_key(value):
    value=normal(value).replace(' ','').replace('　','')
    digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    def convert(match):
        text=match[1]
        if '十' in text:
            head,tail=text.split('十',1)
            number=(digits.get(head,1)*10)+digits.get(tail,0)
        else:
            number=digits.get(text)
        return (str(number) if number is not None else text)+'丁目'
    return re.sub(r'([一二三四五六七八九十]+)丁目',convert,value)



def saved_address_point_key(address):
    value=normal(address)
    return hashlib.sha256(('saved-address-point-v1\0'+value).encode('utf-8')).hexdigest() if value else None



def town_matches(town,address):
    wanted=address_key(town);actual=address_key(address)
    match=re.fullmatch(r'(.*?)(\d+)丁目',wanted)
    if match:
        base,number=match.groups()
        return bool(re.search(r'(?:^|[都道府県市区郡町村])'+re.escape(base+number)+r'(?:丁目|[-ー−－番号]|$)',actual))
    return bool(re.search(r'(?:^|[都道府県市区郡町村])'+re.escape(wanted)+r'(?=\d|[-ー−－番号]|$)',actual))


def parsed_layout(raw,rough=True):
    value=normal(raw).upper().replace('　',' ').strip()
    match=re.match(r'^(\d+(?:LDK|SLDK|SDK|DK|SK|LK|K|L|R))(?=$|[\s(（+/])',value)
    return (re.sub(r'^(\d+)S(LDK|DK|K)$',r'\1\2',match[1]) if rough else match[1]) if match else '1R' if re.match(r'^ワンルーム(?=$|[\s(（+/])',value) else None


def resolve_layout(web,raw,requested=None,url=''):
    observed=parsed_layout(raw)
    if observed:
        if observed not in ALL_TARGET_LAYOUTS or requested and observed!=requested:
            trace(web,'layout',{'url':url,'observed':raw,'parsed':observed,'requested':requested,'reason':'explicit_layout_does_not_match_search'});return None
        return observed
    if requested in ALL_TARGET_LAYOUTS:
        trace(web,'layout_assumed',{'url':url,'observed':raw,'assigned':requested,'source':'single_layout_search','confirmed_from_listing':False},'WARNING');return requested
    trace(web,'layout',{'url':url,'observed':raw,'reason':'no_readable_layout_and_no_single_layout_search'},'WARNING');return None


def discover_layout_inputs(soup,provider):
    result={}
    for field in soup.select('input[name],select[name] option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '')
        if not name or not ('madori' in name.lower() if provider=='HOME’S' else name=='md'):continue
        options=[]
        if field.get('id'):
            label=soup.find('label',attrs={'for':field['id']})
            if label:options.append(label.get_text(' ',strip=True))
        label=field.find_parent('label')
        if label:options.append(label.get_text(' ',strip=True))
        if field.name=='option':options.append(field.get_text(' ',strip=True))
        options.extend([field.get('aria-label',''),field.get('data-label','')])
        for text in options:
            layout=parsed_layout(text,rough=False)
            allowed=ALL_TARGET_LAYOUTS
            if layout in allowed and str(field.get('value','')):
                result[layout]=(name,str(field['value']));break
    return result


def single_layout_parameters(web,provider,requested):
    """Read public form codes rather than guessing undocumented provider identifiers."""
    allowed=ALL_TARGET_LAYOUTS
    if requested not in allowed:raise AppError('単独検索する間取りを確認してください。')
    lock=getattr(web,'layout_form_lock',None)
    if lock is None:lock=threading.Lock();web.layout_form_lock=lock
    with lock:
        cache=getattr(web,'layout_form_cache',None)
        if cache is None:cache={};web.layout_form_cache=cache
        if provider not in cache:
            urls=['https://www.homes.co.jp/chintai/tokyo/list/','https://www.homes.co.jp/chintai/tokyo/','https://www.homes.co.jp/chintai/theme/14130/tokyo/list/'] if provider=='HOME’S' else ['https://suumo.jp/jj/chintai/ichiran/FR301FC001/','https://suumo.jp/chintai/tokyo/']
            reasons=[]
            for url in urls:
                try:
                    if not web.permitted(url):raise AppError('間取り条件フォームの自動取得は許可されていません。')
                    reply=web.fetch(url,params={'ar':'030','bs':'040','ta':'13'} if '/FR301FC001/' in url else {})
                    filters=discover_layout_inputs(BeautifulSoup(reply.text,'html.parser'),provider)
                    if not filters:raise AppError(provider+'の間取り条件フォームを読み取れません。')
                    cache[provider]=filters
                    trace(web,'layout_filter_discovered',{'provider':provider,'source_url':url,'filters':filters},stage='search_conditions');break
                except AppError as exc:
                    reasons.append(str(exc));trace(web,'form_alternative',{'provider':provider,'failed_url':url,'message':str(exc),'next_public_page':url!=urls[-1]},'WARNING','search_conditions')
            if provider not in cache:cache[provider]='／'.join(dict.fromkeys(reasons))+' 単独条件を確認できないため一括検索へ切り替えません。'
        filters=cache[provider]
        if isinstance(filters,str):raise AppError(filters)
        if requested not in filters:raise AppError(provider+'では'+requested+'の単独間取り条件を確認できません。他の間取りの検索は続けます。')
        name,value=filters[requested]
        if provider=='HOME’S':
            # Public form and map request share the madori identifiers; preserve names already under cond.
            name=name if name.startswith('cond[') else 'cond[madori]['+value+']'
        params={name:value}
        trace(web,'single_layout_query',{'provider':provider,'requested':requested,'parameters':params,'source':'public_form'},stage='search_conditions')
        return params

def meters(a, b):
    a1,b1,a2,b2 = map(math.radians, (*a,*b))
    h = math.sin((a2-a1)/2)**2 + math.cos(a1)*math.cos(a2)*math.sin((b2-b1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1,max(0,h))))


def rectangle(center, radius):
    lat,lng=center;dy=radius/111320;dx=radius/(111320*math.cos(math.radians(lat)))
    return lat-dy,lng-dx,lat+dy,lng+dx


def in_rectangle(point, bounds):
    return bounds[0]<=point[0]<=bounds[2] and bounds[1]<=point[1]<=bounds[3]


def viewport_bounds(data):
    try:
        b=data['bounds'];s=float(b['_southWest']['lat']);w=float(b['_southWest']['lng'])
        n=float(b['_northEast']['lat']);e=float(b['_northEast']['lng'])
        if not all(math.isfinite(v) for v in (s,w,n,e)) or not (34<=s<n<=37 and 138<=w<e<=141):
            return None
        return (s,w,n,e)
    except (KeyError,TypeError,ValueError): return None


def bounds_center(bounds):
    s,w,n,e=bounds
    return ((s+n)/2,(w+e)/2)


def bounds_radius(bounds):
    return max(meters(bounds_center(bounds),p) for p in ((bounds[0],bounds[1]),(bounds[2],bounds[3])))


def tiles_in_bounds(bounds,zoom=15):
    def tile(lat,lng):
        size=2**zoom
        return int((lng+180)/360*size),int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*size)
    s,w,n,e=bounds;x1,y1=tile(n,w);x2,y2=tile(s,e)
    return [(x,y) for x in range(x1,x2+1) for y in range(y1,y2+1)]


def bounded_results(items,fn,workers=6,stop_event=None):
    """Bound simultaneous requests, never the number of items searched."""
    iterator=iter(items)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        def fill():
            while len(pending)<workers:
                try: item=next(iterator)
                except StopIteration: break
                pending[pool.submit(fn,item)]=item
        fill()
        while pending:
            if stop_event is not None and stop_event.is_set():
                for f in pending:f.cancel()
                raise SearchCancelled()
            ready,_=futures.wait(pending,timeout=.4,return_when=futures.FIRST_COMPLETED)
            if not ready: yield None,None,None
            for f in ready:
                item=pending.pop(f)
                try: yield item,f.result(),None
                except Exception as exc: yield item,None,exc
            fill()


def amount(value):
    text=normal(value).replace(",", "").replace(" ", "")
    if text in ("", "-", "―", "なし", "無"): return 0
    match=re.fullmatch(r"(\d+(?:\.\d+)?)(万円|円)?(?:/月)?",text)
    if not match: raise AppError("金額を読み取れません")
    return round(float(match[1])*(10000 if match[2]=="万円" else 1))


def age_info(value):
    text=normal(value); now=datetime.now(timezone.utc)
    date=re.search(r"((?:19|20)\d{2})年\s*(\d{1,2})月",text)
    if date:
        y,m=map(int,date.groups())
        if not 1<=m<=12: return None,None
        age=now.year-y-int(now.month<m)
        return age,f"{y:04d}-{m:02d}"
    age=re.search(r"築\s*(\d{1,3})年",text)
    if age: return int(age[1]),None
    if "新築" in text: return 0,None
    return None,None


def is_src(value):
    value=normal(value).upper().replace(" ","")
    return "鉄骨鉄筋" in value or bool(re.search(r"(?<![A-Z])SRC(?![A-Z])",value))


def monthly_price(row):
    return float(row.get('rent') or 0)+float(row.get('fees') or 0)


def has_point(row):
    try:return math.isfinite(float(row['latitude'])) and math.isfinite(float(row['longitude'])) and 34<=float(row['latitude'])<=37 and 138<=float(row['longitude'])<=141
    except (ValueError,TypeError,KeyError):return False


def listing_in_bounds(row,bounds):
    if bounds is None:return True
    if has_point(row):return in_rectangle((row['latitude'],row['longitude']),bounds)
    old=row.get('search_bounds')
    return isinstance(old,(list,tuple)) and len(old)==4 and old[0]<=bounds[2] and old[2]>=bounds[0] and old[1]<=bounds[3] and old[3]>=bounds[1]


def acquisition_time_jst(value):
    if not value:return '未記録'
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:return '未記録'
        return dt.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST')
    except (ValueError,TypeError):return '未記録'


def saved_address_requires_reacquisition(value):
    """Return True only for clearly incomplete saved addresses.

    This function is used solely to decide whether an old acquisition ID may be
    skipped.  It does NOT geocode or create an address and never reads a provider
    page.  Town/chome-only rows are deliberately re-fetched.
    """
    address=normal(value).replace('　','').strip()
    if not address:return True
    # Examples that must be re-fetched: 高島平二丁目 / 高島平2丁目.
    if re.search(r'(?:[0-9０-９一二三四五六七八九十百]+丁目)\s*$',address):return True
    # A clearly detailed Japanese address has something after 丁目, or an explicit
    # block/lot/house-number separator.  Do not reject legacy detailed rows merely
    # because their notation differs.
    if '丁目' in address:
        tail=address.rsplit('丁目',1)[1]
        return not bool(re.search(r'[0-9０-９一二三四五六七八九十百]',tail))
    if re.search(r'[0-9０-９]+\s*[-‐‑‒–—―ー−－]\s*[0-9０-９]+',address):return False
    if re.search(r'[0-9０-９]+\s*番(?:地)?|[0-9０-９]+\s*号',address):return False
    # A former build stored e.g. 東京都豊島区東池袋3 without 丁目, block or lot.
    # Such bare chome numbers are *not* detailed addresses.  Do not silently
    # treat their older acquisition ledgers as verified or draw them on the map.
    municipality=re.search(r'(?:区|市|町|村)([^\d０-９]+)$',address)
    if municipality and len(municipality.group(1))>=2:return True
    if re.search(r'(?:区|市|町|村)[^0-9０-９\-‐‑‒–—―ー−－]{2,40}[0-9０-９]{1,2}$',address):return True
    return False


def normalized_dwelling_type(value):
    text=normal(value).lower().replace(' ', '')
    if text in ('house','detached','kodate','一戸建て','一戸建','戸建て','戸建','貸家','一軒家'):return 'house'
    return 'mansion'


def listing_dwelling_type(row):
    if not isinstance(row,dict):return 'mansion'
    return normalized_dwelling_type(row.get('dwelling_type') or row.get('property_type') or row.get('building_type') or '')


def listing_identity_payload(compact):
    payload={k:compact[k] for k in ('rent','layout','address')}
    if compact.get('dwelling_type')=='house':payload['dwelling_type']='house'
    return payload


def compact_saved_listing(row):
    """Persistent listing payload: rent, layout, dwelling type, inferred address and acquisition time."""
    try:
        rent=row.get('rent');layout=normal(row.get('layout'));address=normal(row.get('address') or row.get('inferred_address'))
        dwelling_type=listing_dwelling_type(row)
        parsed=parsed_layout(layout,rough=False)
        if not isinstance(rent,(int,float)) or not math.isfinite(float(rent)) or float(rent)<=0:return None
        if dwelling_type=='house':
            if not parsed:return None
            layout=parsed
        elif layout not in ALL_TARGET_LAYOUTS:return None
        if not address:return None
        stamp=row.get('fetched_at')
        if stamp:
            parsed_stamp=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
            if parsed_stamp.tzinfo is None:return None
            stamp=parsed_stamp.astimezone(timezone.utc).isoformat(timespec='seconds')
        result={'rent':int(round(float(rent))),'layout':layout,'address':address[:220],'dwelling_type':dwelling_type,'fetched_at':stamp or None}
        property_id=row.get('property_id')
        if isinstance(property_id,str) and re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',property_id):result['property_id']=property_id
        return result
    except (ValueError,TypeError,AttributeError):return None


def newest_observation(old,new):
    if not old:return new
    try:
        a=datetime.fromisoformat(str(old.get('fetched_at')).replace('Z','+00:00'))
        b=datetime.fromisoformat(str(new.get('fetched_at')).replace('Z','+00:00'))
        if a.tzinfo and b.tzinfo and a>b:return old
    except (ValueError,TypeError):pass
    return new


def compact_listing_key(row):
    compact=compact_saved_listing(row)
    if not compact:return None
    identity={'property_id':compact['property_id']} if compact.get('property_id') else listing_identity_payload(compact)
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def valid_unit(row):
    try:
        compact=compact_saved_listing(row)
        if not compact:return False
        if not row.get('key'):return False
        provider=row.get('provider');url=row.get('listing_url')
        return not (provider or url) or bool(provider and url and safe_url(url,provider))
    except (ValueError,TypeError,KeyError):return False


def optional_amount(value):
    if value is None or not str(value).strip():return None
    try:return amount(value)
    except (AppError,ValueError,TypeError):return None


def partial_listing(provider,url,title,address,layout,rent,fees,area,region,bounds,location=None,structure=None,age=None,built_ym=None,floor='',dwelling_type=None):
    def number(value,minimum,maximum):
        try:return value if value is not None and math.isfinite(float(value)) and minimum<=float(value)<=maximum else None
        except (ValueError,TypeError):return None
    rent=number(rent,1,float('inf'));fees=number(fees,0,float('inf'));area=number(area,.01,10000);age=number(age,0,150)
    point=region.get('point') if region else None
    precise=bool(location and has_point(location))
    location=dict(location or {})
    # SUUMO can now be stored without a building coordinate if its actual
    # published address identifies a map-renderable town/chome.  Never invent
    # a point at the search-region centroid; it would corrupt point maps.
    if provider in ('HOME’S','HOMES') and not precise:return None
    if provider=='SUUMO' and not precise and not town_chome_key(address):return None
    if not precise:
        if provider=='SUUMO':
            location={**location,'latitude':None,'longitude':None,
                      'coordinate_precision':'town','location_method':location.get('location_method') or 'SUUMO掲載所在地→町丁目',
                      'map_address':address,'address_match':'町丁目確定・建物位置未確認'}
        else:
            okay=isinstance(point,(list,tuple)) and len(point)==2 and in_rectangle(point,bounds)
            location=dict(latitude=point[0] if okay else None,longitude=point[1] if okay else None,
                          location_method='町丁目の検索地点（建物位置未確認）' if okay else '地域のみ確認・地図位置未確認',
                          map_address=region.get('label','') if region else '',address_match='町丁目単位')
    coord_precision=location.pop('coordinate_precision','building' if precise else 'town')
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載募集'),address=str(address or ''),layout=layout,
             rent=rent if rent and rent>0 else None,fees=fees,area=area,floor=floor,structure=structure,age=age,built_ym=built_ym,
             provider=provider,listing_url=url,fetched_at=utc_now(),coordinate_precision=coord_precision if precise else 'town',
             region_label=region.get('label','') if region else '',search_bounds=list(bounds),dwelling_type=normalized_dwelling_type(dwelling_type or 'mansion'),**location)
    row['price_basis']='管理費込み' if fees is not None else '家賃のみ・管理費未確認'
    row['missing_fields']=[k for k in ('rent','fees','area','structure','age') if row.get(k) is None]
    if not precise:row['missing_fields'].append('building_location')
    return row if valid_unit(row) else None


def merge_listing(old,new):
    """Incomplete new observations must not erase previously acquired fields or map points."""
    if not old:return dict(new)
    if newest_observation(old,new) is old:return dict(old)
    out=dict(old);retained=[]
    for k,v in new.items():
        if v is not None and v!='' and k!='missing_fields':out[k]=v
        elif old.get(k) is not None and old.get(k)!='':retained.append(k)
    rank={'town':1,'address':2,'building':3,'listing_map':4}
    if has_point(old) and (not has_point(new) or rank.get(old.get('coordinate_precision'),0)>rank.get(new.get('coordinate_precision'),0)):
        for k in ('latitude','longitude','coordinate_precision','location_method','map_address','inferred_address','position_source_url','address_precision','address_match'):
            if k in old:out[k]=old[k];retained.append(k)
    elif has_point(new) and ((new.get('latitude'),new.get('longitude'))!=(old.get('latitude'),old.get('longitude')) or new.get('position_source_url')!=old.get('position_source_url')):
        for k in ('map_address','inferred_address','address_precision','address_match','position_source_url','location_method'):
            out[k]=new.get(k,'')
    a,b=address_key(old.get('address','')),address_key(new.get('address',''))
    if a and b and b in a and len(a)>len(b):out['address']=old['address'];retained.append('address')
    out['missing_fields']=[k for k in ('rent','fees','area','structure','age') if out.get(k) is None]
    if not has_point(out) or out.get('coordinate_precision') in ('town','address'):out['missing_fields'].append('building_location')
    out['price_basis']='管理費込み' if out.get('fees') is not None else '家賃のみ・管理費未確認'
    if retained:out['retained_fields_from_previous']=sorted(set(retained))
    return out


def rental_network_settings():
    def setting(name,default=None):
        value=os.environ.get(name)
        if value is None:
            try:value=st.secrets.get(name,default)
            except Exception:value=default
        return value
    values=setting('RENTAL_HTTP_PROXIES',[]) or []
    if isinstance(values,str):values=[x.strip() for x in values.splitlines() if x.strip()]
    single=setting('RENTAL_HTTP_PROXY','')
    if single:values=[single,*values]
    if not isinstance(values,(list,tuple)):raise AppError('RENTAL_HTTP_PROXIESを接続URLの配列で設定してください。')
    routes=[]
    for value in values:
        value=str(value).strip()
        try:
            u=urlparse(value)
            if u.scheme not in ('http','https') or not u.hostname or u.port is None or u.query or u.fragment or any(c.isspace() for c in value):raise ValueError()
        except ValueError:raise AppError('プロキシURLは http://ユーザー:パスワード@ホスト:ポート の形式で設定してください。') from None
        if value not in routes:routes.append(value)
    # Optional FIRST-STAGE-ONLY proxy routes. These are passed to requests on
    # each Google/Yahoo request; they never alter OS-wide proxy configuration.
    stage1_proxy_values=setting('STAGE1_SEARCH_HTTP_PROXIES',[]) or []
    if isinstance(stage1_proxy_values,str):
        stage1_proxy_values=[x.strip() for x in stage1_proxy_values.splitlines() if x.strip()]
    stage1_proxy_single=setting('STAGE1_SEARCH_HTTP_PROXY','')
    if stage1_proxy_single:stage1_proxy_values=[stage1_proxy_single,*stage1_proxy_values]
    if not isinstance(stage1_proxy_values,(list,tuple)):
        raise AppError('STAGE1_SEARCH_HTTP_PROXIES は接続URLの配列で設定してください。')
    stage1_routes=[]
    for value in stage1_proxy_values:
        value=str(value).strip()
        try:
            u=urlparse(value)
            if (u.scheme not in ('http','https') or not u.hostname or u.port is None
                    or u.query or u.fragment or any(c.isspace() for c in value)):
                raise ValueError()
        except ValueError:
            raise AppError('第1段階プロキシは http://ユーザー:パスワード@ホスト:ポート で設定してください。') from None
        if value not in stage1_routes:stage1_routes.append(value)
    # Direct search is enabled as requested. Set NO to disable it if a service
    # prohibits automated fetching or a provider account requires API use.
    direct_mode=str(setting('STAGE1_SEARCH_DIRECT','YES') or 'YES').strip().upper()=='YES'
    # Stage 1 only: licensed search API, never an impersonated Google/Yahoo browser.
    # An explicit affirmative secret is needed because third-party APIs may bill.
    stage1_mode=str(setting('STAGE1_SEARCH_API_PROVIDER','off') or 'off').strip().lower()
    accepted=str(setting('STAGE1_SEARCH_API_APPROVED','') or '').strip().upper()=='YES'
    if accepted and stage1_mode not in ('off','brave','google_legacy'):
        raise AppError('STAGE1_SEARCH_API_PROVIDER は off / brave / google_legacy のいずれかにしてください。')
    stage1={'provider':'off','api_key':'','engine_id':'','max_requests':0}
    if accepted and stage1_mode!='off':
        key=str(setting('STAGE1_SEARCH_API_KEY','') or '').strip()
        engine=str(setting('STAGE1_GOOGLE_CSE_ID','') or '').strip()
        if not key or stage1_mode=='google_legacy' and not engine:
            raise AppError('第1段階の正規検索APIを使うにはAPIキーと必要な検索エンジンIDを設定してください。')
        try:budget=int(str(setting('STAGE1_SEARCH_API_MAX_REQUESTS',25) or 25))
        except (ValueError,TypeError):raise AppError('STAGE1_SEARCH_API_MAX_REQUESTS は整数で指定してください。') from None
        if not 1<=budget<=200:raise AppError('第1段階の検索API要求上限は1～200回にしてください。')
        stage1.update(provider=stage1_mode,api_key=key,engine_id=engine,max_requests=budget)
    return {'proxies':routes,'stage1_search':stage1,'stage1_direct':direct_mode,'stage1_http_proxies':stage1_routes}


@st.cache_resource
def saved_position_memory():
    return {'points':{},'flights':{},'lock':threading.RLock(),
            'http':{'gates':{},'slots':{},'starts':{},'backoff':{},'lock':threading.RLock()}}


def remember_saved_position(memory,address,point):
    if not memory or not point:return
    try:
        lat,lng,title=point
        if not has_point({'latitude':lat,'longitude':lng}):return
    except (ValueError,TypeError):return
    with memory['lock']:
        points=memory['points']
        if len(points)>=1200 and normal(address) not in points:points.pop(next(iter(points)))
        points[normal(address)]=(time.monotonic(),(float(lat),float(lng),str(title)))


def cached_saved_position(memory,address):
    if not memory:return None
    with memory['lock']:
        entry=memory['points'].get(normal(address))
        return entry[1] if entry and time.monotonic()-entry[0]<86400 else None


@st.cache_resource(show_spinner=False)
def _v147_shared_acquisition_ledger():
    # Only this script's namespace, not shared runtime/OS caches.
    return {'lock':threading.RLock(),'items':{}}


class Database:
    """Fresh typed storage; no legacy tables/payloads or job lookups."""
    def __init__(self, config=None):
        def setting(name,default=""):
            value=config.get(name,default) if config is not None else os.environ.get(name)
            if value is None:
                try: value=st.secrets.get(name,default)
                except Exception: value=default
            return str(value or "").strip()
        self.url=setting('SUPABASE_URL').rstrip('/')
        self.key=setting('SUPABASE_SECRET_KEY') or setting('SUPABASE_SERVICE_ROLE_KEY')
        self.namespace=setting('SUPABASE_NAMESPACE','sumai-compass')
        try:
            u=urlparse(self.url)
            okay=(u.scheme=='https' and u.hostname and u.hostname.endswith('.supabase.co')
                  and not u.username and not u.password and u.port in (None,443)
                  and u.path in ('','/') and not u.query and not u.fragment)
        except ValueError: okay=False
        if not okay or not self.key: raise AppError('「設定」→「初期設定」の接続情報をStreamlit Secretsに設定してください。')
        if not re.fullmatch(r'[\w-]{1,80}',self.namespace,flags=re.ASCII): raise AppError('保存領域名の形式を確認してください。')
        self.headers={'apikey':self.key,'Content-Type':'application/json','Accept':'application/json'}
        if not self.key.startswith('sb_secret_'):
            try:
                parts=self.key.split('.')
                payload=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
                if len(parts)!=3 or payload.get('role')!='service_role': raise ValueError()
            except Exception: raise AppError('Secret keyまたはservice_roleキーを設定してください。') from None
            self.headers['Authorization']='Bearer '+self.key
        self.session=requests.Session();self.address_points=saved_position_memory();self._saved_address_coords={}
    def call(self,method,table,params=None,data=None,prefer=None):
        if table not in (UNIT_TABLE,SEARCH_TABLE,PLACE_TABLE): raise AppError('保存先が不正です。')
        headers=dict(self.headers)
        if prefer: headers['Prefer']=prefer
        response=None
        for attempt in range(3):
            try:
                response=self.session.request(method,self.url+'/rest/v1/'+table,params=params,json=data,
                                              headers=headers,timeout=(4,15),allow_redirects=False)
            except requests.RequestException:
                if attempt==2:raise AppError('Supabaseに接続できません。検索結果は画面に保持されます。') from None
                time.sleep(.5*(attempt+1))
                continue
            # All write requests use idempotent on_conflict semantics. Retry only
            # transient server faults; never retry a 401/403, SQL error or malformed data.
            if response.status_code not in (500,502,503,504) or attempt==2:break
            # SQLSTATE 57014 is a deterministic DB statement timeout. Sending
            # the exact same query another two times increases I/O pressure.
            try:
                db_error=response.json() if response.content else {}
            except (ValueError,TypeError):
                db_error={}
            if isinstance(db_error,dict) and str(db_error.get('code') or '')=='57014':break
            time.sleep(.5*(attempt+1))
        if getattr(self,'audit',None) and not getattr(self,'_saving_audit',False):
            self.audit.add('database','db_response','INFO' if 200<=response.status_code<300 else 'ERROR',{'method':method,'table':table,'status':response.status_code,'bytes':len(response.content)})
        if not 200<=response.status_code<300:
            body={}
            try:
                raw=response.json()
                if isinstance(raw,dict):body=raw
            except (ValueError,TypeError):pass
            code=str(body.get('code') or '')[:40]
            db_message=str(body.get('message') or '')[:360]
            db_details=str(body.get('details') or '')[:360]
            db_hint=str(body.get('hint') or '')[:200]
            status=response.status_code
            diagnostic={'status':status,'method':method,'table':table,
                        'code':code,'server_message':db_message,
                        'server_details':db_details,'server_hint':db_hint,
                        'request_id':str(response.headers.get('x-request-id') or '')[:100],
                        'query':{str(k):str(v)[:160] for k,v in (params or {}).items()} if isinstance(params,dict) else {}}
            if status in (401,403) or code=='42501':
                msg='Supabaseの認証・テーブル権限を確認してください。'
            elif status==404 or code in ('42P01','PGRST205'):
                msg='Supabaseの保存テーブルがありません。「設定」→「初期設定」のSQLを確認してください。'
            elif code=='57014':
                msg='SupabaseのDB処理が時間制限を超えました。保存済み履歴の検索負荷とインデックスを確認してください。'
            elif status in (500,502,503,504):
                msg='Supabase側でHTTP '+str(status)+'が発生しました。接続先・テーブル照会は診断画面で確認し、DB処理負荷/一時障害とSQL不一致を区別してください。'
            else:
                msg='Supabase APIがHTTP '+str(status)+'を返しました。DB応答コードとテーブル構造を確認してください。'
            if code:msg+=' エラーコード '+code+'。'
            if db_message:msg+=' 応答: '+db_message
            error=AppError(msg)
            error.diagnostic=diagnostic
            raise error
        if not response.content.strip(): return None
        try: return response.json()
        except ValueError: raise AppError('Supabaseからの応答を確認できません。') from None
    def check(self):
        """Probe each schema with indexed, minimal-column queries, not wide JSON scans.

        A 200 empty result confirms the endpoint/permissions; it does not prove
        subsequent writes will succeed, so save_search/save_units still validate writes.
        """
        probes=((UNIT_TABLE,'key','key','__sumai_health_probe__'),
                (SEARCH_TABLE,'id','id',AUTO_COLLECTION_ID),
                (PLACE_TABLE,'key','key','__sumai_health_probe__'))
        for table,column,field,value in probes:
            try:
                rows=self.call('GET',table,{'namespace':'eq.'+self.namespace,
                    field:'eq.'+value,'select':column,'limit':1})
            except AppError as exc:
                exc.diagnostic={**getattr(exc,'diagnostic',{}),'operation':'schema_probe','table':table}
                raise
            if not isinstance(rows,list):raise AppError('保存テーブルの応答形式が不正です：'+table)
    def acquisition_payload(self,rows):
        unique={}
        for row in rows:
            property_id=row.get('property_id')
            if not property_id:continue
            stamp=row.get('fetched_at') or utc_now()
            try:
                parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if parsed.tzinfo is None:raise ValueError()
                stamp=parsed.astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError,AttributeError):raise AppError('物件IDの取得日時を確認できません。') from None
            if property_id in unique and unique[property_id]['finished_at']>=stamp:continue
            unique[property_id]={'namespace':self.namespace,'id':('acquired.v150.' if row.get('address_quality_version')==4 else 'acquired.')+hashlib.sha256(property_id.encode()).hexdigest(),
                'status':'rental_acquisition','started_at':stamp,'finished_at':stamp,
                'conditions':{'provider':'SUUMO','property_id':property_id,'schema':2},
                'summary':{'last_success_at':stamp,'listing_key':compact_listing_key(row),
                           'address_complete':not saved_address_requires_reacquisition(row.get('address') or row.get('inferred_address')),
                           'map_compatible':bool(town_chome_key(row.get('address') or row.get('inferred_address'))),
                           'address_quality_version':4 if row.get('address_quality_version')==4 else 3}}
        return list(unique.values())

    def save_units(self,rows):
        address_points={}
        for r in rows:
            address=normal(r.get('address') or r.get('inferred_address')) if isinstance(r,dict) else ''
            if address and isinstance(r,dict) and has_point(r) and r.get('coordinate_precision') not in ('town','unknown'):
                address_points[address]=(float(r['latitude']),float(r['longitude']),normal(r.get('map_address') or address))
        unique={};v150_keys=set()
        for r in rows:
            compact=compact_saved_listing(r)
            if compact:
                key=compact_listing_key(compact);unique[key]=newest_observation(unique.get(key),compact)
                if r.get('address_quality_version')==4 and compact.get('property_id'):
                    v150_keys.add(key)
        # An old schema-7 listing with the same property_id remains unchanged.
        # A version-150 row gets a separate, stable storage identity. All map
        # summaries continue to dedupe by original SUUMO property_id.
        storage_keys={key:hashlib.sha256(('sumai-map-v150\0'+compact['property_id']).encode()).hexdigest()
            if key in v150_keys else key for key,compact in unique.items()}
        saved=set();items=list(unique.items());self.last_save_stats={'new':0,'updated':0,'failed':0}
        if not hasattr(self,'job_new_keys'):self.job_new_keys=set()
        if not hasattr(self,'covered_acquisitions'):self.covered_acquisitions={}
        for offset in range(0,len(items),60):
            batch=items[offset:offset+60]
            unknown=[key for key,compact in batch if not compact.get('fetched_at')]
            if unknown:
                previous=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'status':'eq.rental_listing',
                    'select':'id,summary','id':'in.('+','.join('listing.'+key for key in unknown)+')','limit':200})
                if not isinstance(previous,list):raise AppError('以前の取得日時を確認できません。')
                stamps={str(r.get('id','')).removeprefix('listing.'):((r.get('summary') or {}).get('listing') or {}).get('fetched_at') for r in previous}
                for key,compact in batch:
                    if not compact.get('fetched_at') and stamps.get(key):compact['fetched_at']=stamps[key]
            payload=[dict(namespace=self.namespace,id='listing.'+storage_keys[key],status='rental_listing',started_at=utc_now(),finished_at=None,
                conditions={'record_type':'rental_listing','schema':8 if key in v150_keys else 7,
                            'fields':['property_id','rent','layout','address','dwelling_type','fetched_at'],
                            'address_origin':'suumo_source_or_map_town_chome' if key in v150_keys else 'legacy_provider_mapping',
                            'map_granularity':'town_chome' if key in v150_keys else 'street_number'},
                summary={'listing':compact}) for key,compact in batch]
            result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},payload,'resolution=ignore-duplicates,return=representation')
            if not isinstance(result,list):raise AppError('Supabaseから新規保存の確認が得られません。')
            inserted={str(r.get('id','')).removeprefix('listing.') for r in result if isinstance(r,dict) and r.get('id')}
            self.job_new_keys.update(key for key,_ in batch if storage_keys[key] in inserted)
            updates=[r for r in payload if r['id'].removeprefix('listing.') not in inserted]
            ledger=self.acquisition_payload([{**compact,'address_quality_version':4} if key in v150_keys else compact
                                              for key,compact in batch])
            for entry in ledger:
                entry_pid=entry['conditions'].get('property_id')
                matching=next((key for key,compact in batch if compact.get('property_id')==entry_pid),None)
                if matching is not None:entry['summary']['listing_key']=storage_keys[matching]
            rest=updates+ledger
            if rest:
                response=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},rest,'resolution=merge-duplicates,return=representation')
                returned={r.get('id') for r in response if isinstance(r,dict)} if isinstance(response,list) else set()
                if not {r['id'] for r in rest}<=returned:raise AppError('募集情報の更新・取得済みIDの保存を確認できません。')
            for row in ledger:
                pid=row['conditions']['property_id']
                self.covered_acquisitions[pid]=(row['finished_at'],row['summary']['listing_key'])
                if getattr(self,'acquisition_cache',None) is not None and row['summary'].get('map_compatible'):
                    self.acquisition_cache[pid]=row['finished_at']
            # A successful POST alone is not proof that an address/rent/layout
            # survived storage. Read back the same IDs from this app's namespace.
            expected_by_id={'listing.'+storage_keys[key]:compact for key,compact in batch}
            confirmed=self.call('GET',SEARCH_TABLE,{
                'namespace':'eq.'+self.namespace,
                'id':'in.('+','.join(expected_by_id)+')',
                'select':'id,summary','limit':len(expected_by_id)})
            if not isinstance(confirmed,list):
                raise AppError('Supabaseに保存した物件の住所・家賃の再読込に失敗しました。')
            seen=set()
            for item in confirmed:
                if not isinstance(item,dict):continue
                rid=str(item.get('id') or '');expected=expected_by_id.get(rid)
                if expected is None:continue
                got=((item.get('summary') or {}).get('listing') or {})
                if (not isinstance(got,dict) or
                    address_key(got.get('address'))!=address_key(expected.get('address')) or
                    got.get('layout')!=expected.get('layout') or
                    got.get('rent')!=expected.get('rent') or
                    got.get('property_id')!=expected.get('property_id') or
                    not town_chome_key(got.get('address'))):
                    raise AppError('保存後の読み戻しで住所・家賃・間取り・物件IDの不一致を検出しました。')
                seen.add(rid)
            if seen!=set(expected_by_id):
                raise AppError('保存後の読み戻しで物件の一部を確認できませんでした。')
            trace(self,'v138_save_address_readback_verified',{
                'count':len(seen),'ids_verified':len(expected_by_id),
                'verified_examples':[{'property_id':x.get('property_id'),
                        'address':x.get('address'),'layout':x.get('layout'),
                        'rent_yen':x.get('rent')}
                    for x in list(expected_by_id.values())[:6]],
                'address_precision':'map_town_chome_or_better','result':'confirmed_after_database_get'},stage='database')
            saved.update(key for key,_ in batch)
            # Only fully read-back-verified units may advance the shared skip cache.
            shared=_v147_shared_acquisition_ledger()
            with shared['lock']:
                entry=shared['items'].get((self.url,self.namespace))
                if entry:
                    for _,listing in batch:
                        pid=listing.get('property_id')
                        if pid and listing.get('fetched_at') and town_chome_key(listing.get('address')):
                            entry['ids'][pid]=listing['fetched_at']
            self.last_save_stats['new']+=len(inserted);self.last_save_stats['updated']+=len(updates)
            # Notify after each fully confirmed batch.  Even if a later batch
            # fails, already-persisted listings will reach the town map. These
            # local signals are coalesced into a bounded background refresh.
            try:notify_town_summary_saved(self)
            except Exception as exc:self.last_save_stats['map_refresh_notice_error']=type(exc).__name__
        if not set(unique)<=saved:raise AppError('Supabaseから募集情報の保存確認が得られません。')
        if address_points:
            try:self.save_address_points(address_points)
            except AppError as exc:self.last_save_stats['address_cache_error']=str(exc)
        return saved
    def save_search(self,search):
        result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},
                         [dict(search,namespace=self.namespace)],'resolution=merge-duplicates,return=representation')
        if not isinstance(result,list) or not any(x.get('id')==search['id'] for x in result if isinstance(x,dict)):
            raise AppError('検索履歴の保存を確認できません。')
    def save_acquisition_ids(self,rows):
        payload=self.acquisition_payload(rows)
        covered=getattr(self,'covered_acquisitions',{})
        payload=[r for r in payload if covered.get(r['conditions']['property_id'])!=(r['finished_at'],r['summary']['listing_key'])]
        if not payload:return
        result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},payload,'resolution=merge-duplicates,return=representation')
        returned={r.get('id') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
        if not {r['id'] for r in payload}<=returned:raise AppError('物件IDの取得済み管理情報を保存できません。')

    def load_recent_acquisition_ids(self):
        """Read acquisition IDs once per controller, without re-reading every listing.

        New ledger entries contain the address-complete flag at write time. Only
        legacy ledger entries require indexed point lookups. An incomplete or
        unlinked saved listing is NEVER allowed to suppress reacquisition.
        """
        now=time.monotonic()
        shared=_v147_shared_acquisition_ledger()
        shared_key=(self.url,self.namespace)
        with shared['lock']:
            saved_entry=shared['items'].get(shared_key)
            if saved_entry and now-saved_entry['loaded_at']<1200:
                self.acquisition_cache=dict(saved_entry['ids'])
                self.acquisition_cache_at=now
                self.last_acquisition_load_stats={'source':'app_process_cache','recent_ids':len(self.acquisition_cache),
                                                   'full_listing_scan':False,'pages':0}
                return dict(self.acquisition_cache)
        cached=getattr(self,'acquisition_cache',None)
        if cached is not None and now-getattr(self,'acquisition_cache_at',0)<28800:
            return dict(cached)
        cutoff=acquisition_cutoff().isoformat(timespec='seconds')
        ledger={};legacy={};last_id='';pages=0;raw_count=0
        while True:
            params={'namespace':'eq.'+self.namespace,
                    'status':'eq.rental_acquisition',
                    'finished_at':'gte.'+cutoff,
                    'and':'(id.gte.acquired.,id.lt.acquired/)',
                    'select':'id,conditions,summary,finished_at',
                    'order':'id.asc','limit':500}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('取得済み物件IDを確認できません。')
            pages+=1;raw_count+=len(rows)
            for row in rows:
                if not str(row.get('id') or '').startswith('acquired.'):continue
                cond=row.get('conditions') or {};summary=row.get('summary') or {}
                pid=normal(cond.get('property_id'))
                stamp=summary.get('last_success_at') or row.get('finished_at')
                if not re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',pid) or not recent_acquisition(stamp):continue
                complete=summary.get('address_complete')
                key=normal(summary.get('listing_key'))
                version=summary.get('address_quality_version')
                # Pre-v145 address_complete=True was unreliable for strings such
                # as 東池袋3.  Validate its linked saved listing once rather than
                # incorrectly suppressing re-acquisition indefinitely.
                if (complete is True and version==3) or (version==4 and summary.get('map_compatible') is True):
                    ledger[pid]=stamp
                elif re.fullmatch(r'[0-9a-f]{64}',key):
                    legacy[pid]=(stamp,key)
                # Missing/explicitly incomplete linkage remains a re-fetch target.
            if len(rows)<500:break
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last<=last_id:raise AppError('取得済みIDのページ送りが停止しました。')
            last_id=new_last
        unique_keys=sorted({key for _,key in legacy.values()})
        addresses={};lookups=0
        for offset in range(0,len(unique_keys),125):
            batch=unique_keys[offset:offset+125]
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,
                'id':'in.('+','.join('listing.'+key for key in batch)+')',
                'select':'id,summary','limit':125})
            lookups+=1
            if not isinstance(rows,list):raise AppError('旧取得済み物件の住所品質を確認できません。')
            for item in rows:
                key=str(item.get('id') or '').removeprefix('listing.')
                listing=((item.get('summary') or {}).get('listing') or {})
                if isinstance(listing,dict):
                    addresses[key]=normal(listing.get('address') or listing.get('inferred_address'))
        requeued_incomplete=0;requeued_missing=0
        for pid,(stamp,key) in legacy.items():
            address=addresses.get(key)
            if address is None:requeued_missing+=1;continue
            if not town_chome_key(address):requeued_incomplete+=1;continue
            ledger[pid]=stamp
        self.last_acquisition_load_stats={'pages':pages,'raw_records':raw_count,
            'recent_ids':len(ledger),'full_listing_scan':False,
            'address_lookup_batches':lookups,'legacy_ledger_rows':len(legacy),
            'requeued_incomplete_address':requeued_incomplete,
            'requeued_missing_listing':requeued_missing,'cache_seconds':28800}
        self.acquisition_cache=dict(ledger);self.acquisition_cache_at=now
        with shared['lock']:
            if len(shared['items'])>=6:shared['items'].pop(next(iter(shared['items'])))
            shared['items'][shared_key]={'ids':dict(ledger),'loaded_at':now}
        return dict(ledger)

    def load_units(self,bounds=None,layouts=None,on_progress=None,cancel_event=None):
        """Load saved rental rows using keyset pagination and report storage-vs-display counts.

        Older app versions stored rows without a provider property_id and keyed them by
        rent/layout/address.  Newer rows are keyed by property_id.  Both records can remain
        in Supabase after migration.  They are not silently described as separate current
        listings: the diagnostic explicitly reports how many legacy rows are shadowed by an
        ID-backed row, while unmatched legacy rows remain visible.
        """
        out=[];last_id='';page_limit=500
        self.last_load_diagnostic={'time':utc_now(),'bounds':list(bounds) if bounds else None,'layouts':list(layouts) if layouts is not None else None,
                                   'pages':[],'excluded':{'invalid':0,'layout':0,'legacy_schema':0},'raw_records':0,'accepted_records':0,
                                   'property_id_records':0,'legacy_records':0,'legacy_shadowed':0,'legacy_unmatched':0,
                                   'storage_schema':8,'geocode_on_load':True,'pagination':'id_keyset'}
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            params={'namespace':'eq.'+self.namespace,'select':'id,summary,conditions','status':'eq.rental_listing',
                    'and':'(id.gte.listing.,id.lt.listing/)','order':'id.asc','limit':page_limit}
            if last_id:params['id']='gt.'+last_id
            while True:
                try:
                    rows=self.call('GET',SEARCH_TABLE,params)
                    break
                except AppError as exc:
                    diag=getattr(exc,'diagnostic',{}) or {}
                    if (diag.get('code')=='57014' or diag.get('status') in (500,502,503,504)) and page_limit>50:
                        page_limit=max(50,page_limit//2)
                        params['limit']=page_limit
                        continue
                    raise
            if not isinstance(rows,list):raise AppError('保存した募集情報を読み取れません。')
            self.last_load_diagnostic['pages'].append({'table':SEARCH_TABLE,'after_id':last_id or None,'returned':len(rows),'server_filters':['namespace','rental_listing']})
            self.last_load_diagnostic['raw_records']+=len(rows)
            if not rows:break
            page_start=len(out)
            for item in rows:
                meta=item.get('conditions') or {}
                if int(meta.get('schema') or 0) not in (3,4,5,6,7,8):
                    self.last_load_diagnostic['excluded']['legacy_schema']+=1;continue
                raw=(item.get('summary') or {}).get('listing')
                compact=compact_saved_listing(raw if isinstance(raw,dict) else {})
                if not compact:self.last_load_diagnostic['excluded']['invalid']+=1;continue
                if layouts is not None and compact['layout'] not in layouts:self.last_load_diagnostic['excluded']['layout']+=1;continue
                key=compact_listing_key(compact)
                out.append({**compact,'key':key,'storage_id':str(item.get('id') or ''),'title':compact['address'],'rent':compact['rent'],'layout':compact['layout'],'address':compact['address'],
                            'fetched_at':compact.get('fetched_at'),'fees':0,'loaded_from_compact_storage':True})
            self.last_load_diagnostic['accepted_records']=len(out)
            if on_progress:on_progress(out[page_start:],dict(self.last_load_diagnostic))
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last==last_id:raise AppError('保存データのページ位置を確定できません。')
            last_id=new_last
            if len(rows)<page_limit:break
        property_versions={}
        for r in out:
            pid=r.get('property_id')
            if pid:
                current=property_versions.get(pid)
                if current is None or newest_observation(current,r) is r:property_versions[pid]=r
        property_rows=list(property_versions.values())
        legacy_rows=[r for r in out if not r.get('property_id')]
        represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in property_rows}
        shadowed=[r for r in legacy_rows if r['key'] in represented]
        unmatched=[r for r in legacy_rows if r['key'] not in represented]
        display_rows=property_rows+unmatched
        unique=list({r['key']:r for r in display_rows}.values())
        shadowed_storage_ids={r.get('storage_id') for r in shadowed}
        # Full per-record audit duplicates the entire listing table in RAM.
        # Record-level export is prepared lazily from the already loaded rows.
        self.last_load_record_audit=[]
        self.last_load_diagnostic.update(property_id_records=len(property_rows),legacy_records=len(legacy_rows),legacy_shadowed=len(shadowed),
                                         legacy_unmatched=len(unmatched),before_key_merge=len(display_rows),key_duplicates=len(display_rows)-len(unique),returned=len(unique))
        return unique

    def load_units_since(self,since,layouts=None,cancel_event=None):
        """Load listing rows written/updated since a saved-data refresh started.

        Uses immutable id keyset pagination so concurrent inserts cannot shift offset pages.
        save_units updates SEARCH_TABLE.started_at on every listing upsert; repeated catch-up
        passes therefore capture writes that land while the full scan is running.
        """
        if not since:return []
        out=[];last_id=''
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            params={'namespace':'eq.'+self.namespace,'select':'id,summary,conditions,started_at',
                'status':'eq.rental_listing','started_at':'gte.'+str(since),
                'and':'(id.gte.listing.,id.lt.listing/)','order':'id.asc','limit':250}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('最新の保存物件を再確認できません。')
            if not rows:break
            for item in rows:
                meta=item.get('conditions') or {}
                if int(meta.get('schema') or 0) not in (3,4,5,6,7,8):continue
                raw=(item.get('summary') or {}).get('listing')
                compact=compact_saved_listing(raw if isinstance(raw,dict) else {})
                if not compact:continue
                if layouts is not None and compact['layout'] not in layouts:continue
                key=compact_listing_key(compact)
                out.append({**compact,'key':key,'storage_id':str(item.get('id') or ''),'title':compact['address'],'rent':compact['rent'],'layout':compact['layout'],'address':compact['address'],
                            'fetched_at':compact.get('fetched_at'),'fees':0,'loaded_from_compact_storage':True})
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last==last_id:raise AppError('最新保存分のページ位置を確定できません。')
            last_id=new_last
            if len(rows)<250:break
        property_versions={}
        for r in out:
            pid=r.get('property_id')
            if pid:
                current=property_versions.get(pid)
                if current is None or newest_observation(current,r) is r:property_versions[pid]=r
        property_rows=list(property_versions.values())
        represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in property_rows}
        legacy_rows=[r for r in out if not r.get('property_id') and r['key'] not in represented]
        return list({r['key']:r for r in property_rows+legacy_rows}.values())

    def save_address_points(self,points):
        """Persist address -> coordinate mappings so saved-listing loads do not geocode again."""
        if not isinstance(points,dict) or not points:return 0
        payload=[]
        for address,point in points.items():
            address=normal(address);key=saved_address_point_key(address)
            if not key or not point:continue
            try:
                lat,lng=float(point[0]),float(point[1])
            except (ValueError,TypeError,IndexError):continue
            if not has_point({'latitude':lat,'longitude':lng}):continue
            if getattr(self,'_saved_address_coords',{}).get(key)==(lat,lng):continue
            payload.append(dict(namespace=self.namespace,key=key,title=address[:220],kind=ADDRESS_POINT_KIND,
                                latitude=lat,longitude=lng,fetched_at=utc_now()))
        saved=0
        for offset in range(0,len(payload),500):
            batch=payload[offset:offset+500]
            result=self.call('POST',PLACE_TABLE,{'on_conflict':'namespace,key','select':'key'},batch,
                             'resolution=merge-duplicates,return=representation')
            returned={r.get('key') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
            expected={r['key'] for r in batch}
            if not expected<=returned:raise AppError('住所座標キャッシュの保存を確認できません。')
            saved+=len(expected)
            if not hasattr(self,'_saved_address_coords'):self._saved_address_coords={}
            self._saved_address_coords.update({r['key']:(r['latitude'],r['longitude']) for r in batch})
        return saved

    def load_address_points(self,addresses,cancel_event=None):
        """Look up address point keys by indexed primary key, without full-table scans."""
        wanted={saved_address_point_key(a):normal(a) for a in addresses if saved_address_point_key(a)}
        if not wanted:return {}
        out={}
        keys=list(wanted)
        for offset in range(0,len(keys),125):
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            batch=keys[offset:offset+125]
            rows=self.call('GET',PLACE_TABLE,{
                'namespace':'eq.'+self.namespace,
                'key':'in.('+','.join(batch)+')',
                'select':'key,latitude,longitude','limit':125})
            if not isinstance(rows,list):raise AppError('住所座標キャッシュを読み取れません。')
            for row in rows:
                address=wanted.get(str(row.get('key') or ''))
                if not address:continue
                try:point=(float(row['latitude']),float(row['longitude']),address)
                except (KeyError,ValueError,TypeError):continue
                if has_point({'latitude':point[0],'longitude':point[1]}):out[address]=point
        return out

    def save_places(self,rows,kind):
        if not rows: return set()
        payload=[dict(namespace=self.namespace,key=hashlib.sha256((kind+r['name']+str(r['lat'])+str(r['lng'])).encode()).hexdigest(),
                      title=r['name'],kind=kind,latitude=r['lat'],longitude=r['lng'],fetched_at=utc_now()) for r in rows]
        result=self.call('POST',PLACE_TABLE,{'on_conflict':'namespace,key','select':'key'},payload,
                         'resolution=merge-duplicates,return=representation')
        expected={r['key'] for r in payload}
        returned={r.get('key') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
        if not expected<=returned: raise AppError('周辺施設の保存を確認できません。')
        return expected
    def load_places(self,center,kind):
        s,w,n,e=rectangle(center,1000);offset=0;out=[]
        while True:
            rows=self.call('GET',PLACE_TABLE,{'namespace':'eq.'+self.namespace,'kind':'eq.'+kind,'select':'*','limit':300,
                'offset':offset,'order':'key.asc','and':f'(latitude.gte.{s},latitude.lte.{n},longitude.gte.{w},longitude.lte.{e})'})
            if not isinstance(rows,list): raise AppError('周辺施設を読み取れません。')
            if not rows: break
            for r in rows:
                if meters(center,(r['latitude'],r['longitude']))<=1000:
                    out.append(dict(name=r['title'],lat=r['latitude'],lng=r['longitude']))
            offset+=len(rows)
        return out
    def history(self):
        rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'*','order':'started_at.desc','limit':20,'status':'in.(started,completed,partial,failed,cancelled)'})
        if not isinstance(rows,list): raise AppError('検索履歴を読み取れません。')
        return rows

    def load_diagnostics(self,search_id):
        if not re.fullmatch(r'[0-9a-f]{64}',search_id): raise AppError('検索IDを確認してください。')
        events=[];offset=0
        while True:
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'id,summary','id':'like.'+search_id+'.log.*',
                                              'status':'eq.diagnostic_log','order':'id.asc','limit':300,'offset':offset})
            if not isinstance(rows,list):raise AppError('保存ログの形式を確認できません。')
            if not rows:break
            for row in rows:events.extend(row.get('summary',{}).get('events',[]))
            offset+=len(rows)
        return events

    def save_diagnostic_failures(self,events):
        """Persist every acquisition/error event independently from periodic diagnostic chunks."""
        if not events:return 0
        payload=[]
        for event in events:
            if not isinstance(event,dict):continue
            search_id=str(event.get('search_id') or '')
            seq=event.get('seq')
            if not re.fullmatch(r'[0-9a-f]{64}',search_id):continue
            try:seq=int(seq)
            except (TypeError,ValueError):continue
            context=event.get('context') if isinstance(event.get('context'),dict) else {}
            payload.append(dict(
                namespace=self.namespace,
                id=f'failure.{search_id}.{seq:010d}',
                status='diagnostic_error',
                started_at=event.get('time') or utc_now(),
                finished_at=event.get('time') or utc_now(),
                conditions={
                    'record_type':'diagnostic_error','schema':1,'search_id':search_id,'seq':seq,
                    'build':BUILD,'stage':event.get('stage'),'code':event.get('code'),'level':event.get('level'),
                    'search_mode':event.get('_search_mode'),'ward_code':event.get('_ward_code'),'town_codes':event.get('_town_codes'),
                    'context':context,
                },
                summary={'event':event},
            ))
        if not payload:return 0
        saved=0
        for offset in range(0,len(payload),100):
            batch=payload[offset:offset+100]
            result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},batch,
                             'resolution=merge-duplicates,return=representation')
            returned={r.get('id') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
            expected={r['id'] for r in batch}
            if not expected<=returned:raise AppError('取得エラーの永続保存を確認できません。')
            saved+=len(batch)
        return saved

    def load_diagnostic_failures(self):
        """Load failures from both v68+ per-error rows and older diagnostic-log chunks.

        Automatic collectors can survive Streamlit hot reloads.  A collector started on
        v66/v67 therefore has no per-error persistence hook even while the UI is already
        running newer code.  Historical diagnostic_log chunks remain authoritative, so
        reconstruct failures from them and merge with diagnostic_error rows by search/seq.
        """
        events=[];seen=set()
        def add_event(event):
            if not isinstance(event,dict) or not diagnostic_failure_event(event):return
            key=(str(event.get('search_id') or ''),str(event.get('seq') or ''),str(event.get('stage') or ''),str(event.get('code') or ''))
            if key in seen:return
            seen.add(key);events.append(event)
        last_id=''
        while True:
            params={'namespace':'eq.'+self.namespace,'status':'eq.diagnostic_error','select':'id,summary,conditions,started_at','order':'id.asc','limit':1000}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('保存済み取得エラーを読み取れません。')
            if not rows:break
            for row in rows:add_event((row.get('summary') or {}).get('event'))
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last==last_id:break
            last_id=new_last
            if len(rows)<1000:break
        # Backfill errors recorded before per-error rows existed (and errors from a cached
        # pre-v68 controller that is still running after code deployment).
        last_id=''
        while True:
            params={'namespace':'eq.'+self.namespace,'status':'eq.diagnostic_log','select':'id,summary','order':'id.asc','limit':500}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('過去の詳細作業ログを読み取れません。')
            if not rows:break
            for row in rows:
                for event in ((row.get('summary') or {}).get('events') or []):add_event(event)
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last==last_id:break
            last_id=new_last
            if len(rows)<500:break
        events.sort(key=lambda e:(str(e.get('time') or ''),str(e.get('search_id') or ''),int(e.get('seq') or 0)))
        return events


def save_diagnostic_failures_compat(db,events):
    """Use the current Database implementation even when a cached controller holds an older Database instance."""
    method=getattr(db,'save_diagnostic_failures',None)
    if callable(method):return method(events)
    return Database.save_diagnostic_failures(db,events)


def load_diagnostic_failures_compat(db):
    """Read persisted failures from old cached Database instances after a Streamlit hot reload."""
    method=getattr(db,'load_diagnostic_failures',None)
    if callable(method):return method()
    return Database.load_diagnostic_failures(db)


def persist_cached_controller_failures(controller):
    """Bridge a still-running pre-v68 automatic controller into current error persistence.

    Streamlit cache_resource can keep an AutomaticCollection/AuditLog instance created by
    an older app build.  Those AuditLog objects have no pending_failures queue, so scan only
    their newly appended local events and persist current failure semantics with a fresh DB.
    """
    if controller is None:return 0
    with controller.lock:
        jobs=[j for j in (getattr(controller,'current',None),getattr(controller,'last_job',None)) if j is not None]
    marker=st.session_state.setdefault('_compat_failure_saved_seq',{})
    db=Database();saved=0
    for job in jobs:
        audit=getattr(job,'audit',None)
        if audit is None or hasattr(audit,'pending_failures'):continue
        search_id=str(getattr(audit,'search_id','') or '')
        if not search_id:continue
        try:events=audit.records()
        except Exception:continue
        last=int(marker.get(search_id,0) or 0)
        new=[e for e in events if int(e.get('seq') or 0)>last and diagnostic_failure_event(e)]
        if new:saved+=save_diagnostic_failures_compat(db,new)
        marker[search_id]=max([last]+[int(e.get('seq') or 0) for e in events])
    return saved


GEO_HTTP_CACHE={}
GEO_EMPTY_ADDRESS_TILES={}
GEO_HTTP_LOCK=threading.Lock()


def response_title(text):
    match=re.search(r'<title\b[^>]*>(.*?)</title\s*>',(text or '')[:65536],re.I|re.S)
    return normal(html.unescape(re.sub(r'<[^>]+>',' ',match.group(1))))[:160] if match else ''

def response_encoding(response):
    content_type=response.headers.get('Content-Type','')
    charset=re.search(r'charset\s*=\s*[\"\']?([\w.-]+)',content_type,re.I)
    meta=re.search(br'charset\s*=\s*[\"\']?([\w.-]+)',response.content[:8192],re.I) if not charset else None
    candidate=charset.group(1) if charset else meta.group(1).decode('ascii') if meta else None
    if candidate:
        try:response.content.decode(candidate);return candidate
        except (LookupError,UnicodeError):pass
    try:response.content.decode('utf-8');return 'utf-8'
    except UnicodeError:return response.apparent_encoding or 'utf-8'


class PublicWeb:
    def __init__(self):
        self.address_points=None
        self.local=threading.local();self.cancel_event=threading.Event();self.cache_lock=threading.RLock();self.http_flights={};self.host_slots={};self.detail_slots=threading.BoundedSemaphore(DETAIL_WORKERS);self.jhj_tile_cache={};self.jhj_tile_cached_at={};self.address_result_cache={};self.suumo_location_cache={}
        self.headers={'User-Agent':'SumaiCompassRebuild/1.0 (personal rental research)', 'Accept-Language':'ja'}
        self.host_gates={};self.host_last_request={};self.host_backoff={};self.route_cooldowns={};self.session_primed=threading.local();self.proxy_routes=[];self.route_preferred={};self.route_lock=threading.Lock();self.http_cache={};self.headers.update({'Accept':'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8'})
        self.robots={};self.lock=threading.Lock();self.layout_form_lock=threading.Lock();self.layout_form_cache={};self.provider_cache={};self.provider_lock=threading.RLock();self.unavailable_hosts={};self.failed_detail_urls={};self.provider_runtime_disabled={}
        self.stage1_direct=True;self.stage1_http_proxies=[];self.stage1_search={}
        # HOME'S browser transport is lazy: Chromium is launched only after a genuine
        # server-side 403 from the normal HTTP path.  It is serialized because a
        # single human browser session is the intended navigation model.
        self.homes_browser_lock=threading.RLock();self.homes_browser_driver=None;self.homes_transport_mode='http';self.homes_browser_last_start=0.0;self.homes_browser_error=''
    def configure(self,config):
        self.proxy_routes=list((config or {}).get('proxies',[]))
        self.stage1_search=dict((config or {}).get('stage1_search') or {})
        self.stage1_direct=bool((config or {}).get('stage1_direct',True))
        self.stage1_http_proxies=list((config or {}).get('stage1_http_proxies') or [])
        # Repeated calls across towns must not reset this app's usage counter.
        if not hasattr(self,'stage1_api_lock'):self.stage1_api_lock=threading.RLock()
        if not hasattr(self,'stage1_api_uses'):self.stage1_api_uses=0
        if not hasattr(self,'stage1_api_cache'):self.stage1_api_cache={}
        if not hasattr(self,'stage1_api_disabled'):self.stage1_api_disabled=False
        trace(self,'transport_config',{'proxy_count':len(self.proxy_routes),'rental_route':'proxy' if self.proxy_routes else 'direct','other_services':'direct',
            'stage1_search_api':self.stage1_search.get('provider','off'),'stage1_api_consent':self.stage1_search.get('provider','off')!='off',
            'stage1_direct':self.stage1_direct,'stage1_proxy_count':len(self.stage1_http_proxies)},stage='http_config')
    def session(self,route=None):
        if not hasattr(self.local,'sessions'):self.local.sessions={}
        if route is None and hasattr(self.local,'session'):return self.local.session
        if route not in self.local.sessions:self.local.sessions[route]=requests.Session()
        return self.local.sessions[route]
    def check_cancel(self):
        if self.cancel_event.is_set():raise SearchCancelled()
    def pause(self,seconds):
        if self.cancel_event.wait(max(0.,seconds)):raise SearchCancelled()
    def host_policy(self,host):
        if self.address_points and host and host.endswith('.gsi.go.jp'):return self.address_points['http']
        return {'gates':self.host_gates,'slots':self.host_slots,'starts':self.host_last_request,'backoff':self.host_backoff,'lock':self.route_lock}
    def route_request(self,session,method,url,options):
        self.check_cancel();host=urlparse(url).hostname
        headers=dict(self.headers);headers.update(options.get('headers',{}));options={k:v for k,v in options.items() if k!='headers'}
        policy=self.host_policy(host)
        search_host=host in ('www.google.com','search.yahoo.co.jp') and urlparse(url).path=='/search'
        with policy['lock']:
            gate=policy['gates'].setdefault(host,threading.Lock())
            slots=policy['slots'].setdefault(host,threading.BoundedSemaphore(1 if search_host else PER_HOST_SLOTS))
        while not slots.acquire(timeout=.1):self.check_cancel()
        try:
            while not gate.acquire(timeout=.1):self.check_cancel()
            try:
                interval=(_STAGE1_DIRECT_MIN_INTERVAL if search_host else HOMES_BROWSER_MIN_INTERVAL if host=='www.homes.co.jp' else PER_HOST_MIN_INTERVAL);parser=self.robots.get(urlparse(url).scheme+'://'+urlparse(url).netloc)
                if parser:
                    crawl=parser.crawl_delay(self.headers['User-Agent']);rate=parser.request_rate(self.headers['User-Agent'])
                    if crawl:interval=max(interval,float(crawl))
                    if rate and rate.requests:interval=max(interval,float(rate.seconds)/rate.requests)
                while True:
                    delay=max(0.,policy['starts'].get(host,0.)+interval-time.monotonic(),policy['backoff'].get(host,0.)-time.monotonic())
                    if delay<=0:break
                    self.pause(delay)
                self.check_cancel();policy['starts'][host]=time.monotonic()
            finally:gate.release()
            # Keep one-second start spacing, but do not serialize the response wait.
            return session.request(method,url,headers=headers,timeout=(4,12),allow_redirects=False,**options)
        finally:slots.release()
    def _homes_browser_binary(self):
        """Return an installed Chromium-family binary without downloading anything."""
        for candidate in ('chromium','chromium-browser','google-chrome','google-chrome-stable'):
            path=shutil.which(candidate)
            if path:return path
        return ''
    def _homes_browser_driver(self):
        """Lazily create one real Chromium browser for HOME'S public pages."""
        if self.homes_browser_driver is not None:return self.homes_browser_driver
        if not HOMES_BROWSER_FALLBACK:raise AppError('HOME’Sブラウザ取得は無効です。')
        binary=self._homes_browser_binary()
        if not binary:
            error=AppError('HOME’Sをブラウザ取得するChromiumがありません。packages.txtにchromium / chromium-driverが必要です。')
            error.diagnostic={'browser_transport':'unavailable','reason':'chromium_not_found'};raise error
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except Exception as exc:
            error=AppError('HOME’Sをブラウザ取得するSeleniumがありません。requirements.txtにseleniumが必要です。')
            error.diagnostic={'browser_transport':'unavailable','reason':'selenium_not_found','exception_type':type(exc).__name__};raise error
        options=Options();options.binary_location=binary
        # These are execution-environment flags, not stealth/fingerprint modifications.
        for arg in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--window-size=1365,1200','--lang=ja-JP'):
            options.add_argument(arg)
        options.page_load_strategy='normal'
        driver_path=shutil.which('chromedriver')
        try:
            driver=webdriver.Chrome(service=Service(executable_path=driver_path) if driver_path else Service(),options=options)
            driver.set_page_load_timeout(HOMES_BROWSER_PAGE_TIMEOUT)
            try:driver.set_script_timeout(10)
            except Exception:pass
        except Exception as exc:
            error=AppError('HOME’S用Chromiumを起動できません。Streamlit CloudのChromium/driver設定を確認してください。')
            error.diagnostic={'browser_transport':'startup_failed','reason':'webdriver_start_failed','exception_type':type(exc).__name__};raise error
        self.homes_browser_driver=driver
        trace(self,'homes_browser_started',{'binary':binary,'driver':driver_path or 'selenium-manager','mode':'headless-chromium','stealth':False},stage='transport')
        return driver
    def close_homes_browser(self):
        with self.homes_browser_lock:
            driver=self.homes_browser_driver;self.homes_browser_driver=None
            if driver is not None:
                try:driver.quit()
                except Exception:pass
    def homes_browser_fetch(self,url,params=None):
        """Fetch a HOME'S public GET page through a real Chromium navigation.

        This is deliberately conservative: one browser, one navigation at a time,
        no CAPTCHA solving/stealth/proxy rotation.  A browser-side 403 still opens
        the existing SUUMO fallback circuit rather than being bypassed.
        """
        u=urlparse(url)
        if u.scheme!='https' or u.hostname!='www.homes.co.jp':raise AppError('HOME’Sブラウザ取得先が不正です。')
        pairs=list((params or {}).items()) if isinstance(params,dict) else list(params or [])
        target=requests.Request('GET',url,params=pairs or None).prepare().url
        with self.homes_browser_lock:
            self.check_cancel()
            delay=max(0.,self.homes_browser_last_start+HOMES_BROWSER_MIN_INTERVAL-time.monotonic())
            if delay:self.pause(delay)
            self.homes_browser_last_start=time.monotonic()
            driver=self._homes_browser_driver()
            try:
                current=urlparse(getattr(driver,'current_url','') or '')
                target_u=urlparse(target)
                # Same-host moves use normal browser navigation so cookies/session state
                # are preserved just as they are during ordinary browsing.
                if current.hostname==target_u.hostname and getattr(driver,'current_url','').startswith('http'):
                    driver.execute_script('window.location.assign(arguments[0]);',target)
                else:
                    driver.get(target)
                deadline=time.monotonic()+HOMES_BROWSER_PAGE_TIMEOUT
                while time.monotonic()<deadline:
                    self.check_cancel()
                    try:
                        if driver.execute_script('return document.readyState')=='complete':break
                    except Exception:pass
                    self.pause(.1)
                if HOMES_BROWSER_RENDER_WAIT:self.pause(HOMES_BROWSER_RENDER_WAIT)
                source=driver.page_source or '';final_url=driver.current_url or target;title=normal(driver.title)
            except SearchCancelled:raise
            except Exception as exc:
                error=AppError('HOME’Sのブラウザ取得に失敗しました。')
                error.diagnostic={'browser_transport':'navigation_failed','url':target,'exception_type':type(exc).__name__};raise error
            blocked=bool(re.search(r'(?:ERROR:\s*The request could not be satisfied|Access Denied|403 Forbidden|Request blocked|captcha|verify (?:you|your)|security check)',title+' '+source[:5000],re.I))
            if blocked or not source.strip():
                error=AppError('www.homes.co.jp：ブラウザでもHTTP 403相当のアクセス拒否が返りました。')
                error.diagnostic={'status':403,'browser_transport':'blocked','url':final_url,'page_title':title,'bytes':len(source.encode("utf-8"))};raise error
            response=requests.Response();response.status_code=200;response.url=final_url;response.encoding='utf-8'
            response._content=source.encode('utf-8');response.headers['Content-Type']='text/html; charset=UTF-8';response.headers['X-Sumai-Transport']='chromium'
            self.homes_transport_mode='browser'
            trace(self,'homes_browser_ok',{'url':target,'response_url':final_url,'status':200,'bytes':len(response.content),'page_title':title,'session':'persistent','parallelism':1},stage='transport')
            return response
    def response_info(self,response):
        title=response_title(response.text) if 'html' in response.headers.get('Content-Type','').lower() else ''
        challenge=bool(re.search(r'^(?:just a moment|access denied|attention required|403 forbidden|verify (?:you|your)|security check)',title,re.I))
        return {'page_title':title,'challenge_page':challenge,'response_sha256':hashlib.sha256(response.content).hexdigest(),
                'response_headers':{k:response.headers.get(k,'') for k in ('Server','Content-Type','Retry-After','Via','X-Cache','CF-Ray')}}
    def prime_session(self,session,root,options,route_number):
        if not hasattr(self.session_primed,'keys'):self.session_primed.keys=set()
        if not hasattr(self.session_primed,'references'):self.session_primed.references={}
        key=(root,route_number)
        if key in self.session_primed.keys:return False
        parser=self.robots.get(root)
        if parser is None or not parser.can_fetch(self.headers['User-Agent'],root+'/'):return False
        self.session_primed.keys.add(key)
        # Enter the site's published home page using the same route and cookie jar.
        warm_options={k:v for k,v in options.items() if k in ('proxies',)}
        reply=self.route_request(session,'GET',root+'/',warm_options)
        info=self.response_info(reply)
        trace(self,'session_entry',{'entry_url':root+'/','route_number':route_number,'status':reply.status_code,
                                  'cookie_count':len(getattr(session,'cookies',[])),**info},stage='transport')
        okay=reply.status_code==200 and bool(reply.content.strip()) and not info['challenge_page']
        if okay:self.session_primed.references[key]=root+'/'
        return okay
    def request_routes(self,method,url,**kwargs):
        u=urlparse(url);host=u.hostname;root=u.scheme+'://'+u.netloc
        rental=host in RENTAL_HOSTS
        stage1_page=host in ('www.google.com','search.yahoo.co.jp') and u.path=='/search'
        if stage1_page and self.stage1_http_proxies:
            routes=self.stage1_http_proxies
        else:
            routes=self.proxy_routes if rental and self.proxy_routes else [None]
        with self.route_lock:preferred=self.route_preferred.get(host,0)%len(routes)
        ordered=list(range(preferred,len(routes)))+list(range(preferred))
        last=None;skipped=[]
        for index in ordered:
            self.check_cancel()
            with self.route_lock:until=max(self.route_cooldowns.get((host,index),0.),self.route_cooldowns.get((host,index,u.path),0.))
            if until>time.monotonic():
                skipped.append(index+1);trace(self,'route_cooldown',{'host':host,'route_number':index+1,'remaining_seconds':round(until-time.monotonic())},stage='transport');continue
            route=routes[index];session=self.session(route);options=dict(kwargs)
            if stage1_page:
                options['headers']={**_STAGE1_DIRECT_NAV_HEADERS,**options.get('headers',{})}
            if route:options['proxies']={'http':route,'https':route}
            referer=getattr(self.session_primed,'references',{}).get((root,index+1))
            if referer:options['headers']={**options.get('headers',{}),'Referer':referer}
            attempts=1 if getattr(self.local,'landmark_lookup',False) and host in ('overpass-api.de','overpass.private.coffee','photon.komoot.io') else 3
            for attempt in range(attempts):
                try:
                    response=self.route_request(session,method,url,options);status=response.status_code;info=self.response_info(response)
                    trace(self,'transport_attempt',{'url':url,'method':method,'route':'proxy' if route else 'direct','route_number':index+1 if route else None,'attempt':attempt+1,'status':status,'bytes':len(response.content),**info},stage='transport')
                    last=response
                    blocked=status==403 or info['challenge_page']
                    if rental and blocked and attempt==0 and host!='www.homes.co.jp' and u.path not in ('/','/robots.txt'):
                        if self.prime_session(session,root,options,index+1):
                            headers=dict(options.get('headers',{}));headers['Referer']=root+'/';options['headers']=headers
                            trace(self,'session_retry',{'url':url,'route_number':index+1,'change':'same-route public entry cookies and actual Referer'},stage='transport');continue
                    # An HTTP 429 from an independent building-identity search is
                    # not a transient SUUMO error. Stop that host immediately. Do not
                    # override Retry-After or switch identities/cookies to bypass it.
                    if status==429 and host in ('www.google.com','search.yahoo.co.jp') and u.path=='/search':
                        raw_retry=response.headers.get('Retry-After','')
                        try:requested_wait=max(0.,float(raw_retry))
                        except (ValueError,TypeError):
                            try:requested_wait=max(0.,(parsedate_to_datetime(raw_retry)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError,TypeError,OverflowError):requested_wait=0.
                        pause_seconds=max(3600.,requested_wait)
                        with self.cache_lock:
                            breakers=getattr(self,'identity_search_429',None)
                            if breakers is None:breakers={};self.identity_search_429=breakers
                            breakers[host]=max(breakers.get(host,0.),time.monotonic()+pause_seconds)
                        trace(self,'identity_search_rate_limited',{'host':host,'status':429,
                            'next_stage':'authoritative_address_registry','pause_seconds':round(pause_seconds)},'WARNING','address.fallback')
                        # 429 is a host-level refusal: never try another proxy identity.
                        return response
                    # OSM's public endpoint has fair-use limits. A 429 is a
                    # refusal, not a signal to retry under another identity.
                    if host in ('overpass-api.de','overpass.private.coffee') and status==429:
                        trace(self,'osm_rate_limited',{'host':host,'status':429,
                            'next_stage':'stop_independent_lookup'},'WARNING','address.stage2')
                        break
                    retry=status in (429,500,502,503,504)
                    if retry:
                        raw=response.headers.get('Retry-After','')
                        try:delay=max(0.,float(raw))
                        except (ValueError,TypeError):
                            try:delay=max(0.,(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError,TypeError,OverflowError):delay=2**attempt
                        policy=self.host_policy(host)
                        with policy['lock']:policy['backoff'][host]=max(policy['backoff'].get(host,0.),time.monotonic()+delay)
                        if delay>30:
                            with self.route_lock:self.route_cooldowns[(host,index)]=time.monotonic()+delay
                            trace(self,'route_cooldown',{'host':host,'route_number':index+1,'remaining_seconds':round(delay),'reason':'Retry-After'},stage='transport');break
                        if attempt<attempts-1:
                            trace(self,'http_retry',{'url':url,'status':status,'wait_seconds':delay,'route_number':index+1 if route else None},'WARNING','transport');self.pause(delay);continue
                    if blocked:
                        with self.route_lock:self.route_cooldowns[(host,index,u.path)]=time.monotonic()+60
                        if info['challenge_page'] and status<400:
                            error=AppError(host+'：物件本文ではなくアクセス検証画面が返りました。');error.diagnostic={'status':status,**info};last=error
                        break
                    if status==202 and not response.content.strip() or retry:break
                    with self.route_lock:self.route_preferred[host]=index
                    return response
                except (requests.Timeout,requests.ConnectionError) as exc:
                    trace(self,'proxy_error' if route else 'network',{'url':url,'route_number':index+1 if route else None,'attempt':attempt+1,'exception_type':type(exc).__name__},'WARNING','transport')
                    last=exc
                    if attempt<attempts-1:self.pause(2**attempt);continue
                    break
            if len(ordered)>1:trace(self,'proxy_switch',{'url':url,'failed_route_number':index+1,'remaining_routes':len(ordered)-ordered.index(index)-1},'WARNING','transport')
        if isinstance(last,Exception):raise last
        if last is None:
            error=AppError(host+'：取得経路の待機中です（直前の403・429等）。設定した全経路の待機が終わってから再検索してください。');error.diagnostic={'skipped_routes':skipped};raise error
        return last
    def _fetch_uncached(self,url,method='GET',**kwargs):
        allowed=tuple(RENTAL_HOSTS)+('mreversegeocoder.gsi.go.jp','msearch.gsi.go.jp','maps.gsi.go.jp','cyberjapandata.gsi.go.jp','japanese-addresses-v2.geoloniamaps.com','overpass-api.de','overpass.private.coffee','photon.komoot.io','geolonia.github.io','img01.suumo.com','img02.suumo.com','maps.googleapis.com','maps.google.com','html.duckduckgo.com','www.google.com','search.yahoo.co.jp','myhome.nifty.com','www.mansion-review.jp','www.e-room.co','www.housecom.jp','vidax-gotanda.jp','www.goodrooms.jp','www.able.co.jp','www.mec-h.com','www.start-line.co.jp','www.m-standard.co.jp','www.property-bank.co.jp','lifullhomes-index.jp')+tuple(_EXTERNAL_IDENTITY_HOSTS)
        u=urlparse(url)
        if u.scheme!='https' or u.hostname not in allowed or u.username or u.password: raise AppError('取得先が不正です。')
        # After one genuine HOME'S 403, use one persistent real Chromium session for
        # the rest of this run instead of repeatedly sending blocked raw HTTP requests.
        if method=='GET' and u.hostname=='www.homes.co.jp' and u.path!='/robots.txt' and self.homes_transport_mode=='browser' and not getattr(self.local,'stage1_identity_only',False):
            return self.homes_browser_fetch(url,kwargs.get('params'))
        if method=='GET' and not kwargs.get('params') and url in self.http_cache:
            trace(self,'cache_hit',{'url':url,'source':'successful_detail_http_cache'},stage='http_cache');return self.http_cache[url]
        if u.hostname in self.unavailable_hosts:raise AppError(self.unavailable_hosts[u.hostname])
        if method=='GET' and not kwargs.get('params') and url in self.failed_detail_urls:raise AppError(self.failed_detail_urls[url])
        try:
            response=self.request_routes(method,url,**kwargs)
            for _ in range(3):
                if response.status_code not in (301,302,303,307,308): break
                target=urljoin(response.url,response.headers.get('Location',''))
                v=urlparse(target)
                if v.scheme!='https' or v.hostname!=u.hostname:
                    # Do not follow an untrusted, cross-host redirect.  Keep the
                    # evidence needed to distinguish SUUMO redirects from GSI /
                    # independent-search redirects in the next real failure log.
                    diagnostic={'status':response.status_code,'reason':'offsite_redirect',
                        'source_url':response.url,'source_host':u.hostname,
                        'target_host':v.hostname or '',
                        'target_scheme':v.scheme,
                        'target_path':v.path[:250],
                        'redirect_step':_+1}
                    trace(self,'redirect_offsite',diagnostic,'ERROR','transport')
                    error=AppError('取得先が別のサイトに移動しました（'+str(u.hostname)+' → '+str(v.hostname or '不明')+'）。')
                    error.diagnostic=diagnostic
                    raise error
                response=self.request_routes('GET',target)
            response.raise_for_status()
            if response.status_code==202 and not response.content.strip():
                message=f'{u.hostname}：HTTP 202・本文0バイト。掲載データが届いていません。この検索中は同じ取得元への問い合わせを停止します。'
                self.unavailable_hosts[u.hostname]=message;error=AppError(message);error.diagnostic={'status':202,'bytes':0};raise error
        except requests.Timeout as exc:
            error=AppError(f'{u.hostname}：接続または応答がタイムアウトしました。');error.diagnostic={'exception_type':type(exc).__name__,'reason':'接続経路の通信失敗'};raise error from None
        except requests.HTTPError as exc:
            # HOME'S sometimes rejects Streamlit Cloud's lightweight HTTP client while
            # serving the same public page to an ordinary browser.  On a genuine 403,
            # retry once through real Chromium.  If Chromium is unavailable or is also
            # refused, the existing circuit breaker switches the run to SUUMO only.
            if method=='GET' and u.hostname=='www.homes.co.jp' and u.path!='/robots.txt' and exc.response.status_code==403 and HOMES_BROWSER_FALLBACK and not getattr(self.local,'stage1_identity_only',False):
                try:
                    trace(self,'homes_http403_browser_retry',{'url':url,'parameters':kwargs.get('params') or {},'decision':'real_chromium_once'},'WARNING','transport')
                    return self.homes_browser_fetch(url,kwargs.get('params'))
                except AppError as browser_exc:
                    diagnostic=dict(getattr(browser_exc,'diagnostic',{}) or {});diagnostic.setdefault('status',403);diagnostic['raw_http_status']=403
                    browser_exc.diagnostic=diagnostic;self.homes_browser_error=str(browser_exc)
                    raise browser_exc from None
            message=f'{u.hostname}：HTTP {exc.response.status_code}（公開ページの取得失敗）。'
            if method=='GET' and not kwargs.get('params'):
                if len(self.failed_detail_urls)>=256:self.failed_detail_urls.pop(next(iter(self.failed_detail_urls)),None)
                self.failed_detail_urls[url]=message
            error=AppError(message);error.diagnostic={**self.response_info(exc.response),'status':exc.response.status_code}
            if u.hostname=='cyberjapandata.gsi.go.jp' and u.path.startswith('/xyz/experimental_jhj/') and exc.response.status_code==404:
                code=re.search(r'<Code>([^<]+)</Code>',exc.response.text[:4096])
                if code:error.diagnostic['server_error_code']=code.group(1)
            raise error from None
        except requests.exceptions.ProxyError:
            raise AppError('プロキシ接続失敗。接続先・ポート・認証情報・HTTPS CONNECT対応を確認してください。') from None
        except requests.ConnectionError as exc:
            error=AppError(f'{u.hostname}：接続できません（DNS・ネットワーク・接続先を確認）。');error.diagnostic={'exception_type':type(exc).__name__,'reason':'接続経路の通信失敗'};raise error from None
        except requests.RequestException: raise AppError(f'{u.hostname}：通信処理に失敗しました。') from None
        response.encoding='utf-8' if 'gsi.go.jp' in u.hostname else response_encoding(response)
        # Do not store this full response under a second URL key. fetch() owns
        # a single bounded cache, preventing duplicate in-memory HTML documents.
        return response
    def fetch(self,url,method='GET',**kwargs):
        self.check_cancel()
        if method!='GET':return self._fetch_uncached(url,method,**kwargs)
        params=kwargs.get('params') or {}
        pairs=list(params.items()) if isinstance(params,dict) else list(params)
        key=(url,urlencode(sorted(pairs,key=lambda p:str(p[0])),doseq=True))
        geo=urlparse(url).hostname in ('maps.gsi.go.jp','cyberjapandata.gsi.go.jp','mreversegeocoder.gsi.go.jp')
        if geo:
            with GEO_HTTP_LOCK:shared=GEO_HTTP_CACHE.get(key)
            if shared and time.monotonic()-shared[0]<86400:return shared[1]
        with self.cache_lock:
            if key in self.http_cache:return self.http_cache[key]
            flight=self.http_flights.get(key);owner=flight is None
            if owner:flight=futures.Future();self.http_flights[key]=flight
        if not owner:
            while not flight.done():self.pause(.05)
            return flight.result()
        try:
            result=self._fetch_uncached(url,method,**kwargs)
            # Retain only small, recent responses. A Response includes headers,
            # decoded body and transport metadata; thousands of these exhaust RAM.
            # In-flight deduplication still serves simultaneous callers regardless
            # of cache retention. Never truncate the response returned to parsers.
            response_bytes=len(result.content)
            if response_bytes<=160_000:
                with self.cache_lock:
                    if len(self.http_cache)>=8:
                        self.http_cache.pop(next(iter(self.http_cache)),None)
                    self.http_cache[key]=result
            if geo and response_bytes<=96_000:
                with GEO_HTTP_LOCK:
                    if len(GEO_HTTP_CACHE)>=8:
                        GEO_HTTP_CACHE.pop(next(iter(GEO_HTTP_CACHE)),None)
                    GEO_HTTP_CACHE[key]=(time.monotonic(),result)
            flight.set_result(result);return result
        except BaseException as exc:
            flight.set_exception(exc);raise
        finally:
            with self.cache_lock:self.http_flights.pop(key,None)
    def permitted(self,url):
        u=urlparse(url);root=u.scheme+'://'+u.netloc
        with self.lock: parser=self.robots.get(root)
        if parser is None:
            try:
                parser=urllib.robotparser.RobotFileParser();parser.parse(self.fetch(root+'/robots.txt').text.splitlines())
            except AppError as exc:
                if 'HTTP 404' in str(exc): parser=urllib.robotparser.RobotFileParser();parser.parse([])
                else: raise AppError('自動取得ルールを確認できません：'+str(exc)) from None
            with self.lock: self.robots[root]=parser
        permitted=parser.can_fetch(self.headers['User-Agent'],url)
        trace(self,'robots_allowed' if permitted else 'robots_disallowed',{'url':url,'rule_source':root+'/robots.txt','permitted':permitted},'INFO' if permitted else 'WARNING','robots')
        return permitted



_original_public_fetch=PublicWeb.fetch
def audited_public_fetch(self,url,method='GET',**kwargs):
    started=time.monotonic()
    details={'url':url,'method':method,'parameters':kwargs.get('params',kwargs.get('data',{}))}
    try:
        cached=method=='GET' and not kwargs.get('params') and url in self.http_cache
        response=_original_public_fetch(self,url,method,**kwargs)
        details.update(status=response.status_code,elapsed_ms=round((time.monotonic()-started)*1000),bytes=len(response.content),
                       content_type=response.headers.get('Content-Type',''),response_url=response.url,
                       response_sha256=hashlib.sha256(response.content).hexdigest())
        if 'html' in details['content_type'].lower():
            details['page_title']=response_title(response.text)
            details['html_selector_counts']='取得先の解析工程で記録（通信ログではHTML全体を再解析しない）'
        trace(self,'cache_hit' if cached else 'http_ok',details,stage='http_cache' if cached else 'http');return response
    except Exception as exc:
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),exception_type=type(exc).__name__,technical_exception=getattr(exc,'diagnostic',{}),message=str(exc) if isinstance(exc,AppError) else '応答処理失敗')
        match=re.search(r'HTTP (\d{3})',details['message']);details['status']=int(match[1]) if match else None
        empty_tile=(urlparse(url).hostname=='cyberjapandata.gsi.go.jp' and urlparse(url).path.startswith('/xyz/experimental_jhj/') and details['technical_exception'].get('status')==404 and details['technical_exception'].get('server_error_code')=='NoSuchKey')
        trace(self,'address_tile_empty_response' if empty_tile else diagnosis_code(details['message']),details,'INFO' if empty_tile else 'ERROR','http');raise
PublicWeb.fetch=audited_public_fetch

_original_db_call=Database.call
def audited_db_call(self,method,table,params=None,data=None,prefer=None):
    audit=getattr(self,'audit',None);started=time.monotonic()
    details={'method':method,'table':table,'row_count':len(data) if isinstance(data,list) else None}
    try:
        result=_original_db_call(self,method,table,params,data,prefer)
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),returned_rows=len(result) if isinstance(result,list) else None)
        if audit and not getattr(self,'_saving_audit',False):audit.add('database','db_ok','INFO',details)
        return result
    except Exception as exc:
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),message=str(exc) if isinstance(exc,AppError) else type(exc).__name__,
                       diagnostic=getattr(exc,'diagnostic',{}))
        if audit and not getattr(self,'_saving_audit',False):audit.add('database','save','ERROR',details)
        raise
Database.call=audited_db_call

def canonical_provider(provider):
    return PROVIDER_CANONICAL.get(str(provider or ''),str(provider or ''))


def safe_url(url,provider):
    try:
        provider=canonical_provider(provider);u=urlparse(url);host=PROVIDER_HOST.get(provider)
        if not host or u.scheme!='https' or u.hostname!=host or u.username or u.password or u.port not in (None,443):
            return False
        path=u.path or '/'
        prefixes={
            'スマイティ':('/chintai/',),'HOME’S':('/chintai/',),'SUUMO':('/chintai/',),
            'カナリー':('/chintai/',),'アットホーム':('/chintai/',),
            'CHINTAI':('/detail/','/tokyo/','/kanagawa/','/saitama/','/chiba/'),
            'Comfy':('/properties/','/rooms/','/rentals/','/'),
            'アパマンショップ':('/chintai/','/tokyo/','/kanagawa/','/saitama/','/chiba/'),
        }
        return any(path.startswith(p) for p in prefixes.get(provider,()))
    except (ValueError,TypeError): return False


def municipalities(web):
    text=web.fetch('https://maps.gsi.go.jp/js/muni.js').text
    table={}
    for code,value in re.findall(r'(?:GSI\.MUNI_ARRAY|muniArray)\["(\d+)"\]\s*=\s*[\'"]([^\'";]+)',text):
        fields=value.split(',')
        if len(fields)>=4: table[code]=(fields[0],fields[1],fields[3])
    if not table: raise AppError('国土地理院の地域一覧を読み取れません。')
    return table


def reverse(web,point,munis,force=False):
    if getattr(web,'reverse_unavailable',False) and not force: return None
    data=web.fetch('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress',
                   params={'lat':point[0],'lon':point[1]}).json().get('results',{})
    code=str(data.get('muniCd',''));town=str(data.get('lv01Nm',''))
    trace(web,'reverse_result' if code and town and code in munis else 'parse',{'point':point,'municipality_code':code,'town':town,'known_municipality':code in munis},level='INFO' if code and town and code in munis else 'WARNING',stage='geography')
    if not code or not town or code not in munis: return None
    pref,prefecture,city=munis[code]
    return dict(code=code,pref=pref,town=town,label=prefecture+city+town,point=point)


def _tile_xy(point,z=18):
    lat,lng=map(float,point);n=2**z
    x=int((lng+180.0)/360.0*n)
    y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
    return x,y


def _property_value(props,exact=(),contains=()):
    if not isinstance(props,dict):return ''
    normalized={normal(k).replace(' ','').replace('　',''):v for k,v in props.items()}
    for key in exact:
        nk=normal(key).replace(' ','').replace('　','')
        if nk in normalized and normalized[nk] not in (None,''):return normal(normalized[nk])
    for nk,value in normalized.items():
        if value in (None,''):continue
        if any(token in nk for token in contains):return normal(value)
    return ''


def _cache_jhj_features(web,key,features,cached_at=None):
    with web.cache_lock:
        cache=web.jhj_tile_cache;times=web.jhj_tile_cached_at
        if key not in cache and len(cache)>=12:
            oldest=next(iter(cache));cache.pop(oldest,None);times.pop(oldest,None)
        cache[key]=features;times[key]=time.monotonic() if cached_at is None else cached_at


def _v140_jhj_tile_under_gate(web,x,y,z=18):
    """Fetch GSI residential-address points once per tile. Images are never persisted."""
    cache=getattr(web,'jhj_tile_cache',None)
    if cache is None:cache={};web.jhj_tile_cache=cache
    key=(z,x,y)
    with web.cache_lock:
        at=getattr(web,'jhj_tile_cached_at',{}).get(key,0)
        if key in cache and time.monotonic()-at<(21600 if not cache[key] else 86400):return cache[key]
        cache.pop(key,None)
    with GEO_HTTP_LOCK:empty_at=GEO_EMPTY_ADDRESS_TILES.get(key)
    if empty_at is not None and time.monotonic()-empty_at<21600:
        # A cached empty tile is not another HTTP error. The initial real
        # NoSuchKey is already logged. Preserve cached empty result quietly.
        _cache_jhj_features(web,key,[],empty_at)
        return []
    url=f'https://cyberjapandata.gsi.go.jp/xyz/experimental_jhj/{z}/{x}/{y}.geojson'
    try:
        data=web.fetch(url).json()
        if not isinstance(data,dict) or data.get('type')!='FeatureCollection' or not isinstance(data.get('features'),list):raise AppError('住所タイルが有効なGeoJSON FeatureCollectionではありません。')
        features=data['features']
    except (AppError,ValueError,TypeError) as exc:
        diagnostic=getattr(exc,'diagnostic',{})
        if diagnostic.get('status')==404 and diagnostic.get('server_error_code')=='NoSuchKey':
            _cache_jhj_features(web,key,[])
            with GEO_HTTP_LOCK:
                if len(GEO_EMPTY_ADDRESS_TILES)>1000:
                    for old_key,when in list(GEO_EMPTY_ADDRESS_TILES.items()):
                        if time.monotonic()-when>21600:GEO_EMPTY_ADDRESS_TILES.pop(old_key,None)
                    if len(GEO_EMPTY_ADDRESS_TILES)>1000:
                        for old_key in list(GEO_EMPTY_ADDRESS_TILES)[:200]:GEO_EMPTY_ADDRESS_TILES.pop(old_key,None)
                GEO_EMPTY_ADDRESS_TILES[key]=time.monotonic()
            trace(web,'address_tile_empty',{'url':url,'tile':list(key),'status':404,'server_error_code':'NoSuchKey','reason':'この住所タイルのオブジェクトが存在しないため空タイルとして扱う'},stage='address.tiles')
            return []
        trace(web,'address_tile_failed',{'url':url,'exception_type':type(exc).__name__,'reason':str(exc),'diagnostic':getattr(exc,'diagnostic',{})},'WARNING','address.tiles')
        raise
    _cache_jhj_features(web,key,features)
    return features



def _jhj_tile(web,x,y,z=18):
    """Single-flight for each GSI tile in this app process (404s included).

    Multiple apartment workers previously issued the same request concurrently
    before the empty-tile cache was populated. A tile gate is not an OS lock.
    """
    key=(int(z),int(x),int(y))
    with web.cache_lock:
        gates=getattr(web,'v140_jhj_inflight',None)
        if gates is None:gates={};web.v140_jhj_inflight=gates
        gate=gates.setdefault(key,threading.Lock())
        if len(gates)>160:
            for old,other in list(gates.items()):
                if old!=key and not other.locked():
                    gates.pop(old,None)
                    if len(gates)<=120:break
    with gate:
        return _v140_jhj_tile_under_gate(web,x,y,z)

def map_point_to_residential_address(web,point,munis,max_distance_m=90,expected_code=None,expected_towns=None):
    expected_towns=tuple(normal(x) for x in (expected_towns or ()) if normal(x))
    key=(round(float(point[0]),6),round(float(point[1]),6),int(max_distance_m),str(expected_code or ''),tuple(address_key(x) for x in expected_towns))
    with web.cache_lock:
        cached=web.address_result_cache.get(key)
    if cached:
        age=time.monotonic()-cached[0];value=cached[1];ttl=86400 if value else ADDRESS_NEGATIVE_CACHE_SECONDS
        if age<ttl:
            trace(web,'address_cached' if value else 'address_negative_cached',{'point':list(point),'address':value.get('address') if value else None,'age_seconds':round(age,1)},stage='location')
            return dict(value) if value else None
    result=_map_point_to_residential_address(web,point,munis,max_distance_m,expected_code,expected_towns)
    reference=result.get('reference_point') if result else None
    if result and reference:remember_saved_position(web.address_points,result['address'],(*reference,result['address']))
    with web.cache_lock:
        cache=web.address_result_cache
        if len(cache)>=768:cache.pop(next(iter(cache)))
        cache[key]=(time.monotonic(),dict(result) if result else None)
    return result


def _map_point_to_residential_address(web,point,munis,max_distance_m=90,expected_code=None,expected_towns=()):
    """Map pin -> nearest GSI residential-address point with a verified town.

    Fast path: the provider search already supplies a municipality/town constraint. We
    compare the nearest JHJ residential point against that constraint and avoid a
    separate reverse-geocoder request when they agree. Boundary/ambiguous cases fall
    back to the reverse geocoder before acceptance, preserving the stricter check.
    """
    x,y=_tile_xy(point,18);candidates=[];tile_errors=[];feature_count=0
    z=18;n=2**z
    fx=(float(point[1])+180)/360*n
    fy=(1-math.asinh(math.tan(math.radians(float(point[0]))))/math.pi)/2*n
    tile_m=40075016.686*math.cos(math.radians(float(point[0])))/n
    tiles=[]
    for dx in (-1,0,1):
        for dy in (-1,0,1):
            tx,ty=x+dx,y+dy
            gx=max(tx-fx,0.,fx-(tx+1));gy=max(ty-fy,0.,fy-(ty+1))
            if math.hypot(gx,gy)*tile_m<=max_distance_m+2:tiles.append((tx,ty))
    def read_tile(tile):return _jhj_tile(web,*tile,18)
    with web.cache_lock:
        tile_cache=web.jhj_tile_cache;tile_times=web.jhj_tile_cached_at;now=time.monotonic()
        cached_tiles=[(t,tile_cache[(18,*t)],None) for t in tiles if (18,*t) in tile_cache and now-tile_times.get((18,*t),0)<(21600 if not tile_cache[(18,*t)] else 86400)]
    tile_results=cached_tiles if len(cached_tiles)==len(tiles) else bounded_results(tiles,read_tile,workers=2,stop_event=web.cancel_event)
    for tile,features,error in tile_results:
        if tile is None:continue
        if error:
            tile_errors.append({'tile':list(tile),'error':str(error),'exception_type':type(error).__name__});continue
        feature_count+=len(features)
        for feature in features:
            if not isinstance(feature,dict):continue
            geo=feature.get('geometry') or {};coords=geo.get('coordinates')
            if geo.get('type')!='Point' or not isinstance(coords,(list,tuple)) or len(coords)<2:continue
            try:lng2,lat2=float(coords[0]),float(coords[1])
            except (ValueError,TypeError):continue
            props=feature.get('properties') or {}
            code=_property_value(props,('市区町村コード','市町村コード','municipality_code','muniCd'),('市区町村コード','市町村コード'))
            if len(code)>5 and code[:5] in munis:code=code[:5]
            town=_property_value(props,('町又は字の名称','町字名','町名'),('町又は字','町字'))
            town=re.sub(r'[-－](\d+)$',r'\1丁目',town)
            block=_property_value(props,('街区符号','街区'),('街区符号',))
            base=_property_value(props,('基礎番号','住居番号'),('基礎番号',))
            if not town or not block or not base:continue
            dist=meters(point,(lat2,lng2))
            candidates.append((dist,code,town,block,base,lat2,lng2,props))
    if tile_errors and candidates:
        # A neighboring tile failure must not discard valid detailed-address points
        # already obtained from the other required tiles. Keep the candidates and
        # record the partial failure for diagnostics.
        trace(web,'address_tiles_partial_failure',{'point':list(point),'tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'candidate_count':len(candidates),'decision':'continue_with_available_candidates'},'WARNING','address.tiles')
    elif tile_errors and not candidates:
        # Retry only failed residential tiles once. PublicWeb remembers failed URLs,
        # so clear that short-lived memo before this controlled single retry.
        retry_errors=[]
        for item in list(tile_errors):
            tile=tuple(item.get('tile') or ())
            if len(tile)!=2:continue
            url=f'https://cyberjapandata.gsi.go.jp/xyz/experimental_jhj/18/{tile[0]}/{tile[1]}.geojson'
            with web.cache_lock:web.failed_detail_urls.pop(url,None)
            try:features=_jhj_tile(web,*tile,18)
            except Exception as exc:
                retry_errors.append({'tile':list(tile),'error':str(exc),'exception_type':type(exc).__name__});continue
            feature_count+=len(features)
            for feature in features:
                if not isinstance(feature,dict):continue
                geo=feature.get('geometry') or {};coords=geo.get('coordinates')
                if geo.get('type')!='Point' or not isinstance(coords,(list,tuple)) or len(coords)<2:continue
                try:lng2,lat2=float(coords[0]),float(coords[1])
                except (ValueError,TypeError):continue
                props=feature.get('properties') or {}
                code=_property_value(props,('市区町村コード','市町村コード','municipality_code','muniCd'),('市区町村コード','市町村コード'))
                if len(code)>5 and code[:5] in munis:code=code[:5]
                town=_property_value(props,('町又は字の名称','町字名','町名'),('町又は字','町字'))
                town=re.sub(r'[-－](\d+)$',r'\1丁目',town)
                block=_property_value(props,('街区符号','街区'),('街区符号',))
                base=_property_value(props,('基礎番号','住居番号'),('基礎番号',))
                if not town or not block or not base:continue
                candidates.append((meters(point,(lat2,lng2)),code,town,block,base,lat2,lng2,props))
        trace(web,'address_tiles_retry',{'point':list(point),'initial_tile_errors':tile_errors,'remaining_errors':retry_errors,'candidate_count':len(candidates)},'INFO' if candidates else 'WARNING','address.tiles')
        tile_errors=retry_errors
    if not candidates:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'GSI住居表示住所の候補なし','tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'maximum_distance_m':max_distance_m},'WARNING','location')
        return None
    compatible=[c for c in candidates
        if (not expected_code or not c[1] or c[1]==str(expected_code))
        and (not expected_towns or any(town_matches(t,c[2]) or address_key(t)==address_key(c[2])
            for t in expected_towns))]
    # Never fabricate a town-specific address from unrelated GSI points.
    # If none matches, the existing reverse-geocoder safeguard still applies.
    best=min(compatible or candidates,key=lambda c:c[0])
    if compatible and len(compatible)!=len(candidates):
        trace(web,'v142_expected_town_candidates_preferred',{'matching':len(compatible),
            'total':len(candidates),'chosen_distance_m':round(best[0],2)},stage='location')
    if best[0]>max_distance_m:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'最近傍の住居表示住所が遠すぎる','distance_m':round(best[0],1),'tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'maximum_distance_m':max_distance_m},'WARNING','location')
        return None
    _,code,town,block,base,lat2,lng2,_=best
    expected_code=str(expected_code or '')
    expected_towns=tuple(expected_towns or ())
    expected_match=(not expected_code or code==expected_code) and (not expected_towns or any(town_matches(t,town) or address_key(t)==address_key(town) for t in expected_towns))
    official=None
    if not code or code not in munis or not expected_match:
        # Ambiguous boundary/missing-code cases use the official reverse geocoder as
        # a verification fallback; the common case needs no extra HTTP request.
        official=reverse(web,point,munis,force=True)
        if not official:
            trace(web,'map_address_unresolved',{'point':list(point),'failed_stage':'address.reverse_geocode','reason':'境界確認で有効な市区町村・町丁目を取得できない'},'WARNING','location');return None
        if expected_code and official['code']!=expected_code:
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'物件位置の市区町村が検索対象と一致しない','official_code':official['code'],'expected_code':expected_code},'WARNING','location');return None
        if expected_towns and not any(town_matches(t,official['town']) or address_key(t)==address_key(official['town']) for t in expected_towns):
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'物件位置の町丁目が検索対象と一致しない','official_town':official['town'],'expected_towns':list(expected_towns)},'WARNING','location');return None
        matching=[c for c in candidates if (not c[1] or c[1]==official['code']) and address_key(c[2])==address_key(official['town'])]
        if not matching:
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'逆ジオコーディング町丁目内に住居表示候補なし','town':official['town'],'candidate_count':len(candidates)},'WARNING','location');return None
        best=min(matching,key=lambda c:c[0])
        if best[0]>max_distance_m:return None
        _,code,town,block,base,lat2,lng2,_=best
    if not code and official:code=official['code']
    if not code or code not in munis:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'住所候補の市区町村コードを確認できない','candidate_code':code},'WARNING','location');return None
    pref_code,prefecture,city=munis[code]
    address=f'{prefecture}{city}{town}{block}-{base}'
    result={'address':address,'distance_m':best[0],'reference_point':[lat2,lng2],
            'official_region':official or {'code':code,'pref':pref_code,'town':town,'label':prefecture+city+town},
            'verification':'jhj_nearest_expected_town' if official is None else 'reverse_geocoder_boundary_fallback'}
    trace(web,'map_address_from_gsi_jhj',{'map_point':list(point),'address':address,'reference_point':[lat2,lng2],'distance_m':round(best[0],1),'verification':result['verification']},stage='location')
    return result


def gsi_regional_tasks(web,bounds,notify):
    """All residential-label points plus an uncapped 100 m grid and edges."""
    munis=municipalities(web);s,w,n,e=bounds
    reverse(web,bounds_center(bounds),munis)
    labels=set();errors=done=0;tiles=tiles_in_bounds(bounds)
    def label_tile(tile):
        x,y=tile
        data=web.fetch(f'https://cyberjapandata.gsi.go.jp/xyz/experimental_nrpt/15/{x}/{y}.geojson').json()
        if not isinstance(data,dict) or not isinstance(data.get('features'),list):
            raise AppError('地名タイルの形式を確認できません。')
        points=[]
        for f in data['features']:
            geo=f.get('geometry') or {}
            if geo.get('type')!='Point': continue
            try:
                lng,lat=map(float,geo['coordinates'][:2])
                if in_rectangle((lat,lng),bounds): points.append((lat,lng))
            except (KeyError,TypeError,ValueError): continue
        return points
    for tile,points,error in bounded_results(tiles,label_tile,workers=12,stop_event=web.cancel_event):
        if tile is not None:
            done+=1
            if error: errors+=1
            else: labels.update(points)
        notify('地名タイル',done,len(tiles),len(labels),errors)
    # Include every edge; no 15x15/sample-count cap. Labels cover small named areas between grid points.
    ny=max(1,math.ceil((n-s)*111320/100))
    nx=max(1,math.ceil((e-w)*111320*math.cos(math.radians((s+n)/2))/100))
    grid=((s+(n-s)*y/ny,w+(e-w)*x/nx) for y in range(ny+1) for x in range(nx+1))
    import itertools
    points=itertools.chain(sorted(labels),grid)
    total=len(labels)+(nx+1)*(ny+1);done=0;regions={}
    def identify(point): return reverse(web,point,munis)
    for point,region,error in bounded_results(points,identify,workers=8,stop_event=web.cancel_event):
        if point is not None:
            done+=1
            if error:
                errors+=1
                if errors>=6: raise AppError('地名判定の接続失敗が続いています：'+str(error))
            elif region: regions[(region['code'],address_key(region['town']))]=region
        notify('地名・丁目判定',done,total,len(regions),errors)
    if not regions: raise AppError('地図範囲の地名を確認できません。国土地理院への接続を確認してください。')
    return sorted(regions.values(),key=lambda r:r['label']),munis,errors



CATALOG_CACHE={}
CATALOG_LOCK=threading.Lock()

def catalog_json(web,url):
    with CATALOG_LOCK: cached=CATALOG_CACHE.get(url)
    if cached and time.monotonic()-cached[0]<86400:
        trace(web,'cache_hit',{'url':url,'age_seconds':round(time.monotonic()-cached[0])},stage='geography');return cached[1]
    try: data=web.fetch(url).json()
    except ValueError: raise AppError('町丁目データのJSONを読み取れません。') from None
    if not isinstance(data,dict) or not isinstance(data.get('data'),list): raise AppError('町丁目データの形式が不正です。')
    with CATALOG_LOCK: CATALOG_CACHE[url]=(time.monotonic(),data)
    return data


def catalog_regions(web,bounds,notify):
    """Independent ABR-derived town catalog; search all towns of overlapping municipalities.
    Representative town points are not boundaries, so neighboring municipalities and
    every town without a point are included rather than imposing a candidate cap.
    """
    root='https://japanese-addresses-v2.geoloniamaps.com/api/ja/'
    index=catalog_json(web,root[:-1]+'.json')
    if not isinstance(index,dict) or not isinstance(index.get('data'),list): raise AppError('町丁目一覧を読み取れません。')
    cities=[];munis={};regions={};errors=[]
    for pref in index['data']:
        pc=str(pref.get('code','')).zfill(6)[:2]
        if pc not in ('11','12','13','14'): continue
        for city in pref.get('cities',[]):
            name=str(city.get('city',''))+str(city.get('ward') or '')
            code=str(city.get('code','')).zfill(6)[:5]
            munis[code]=(pc,pref['pref'],name)
            cities.append((code,pc,pref['pref'],name))
    def towns(city):
        code,pc,pref,name=city
        data=catalog_json(web,root+quote(pref,safe='')+'/'+quote(name,safe='')+'.json')
        if not isinstance(data,dict) or not isinstance(data.get('data'),list): raise AppError(name+'：町丁目一覧の形式が不正です。')
        return data['data']
    done=0;s,w,n,e=bounds
    # Broad buffer protects against town representative points outside the visible boundary.
    padded=(s-.018,w-.023,n+.018,e+.023)
    for city,rows,error in bounded_results(cities,towns,workers=12,stop_event=web.cancel_event):
        if city is not None:
            done+=1
            if error: errors.append(city[3]+'：'+str(error))
            else:
                code,pc,pref,name=city
                nearby=False
                for row in rows:
                    point=row.get('point')
                    if isinstance(point,list) and len(point)>=2 and in_rectangle((point[1],point[0]),padded): nearby=True;break
                if nearby:
                    for row in rows:
                        town=''.join(str(row.get(k) or '') for k in ('oaza_cho','chome','koaza'))
                        if not town: continue
                        point=row.get('point');point=(point[1],point[0]) if isinstance(point,list) and len(point)>=2 else bounds_center(bounds)
                        regions[(code,address_key(town))]=dict(code=code,pref=pc,town=town,label=pref+name+town,point=point)
        notify('独立した町丁目一覧',done,len(cities),len(regions),len(errors))
    if errors: raise AppError('町丁目一覧の一部を取得できません。網羅性を確認できないため検索を開始しません。'+ '／'.join(errors[:3]))
    if not regions: raise AppError('表示範囲と近隣に対応する町丁目がありません。地図範囲を確認してください。')
    return sorted(regions.values(),key=lambda r:r['label']),munis,0


def regional_tasks(web,bounds,notify):
    reasons=[]
    try: return gsi_regional_tasks(web,bounds,notify)
    except Exception as exc:
        reasons.append(str(exc) if isinstance(exc,AppError) else '国土地理院の応答形式を確認できません。')
        web.reverse_unavailable=True
        notify('地名取得先を切替：'+reasons[-1],0,1,0,1)
    try: return catalog_regions(web,bounds,notify)
    except Exception as exc:
        reasons.append(str(exc) if isinstance(exc,AppError) else '独立した町丁目一覧の応答形式を確認できません。')
        raise AppError('地名取得の両経路が失敗しました。'+'／'.join(reasons)) from None

def labeled(soup,labels):
    for label in soup.find_all(['th','dt']):
        if normal(label.get_text(' ',strip=True)).replace(' ','') in labels:
            value=label.find_next_sibling(['td','dd'])
            if value: return value.get_text(' ',strip=True)
    return ''


def json_nodes(value):
    if isinstance(value,dict):
        yield value
        for x in value.values():
            if isinstance(x,(dict,list)): yield from json_nodes(x)
    elif isinstance(value,list):
        for x in value: yield from json_nodes(x)


def coordinate(soup,text,hint=None):
    candidates=[]
    def add(lat,lng,method,priority):
        try:
            lat,lng=float(lat),float(lng)
            if math.isfinite(lat) and math.isfinite(lng) and 34<=lat<=37 and 138<=lng<=141:
                item=(lat,lng,method,priority)
                if item not in candidates:candidates.append(item)
        except (ValueError,TypeError):pass
    pair=r'(3[4-7]\.\d+)\s*[,;| ]\s*(1(?:3[89]|40)\.\d+)'
    for script in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.get_text())
        except ValueError:continue
        for node in json_nodes(data):
            types=node.get('@type',[]);types=[types] if isinstance(types,str) else types
            if any(t in ('Organization','RealEstateAgent','LocalBusiness','Place','Store') for t in types):continue
            geo=node.get('geo')
            if isinstance(geo,dict):add(geo.get('latitude'),geo.get('longitude'),'掲載物件JSON-LD',6)
    regex_text=re.sub(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>.*?</script>', '',text,flags=re.I|re.S)
    # Handle quoted and unquoted JS fields, property-prefixed names, and both pair orders.
    la=r'(?:bukken|property|building|map|center)?(?:lat|latitude|ido)'
    lo=r'(?:bukken|property|building|map|center)?(?:lng|lon|longitude|keido)'
    for pattern,reverse_pair in [(rf'(?<!\w)["\']?{la}["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)["\']?.{{0,160}}?(?<!\w)["\']?{lo}["\']?\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)',False),
                                 (rf'(?<!\w)["\']?{lo}["\']?\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)["\']?.{{0,160}}?(?<!\w)["\']?{la}["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)',True)]:
        for match in re.finditer(pattern,html.unescape(regex_text),re.S|re.I):
            names=re.findall(r'(?<!\w)["\']?('+la+'|'+lo+r')["\']?\s*[:=]',match[0],re.I)
            explicit=len(names)>=2 and all(re.match(r'(?:bukken|property|building)',n,re.I) for n in (names[0],names[-1]))
            add(match[2] if reverse_pair else match[1],match[1] if reverse_pair else match[2],'掲載物件の座標設定' if explicit else '地図中心候補（未検証）',5 if explicit else 1)
    for tag in soup.select('input[value],[data-lat],[data-latitude],[data-ido]'):
        attrs={str(k).lower():v for k,v in tag.attrs.items()}
        for a,b in [('data-lat','data-lng'),('data-lat','data-lon'),('data-latitude','data-longitude'),('data-ido','data-keido')]:
            if a in attrs and b in attrs:add(attrs[a],attrs[b],'掲載地図属性',5 if re.search(r'bukken|property|building',str(tag.attrs),re.I) else 1)
        name=str(tag.get('name') or tag.get('id') or '').lower();value=html.unescape(str(tag.get('value','')))
        if re.search(r'(?:map|gmap|bukken|property|coord|latlng)',name):
            for match in re.finditer(pair,value):add(match[1],match[2],'掲載地図の座標入力',5 if re.search(r'bukken|property|point',name) else 2)
    fields={re.sub(r'[^a-z]','',str(tag.get('name') or tag.get('id') or '').lower()):tag.get('value','') for tag in soup.select('input[value]')}
    for prefix in ('','bukken','property','building','map','center','js'):
        for a,b in [('lat','lng'),('latitude','longitude'),('lat','lon'),('ido','keido')]:
            if prefix+a in fields and prefix+b in fields:add(fields[prefix+a],fields[prefix+b],'掲載地図の座標入力',5 if prefix in ('bukken','property','building') else 2)
    for match in re.finditer(r'(?:LatLng|setView)\s*\(\s*\[?\s*'+pair,text,re.I):add(match[1],match[2],'掲載地図設定（中心・未検証）',1)
    for tag in soup.select('[href],[src],[data-src],[data-url]'):
        for attr in ('href','src','data-src','data-url'):
            url=html.unescape(str(tag.get(attr) or ''));params=parse_qs(urlparse(url).query)
            for key in ('markers','ll','q','query','center'):
                for value in params.get(key,[]):
                    for match in re.finditer(pair,value):add(match[1],match[2],'掲載地図URLの物件ピン' if key=='markers' else '掲載地図URL',5 if key=='markers' and re.search(r'物件|bukken|property|building',str(tag.attrs),re.I) and not re.search(r'店舗|会社|営業|アクセス',str(tag.attrs)) else 1)
            for a,b in [('lat','lng'),('lat','lon'),('latitude','longitude'),('ido','keido')]:
                if a in params and b in params:add(params[a][0],params[b][0],'掲載地図URLの座標（中心・未検証）',1)
            for match in re.finditer(r'!3d(3[4-7]\.\d+)!4d(1(?:3[89]|40)\.\d+)',url):add(match[1],match[2],'埋め込み地図の位置（中心・未検証）',1)
    for match in re.finditer(r'(?:bukken|property|building)(?:Marker|Pin)\s*=\s*(?:L\.)?marker\s*\(\s*\[\s*'+pair,text,re.I):add(match[1],match[2],'物件地図のマーカー座標',5)
    for match in re.finditer(r'(?:bukken|property|building)(?:Marker|Pin)\s*=.{0,120}?position\s*:\s*new\s+(?:google\.maps\.)?LatLng\s*\(\s*'+pair,text,re.I):add(match[1],match[2],'物件地図のマーカー座標',5)
    if hint:add(hint[0],hint[1],'HOME’S物件検索地図',6)
    return candidates


def image_pin_point(content,center,zoom,scale=1):
    """Recognize a colored map pin, then project its tip using declared Web Mercator metadata."""
    import numpy as np
    from PIL import Image
    image=Image.open(io.BytesIO(content)).convert('RGB');pixels=np.asarray(image,dtype=np.int16)
    height,width=pixels.shape[:2];r,g,b=pixels[:,:,0],pixels[:,:,1],pixels[:,:,2]
    mask=(r>150)&(r-g>65)&(r-b>50) # Red location-pin foreground, not black text/gray roads.
    seen=np.zeros(mask.shape,dtype=bool);components=[]
    for y,x in zip(*np.where(mask)):
        if seen[y,x]:continue
        stack=[(int(x),int(y))];seen[y,x]=True;pts=[]
        while stack:
            xx,yy=stack.pop();pts.append((xx,yy))
            for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                if 0<=nx<width and 0<=ny<height and mask[ny,nx] and not seen[ny,nx]:seen[ny,nx]=True;stack.append((nx,ny))
        xs,ys=zip(*pts);w,h=max(xs)-min(xs)+1,max(ys)-min(ys)+1
        # A pin must have a head and a tapering lower tip; a red road/label isn't enough.
        if len(pts)<20 or not (5<=w<=80*scale and 8<=h<=100*scale and .8<=h/w<=3.5):continue
        bottom=[xx for xx,yy in pts if yy>=max(ys)-max(1,int(h*.1))]
        if not bottom or max(bottom)-min(bottom)+1>w*.65:continue
        rows_width=[sum(1 for xx,yy in pts if yy==qy) for qy in range(min(ys),max(ys)+1)]
        if max(rows_width[:max(1,int(h*.08))])>w*.7:continue
        crop=mask[min(ys):max(ys)+1,min(xs):max(xs)+1];background=~crop;outside=np.zeros(crop.shape,dtype=bool)
        todo=[(xx,yy) for yy in range(h) for xx in range(w) if background[yy,xx] and (xx in (0,w-1) or yy in (0,h-1))]
        for xx,yy in todo:outside[yy,xx]=True
        while todo:
            xx,yy=todo.pop()
            for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                if 0<=nx<w and 0<=ny<h and background[ny,nx] and not outside[ny,nx]:outside[ny,nx]=True;todo.append((nx,ny))
        if int((background & ~outside).sum())<max(4,int(w*h*.02)):continue
        components.append({'pixel':[statistics.median(bottom),max(ys)],'pixels':len(pts),'bbox':[min(xs),min(ys),max(xs),max(ys)]})
    components.sort(key=lambda v:-v['pixels'])
    if not components:return None,{'reason':'画像に物件ピン形状を検出できない','width':width,'height':height}
    if len(components)>1:return None,{'reason':'同程度のピン候補が複数あり物件ピンを特定できない','candidates':components}
    pin=components[0];world=256*(2**float(zoom));lat,lng=map(float,center)
    x=(lng+180)/360*world;y=(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*world
    x+=(pin['pixel'][0]-width/2)/float(scale);y+=(pin['pixel'][1]-height/2)/float(scale)
    point=(math.degrees(math.atan(math.sinh(math.pi*(1-2*y/world)))),x/world*360-180)
    return point,{'method':'赤色ピンの連結領域・先端認識＋Web Mercator逆投影','center':list(center),'zoom':zoom,'scale':scale,'pin':pin,'width':width,'height':height,'point':list(point)}


def _v141_image_georef_notice(web,details):
    """Report one missing metadata reason per SUUMO listing, not per image URL."""
    listing=str(getattr(DIAG_CONTEXT,'scope',{}).get('listing_url') or '')
    key=(listing,normal(details.get('reason')))
    with web.cache_lock:
        seen=getattr(web,'v141_image_georef_seen',None)
        if seen is None:seen=set();web.v141_image_georef_seen=seen
        if key in seen:return
        if len(seen)>400:seen.clear()
        seen.add(key)
    # Keep this failure visible for diagnostics; no unsupported image can
    # safely yield coordinates. A verified map-center identity fallback is
    # separately attempted, without inventing image metadata.
    trace(web,'image_georef_missing',details,'WARNING','image_location')


def map_image_candidates(web,soup,source_url):
    """Find georeferenced static-map images, then recognize the property pin from pixels."""
    results=[];entries=[]
    for tag in soup.select('img[src],img[data-src],img[data-original]'):
        raw=tag.get('data-src') or tag.get('data-original') or tag.get('src') or ''
        url=urljoin(source_url or '',html.unescape(raw));label=' '.join(str(tag.get(k,'') or '') for k in ('alt','id','class'))
        entries.append((url,label,str(tag.get('data-center','') or ''),tag.get('data-zoom')))
    # SUUMO may keep the Google Static Maps URL in script/data rather than a literal <img> node.
    raw_html=html.unescape(str(soup)).replace('\\/','/').replace('\\u0026','&').replace('\\x26','&')
    for match in re.finditer(r'https://(?:maps\.googleapis\.com|maps\.google\.com)/[^"\'<>\s]*staticmap\?[^"\'<>\s]+',raw_html,re.I):
        url=match.group(0).rstrip('),;');entries.append((url,'script staticmap','',''))
    seen_urls=set()
    for url,label,data_center,data_zoom in entries:
        if not url or url in seen_urls:continue
        seen_urls.add(url);params=parse_qs(urlparse(url).query)
        if not ('staticmap' in url.lower() or re.search('地図|map',label,re.I)):continue
        if re.search('店舗|会社|営業|アクセス',label):continue
        if not ('kankyo' in (source_url or '') or 'FR301FD003' in (source_url or '') or re.search('物件|bukken|property|staticmap',label,re.I)):continue
        center_text=params.get('center',[data_center])[0];match=re.fullmatch(r'\s*(3[4-7]\.\d+)\s*,\s*(1(?:3[89]|40)\.\d+)\s*',center_text)
        try:zoom=float(params.get('zoom',[data_zoom])[0]);scale=float(params.get('scale',[1])[0])
        except (ValueError,TypeError):zoom=None;scale=1
        if not match or zoom is None or not math.isfinite(zoom) or not math.isfinite(scale) or not (0<=zoom<=22 and scale in (1,2)):
            _v141_image_georef_notice(web,{'image_host':urlparse(url).hostname,'reason':'地図画像の基準座標・ズームを読み取れない'});continue
        supported=urlparse(url).hostname in ('maps.googleapis.com','maps.google.com') and 'staticmap' in urlparse(url).path
        size=re.fullmatch(r'(\d+)x(\d+)',params.get('size',[''])[0])
        if not supported or not size:
            _v141_image_georef_notice(web,{'reason':'地図画像の投影方式・元の画像サイズを検証できない'});continue
        try:
            if not web.permitted(url):continue
            response=web.fetch(url)
            from PIL import Image
            actual=Image.open(io.BytesIO(response.content)).size
            expected=(int(size[1])*int(scale),int(size[2])*int(scale))
            if actual!=expected:
                _v141_image_georef_notice(web,{'reason':'元画像と宣言サイズが一致せず、拡縮・切り抜きの可能性','actual_size':list(actual),'expected_size':list(expected)});continue
            point,details=image_pin_point(response.content,(float(match[1]),float(match[2])),zoom,scale)
            details.update(image_url=url,image_sha256=hashlib.sha256(response.content).hexdigest(),projection='Google Static Maps Web Mercator')
            trace(web,'image_pin_result',details,'INFO' if point else 'WARNING','image_location')
            if point:
                if not hasattr(web,'image_point_sources'):web.image_point_sources={}
                web.image_point_sources[point]=url;results.append((*point,'掲載地図画像の物件ピン認識',5))
        except Exception as exc:trace(web,'image_pin_error',{'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '画像を解析できない'},'WARNING','image_location')
    return results


def map_links(soup,source_url):
    links=[];origin=urlparse(source_url).hostname
    for tag in soup.select('a[href],iframe[src],[data-url],[data-href],[onclick],form[action]'):
        label=' '.join([tag.get_text(' ',strip=True),str(tag.get('title') or ''),str(tag.get('id') or ''),str(tag.get('class') or ''),str(tag.get('onclick') or ''),str(tag.get('alt') or ''),str(tag.get('aria-label') or '')]+[str(img.get('alt') or '') for img in tag.select('img')])
        targets=[str(tag.get(attr) or '') for attr in ('href','src','data-url','data-href','action')]
        targets.extend(re.findall(r"(?:window\.open|location(?:\.href)?\s*=)\s*\(?\s*['\"]([^'\"]+)['\"]",str(tag.get('onclick') or '')))
        for raw in targets:
            target=urljoin(source_url,html.unescape(raw));u=urlparse(target)
            if not raw or u.scheme!='https' or u.hostname!=origin or u.username or u.password:continue
            if re.search(r'地図|周辺環境|(?:gmap|map|kankyo|FR301FD003)',label+' '+u.path+' '+u.query,re.I) and target!=source_url and target not in links:links.append(target)
    return links


def locate(web,soup,text,address,bounds,munis,hint=None,source_url=None):
    candidates=coordinate(soup,text,hint);source_by_point={p[:2]:source_url for p in candidates}
    if not any(p[3]>=5 for p in candidates):candidates.extend(map_image_candidates(web,soup,source_url))
    # A map-center address lookup must never suppress following the actual property-map link.
    if source_url:
        for target in map_links(soup,source_url):
            try:
                if not web.permitted(target):continue
                reply=web.fetch(target);other=BeautifulSoup(reply.text,'html.parser')
                extra=coordinate(other,reply.text)
                if not any(p[3]>=5 for p in extra):extra.extend(map_image_candidates(web,other,target))
                for p in extra:source_by_point[p[:2]]=target
                candidates.extend(extra)
                trace(web,'map_link_result',{'source_url':source_url,'map_url':target,'candidates':extra},stage='location')
            except AppError as exc:trace(web,'location',{'source_url':source_url,'map_url':target,'message':str(exc)},'WARNING','location')
    candidates=[p for p in candidates if p[3]>=4]
    if any(not in_rectangle(p[:2],bounds) for p in candidates):trace(web,'published_point_outside_bounds',{'points':[list(p[:2]) for p in candidates if not in_rectangle(p[:2],bounds)],'retained':True},stage='location')
    trace(web,'location_candidates',{'address':address,'bounds':bounds,'in_bounds_candidates':candidates},stage='location')
    location_precision='listing_map'
    if candidates:
        lat,lng,method,priority=max(candidates,key=lambda p:p[3])
        distinct={(round(p[0],6),round(p[1],6)) for p in candidates if p[3]==priority}
        if len(distinct)>1:
            trace(web,'map_multiple_candidates',{'method':method,'same_priority_candidates':list(distinct),'retained_as_unresolved':True},'WARNING','location');return None
    elif re.search(r'\d+(?:丁目|番|号)|\d+-\d+',normal(address)):
        data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json();matches=[]
        for item in data if isinstance(data,list) else []:
            try:
                lng,lat=map(float,item['geometry']['coordinates']);title=normal(item.get('properties',{}).get('title',''))
                if in_rectangle((lat,lng),bounds) and title and address_key(title)==address_key(address):matches.append((lat,lng))
            except (KeyError,ValueError,TypeError):continue
        if not matches:return None
        lat,lng=matches[0];method='住所検索（地図座標を取得できず）';location_precision='address' if re.search(r'\d+[-番]\d+',normal(address)) else 'town'
    else:return None
    try:region=reverse(web,(lat,lng),munis)
    except Exception as exc:
        region=None;trace(web,'reverse_address_pending',{'point':[lat,lng],'exception_type':type(exc).__name__,'retained':True},'WARNING','location')
    inferred=region['label'] if region else '';a,b=address_key(address),address_key(inferred)
    match='逆算住所未確認' if not b else '掲載住所未確認' if not a else '町字一致・番地未検証' if b in a or a==b else '町字不一致・掲載座標を保持'
    # Preserve the published map point and both addresses; inverse geocoding isn't a deletion gate.
    if match=='町字不一致・掲載座標を保持':trace(web,'map_address_difference',{'listing_address':address,'inferred_address':inferred,'point':[lat,lng],'retained':True},'WARNING','location')
    location={'latitude':lat,'longitude':lng,'location_method':method,'map_address':inferred,'inferred_address':inferred,
        'address_match':match,'coordinate_precision':location_precision,'position_source_url':getattr(web,'image_point_sources',{}).get((lat,lng)) or source_by_point.get((lat,lng),source_url or ''),
        'address_precision':'町字（GSI lv01）・番地未確認' if inferred else '未確認'}
    trace(web,'location_ok',{'listing_address':address,'inferred_address':inferred,'match':match,'location':location},stage='location')
    return location


def create_unit(provider,url,title,address,layout,rent,fees,area,floor,age,built_ym,location):
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載物件')[:200],address=str(address or '')[:200],
             layout=normal(layout),rent=rent,fees=fees,area=area,floor=str(floor or '')[:80],structure='SRC',age=age,
             built_ym=built_ym,provider=provider,listing_url=url,fetched_at=utc_now(),**location)
    if not valid_unit(row):
        trace(None,'parse',{'reason':'constructed_unit_validation_failed','observed_fields':row},'WARNING','validation');return None
    return row


def homes_area_groups(regions,munis):
    grouped={}
    for region in regions:
        code=str(region.get('code',''));town=normal(region.get('town',''));base=town_base_name(town)
        if not code or not base:continue
        key=(code,address_key(base));row=grouped.get(key)
        municipality=munis.get(code) if isinstance(munis,dict) else None
        city=str(municipality[2]) if isinstance(municipality,(list,tuple)) and len(municipality)>=3 else provider_city_name(region,munis)
        if row is None:
            row=dict(region);row.update(town=base,homes_town=base,city_name=city,visible_towns=[],owner_label=region.get('label',''))
            grouped[key]=row
        if town and town not in row['visible_towns']:row['visible_towns'].append(town)
    return list(grouped.values())


def _named_homes_link(soup,base_url,label,path_hint=''):
    wanted=address_key(label);candidates=[]
    for a in soup.select('a[href]'):
        shown=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',normal(a.get_text(' ',strip=True)))
        if address_key(shown)!=wanted:continue
        target=urljoin(base_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname!='www.homes.co.jp' or not u.path.startswith('/chintai/'):continue
        if path_hint and path_hint not in u.path:continue
        candidates.append(target)
    if not candidates:return None
    return sorted(candidates,key=lambda u:(u.endswith('/list/'),len(u)))[0]


def homes_prepare_region(web,region):
    """Visible-map city/town -> HOME'S city page -> 町域 -> grouped filters."""
    cache=getattr(web,'homes_region_cache',None)
    if cache is None:cache={};web.homes_region_cache=cache
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('HOMESの対象都県を確認できません。')
    key=(alias,str(region.get('code','')),address_key(region.get('homes_town') or region.get('town','')))
    if key in cache:return cache[key]
    city=normal(region.get('city_name') or provider_city_name(region,{}))
    city_url=homes_direct_city_list_url(region,'mansion')
    if city_url:
        trace(web,'homes_city_selected',{'city':city,'url':city_url,'source':'東京都23区の公開区別list URLへ直接移動（都トップページを経由しない）'},stage='search_conditions')
    else:
        root=f'https://www.homes.co.jp/chintai/mansion/{alias}/'
        if not web.permitted(root):raise AppError('HOMESの市区郡選択ページの自動取得が許可されていません。')
        root_reply=web.fetch(root);root_soup=BeautifulSoup(root_reply.text,'html.parser')
        city_url=_named_homes_link(root_soup,root,city)
        if not city_url:raise AppError('HOMESの市区郡一覧から'+city+'を確認できません。')
        trace(web,'homes_city_selected',{'city':city,'url':city_url,'source':'表示地図から作成した区名・町名リスト'},stage='search_conditions')
    if not web.permitted(city_url):raise AppError('HOMESの区別一覧ページの自動取得が許可されていません。')
    city_reply=web.fetch(city_url);city_soup=BeautifulSoup(city_reply.text,'html.parser')
    wanted=normal(region.get('homes_town') or town_base_name(region.get('town','')))
    town_url=_named_homes_link(city_soup,city_url,wanted,'-town')
    base_params={}
    if town_url:
        town_reply=web.fetch(town_url);base_url=town_url
        trace(web,'homes_town_selected',{'city':city,'town':wanted,'method':'HOMES町域リンク','url':town_url},stage='search_conditions')
    else:
        control=_form_control_by_label(city_soup,wanted)
        if not control:raise AppError('HOMESの「町域の指定」から'+wanted+'を確認できません。')
        name,value=control;base_params[name]=value;base_url=city_url;town_reply=web.fetch(base_url,params=base_params)
        trace(web,'homes_town_selected',{'city':city,'town':wanted,'method':'HOMES町域フォーム','parameter':{name:value}},stage='search_conditions')
    soup=BeautifulSoup(town_reply.text,'html.parser');page_text=normal(soup.get_text(' ',strip=True))
    layout_filters=discover_layout_inputs(soup,'HOME’S')
    for layout in HOMES_LAYOUTS:
        if layout not in layout_filters:
            try:
                params=single_layout_parameters(web,'HOME’S',layout)
                if len(params)==1:layout_filters[layout]=next(iter(params.items()))
            except AppError:pass
    missing=[x for x in HOMES_LAYOUTS if x not in layout_filters]
    if missing:raise AppError('HOMESの間取り選択値を確認できません：'+','.join(missing))
    age15=_form_control_by_label(soup,'15年以内') or _form_control_by_label(city_soup,'15年以内')
    if not age15 and '15年以内' in page_text:age15=('cond[houseageh]','15')
    if not age15:raise AppError('HOMESの「築年数：15年以内」の公開条件を確認できません。')
    mansion=None
    if '/chintai/mansion/' not in urlparse(base_url).path:
        mansion=_form_control_by_label(soup,'マンション') or _form_control_by_label(city_soup,'マンション')
        if not mansion:raise AppError('HOMESの「建物の種類：マンション」を町域ページで確認できません。')
    result={'url':base_url,'base_params':base_params,'layout_filters':layout_filters,'age15':age15,'mansion':mansion,'town':wanted,'city':city,
            'building_type':'マンション'}
    cache[key]=result
    trace(web,'homes_filters_ready',{'city':city,'town':wanted,'building_type':'マンション','age15':age15,'layout_filters':layout_filters},stage='search_conditions')
    return result


def homes_group_params(prepared,layouts):
    params=dict(prepared['base_params']);age_name,age_value=prepared['age15'];params[age_name]=age_value
    if prepared.get('mansion'):
        mansion_name,mansion_value=prepared['mansion'];params[mansion_name]=mansion_value
    grouped={}
    for layout in layouts:
        name,value=prepared['layout_filters'][layout];grouped.setdefault(name,[]).append(value)
    for name,values in grouped.items():params[name]=values if len(values)>1 else values[0]
    return params


def homes_prepare_house_region(web,region):
    """Visible-map city/town -> HOME'S detached-house (kodate) town search."""
    cache=getattr(web,'homes_house_region_cache',None)
    if cache is None:cache={};web.homes_house_region_cache=cache
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('HOMES一戸建ての対象都県を確認できません。')
    wanted=normal(region.get('homes_town') or town_base_name(region.get('town','')))
    key=(alias,str(region.get('code','')),address_key(wanted))
    if key in cache:return cache[key]
    city=normal(region.get('city_name') or provider_city_name(region,{}))
    city_url=homes_direct_city_list_url(region,'kodate')
    if city_url:
        trace(web,'homes_house_city_selected',{'city':city,'url':city_url,'source':'東京都23区の公開区別list URLへ直接移動（都トップページを経由しない）'},stage='search_conditions')
    else:
        root=f'https://www.homes.co.jp/chintai/kodate/{alias}/'
        if not web.permitted(root):raise AppError('HOMES一戸建ての市区郡選択ページの自動取得が許可されていません。')
        root_reply=web.fetch(root);root_soup=BeautifulSoup(root_reply.text,'html.parser')
        city_url=_named_homes_link(root_soup,root,city,'/kodate/')
        if not city_url:raise AppError('HOMES一戸建ての市区郡一覧から'+city+'を確認できません。')
    if not web.permitted(city_url):raise AppError('HOMES一戸建ての区別一覧ページの自動取得が許可されていません。')
    city_reply=web.fetch(city_url);city_soup=BeautifulSoup(city_reply.text,'html.parser')
    town_url=_named_homes_link(city_soup,city_url,wanted,'-town');base_params={}
    if town_url:
        town_reply=web.fetch(town_url);base_url=town_url
        trace(web,'homes_house_town_selected',{'city':city,'town':wanted,'method':'HOMES一戸建て町域リンク','url':town_url},stage='search_conditions')
    else:
        control=_form_control_by_label(city_soup,wanted)
        if not control:raise AppError('HOMES一戸建ての「町域の指定」から'+wanted+'を確認できません。')
        name,value=control;base_params[name]=value;base_url=city_url;town_reply=web.fetch(base_url,params=base_params)
        trace(web,'homes_house_town_selected',{'city':city,'town':wanted,'method':'HOMES一戸建て町域フォーム','parameter':{name:value}},stage='search_conditions')
    soup=BeautifulSoup(town_reply.text,'html.parser');page_text=normal(soup.get_text(' ',strip=True))
    age40=_form_control_by_label(soup,'40年以内') or _form_control_by_label(city_soup,'40年以内')
    if not age40 and '40年以内' in page_text:age40=('cond[houseageh]','40')
    if not age40:age40=('cond[houseageh]','40')  # current public HOME'S control, verified 2026-10-09
    area50=_form_control_by_label(soup,'50㎡以上') or _form_control_by_label(soup,'50m²以上') or _form_control_by_label(soup,'50m2以上')
    if not area50:area50=('cond[housearea]','50')  # current public HOME'S control, verified 2026-10-09
    result={'url':base_url,'base_params':base_params,'age40':age40,'area50':area50,'town':wanted,'city':city,'building_type':'一戸建て'}
    cache[key]=result
    trace(web,'homes_house_filters_ready',{'city':city,'town':wanted,'building_type':'一戸建て','age40':age40,'area50':area50},stage='search_conditions')
    return result


def homes_house_params(prepared):
    params=dict(prepared['base_params'])
    age_name,age_value=prepared['age40'];params[age_name]=age_value
    area_name,area_value=prepared['area50'];params[area_name]=area_value
    return params


def homes_map_url(soup,detail_url):
    """Return a HOME'S-owned map destination when present.

    HOME'S often embeds Google Maps directly in the detail page.  External Google
    URLs are therefore evidence carried by the HOME'S page, not a page we need to
    fetch.  Returning detail_url is intentional when the map is embedded in place.
    """
    embedded=False
    for tag in soup.select('a[href],iframe[src],img[src],script'):
        raw=' '.join(str(tag.get(attr) or '') for attr in ('href','src'))
        label=' '.join([normal(tag.get_text(' ',strip=True)),normal(tag.get('title')),normal(tag.get('aria-label')),raw])
        if re.search(r'地図を見る|google\.com/maps|maps\.google|[?&](?:ll|center)=|@3[4-7]\.\d+,1(?:3[89]|40)\.\d+',label,re.I):embedded=True
        if tag.name=='a' and tag.get('href'):
            target=urljoin(detail_url,tag.get('href','')).split('#')[0];u=urlparse(target)
            if u.hostname=='www.homes.co.jp' and target!=detail_url and re.search(r'地図|map',label+' '+u.path,re.I):return target
    links=map_links(soup,detail_url)
    for target in links:
        target=target.split('#')[0]
        if urlparse(target).hostname=='www.homes.co.jp' and target!=detail_url:return target
    return detail_url if embedded else None


def _homes_map_documents(web,detail_soup,detail_url):
    """Collect provider-owned documents that contain the listing's map evidence."""
    docs=[(detail_url,detail_soup)]
    target=homes_map_url(detail_soup,detail_url)
    if target and target!=detail_url and urlparse(target).hostname=='www.homes.co.jp':
        if not web.permitted(target):raise AppError('HOMESの地図ページの自動取得が許可されていません。')
        reply=web.fetch(target);docs.append((target,BeautifulSoup(reply.text,'html.parser')))
    if not target:
        # Some current HOME'S pages expose the map iframe without a separate HOME'S
        # map URL.  The detail document itself is still the provider-published map page.
        target=detail_url
    return target,docs


def _homes_identity_from_detail(soup,raw_layout='',raw_age='',raw_area=''):
    """Non-address identity fields used only when map-coordinate address lookup misses."""
    candidates=[]
    for selector in ('h1','.mod-bukkenName','.prg-bukkenName','.property-name','meta[property="og:title"]'):
        tag=soup.select_one(selector)
        if not tag:continue
        value=normal(tag.get('content') if tag.name=='meta' else tag.get_text(' ',strip=True))
        if value:candidates.append(value)
    generic=('賃貸マンション','賃貸一戸建て','賃貸戸建て','賃貸物件','マンション','一戸建て','戸建て','貸家','HOME’S','LIFULL HOME’S')
    building_name=''
    for value in candidates:
        cleaned=re.sub(r'\s*[-｜|].*$','',value).strip()
        if len(cleaned)>=3 and not any(g==cleaned for g in generic) and not re.search(r'徒歩\s*\d+分|賃貸マンション$',cleaned):
            building_name=cleaned;break
    return {'building_name':building_name,'raw_layout':raw_layout,'raw_age':raw_age,'raw_area':raw_area}


def homes_location_from_map_image(web,detail_soup,detail_url,bounds,munis,expected_towns=None,expected_code=None,identity=None):
    """HOME'S detail -> 地図を見る -> map image/Google center -> detailed address.

    The listing/list textual address is deliberately absent from this function.
    """
    identity=dict(identity or {})
    target,docs=_homes_map_documents(web,detail_soup,detail_url)
    expected_towns=tuple(normal(x) for x in (expected_towns or ()) if normal(x))
    all_candidates=[]
    for map_url,soup in docs:
        candidates=map_image_candidates(web,soup,map_url)
        all_candidates.extend((p,map_url) for p in candidates if p[3]>=5)
    # 1) Image-recognized provider map pin, as requested.  Require one top-priority point.
    lat=lng=None;source=target;position_kind='';method=''
    if all_candidates:
        priority=max(p[0][3] for p in all_candidates);best=[item for item in all_candidates if item[0][3]==priority]
        distinct={(round(item[0][0],6),round(item[0][1],6)) for item in best}
        trace(web,'homes_map_image_candidates',{'detail_url':detail_url,'map_url':target,'usable_candidates':[x[0] for x in all_candidates],'top_priority':priority,'distinct_top':list(distinct),'bounds':list(bounds)},stage='image_location')
        if len(distinct)==1:
            lat,lng=next(iter(distinct));source=next(u for p,u in best if (round(p[0],6),round(p[1],6))==(lat,lng));position_kind='image_pin';method='HOMES「地図を見る」の地図画像ピン認識→詳細住所推定'
        else:
            trace(web,'map_multiple_candidates',{'source':'HOMES地図画像','same_priority_candidates':list(distinct),'decision':'continue_to_published_map_center'},'WARNING','image_location')
    # 2) Current HOME'S pages may embed Google Maps and expose ll=/center=/@ coordinates.
    # These coordinates are read from the HOME'S-published map document, never from text address.
    if lat is None:
        center_candidates=[]
        for map_url,soup in docs:
            point,meta=_suumo_map_center_points(soup,map_url)  # generic Google-map coordinate parser
            if point:center_candidates.append((point,map_url,meta))
        unique={x[0] for x in center_candidates}
        if len(unique)==1:
            (lat,lng),source,meta=center_candidates[0];position_kind='map_center';method='HOMES「地図を見る」の公開Google地図中心座標→詳細住所推定'
            trace(web,'homes_map_center_fallback',{'detail_url':detail_url,'map_url':source,'point':[lat,lng],'meta':meta},stage='location')
        elif len(unique)>1:
            counts={}
            for point,map_url,meta in center_candidates:counts[point]=counts.get(point,0)+1
            ranked=sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))
            if len(ranked)==1 or ranked[0][1]>ranked[1][1]:
                (lat,lng),_=ranked[0];source=next(u for p,u,m in center_candidates if p==(lat,lng));position_kind='map_center';method='HOMES「地図を見る」の公開Google地図中心座標→詳細住所推定'
    if lat is None:
        trace(web,'homes_map_position_unresolved',{'detail_url':detail_url,'map_url':target,'reason':'地図画像ピン・公開Google地図中心座標のいずれも取得できない'},'WARNING','location')
        return None
    if not in_rectangle((lat,lng),bounds):
        trace(web,'published_point_outside_bounds',{'map_url':source,'point':[lat,lng],'bounds':list(bounds),'retained':True,'reason':'対象町域の確認を優先し、初期表示範囲外でも保存判定を継続'},stage='location')

    # An image pin can use the strict town validator.  HOME'S map-center positions can
    # be generalized, so use municipality first and let independent identity resolve a
    # detailed address when the JHJ point is not defensible.
    # The selected HOME'S 町域 is an allowed search-scope validator (not an address
    # input).  Require the inferred detailed address to stay in that selected town.
    strict_towns=expected_towns
    max_distance=90 if position_kind=='image_pin' else 180
    inferred=map_point_to_residential_address(web,(lat,lng),munis,max_distance_m=max_distance,expected_code=expected_code,expected_towns=strict_towns)
    external=None
    if not inferred:
        external=_cross_source_address_from_identity(web,identity,(lat,lng),munis,expected_code)
        inferred=external
    if not inferred or not _detailed_address(inferred.get('address')):
        trace(web,'homes_detailed_address_unresolved',{'detail_url':detail_url,'map_url':source,'point':[lat,lng],'position_kind':position_kind,'reason':'物件地図から詳細住所を推定できないため保存しない'},'WARNING','location')
        return None
    address=inferred['address'];external_used=bool(external)
    if expected_towns and not external_used and not any(town_matches(t,address) for t in expected_towns):
        trace(web,'map_address_difference',{'source':'HOMES物件地図','map_derived_address':address,'expected_towns':list(expected_towns),'point':[lat,lng],'retained':False},'WARNING','location');return None
    ref=inferred.get('reference_point') or [lat,lng]
    return {'latitude':float(ref[0]) if external_used else lat,'longitude':float(ref[1]) if external_used else lng,
            'location_method':('HOMES物件固有地図→建物同一性を独立ソース照合→GSI住所確認' if external_used else method),
            'map_address':address,'inferred_address':address,
            'address_match':('独立ソースで建物同一性を照合しGSIで番地確認' if external_used else '物件地図座標から番地・住居番号相当まで推定'),
            'coordinate_precision':'building' if external_used else 'listing_map',
            'position_source_url':source,
            'address_precision':('独立複数ソース照合＋GSI住所検索' if external_used else 'GSI住居表示・街区符号/基礎番号'),
            'address_distance_m':round(float(inferred.get('distance_m') or 0),2),
            'homes_map_hint_latitude':lat,'homes_map_hint_longitude':lng,
            'homes_map_position_kind':position_kind}


def homes_detail(web,url,region,bounds,munis,layouts):
    begin_listing(web,'HOME’S',url)
    text=web.fetch(url).text;soup=BeautifulSoup(text,'html.parser')
    # Do not read the detail/list textual address.  Location/address comes from the
    # provider-published map only (plus independent corroboration after map lookup).
    raw_layout=labeled(soup,('間取り','間取'))
    if not raw_layout:
        m=re.search(r'間取り\s*((?:\d+)(?:LDK|DK|K))',normal(soup.get_text(' ',strip=True)),re.I);raw_layout=m.group(1) if m else ''
    layout=parsed_layout(raw_layout)
    if layout not in layouts:return reject_listing(web,'HOME’S',url,'detail.layout','condition' if layout else 'missing_data','間取りが検索条件外、または読み取れない',{'raw':raw_layout,'parsed':layout},list(layouts))
    page_text=normal(soup.get_text(' ',strip=True));kind=labeled(soup,('建物種別','建物の種類','種別'))
    heading=normal(soup.select_one('h1').get_text(' ',strip=True)) if soup.select_one('h1') else ''
    if 'マンション' not in normal(kind+' '+heading+' '+page_text[:600]):return reject_listing(web,'HOME’S',url,'detail.building_type','condition' if kind else 'missing_data','マンションであることを確認できない',{'raw':kind,'heading':heading,'page_excerpt':page_text[:600]},'マンション')
    raw_age=labeled(soup,('築年数','築年月','築年'));age,_=age_info(raw_age)
    if age is not None and age>15:return reject_listing(web,'HOME’S',url,'detail.age','condition','築年数が15年を超える',{'raw':raw_age,'years':age},'15年以内')
    rent=optional_amount(labeled(soup,('賃料','家賃')))
    if not rent:return reject_listing(web,'HOME’S',url,'detail.rent','missing_data','家賃を正の数値として読み取れない',{'raw':labeled(soup,('賃料','家賃')),'parsed':rent},'家賃（円）>0')
    raw_area=labeled(soup,('専有面積','面積'))
    identity=_homes_identity_from_detail(soup,raw_layout,raw_age,raw_area)
    expected_towns=region.get('visible_towns') or [region.get('town','')]
    try:location=homes_location_from_map_image(web,soup,url,bounds,munis,expected_towns,region.get('code'),identity)
    except AppError as exc:
        trace(web,'location_pending',{'url':url,'message':str(exc),'source':'HOMES地図を見る'},'WARNING','location');location=None
    if not location or not location.get('inferred_address'):
        trace(web,'location_pending',{'url':url,'reason':'物件地図から詳細住所を推定できないため保存しない','source':'HOMES地図を見る'},'WARNING','location');return reject_listing(web,'HOME’S',url,'map.address_inference','location_failure','物件地図から詳細住所を推定できないため保存しない',location,'番地までの推定住所')
    address=location['inferred_address']
    row=partial_listing('HOME’S',url,'HOMES掲載募集',address,layout,rent,None,None,region,bounds,location,None,age,None,'')
    if not row:return reject_listing(web,'HOME’S',url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・詳細住所・物件URL')
    return row

def homes_collect(web,region,bounds,munis,emit):
    """HOMES: ward/town -> grouped filters -> each detail -> provider map -> detailed address."""
    prepared=homes_prepare_region(web,region);seen_urls=set();visible_towns=region.get('visible_towns') or [region.get('town','')]
    scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    for group_number,layouts in enumerate(HOMES_LAYOUT_GROUPS,1):
        emit('message',f'HOMES条件{group_number}｜マンション｜築15年以内｜'+ '・'.join(layouts))
        params=homes_group_params(prepared,layouts);current_url=prepared['url'];current_params=params;seen_pages=set();found=0
        while current_url:
            reply=web.fetch(current_url,params=current_params or None);current_params=None;soup=BeautifulSoup(reply.text,'html.parser')
            signature=hashlib.sha256(reply.content).hexdigest()
            if signature in seen_pages:raise AppError('HOMESのページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature);detail_urls=[]
            for a in soup.select('a[href]'):
                label=normal(a.get_text(' ',strip=True));url=urljoin(reply.url,a.get('href','')).split('#')[0]
                path=urlparse(url).path
                is_detail=bool(re.search(r'^/chintai/(?:b-[^/]+|room/[^/]+)/?$',path))
                explicit=('詳細を見る' in label or '物件詳細' in label or 'prg-detailLink' in str(a.get('class') or '') or 'prg-bukkenNameAnchor' in str(a.get('class') or ''))
                if safe_url(url,'HOME’S') and is_detail and (explicit or label):
                    if url not in seen_urls:seen_urls.add(url);detail_urls.append(url)
            emit('candidate',len(detail_urls));found+=len(detail_urls)

            def detail_work(item):
                while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
                try:
                    index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                    emit('message',f'HOMES条件{group_number} 詳細ページ {index}/{len(detail_urls)}件');emit('detail',1)
                    try:
                        begin_listing(web,'HOME’S',url)
                        if not web.permitted(url):raise AppError('HOMES詳細ページの自動取得が許可されていません。')
                        row=homes_detail(web,url,region,bounds,munis,layouts)
                        if row:
                            if visible_towns and row.get('address') and not any(town_matches(t,row['address']) for t in visible_towns):
                                reject_listing(web,'HOME’S',url,'address.town_match','condition','推定住所が対象町域と一致しない',row['address'],list(visible_towns));emit('rejected',1);return 0
                            emit('unit',row);return 1
                        emit('rejected',1);return 0
                    except (AppError,ValueError,TypeError) as exc:
                        if is_homes_http403_error(exc):raise
                        trace(web,'listing_failed',{'provider':'HOME’S','url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection')
                        emit('rejected',1);emit('issue','HOMES詳細確認｜'+str(exc));trace(web,'detail_optional_error',{'url':url,'message':str(exc)},'WARNING','collector');return 0
                finally:web.detail_slots.release()

            for item,_,error in bounded_results(list(enumerate(detail_urls,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
                if error:
                    if is_homes_http403_error(error):raise error
                    url=item[1] if isinstance(item,tuple) and len(item)>1 else ''
                    trace(web,'listing_failed',{'provider':'HOME\u2019S','url':url,'decision':'failed','failed_stage':'detail.worker',
                        'category':'acquisition_failure','reason':'parallel detail worker exception','exception_type':type(error).__name__,
                        'observed':{},'expected':'listing detail processing completes','evidence':[]},'ERROR','rejection')
                    emit('rejected',1);emit('issue','HOMES detail worker | '+type(error).__name__)
            next_url=generic_next_page(soup,reply.url,'HOME’S')
            current_url=next_url
        if not found:trace(web,'empty',{'provider':'HOME’S','region':region.get('label'),'group':list(layouts),'reason':'条件一覧に候補なし'},stage='collector')
    homes_house_collect(web,region,bounds,munis,emit)


def homes_house_detail(web,url,region,bounds,munis):
    """HOME'S detached house: detail facts + provider map; never use textual address."""
    begin_listing(web,'HOME’S',url)
    text=web.fetch(url).text;soup=BeautifulSoup(text,'html.parser');page_text=normal(soup.get_text(' ',strip=True))
    raw_layout=labeled(soup,('間取り','間取'))
    if not raw_layout:
        m=re.search(r'間取り\s*((?:\d+)(?:S?LDK|S?DK|SK|LK|K|L|R))',page_text,re.I);raw_layout=m.group(1) if m else ''
    layout=parsed_layout(raw_layout,rough=False)
    if not layout:return reject_listing(web,'HOME’S',url,'detail.layout','missing_data','一戸建ての間取りを読み取れない',{'raw':raw_layout},'間取り')
    kind=labeled(soup,('建物種別','建物の種類','種別'));heading=normal(soup.select_one('h1').get_text(' ',strip=True)) if soup.select_one('h1') else ''
    type_text=normal(kind+' '+heading+' '+page_text[:800])
    if kind and re.search(r'マンション|アパート',normal(kind)) and not re.search(r'一戸建(?:て)?|戸建(?:て)?|貸家',type_text):
        return reject_listing(web,'HOME’S',url,'detail.building_type','condition','建物種別が一戸建てと一致しない',{'raw':kind,'heading':heading},'一戸建て')
    raw_age=labeled(soup,('築年数','築年月','築年'));age,_=age_info(raw_age)
    if age is None or age>HOUSE_MAX_AGE:return reject_listing(web,'HOME’S',url,'detail.age','missing_data' if age is None else 'condition','築年数が40年以内と確認できない',{'raw':raw_age,'years':age},'40年以内')
    raw_area=labeled(soup,('建物面積','専有面積','面積'))
    if not raw_area:raw_area=regex_after_label(page_text,('建物面積','専有面積','面積'),r'\d+(?:\.\d+)?\s*(?:m2|m²|㎡|平米)')
    area=_identity_area(raw_area)
    if area is None or area<HOUSE_MIN_AREA:return reject_listing(web,'HOME’S',url,'detail.area','missing_data' if area is None else 'condition','面積が50㎡以上と確認できない',{'raw':raw_area,'sqm':area},'50㎡以上')
    raw_rent=labeled(soup,('賃料','家賃'));rent=optional_amount(raw_rent)
    if not rent:return reject_listing(web,'HOME’S',url,'detail.rent','missing_data','家賃を正の数値として読み取れない',{'raw':raw_rent,'parsed':rent},'家賃（円）>0')
    identity=_homes_identity_from_detail(soup,raw_layout,raw_age,raw_area);expected_towns=region.get('visible_towns') or [region.get('town','')]
    try:location=homes_location_from_map_image(web,soup,url,bounds,munis,expected_towns,region.get('code'),identity)
    except AppError as exc:
        trace(web,'location_pending',{'url':url,'message':str(exc),'source':'HOMES一戸建て地図を見る'},'WARNING','location');location=None
    if not location or not location.get('inferred_address'):
        return reject_listing(web,'HOME’S',url,'map.address_inference','location_failure','一戸建ての物件地図から詳細住所を推定できないため保存しない',location,'番地までの推定住所')
    address=location['inferred_address']
    row=partial_listing('HOME’S',url,'HOMES一戸建て掲載募集',address,layout,rent,None,area,region,bounds,location,'一戸建て',age,None,'',dwelling_type='house')
    if not row:return reject_listing(web,'HOME’S',url,'listing.validation','invalid_data','一戸建ての保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'area':area,'address':address,'location':location},'家賃・間取り・50㎡以上・詳細住所')
    return row


def homes_house_collect(web,region,bounds,munis,emit):
    """HOME'S: same ward/town flow, detached house, <=40y, >=50 sqm."""
    prepared=homes_prepare_house_region(web,region);params=homes_house_params(prepared);seen_urls=set();seen_pages=set();found=0
    visible_towns=region.get('visible_towns') or [region.get('town','')];scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    current_url=prepared['url'];current_params=params
    emit('message','HOMES条件3｜一戸建て｜築40年以内｜50㎡以上')
    while current_url:
        reply=web.fetch(current_url,params=current_params or None);current_params=None;soup=BeautifulSoup(reply.text,'html.parser')
        signature=hashlib.sha256(reply.content).hexdigest()
        if signature in seen_pages:raise AppError('HOMES一戸建てのページ送りが同じ一覧を返しました。全ページを確認できていません。')
        seen_pages.add(signature);detail_urls=[]
        for a in soup.select('a[href]'):
            label=normal(a.get_text(' ',strip=True));url=urljoin(reply.url,a.get('href','')).split('#')[0];path=urlparse(url).path
            is_detail=bool(re.search(r'^/chintai/(?:b-[^/]+|room/[^/]+)/?$',path))
            explicit=('詳細を見る' in label or '物件詳細' in label or 'prg-detailLink' in str(a.get('class') or '') or 'prg-bukkenNameAnchor' in str(a.get('class') or ''))
            if safe_url(url,'HOME’S') and is_detail and (explicit or label) and url not in seen_urls:
                seen_urls.add(url);detail_urls.append(url)
        emit('candidate',len(detail_urls));found+=len(detail_urls)
        def detail_work(item):
            while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
            try:
                index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                emit('message',f'HOMES一戸建て 詳細ページ {index}/{len(detail_urls)}件');emit('detail',1)
                try:
                    begin_listing(web,'HOME’S',url)
                    if not web.permitted(url):raise AppError('HOMES一戸建て詳細ページの自動取得が許可されていません。')
                    row=homes_house_detail(web,url,region,bounds,munis)
                    if row:
                        if visible_towns and row.get('address') and not any(town_matches(t,row['address']) for t in visible_towns):
                            reject_listing(web,'HOME’S',url,'address.town_match','condition','一戸建ての推定住所が対象町域と一致しない',row['address'],list(visible_towns));emit('rejected',1);return 0
                        emit('unit',row);return 1
                    emit('rejected',1);return 0
                except (AppError,ValueError,TypeError) as exc:
                    if is_homes_http403_error(exc):raise
                    trace(web,'listing_failed',{'provider':'HOME’S','url':url,'decision':'failed','failed_stage':'house.detail','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__},'ERROR','rejection')
                    emit('rejected',1);emit('issue','HOMES一戸建て詳細確認｜'+str(exc));return 0
            finally:web.detail_slots.release()
        for item,_,error in bounded_results(list(enumerate(detail_urls,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
            if error:
                if is_homes_http403_error(error):raise error
                url=item[1] if isinstance(item,tuple) and len(item)>1 else ''
                trace(web,'listing_failed',{'provider':'HOME’S','url':url,'decision':'failed','failed_stage':'house.detail.worker','category':'acquisition_failure','reason':'parallel detail worker exception','exception_type':type(error).__name__},'ERROR','rejection')
                emit('rejected',1);emit('issue','HOMES一戸建て detail worker | '+type(error).__name__)
        current_url=generic_next_page(soup,reply.url,'HOME’S')
    if not found:trace(web,'empty',{'provider':'HOME’S','region':region.get('label'),'group':'一戸建て','reason':'築40年以内・50㎡以上の候補なし'},stage='collector')


def _field_texts(soup,field):
    """Collect visible labels even when SUUMO places checkbox text outside <label>."""
    texts=[]
    if field.get('id'):
        label=soup.find('label',attrs={'for':field['id']})
        if label:texts.append(label.get_text(' ',strip=True))
    label=field.find_parent('label')
    if label:texts.append(label.get_text(' ',strip=True))
    if field.name=='option':texts.append(field.get_text(' ',strip=True))
    texts.extend([field.get('aria-label',''),field.get('data-label',''),field.get('title','')])
    # SUUMO's area modal may render the input and visible town name as siblings.
    parent=field.parent
    for _ in range(4):
        if parent is None:break
        if getattr(parent,'name',None) in ('li','td','th','div','p','span','dd','dt'):
            text=parent.get_text(' ',strip=True)
            if text and len(text)<=160:texts.append(text)
        parent=getattr(parent,'parent',None)
    for sibling in (field.previous_sibling,field.next_sibling):
        if sibling is not None:
            try:text=sibling.get_text(' ',strip=True)
            except AttributeError:text=str(sibling)
            if normal(text):texts.append(text)
    return list(dict.fromkeys(normal(x) for x in texts if normal(x)))

def _form_control_by_label(soup,wanted,names=None):
    """Return the public form name/value whose own label equals the requested visible option."""
    wanted=normal(wanted).replace(' ','')
    for field in soup.select('input[name][value],select[name] option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '')
        if not name or names and name not in names:continue
        for label in _field_texts(soup,field):
            cleaned=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',label).replace(' ','')
            if cleaned==wanted:
                return name,str(field.get('value',''))
    return None


def suumo_area_groups(regions,munis):
    """Collapse GSI chome points into SUUMO's municipality + town selector level."""
    grouped={}
    for region in regions:
        code=str(region.get('code',''));town=normal(region.get('town',''));base=town_base_name(town)
        if not code or not base:continue
        key=(code,address_key(base));row=grouped.get(key)
        municipality=munis.get(code) if isinstance(munis,dict) else None
        city=str(municipality[2]) if isinstance(municipality,(list,tuple)) and len(municipality)>=3 else provider_city_name(region,munis)
        if row is None:
            row=dict(region)
            row.update(town=base,suumo_town=base,city_name=city,visible_towns=[],owner_label=region.get('label',''))
            grouped[key]=row
        if town and town not in row['visible_towns']:row['visible_towns'].append(town)
    return list(grouped.values())


def _suumo_town_selector(soup,raw_html,wanted,base_url):
    """Resolve SUUMO town selection from links, controls, hidden modal DOM or embedded JSON."""
    wanted=normal(wanted);wanted_key=address_key(wanted)
    for a in soup.select('a[href]'):
        target=urljoin(base_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname!='suumo.jp' or not re.search(r'/chintai/[^/]+/sc_[^/]+/oz_\d+/?$',u.path):continue
        label=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',normal(a.get_text(' ',strip=True)))
        if address_key(label)==wanted_key:return {'url':target,'method':'SUUMO町名URL'}
    # Accept only explicitly labelled town controls. Listing IDs and nearby
    # unrelated town links must never become a town search condition.
    control=_form_control_by_label(soup,wanted,names=('oz',))
    if control and re.fullmatch(r'\d{8}',str(control[1])):
        return {'control':control,'method':'SUUMO町名フォーム'}

    return None


def _suumo_canonical_city_url(soup,response_url,raw_html=''):
    """Resolve the municipality SEO page even when SUUMO hides area controls in script/modal HTML."""
    response_clean=response_url.split('#')[0];u0=urlparse(response_clean)
    if u0.scheme=='https' and u0.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u0.path):
        return u0._replace(query='',fragment='').geturl()
    candidates=[]
    canonical=soup.select_one('link[rel="canonical"][href]')
    if canonical:candidates.append(canonical.get('href'))
    og=soup.select_one('meta[property="og:url"][content]')
    if og:candidates.append(og.get('content'))
    for raw in candidates:
        target=urljoin(response_url,raw or '').split('#')[0];u=urlparse(target)
        if u.scheme=='https' and u.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return u._replace(query='',fragment='').geturl()
    for a in soup.select('a[href]'):
        target=urljoin(response_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.scheme=='https' and u.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return u._replace(query='',fragment='').geturl()
    decoded=html.unescape(raw_html or '').replace('\\/','/')
    for match in re.finditer(r'(?:https://suumo\.jp)?(/chintai/[^/"\']+/sc_[^/"\']+/)',decoded,re.I):
        target=urljoin('https://suumo.jp',match.group(1));u=urlparse(target)
        if u.hostname=='suumo.jp':return target
    return None


def _suumo_probe_town_url(web,canonical_url,municipality_code,city_name,wanted,max_suffix=120):
    """Fallback: resolve SUUMO's public oz_ town page without using municipality-wide results."""
    if not canonical_url:return None
    u=urlparse(canonical_url)
    if u.hostname!='suumo.jp' or not re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return None
    code=re.sub(r'\D','',str(municipality_code or ''))
    if not re.fullmatch(r'\d{5}',code):return None
    cache=getattr(web,'suumo_town_probe_cache',None)
    if cache is None:cache={};web.suumo_town_probe_cache=cache
    key=(canonical_url.rstrip('/')+'/',code)
    state=cache.setdefault(key,{'towns':{},'next_suffix':1,'finished':False})
    wanted_key=address_key(wanted)
    if wanted_key in state['towns']:return state['towns'][wanted_key]
    if state.get('finished'):return None
    base=canonical_url.rstrip('/')+'/'
    start=max(1,int(state.get('next_suffix',1)))
    for suffix in range(start,max_suffix+1):
        state['next_suffix']=suffix+1
        target=urljoin(base,f'oz_{code}{suffix:03d}/')
        try:
            if not web.permitted(target):continue
            reply=web.fetch(target);soup=BeautifulSoup(reply.text,'html.parser')
        except AppError as exc:
            trace(web,'suumo_town_probe',{'url':target,'suffix':suffix,'status':'unavailable','message':str(exc)},'WARNING','search_conditions')
            continue
        heading=normal(soup.select_one('h1').get_text(' ',strip=True) if soup.select_one('h1') else '')
        page_title=normal(soup.select_one('title').get_text(' ',strip=True) if soup.select_one('title') else '')
        source=heading or page_title
        town=''
        if city_name:
            match=re.search(re.escape(normal(city_name))+r'(.+?)の賃貸',source)
            if match:town=normal(match.group(1))
        if not town:
            match=re.search(r'^[^ ]+?区(.+?)の賃貸',source)
            if match:town=normal(match.group(1))
        if town:
            state['towns'][address_key(town)]=reply.url.split('#')[0]
            trace(web,'suumo_town_probe',{'url':reply.url,'suffix':suffix,'status':'found','town':town},stage='search_conditions')
            if address_key(town)==wanted_key:return reply.url.split('#')[0]
    state['finished']=True
    return state['towns'].get(wanted_key)


TOKYO_WARDS={str(13101+i):name for i,name in enumerate(('千代田区','中央区','港区','新宿区','文京区','台東区','墨田区','江東区','品川区','目黒区','大田区','世田谷区','渋谷区','中野区','杉並区','豊島区','北区','荒川区','板橋区','練馬区','足立区','葛飾区','江戸川区'))}


# v152: Category definitions are explicit and visible in the UI. The four
# directions + the three central wards partition all 23 wards; central-five
# overlaps the direction groups intentionally. Categories are quick-add only.
TOKYO_WARD_CATEGORIES = {
    '城東': ('13106','13107','13108','13118','13121','13122','13123'),
    '城西': ('13104','13113','13114','13115','13120'),
    '城南': ('13109','13110','13111','13112'),
    '城北': ('13105','13116','13117','13119'),
    '都心3区': ('13101','13102','13103'),
    '都心5区': ('13101','13102','13103','13104','13113'),
}


def automatic_ward_codes(settings):
    '''Return user-selected ward order, with v151's single ward compatible.'''
    if not isinstance(settings, dict):
        raise AppError('自動収集の対象区設定が不正です。')
    raw=settings.get('ward_codes')
    if raw is None:
        raw=[settings.get('ward_code')]
    if isinstance(raw,str):
        raw=[raw]
    if not isinstance(raw,(list,tuple)) or not raw:
        raise AppError('自動収集する区を一つ以上選択してください。')
    if any(not isinstance(code,str) or code not in TOKYO_WARDS for code in raw):
        raise AppError('自動収集する区は東京23区から選択してください。')
    # Duplicates are never re-scanned because of overlapping category choices.
    return list(dict.fromkeys(raw))


def automatic_ward_town_filters(settings):
    '''Normalize persisted per-ward filters without modifying old saved rows.'''
    wards=automatic_ward_codes(settings)
    raw=settings.get('town_codes_by_ward')
    if raw is None:
        raw={settings.get('ward_code'):settings.get('town_codes',[])}
    if not isinstance(raw,dict):
        raise AppError('区ごとの町名指定が不正です。')
    if any(code not in wards for code in raw):
        raise AppError('対象区以外の町名指定があります。')
    normalized={}
    for code in wards:
        values=raw.get(code,[])
        if not isinstance(values,list) or any(
            not isinstance(value,str) or not re.fullmatch(re.escape(code)+r'\d{3}',value)
            for value in values
        ):
            raise AppError('町名の選択が不正です：'+TOKYO_WARDS[code])
        normalized[code]=list(dict.fromkeys(values))
    return normalized


def automatic_add_area_categories():
    '''Streamlit on_change callback: append only *newly* selected categories.'''
    chosen=list(st.session_state.get('automatic_area_categories') or [])
    previous=set(st.session_state.get('automatic_applied_area_categories') or [])
    selected=list(st.session_state.get('automatic_wards') or [])
    for label in chosen:
        if label not in previous:
            for code in TOKYO_WARD_CATEGORIES.get(label,()):
                if code not in selected:
                    selected.append(code)
    st.session_state['automatic_wards']=selected
    st.session_state['automatic_applied_area_categories']=chosen

def acquisition_cutoff(now=None):
    now=now or datetime.now(timezone.utc);month_index=now.year*12+now.month-1-3
    year,month=divmod(month_index,12);month+=1
    return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1]))

def suumo_property_id(url):
    u=urlparse(url);bc=parse_qs(u.query).get('bc',[''])[0]
    if re.fullmatch(r'\d+',bc):return 'SUUMO:bc:'+bc
    match=re.search(r'/bc_(\d+)',u.path)
    if match:return 'SUUMO:bc:'+match[1]
    match=re.search(r'/jnc_(\d+)',u.path)
    return 'SUUMO:jnc:'+match[1] if match else None

def recent_acquisition(value,now=None):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:return False
        now=now or datetime.now(timezone.utc)
        return acquisition_cutoff(now)<=dt<=now
    except (ValueError,TypeError):return False

def suumo_ward_regions(web,code,include_municipalities=True):
    if code not in TOKYO_WARDS:raise AppError('東京23区から対象区を選択してください。')
    url='https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
    params={'ar':'030','bs':'040','ta':'13','sc':code,'pc':'50','srch_navi':'1'}
    if not web.permitted(url):raise AppError('SUUMOの区選択ページの自動取得が許可されていません。')
    reply=web.fetch(url,params=params);soup=BeautifulSoup(reply.text,'html.parser')
    documents=[soup];form=soup.find('form',id='js-machiSelectForm')
    if form and form.get('action'):
        target=urljoin(reply.url,form['action'])
        if urlparse(target).hostname!='suumo.jp' or not web.permitted(target):raise AppError('SUUMOの町名選択ページを確認できません。')
        fields=[(f['name'],f.get('value','')) for f in form.select('input[type="hidden"][name]') if f['name']!='oz']
        popup=web.fetch(target,params=fields);documents.append(BeautifulSoup(popup.text,'html.parser'))
    regions={}
    for document in documents:
        for field in document.select('input[name="oz"][value]'):
            oz=str(field['value'])
            if not re.fullmatch(re.escape(code)+r'\d{3}',oz):continue
            label=document.find('label',attrs={'for':field.get('id')})
            if label is None:label=field.find_parent('label')
            if label is None:continue
            link=label.find('a');name=normal((link or label).get_text(' ',strip=True))
            name=re.sub(r'\s*[（(][\d,]+(?:件)?[）)]\s*$','',name)
            if not name:continue
            prepared={'url':url,'base_params':{**params,'oz':oz},'town':name,'selection_method':'区の公開町名選択フォーム',
                      'mansion':('ts',SUUMO_BUILDING_TYPE_CODES['マンション']),'age15':('cn','15'),'layout_filters':{layout:('md',value) for layout,value in SUUMO_LAYOUT_CODES.items()}}
            regions[oz]={'code':code,'pref':'13','town':name,'suumo_town':name,'city_name':TOKYO_WARDS[code],
                         'label':'東京都'+TOKYO_WARDS[code]+name,'suumo_prepared':prepared}
    if not regions:raise AppError('選択した区のSUUMO町名リストを取得できません。')
    trace(web,'suumo_ward_towns',{'ward':TOKYO_WARDS[code],'municipality_code':code,'town_count':len(regions)},stage='search_conditions')
    return list(regions.values()),municipalities(web) if include_municipalities else {},0


def select_ward_towns(regions,town_codes):
    if not town_codes:return regions
    selected=set(town_codes)
    result=[r for r in regions if r.get('suumo_prepared',{}).get('base_params',{}).get('oz') in selected]
    found={r['suumo_prepared']['base_params']['oz'] for r in result}
    if selected-found:raise AppError('選択した町名をSUUMOの町名一覧で確認できません。町名を読み込み直してください。')
    return result

def ward_town_selector(ward_code,prefix,saved_codes=None,disabled=False):
    cache=st.session_state.setdefault('ward_town_cache',{})
    cached=cache.get(ward_code)
    if st.button('町名を読み込む・更新',key=prefix+'_load_towns',disabled=disabled):
        try:
            with st.spinner('SUUMOの町名一覧を読み込んでいます'):
                web=PublicWeb();web.configure(rental_network_settings())
                regions,munis,_=suumo_ward_regions(web,ward_code)
                cache[ward_code]={'regions':regions,'munis':munis}
                cached=cache[ward_code]
        except Exception as exc:st.error('町名一覧の取得に失敗しました：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    if not cached:
        st.caption('町名を絞る場合は「町名を読み込む・更新」を押してください。読み込まなくても区全体を検索できます。')
        retained=[c for c in (saved_codes or []) if isinstance(c,str) and re.fullmatch(re.escape(ward_code)+r'\d{3}',c)]
        if retained:st.caption('保存済みの町名指定 '+str(len(retained))+'町を保持しています。変更する場合は町名一覧を読み込んでください。')
        return retained
    options={r['suumo_prepared']['base_params']['oz']:r['town'] for r in cached['regions']}
    widget_key=prefix+'_towns_'+ward_code;stored_key=prefix+'_selected_'+ward_code
    defaults=st.session_state.get(stored_key,saved_codes or [])
    def keep_selection():st.session_state[stored_key]=list(st.session_state.get(widget_key,[]))
    selected=st.multiselect('対象の町名（未選択なら区全体）',list(options),default=[c for c in defaults if c in options],
                          format_func=lambda code:options[code],key=widget_key,disabled=disabled,on_change=keep_selection)
    st.session_state[stored_key]=list(selected)
    return selected


def suumo_prepare_region(web,region):
    """Resolve the visible-map municipality/town to a SUUMO search target.

    Preferred route is SUUMO's public town (oz_) page, which is the result of
    selecting 市区郡 -> 町名. Municipality-wide or free-word address search is
    never substituted when the town selector cannot be confirmed.
    """
    if region.get('suumo_prepared'):return dict(region['suumo_prepared'])
    cache=getattr(web,'suumo_region_cache',None)
    if cache is None:cache={};web.suumo_region_cache=cache
    wanted=normal(region.get('suumo_town') or town_base_name(region.get('town','')))
    key=(str(region.get('pref','')),str(region.get('code','')),address_key(wanted))
    if key in cache:return cache[key]

    search='https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
    city_params={'ar':'030','bs':'040','ta':str(region['pref']),'sc':str(region['code']),'pc':'50','srch_navi':'1'}
    if not web.permitted(search):raise AppError('SUUMOの市区郡選択ページの自動取得が許可されていません。')
    trace(web,'suumo_city_selected',{'municipality_code':region['code'],'city':region.get('city_name',''),'source':'表示地図から作成した市区郡・町名リスト'},stage='search_conditions')

    city_reply=web.fetch(search,params=city_params);city_soup=BeautifulSoup(city_reply.text,'html.parser')
    observed_layout_inputs=discover_layout_inputs(city_soup,'SUUMO')
    selector=_suumo_town_selector(city_soup,city_reply.text,wanted,city_reply.url)
    canonical_url=_suumo_canonical_city_url(city_soup,city_reply.url,city_reply.text)
    if not selector:
        form=city_soup.find('form',id='js-machiSelectForm')
        if form and form.get('action'):
            target=urljoin(city_reply.url,form['action'])
            fields=[(f['name'],f.get('value','')) for f in form.select('input[type="hidden"][name]') if f['name'] not in ('oz',)]
            try:
                if urlparse(target).hostname=='suumo.jp' and web.permitted(target):
                    popup=web.fetch(target,params=fields)
                    popup_soup=BeautifulSoup(popup.text,'html.parser')
                    control=_form_control_by_label(popup_soup,wanted,names=('oz',))
                    if control and re.fullmatch(r'\d{8}',str(control[1])):selector={'control':control,'method':'公開町名変更フォーム'}
                    trace(web,'suumo_town_selector_page',{'city':region.get('city_name'),'url':target,'found':bool(control)},stage='search_conditions')
            except AppError as exc:trace(web,'form_alternative',{'failed_url':target,'message':str(exc)},'WARNING','search_conditions')


    if not selector and canonical_url:
        try:
            if web.permitted(canonical_url):
                canonical_reply=web.fetch(canonical_url);canonical_soup=BeautifulSoup(canonical_reply.text,'html.parser')
                selector=_suumo_town_selector(canonical_soup,canonical_reply.text,wanted,canonical_reply.url)
                trace(web,'suumo_town_selector_page',{'city':region.get('city_name',''),'town':wanted,'url':canonical_url,'found':bool(selector)},stage='search_conditions')
        except AppError as exc:
            trace(web,'form_alternative',{'provider':'SUUMO','failed_url':canonical_url,'message':str(exc),'next_public_page':True},'WARNING','search_conditions')

    # The SEO town pages currently use /oz_<municipality><town-seq>/.
    # Probe only after normal selector parsing fails, and cache every town found.
    if not selector and canonical_url:
        probed=_suumo_probe_town_url(web,canonical_url,region.get('code'),region.get('city_name',''),wanted,max_suffix=80)
        if probed:
            selector={'url':probed,'method':'SUUMO町名公開ページ確認'}
            trace(web,'suumo_town_probe_selected',{'city':region.get('city_name',''),'town':wanted,'url':probed},stage='search_conditions')

    if selector and selector.get('url'):
        base_url=urlparse(selector['url'])._replace(query='',fragment='').geturl()
        base_params={'pc':'50'}
        method=selector.get('method','SUUMO町名URL')
    elif selector and selector.get('control'):
        name,value=selector['control'];base_url=search;base_params=dict(city_params);base_params[name]=value
        method=selector.get('method','SUUMO町名フォーム')
    else:
        trace(web,'suumo_town_selector_missing',{'city':region.get('city_name',''),'municipality_code':region.get('code'),'town':wanted},'ERROR','search_conditions')
        raise AppError('SUUMOで「市区郡→町名」の町名ページを確認できません。市区郡全体や住所文字列検索では代用しません。')

    # Current public SUUMO form/query values.  We also verify every accepted
    # detail page, so a provider-side parameter change cannot silently admit a
    # wrong layout/building/age.
    result={
        'url':base_url,
        'base_params':base_params,
        'town':wanted,
        'selection_method':method,
        'mansion':('ts',SUUMO_BUILDING_TYPE_CODES['マンション']),
        'age15':('cn','15'),
        'layout_filters':{layout:('md',
               observed_layout_inputs[layout][1] if layout in observed_layout_inputs
               and observed_layout_inputs[layout][0]=='md' else code)
               for layout,code in SUUMO_LAYOUT_CODES.items()},
    }
    cache[key]=result
    trace(web,'suumo_filters_ready',{'city':region.get('city_name',''),'town':wanted,'selection_method':method,
          'mansion':result['mansion'],'age15':result['age15'],'layout_filters':result['layout_filters']},stage='search_conditions')
    return result


def suumo_group_params(prepared,layouts,page=1):
    params=dict(prepared['base_params'])
    params['ts']=SUUMO_BUILDING_TYPE_CODES['マンション']  # 建物の種類：マンション（アパートは除外）
    params['cn']='15'                # 築年数：15年以内
    # Prefer the site's currently observed public form values when available.
    # Never omit the 1R/1K/1DK codes added to the same verified layout set.
    fields=prepared.get('layout_filters') or {}
    params['md']=[fields[x][1] if x in fields and fields[x][0]=='md' else SUUMO_LAYOUT_CODES[x] for x in layouts]
    params['pc']='50'
    # SUUMO SEO town pages paginate in the path (pnz1N.html).
    # A form-control town selection can still use FR301, so retain its public page parameter there.
    if '/jj/chintai/ichiran/FR301FC001/' in prepared['url'] and page>1:params['page']=page
    return params


def suumo_result_buildings(soup):
    """Read SUUMO's building-list cards, allowing div/li layout variation."""
    return soup.select('div.cassetteitem, li.cassetteitem')


def suumo_explicit_zero_results(soup):
    """Accept zero listings only when the response explicitly says so.

    '該当する物件' alone or an unrelated '0件' is NOT zero-result evidence.
    Treat changed markup, error pages and access verification as incomplete.
    """
    text=normal(soup.get_text(' ',strip=True))
    patterns=(
        r'条件に(?:あ|合)う物件(?:が|は)ありません',
        r'ご希望の条件に(?:あ|合)う物件(?:が|は)ありません',
        r'該当(?:する)?物件(?:が|は)(?:ありません|見つかりません)',
        r'物件が見つかりません(?:でした)?',
        r'検索結果(?:は|が)?\s*0\s*件',
        r'該当(?:物件|件数)(?:は|が)?\s*0\s*件',
        r'\b0\s*件\s*不動産会社が掲載',
    )
    return any(re.search(pattern,text) for pattern in patterns)


def suumo_house_card_type(building):
    """Use the *building-type badge*, not text address or unrelated link labels."""
    selectors=(
        '.cassetteitem_content-label', '.cassetteitem_content-title',
        '.cassetteitem_detail-col1', '[class*="cassetteitem_content-label"]',
    )
    for selector in selectors:
        for node in building.select(selector):
            label=normal(node.get_text(' ',strip=True))
            # A type badge must be unambiguous; don't infer from a building name.
            match=re.search(r'賃貸(?:一戸建て|戸建て|貸家|テラスハウス|タウンハウス|アパート|マンション)',label)
            if match:return match.group(0)
    return ''


def suumo_house_other_type(value):
    """SUUMO's third category includes terrace/town houses and 'other'."""
    text=normal(value).replace(' ','')
    if not text or re.search(r'アパート|マンション',text):return False
    return bool(re.search(r'一戸建(?:て)?|戸建(?:て)?|貸家|テラスハウス|タウンハウス|連棟|その他',text))


def _clean_suumo_building_name(value):
    text=normal(value)
    if not text:return ''
    text=re.sub(r'^【?SUUMO】?\s*','',text,flags=re.I)
    # Strip agent / station / address-like suffixes; never keep a textual address here.
    for sep in ('／',' | ',' - ','｜'):
        if sep in text:text=text.split(sep,1)[0].strip()
    text=re.sub(r'\s*[（(](?:[^)）]*(?:駅|都|道|府|県|区|市|町|村)[^)）]*)[)）]\s*$','',text)
    text=re.sub(r'\s+\d{2,4}号室\s*$','',text)
    return text[:120]


def _v140_card_building_name(building):
    """Use only the building TITLE of this SUUMO cassette, never its address.

    Some detail pages have a generic `station + floor count` H1, while the
    parent cassette displays the actual building name. Reject generic titles.
    """
    if building is None:return ''
    for selector in ('div.cassetteitem_content-title','h2.cassetteitem_content-title',
                     '.cassetteitem_content-title','.cassetteitem_content-name'):
        node=building.select_one(selector)
        if not node:continue
        name=_clean_suumo_building_name(node.get_text(' ',strip=True))
        if len(_v133_building_token(name))>=5 and not _V137_GENERIC_BUILDING.search(name):return name
    return ''


def _v140_choose_suumo_name(detail_name,card_hint):
    candidate=_clean_suumo_building_name(detail_name)
    hint=_clean_suumo_building_name(card_hint)
    if candidate and not _V137_GENERIC_BUILDING.search(candidate):return candidate
    if hint and len(_v133_building_token(hint))>=5 and not _V137_GENERIC_BUILDING.search(hint):return hint
    return candidate


def suumo_detail_fields(soup):
    """Read SUUMO listing facts, including the explicitly labelled 所在地.

    The publisher's town/chome is adequate for the town choropleth.  Neither
    the detail-page street number nor a map-center hint is used as proof of a
    building coordinate.
    """
    visible=' '.join(soup.stripped_strings);structured=json_listing_fields(soup)
    address=generic_labeled(soup,('所在地','物件所在地'))
    if not address:
        for node in soup.select('th,dt'):
            if normal(node.get_text(' ',strip=True)) in ('所在地','物件所在地'):
                sibling=node.find_next_sibling(['td','dd'])
                if sibling:address=sibling.get_text(' ',strip=True);break
    raw_layout=structured.get('layout') or generic_labeled(soup,('間取り','間取'))
    if not raw_layout:
        raw_layout=regex_after_label(visible,('間取り','間取'),r'(?:ワンルーム|\d+(?:S?LDK|S?DK|SK|LK|K|L|R))')
    raw_rent=structured.get('rent') or generic_labeled(soup,('賃料','家賃'))
    if not raw_rent:
        m=re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*万円\s*管理費(?:・共益費)?',visible)
        if m:raw_rent=m.group(1)+'万円'
    if not raw_rent:
        raw_rent=regex_after_label(visible,('賃料','家賃'),r'\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円')
    building_type=generic_labeled(soup,('建物種別','建物の種類','種別'))
    raw_age=structured.get('age') or generic_labeled(soup,('築年数','築年月','築年'))
    if not raw_age:
        raw_age=regex_after_label(visible,('築年数','築年月','築年'),r'(?:新築|築\s*\d{1,3}年|(?:19|20)\d{2}年\s*\d{1,2}月)')
    raw_area=structured.get('area') or generic_labeled(soup,('専有面積','面積'))
    if not raw_area:
        raw_area=regex_after_label(visible,('専有面積','面積'),r'\d+(?:\.\d+)?\s*(?:m2|㎡|平米)')
    name_candidates=[]
    h1=soup.find('h1')
    if h1:name_candidates.append(h1.get_text(' ',strip=True))
    og=soup.find('meta',attrs={'property':'og:title'})
    if og and og.get('content'):name_candidates.append(og.get('content'))
    if soup.title and soup.title.string:name_candidates.append(soup.title.string)
    building_name=''
    for candidate in name_candidates:
        cleaned=_clean_suumo_building_name(candidate)
        if cleaned and not re.search(r'(?:東京都|神奈川県|埼玉県|千葉県).*(?:区|市).*(?:丁目|\d[-－])',cleaned):
            building_name=cleaned;break
    return {'visible':visible,'address':normal(address),'source_address':normal(address),'raw_layout':normal(raw_layout),'raw_rent':normal(raw_rent),
            'building_type':normal(building_type),'raw_age':normal(raw_age),'raw_area':normal(raw_area),
            'building_name':normal(building_name),'structured':structured}

def _suumo_next_url(soup,response_url,filter_params):
    """Follow SUUMO's current path pagination (pnz12.html etc.) and preserve filters."""
    target=generic_next_page(soup,response_url,'SUUMO')
    if not target:return None
    u=urlparse(target)
    # Even when the pagination href contains ?page=..., the site may omit the
    # active building type, area, age or layout filters. Keep the search scope
    # stable across every page, without overwriting the page number or path.
    query=parse_qs(u.query,keep_blank_values=True)
    for key,value in filter_params.items():
        if key in ('ts','cn','md','mb','pc','ar','bs','ta','sc','srch_navi','oz'):
            query[key]=[str(v) for v in value] if isinstance(value,(tuple,list)) else [str(value)]
    return u._replace(query=urlencode(query,doseq=True)).geturl()


def suumo_jnc_key(url):
    match=re.search(r'/jnc_(\d+)',urlparse(url).path)
    return match.group(1) if match else ''


def suumo_bc_id(url):
    u=urlparse(url);bc=parse_qs(u.query).get('bc',[''])[0]
    if re.fullmatch(r'\d+',bc):return bc
    match=re.search(r'/bc_(\d+)',u.path)
    return match.group(1) if match else ''


def suumo_kankyo_urls(soup,detail_url):
    """Return published surroundings-map URLs, preferring the canonical BC route.

    SUUMO JNC detail pages can expose both JNC and BC links. The observed v66 failure
    came from using the JNC /kankyo/ page even though the canonical BC /kankyo/ page
    contained js-gmapData with exactly one room marker. Prefer explicit/derived BC
    pages, then fall back to JNC pages without inventing coordinates.
    """
    explicit=[];bc_links=[];jnc_links=[]
    for a in soup.select('a[href]'):
        label=normal(a.get_text(' ',strip=True))+' '+normal(a.get('title'))+' '+normal(a.get('aria-label'))
        target=urljoin(detail_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname!='suumo.jp':continue
        if '/kankyo/' not in u.path and '地図・周辺環境' not in label and '周辺環境' not in label:continue
        if '/bc_' in u.path:bc_links.append(target)
        elif '/jnc_' in u.path:jnc_links.append(target)
        else:explicit.append(target)
    candidates=[]
    def add(url):
        if not url:return
        u=urlparse(url);url=u._replace(fragment='').geturl()
        if safe_url(url,'SUUMO') and url not in candidates:candidates.append(url)
    bc=suumo_bc_id(detail_url)
    if bc:add(f'https://suumo.jp/chintai/bc_{bc}/kankyo/')
    # Preserve the listing's own JNC fallback even if unrelated BC links flood
    # the page. A JNC fallback must never be displaced by recommended rooms.
    u=urlparse(detail_url)
    if '/jnc_' in u.path:add(u._replace(path=u.path.rstrip('/')+'/kankyo/',fragment='').geturl())
    # Canonical metadata identifies THIS document; related cards do not.
    for tag in soup.select('link[rel=canonical][href],meta[property="og:url"][content]')[:3]:
        target=urljoin(detail_url,tag.get('href') or tag.get('content') or '')
        pm=re.search(r'/chintai/bc_(\d+)',urlparse(target).path)
        if pm:add(f'https://suumo.jp/chintai/bc_{pm.group(1)}/kankyo/')
    for url in bc_links[:6]:add(url)
    # A detail page can contain a canonical BC detail link but no direct surroundings link.
    for a in soup.select('a[href]'):
        target=urljoin(detail_url,a.get('href','')).split('#')[0]
        u=urlparse(target);m=re.fullmatch(r'/chintai/bc_(\d+)/?',u.path)
        if u.hostname=='suumo.jp' and m:add(f'https://suumo.jp/chintai/bc_{m.group(1)}/kankyo/')
    u=urlparse(detail_url)
    if '/jnc_' in u.path:add(u._replace(path=u.path.rstrip('/')+'/kankyo/',fragment='').geturl())
    for url in jnc_links[:2]:add(url)
    for url in explicit[:2]:add(url)
    # Keep a bounded fallback; remaining links are usually other buildings.
    return candidates[:8]


def suumo_kankyo_url(soup,detail_url):
    urls=suumo_kankyo_urls(soup,detail_url)
    return urls[0] if urls else ''


def _suumo_room_marker_points(web,soup,map_url):
    """Extract only explicitly published type=room markers from SUUMO map JSON."""
    nodes=[]
    exact=soup.find('script',id='js-gmapData')
    if exact:nodes.append(exact)
    # Keep a conservative fallback for harmless markup changes: JSON script blocks
    # that explicitly contain both a markers array and a room marker.
    for node in soup.find_all('script'):
        if node is exact:continue
        raw=node.string if isinstance(node.string,str) else node.get_text('',strip=False)
        if raw and '"markers"' in raw and '"room"' in raw:nodes.append(node)
    for node in nodes:
        raw=node.string if isinstance(node.string,str) else node.get_text('',strip=False)
        raw=(raw or '').strip()
        try:
            data=json.loads(raw);markers=data.get('markers',[]) if isinstance(data,dict) else []
        except (ValueError,TypeError,AttributeError) as exc:
            trace(web,'suumo_marker_invalid',{'map_url':map_url,'exception_type':type(exc).__name__,'script_id':node.get('id','')},'WARNING','location');continue
        points=[]
        for marker in markers if isinstance(markers,list) else []:
            if not isinstance(marker,dict) or normal(marker.get('type')).lower()!='room':continue
            try:lat,lng=float(marker['lat']),float(marker['lng'])
            except (ValueError,TypeError,KeyError):continue
            if has_point({'latitude':lat,'longitude':lng}):points.append((lat,lng))
        distinct=sorted(set(points))
        if len(distinct)==1:return distinct[0],{'script_id':node.get('id',''),'marker_count':len(markers) if isinstance(markers,list) else 0,'room_marker_count':1}
        trace(web,'suumo_marker_unresolved',{'map_url':map_url,'script_found':True,'script_id':node.get('id',''),
              'room_markers':len(distinct),'marker_types':[x.get('type') for x in markers if isinstance(x,dict)] if isinstance(markers,list) else [],
              'valid_room_points':distinct,'expected':'type=roomの有効座標が一意'},'WARNING','location')
    return None,{'script_found':bool(nodes)}


_JP_DIGIT_TRANS=str.maketrans('０１２３４５６７８９－ー−','0123456789---')
_EXTERNAL_IDENTITY_HOSTS=('myhome.nifty.com','www.mansion-review.jp','www.e-room.co','www.housecom.jp','vidax-gotanda.jp','www.homes.co.jp','www.athome.co.jp','www.goodrooms.jp','www.able.co.jp','www.mec-h.com','www.start-line.co.jp','www.m-standard.co.jp','www.property-bank.co.jp','lifullhomes-index.jp','library.smtrc.jp','roomcore.jp','realestate.yahoo.co.jp','lifullhomes-satei.jp','sumnara.jp','house.goo.ne.jp','www.daikyo-anabuki.co.jp','www.arrival-net.co.jp','www.roompia.jp','www.kokyuchintai.com','www.shamaison.com','www.nichiwa-realestate.co.jp','www.milford-chintai.com','towers.select','www.winspro.co.jp','www.mitsui-chintai.co.jp','www.kkf.co.jp','www.livable.co.jp')


_EXTERNAL_IDENTITY_HOSTS = _EXTERNAL_IDENTITY_HOSTS + ('www.srhome.co.jp', 'landwork-estate.co.jp', 'asuxia.co.jp')

def _normalize_japanese_address(value):
    text=normal(value).translate(_JP_DIGIT_TRANS).replace(' ','').replace('　','')
    text=text.replace('番地','番').replace('番の','番')
    text=re.sub(r'(\d+)番号(\d+)号',r'\1番\2号',text)
    text=re.sub(r'(\d+)番(\d+)号',r'\1-\2',text)
    text=re.sub(r'(\d+)番$',r'\1番',text)
    return text


def _detailed_address(value):
    text=_normalize_japanese_address(value)
    return bool(text and re.search(r'(?:丁目|大字|字).{0,30}\d+(?:番|-|－)\d+(?:[-－]\d+)?(?:号)?$',text))


def _extract_detailed_addresses(text):
    if not text:return []
    compact=unicodedata.normalize('NFKC',str(text)).replace('\u3000',' ')
    # Tokyo-focused because the automatic collector currently operates on Tokyo wards.
    # Require chome + block + base/house number so town-only strings never pass.
    pattern=re.compile(r'東京都\s*[^\s,、。|<>]{1,16}区\s*[^\s,、。|<>]{1,24}?(?:\d+丁目|[一二三四五六七八九十]+丁目)\s*\d+\s*(?:番(?:地)?\s*\d+\s*号?|-\s*\d+)(?!\d)')
    out=[]
    for m in pattern.finditer(compact):
        addr=_normalize_japanese_address(m.group(0))
        if _detailed_address(addr) and addr not in out:out.append(addr)
    return out


def _suumo_map_center_points(soup,map_url):
    """Coordinates published by the property-specific kankyo Google map, not listing text."""
    chunks=[str(soup)]
    for tag in soup.select('a[href],iframe[src],img[src],script'):
        for attr in ('href','src'):
            if tag.get(attr):chunks.append(html.unescape(str(tag.get(attr))))
        if tag.name=='script':chunks.append(tag.get_text('',strip=False) or '')
    blob=' '.join(chunks)
    points=[]
    patterns=(
        (r'[?&](?:ll|center)=\s*(3[4-7]\.\d+)\s*[,%2C]+\s*(1(?:3[89]|40)\.\d+)','google_ll'),
        (r'@\s*(3[4-7]\.\d+)\s*,\s*(1(?:3[89]|40)\.\d+)(?:,|%2C)','google_at'),
        (r'[?&]q=\s*(3[4-7]\.\d+)\s*[,%2C]+\s*(1(?:3[89]|40)\.\d+)','google_q'),
    )
    for pattern,method in patterns:
        for m in re.finditer(pattern,blob,re.I):
            try:p=(float(m.group(1)),float(m.group(2)),method)
            except (ValueError,TypeError):continue
            if has_point({'latitude':p[0],'longitude':p[1]}) and p not in points:points.append(p)
    # Prefer a coordinate repeated by more than one Google-map link. If there is
    # only one distinct coordinate, it is still a usable published map center hint.
    counts={}
    for lat,lng,method in points:
        key=(round(lat,6),round(lng,6));counts[key]=counts.get(key,0)+1
    if not counts:return None,{'map_url':map_url,'points':[]}
    ranked=sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))
    best,count=ranked[0]
    if len(ranked)>1 and ranked[0][1]==ranked[1][1] and meters(best,ranked[1][0])>80:
        return None,{'map_url':map_url,'points':points,'reason':'multiple_map_centers_not_unique'}
    return best,{'map_url':map_url,'points':points,'occurrences':count,'distinct':len(ranked)}


def _identity_year_month(raw_age):
    text=normal(raw_age)
    m=re.search(r'((?:19|20)\d{2})年\s*(\d{1,2})月',text)
    return (int(m.group(1)),int(m.group(2))) if m else None


def _identity_area(raw_area):
    m=re.search(r'(\d+(?:\.\d+)?)',normal(raw_area))
    return float(m.group(1)) if m else None


def _external_result_links(soup,base_url):
    links=[]
    for a in soup.select('a[href]'):
        raw=html.unescape(str(a.get('href') or ''));target=urljoin(base_url,raw)
        u=urlparse(target)
        # DuckDuckGo wraps links in uddg; Google often wraps them in /url?q=.
        q=parse_qs(u.query)
        for key in ('uddg','q','url'):
            candidate=(q.get(key) or [''])[0]
            if candidate.startswith('https://'):target=candidate;u=urlparse(target);break
        if u.scheme=='https' and u.hostname in _EXTERNAL_IDENTITY_HOSTS and target not in links:links.append(target)
    return links


def _identity_match_score(text,identity):
    normalized=normal(text);score=0;evidence=[]
    name=normal(identity.get('building_name'))
    if name and name in normalized:score+=4;evidence.append('building_name')
    ym=_identity_year_month(identity.get('raw_age'))
    if ym and (f'{ym[0]}年{ym[1]}月' in normalized or f'{ym[0]}/{ym[1]:02d}' in normalized):score+=2;evidence.append('built_ym')
    layout=parsed_layout(identity.get('raw_layout'))
    if layout and layout in normalized:score+=1;evidence.append('layout')
    area=_identity_area(identity.get('raw_area'))
    if area is not None and (f'{area:g}㎡' in normalized or f'{area:g}m2' in normalized.lower() or f'{area:g}平米' in normalized):score+=2;evidence.append('area')
    return score,evidence


def _gsi_validate_external_address(web,address,munis,region_code=None):
    try:data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json()
    except Exception as exc:
        trace(web,'external_address_gsi_failed',{'address':address,'exception_type':type(exc).__name__,'reason':str(exc)},'WARNING','address.fallback');return None
    if not isinstance(data,list):return None
    for feature in data[:5]:
        if not isinstance(feature,dict):continue
        props=feature.get('properties') or {};geo=feature.get('geometry') or {};coords=geo.get('coordinates')
        title=_normalize_japanese_address(props.get('title') or address)
        if not _detailed_address(title) or not isinstance(coords,(list,tuple)) or len(coords)<2:continue
        try:lng,lat=float(coords[0]),float(coords[1])
        except (ValueError,TypeError):continue
        official=reverse(web,(lat,lng),munis,force=True)
        if region_code and official and official.get('code')!=str(region_code):continue
        return {'address':title,'reference_point':[lat,lng],'official_region':official,'verification':'external_identity_plus_gsi_address_search'}
    return None


# 2nd-stage fallback: the Digital Agency Address Base Registry, published as
# Geolonia Japanese Addresses v2. Its records have block/house numbers AND
# measured coordinates. No SUUMO text addresses or town centroids are inputs.
# Source specification: https://github.com/geolonia/japanese-addresses-v2
# Strict acceptance deliberately favors false negatives over wrong buildings.
ADDRESS_REGISTRY_HOST = 'japanese-addresses-v2.geoloniamaps.com'
ADDRESS_REGISTRY_MAX_TOWN_RANGE = 640_000
ADDRESS_REGISTRY_MAX_DIST_M = 12.0
ADDRESS_REGISTRY_MIN_GAP_M = 15.0


def _registry_verified_point(web, map_point, munis, region_code=None, expected_towns=None):
    """Official reverse town check; a map-center hint cannot identify a residence."""
    if not map_point or not has_point({'latitude':map_point[0], 'longitude':map_point[1]}):return None
    try:region=reverse(web,map_point,munis,force=True)
    except (AppError,ValueError,TypeError) as exc:
        trace(web,'registry_reverse_unavailable',{'reason':type(exc).__name__},'WARNING','address.stage2');return None
    if not region or region.get('code') not in munis:return None
    if region_code and str(region['code'])!=str(region_code):return None
    bases=[town_base_name(t) for t in (expected_towns or ()) if normal(t)]
    if bases and town_base_name(region.get('town','')) not in bases:return None
    return region


def _registry_manifest(web, region):
    """Bounded manifest with town-specific byte ranges; no ward-wide file download."""
    code=str(region['code'])
    with web.cache_lock:
        cache=getattr(web,'address_registry_manifest_cache',None)
        if cache is None:cache={};web.address_registry_manifest_cache=cache
        if code in cache:return cache[code]
    _,pref,city=region['munis'][code]
    if pref!='東京都' or not re.fullmatch(r'[^/]{1,24}区',city):return []
    url='https://'+ADDRESS_REGISTRY_HOST+'/api/ja/'+quote(pref,safe='')+'/'+quote(city,safe='')+'.json'
    try:
        if not web.permitted(url):return []
        reply=web.fetch(url)
        if len(reply.content)>1_500_000:raise AppError('町域索引が上限を超えています。')
        data=reply.json();towns=data.get('data') if isinstance(data,dict) else None
        if not isinstance(towns,list) or len(towns)>5000:raise AppError('町域索引の形式が不正です。')
    except (AppError,ValueError,TypeError) as exc:
        trace(web,'registry_manifest_unavailable',{'city':city,'reason':str(exc)},'WARNING','address.stage2');return []
    with web.cache_lock:
        if len(cache)>=4:cache.pop(next(iter(cache)))
        cache[code]=towns
    return towns


def _registry_range_text(web, region, offset, length):
    """Download ONLY the town byte range. Never fetch/cache a complete ward TXT.

    Streamed through the same app HTTP gate and read-only session. HTTP 206
    with an exact matching Content-Range is mandatory: an ignored Range (200)
    MUST be closed without downloading the response body.
    """
    if not isinstance(offset,int) or not isinstance(length,int) or offset<0 or not 0<length<=ADDRESS_REGISTRY_MAX_TOWN_RANGE:return None
    pref,city=region['munis'][region['code']][1:3]
    url='https://'+ADDRESS_REGISTRY_HOST+'/api/ja/'+quote(pref,safe='')+'/'+quote(city,safe='')+'-住居表示.txt'
    try:
        if not web.permitted(url):return None
    except AppError as exc:
        trace(web,'registry_range_not_permitted',{'reason':str(exc)},'WARNING','address.stage2');return None
    upper=offset+length-1
    # No proxy rotation, global cache operations or direct system-file operations.
    response=None
    try:
        response=web.route_request(web.session(),'GET',url,{'headers':{
            'Range':f'bytes={offset}-{upper}', 'Accept-Encoding':'identity'},'stream':True})
        if response.status_code!=206:
            trace(web,'registry_range_refused',{'status':response.status_code,'reason':'206_required'},'WARNING','address.stage2');return None
        match=re.fullmatch(r'bytes\s+(\d+)-(\d+)/(?:\d+|\*)',response.headers.get('Content-Range','').strip(),re.I)
        if not match or (int(match[1]),int(match[2]))!=(offset,upper):
            trace(web,'registry_range_refused',{'status':206,'reason':'content_range_mismatch'},'WARNING','address.stage2');return None
        payload=response.raw.read(length+1,decode_content=True)
        if len(payload)!=length:
            trace(web,'registry_range_refused',{'status':206,'reason':'byte_count_mismatch'},'WARNING','address.stage2');return None
        return payload.decode('utf-8-sig')
    except (requests.RequestException,AppError,UnicodeError,OSError,ValueError) as exc:
        trace(web,'registry_range_failed',{'exception_type':type(exc).__name__},'WARNING','address.stage2');return None
    finally:
        if response is not None:response.close()


def _registry_read_rows(text):
    """Parse a v2 chome slice: prc_num1,prc_num2,prc_num3,lng,lat."""
    if not isinstance(text,str) or not text:return []
    rows=[]
    for raw in csv.reader(io.StringIO(text)):
        if len(raw)<5:continue
        first,second,third=tuple(normal(v) for v in raw[:3])
        if not first.isdecimal() or not second.isdecimal():continue
        if third and not third.isdecimal():continue
        if int(first)<=0 or int(second)<=0 or (third and int(third)<=0):continue
        try:lng,lat=map(float,raw[-2:])
        except (TypeError,ValueError):continue
        if not has_point({'latitude':lat,'longitude':lng}):continue
        pieces=[str(int(first)),str(int(second))]
        if third:pieces.append(str(int(third)))
        rows.append(('-'.join(pieces),(lat,lng)))
        if len(rows)>18000:return []  # avoid unexpectedly huge payloads
    return rows



# V133: corroborate the registry with a *named physical building* from an
# independent, public OSM element. Address points alone are not building IDs:
# do not guess an address by relaxing the nearby-candidate distance margin.
_OSM_V133_API = 'https://overpass-api.de/api/interpreter'
_OSM_V133_MAX_REQUESTS = 12  # per collection worker: bound public-API demand
_OSM_V133_MIN_INTERVAL = 4.0  # seconds between requests from this app instance


def _v133_building_token(text):
    value=unicodedata.normalize('NFKC',str(text or '')).casefold()
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u3400-\u9fff]', '',value)


def _v133_osm_address_for_named_building(web,identity,map_point,region,official_ranked):
    """Only exact building-name + exact-address + independent registry match.

    Public Overpass returns OSM *buildings/POIs*, unlike residential frontage
    number points. OSM can be incomplete: no match means no saved address.
    """
    name=normal((identity or {}).get('building_name'))
    name_key=_v133_building_token(name)
    if len(name_key)<5 or (re.search(r'(?:駅|線).*?(?:階建|築\d+年)',name)):
        return None
    if not map_point or not official_ranked:return None
    if not region or not region.get('code') or not region.get('town'):return None
    lat,lng=float(map_point[0]),float(map_point[1])
    # One query per ~100m cell, shared by listings in the *same* collection.
    grid=(round(lat,3),round(lng,3))
    with web.cache_lock:
        cache=getattr(web,'v133_osm_tile_cache',None)
        if cache is None:cache={};web.v133_osm_tile_cache=cache
        gate=getattr(web,'v133_osm_request_gate',None)
        if gate is None:gate=threading.RLock();web.v133_osm_request_gate=gate
        cached=cache.get(grid)
    if cached is None:
        with gate:
            with web.cache_lock:
                cached=cache.get(grid)
                count=getattr(web,'v133_osm_request_count',0)
                last=getattr(web,'v133_osm_last_request',0.)
                disabled=getattr(web,'v133_osm_disabled',False)
            if cached is None and count>=_OSM_V133_MAX_REQUESTS:
                trace(web,'osm_budget_exhausted',{'max_queries':_OSM_V133_MAX_REQUESTS},stage='address.stage2')
                return None
            if cached is None and disabled:return None
            if cached is None:
                # Strong bound: one small 130m cell, named objects only, 12s
                # Overpass server execution limit, max 500KB downloaded response.
                q=f'[out:json][timeout:12];nwr["building"]["name"](around:130,{grid[0]:.3f},{grid[1]:.3f});out center;'
                try:
                    if not web.permitted(_OSM_V133_API):
                        with web.cache_lock:web.v133_osm_disabled=True
                        trace(web,'osm_not_permitted',{'source':'robots'},'WARNING','address.stage2')
                        return None
                    delay=max(0.,_OSM_V133_MIN_INTERVAL-(time.monotonic()-last)) if last else 0.
                    if delay:web.pause(delay)
                    with web.cache_lock:
                        web.v133_osm_request_count=count+1
                        web.v133_osm_last_request=time.monotonic()
                    reply=web.fetch(_OSM_V133_API,params={'data':q})
                    if len(reply.content)>500_000:raise ValueError('OSM response exceeds 500KB')
                    data=reply.json()
                    elements=data.get('elements') if isinstance(data,dict) else None
                    if not isinstance(elements,list) or len(elements)>450:raise ValueError('OSM response exceeds the bounded element limit')
                    cached=elements
                    with web.cache_lock:
                        if len(cache)>=40:cache.pop(next(iter(cache)))
                        cache[grid]=cached
                except SearchCancelled:raise
                except (AppError,ValueError,TypeError,requests.RequestException) as exc:
                    status=(getattr(exc,'diagnostic',{}) or {}).get('status')
                    # An unreachable external OSM server is not retried for
                    # every room; keep this disabled ONLY in this app controller.
                    if status in (403,429,503) or isinstance(exc,AppError):
                        with web.cache_lock:web.v133_osm_disabled=True
                    trace(web,'osm_identity_unavailable',{'reason':type(exc).__name__,'status':status},'WARNING','address.stage2')
                    return None
    code=str(region['code'])
    try:pref,city=region['munis'][code][1:3]
    except (IndexError,KeyError,TypeError):return None
    town=normal(region['town'])
    registry={_normalize_japanese_address(address_key(a)):(dist,point) for dist,a,point in official_ranked}
    hits={}
    for item in cached:
        if not isinstance(item,dict):continue
        tags=item.get('tags') or {}
        if not isinstance(tags,dict):continue
        # A named shop/node alone is not a verified apartment building.
        if item.get('type') not in ('way','relation') or not tags.get('building'):continue
        names=(tags.get('name'),tags.get('name:ja'),tags.get('alt_name'))
        if name_key not in {_v133_building_token(x) for x in names if x}:continue
        point=item.get('center') or item
        if not isinstance(point,dict):continue
        try:p=(float(point['lat']),float(point['lon']))
        except (KeyError,ValueError,TypeError):continue
        # An OSM named object must coincide with this very listing's map pin.
        if meters(map_point,p)>32:continue
        try:building_region=reverse(web,p,region['munis'],force=True)
        except (AppError,ValueError,TypeError):continue
        if not building_region or str(building_region.get('code'))!=code or address_key(building_region.get('town'))!=address_key(town):continue
        if tags.get('addr:city') and _v133_building_token(city) not in _v133_building_token(tags['addr:city']):continue
        if tags.get('addr:suburb') and _v133_building_token(town) not in _v133_building_token(tags['addr:suburb']):continue
        raw=normal(tags.get('addr:full'))
        if raw:
            full=_normalize_japanese_address(address_key(raw))
        else:
            house=normal(tags.get('addr:housenumber'))
            if not re.fullmatch(r'[0-9０-９]+[-－][0-9０-９]+(?:[-－][0-9０-９]+)?',house):continue
            full=_normalize_japanese_address(address_key(pref+city+town+house))
        if full not in registry:continue
        dist,ref=registry[full]
        if dist>50:continue  # frontages can lie well beyond building centroids
        osm_id=item.get('id');kind=item.get('type')
        url=(f'https://www.openstreetmap.org/{kind}/{osm_id}' if kind in ('way','node','relation') and str(osm_id).isdigit() else '')
        hits[full]={'address':full,'reference_point':list(ref),'distance_m':dist,
                    'verification':'osm_exact_named_building_plus_address_registry',
                    'identity_evidence':['exact_building_name','exact_registry_house_number'],
                    'external_hosts':['overpass-api.de',ADDRESS_REGISTRY_HOST],
                    'external_urls':[url] if url else [],'registry_town':town}
    if len(hits)!=1:
        if len(hits)>1:trace(web,'osm_building_address_ambiguous',{'building_name':name,'matches':len(hits)},'WARNING','address.stage2')
        return None
    match=next(iter(hits.values()))
    trace(web,'osm_building_address_verified',{'building_name':name,'address':match['address'],
          'distance_m':round(match['distance_m'],1),'source':match['external_urls'][:1]},stage='address.stage2')
    return match


def _registry_precise_address_from_map(web,map_point,position_kind,munis,region_code=None,expected_towns=None,identity=None):
    """Stage 2: unique nearby official address, never a guessed block or town.

    Reject map centers (not necessarily the building), cross-town candidates,
    multiple near-neighbours and incomplete residential records. No candidate
    is promoted merely because it shares a town with the SUUMO search scope.
    """
    if position_kind not in ('room_marker','image_pin'):
        trace(web,'registry_no_exact_map_pin',{'kind':position_kind},'WARNING','address.stage2');return None
    region=_registry_verified_point(web,map_point,munis,region_code,expected_towns)
    if not region:return None
    region=dict(region,munis=munis)
    towns=_registry_manifest(web,region)
    wanted=address_key(region['town'])
    selected=[]
    for entry in towns:
        if not isinstance(entry,dict):continue
        place=normal(entry.get('oaza_cho'))+normal(entry.get('chome'))
        ranges=entry.get('csv_ranges') or {}
        info=ranges.get('住居表示') if isinstance(ranges,dict) else None
        if address_key(place)!=wanted or not isinstance(info,dict):continue
        try:
            offset=int(info['start']);size=int(info['length'])
        except (ValueError,TypeError,KeyError):continue
        if not (0<=offset and 0<size<=ADDRESS_REGISTRY_MAX_TOWN_RANGE):continue
        selected.append((place,offset,size))
    if len(selected)!=1:
        trace(web,'registry_town_unresolved',{'town':region['town'],'matching_ranges':len(selected)},'WARNING','address.stage2');return None
    place,offset,size=selected[0]
    cache_key=(str(region['code']),offset,size)
    with web.cache_lock:
        cache=getattr(web,'address_registry_town_cache',None)
        if cache is None:cache={};web.address_registry_town_cache=cache
        locks=getattr(web,'address_registry_town_locks',None)
        if locks is None:locks={};web.address_registry_town_locks=locks
        # One app-local gate per ward/town, never a global / OS cache lock.
        gate=locks.setdefault(cache_key,threading.Lock())
    with gate:
        with web.cache_lock:rows=cache.get(cache_key)
        if rows is None:
            raw=_registry_range_text(web,region,offset,size)
            if raw is None:return None
            rows=_registry_read_rows(raw)
            if not rows:
                trace(web,'registry_town_empty',{'town':place,'bytes':size},'WARNING','address.stage2');return None
            with web.cache_lock:
                if len(cache)>=4:cache.pop(next(iter(cache)))
                cache[cache_key]=rows
    # Keep at most four small town gates; in-flight ones may finish independently.
    with web.cache_lock:
        if len(locks)>8:
            for old_key in list(locks):
                if old_key!=cache_key and not locks[old_key].locked():
                    locks.pop(old_key,None)
                    if len(locks)<=8:break
    candidates={}
    for number,point in rows:
        distance=meters(map_point,point)
        if distance>50:continue
        address=region['munis'][region['code']][1]+region['munis'][region['code']][2]+place+number
        if not _detailed_address(address):continue
        prior=candidates.get(address)
        if prior is None or distance<prior[0]:candidates[address]=(distance,point)
    ranked=sorted(((d,a,p) for a,(d,p) in candidates.items()),key=lambda x:x[0])
    if not ranked:
        trace(web,'registry_no_nearby_address',{'town':place,'records':len(rows)},'WARNING','address.stage2');return None
    best_dist,address,address_point=ranked[0]
    second=ranked[1][0] if len(ranked)>1 else float('inf')
    if best_dist>ADDRESS_REGISTRY_MAX_DIST_M or second-best_dist<ADDRESS_REGISTRY_MIN_GAP_M:
        trace(web,'registry_precision_rejected',{'town':place,'nearest_m':round(best_dist,2),
              'runnerup_m':round(second,2) if math.isfinite(second) else None,
              'max_m':ADDRESS_REGISTRY_MAX_DIST_M,'min_gap_m':ADDRESS_REGISTRY_MIN_GAP_M,
              'next_stage':'OSM同一建物・同一番地の独立照合'},'WARNING','address.stage2')
        verified=_v133_osm_address_for_named_building(web,identity,map_point,region,ranked)
        if verified:
            verified.update(official_region={k:region[k] for k in ('code','pref','town','label')},
                            registry_method='Geolonia v2 + OpenStreetMap',
                            registry_candidate_count=len(ranked))
            return verified
        return None
    result={'address':address,'reference_point':list(address_point),'distance_m':best_dist,
            'official_region':{k:region[k] for k in ('code','pref','town','label')},
            'verification':'digital_agency_address_registry_unique_point',
            'registry_method':'Geolonia v2 / デジタル庁アドレス・ベース・レジストリ',
            'registry_candidate_count':len(ranked), 'registry_gap_m':second-best_dist,
            'registry_town':place}
    trace(web,'registry_precise_address_ok',{'address':address,'map_pin':list(map_point),
          'distance_m':round(best_dist,2),'runnerup_m':round(second,2) if math.isfinite(second) else None,
          'town':place,'data':'digital_agency_address_registry'},stage='address.stage2')
    return result




# V136: detailed-address validation from independently published building information.
# We do not infer an exact number from the nearest address point. The external
# source must publish that exact number; the official registry must contain it.
_V136_MAX_REGISTRY_DISTANCE_M = 70.0
# A map center is NOT a property marker. Only two independent matching
# primary building-address sources plus the official exact number may use it.
_V144_MAP_CENTER_MAX_REGISTRY_DISTANCE_M = 230.0
# Process-local limits ONLY for this Streamlit app. No proxy/VPN/system calls,
# no shared system caches, and no cross-container state. A denied host may be
# retried after a bounded pause, while other public sources remain available.
_V144_SOURCE_COOLDOWN_LOCK = threading.RLock()
_V144_SOURCE_UNAVAILABLE_UNTIL = {}


def _v144_source_pause_remaining(host):
    with _V144_SOURCE_COOLDOWN_LOCK:
        deadline=_V144_SOURCE_UNAVAILABLE_UNTIL.get(str(host or ''),0.0)
        remaining=max(0.0,deadline-time.monotonic())
        if not remaining and host in _V144_SOURCE_UNAVAILABLE_UNTIL:
            _V144_SOURCE_UNAVAILABLE_UNTIL.pop(host,None)
        return remaining


def _v144_source_pause(host,status):
    if status not in (403,405,429):return
    delay={403:600.0,405:600.0,429:1800.0}[status]
    with _V144_SOURCE_COOLDOWN_LOCK:
        _V144_SOURCE_UNAVAILABLE_UNTIL[host]=max(_V144_SOURCE_UNAVAILABLE_UNTIL.get(host,0.0),
                                                  time.monotonic()+delay)
        if len(_V144_SOURCE_UNAVAILABLE_UNTIL)>100:
            now=time.monotonic()
            for name,deadline in list(_V144_SOURCE_UNAVAILABLE_UNTIL.items()):
                if deadline<now:_V144_SOURCE_UNAVAILABLE_UNTIL.pop(name,None)
_V136_OWNER_SITES = frozenset(('www.mec-h.com',))


def _v136_external_addresses(text,region):
    """Extract *this municipality and town only*; accept Japanese address spellings.

    A bare N-chome text or the first nearby point is never a full address.
    Suppress land-lot (地番) entries when a 住居表示 number is separately available.
    """
    if not isinstance(text,str) or not isinstance(region,dict):return []
    code=str(region.get('code',''))
    munis=region.get('munis') or {}
    if code not in munis:return []
    _,pref,city=munis[code]
    town=address_key(region.get('town',''))
    m=re.fullmatch(r'(.+?)(\d{1,2})丁目',town)
    if not m:return []
    stem,chome=m.groups()
    if not stem or not chome:return []
    normalized=address_key(unicodedata.normalize('NFKC',text)).translate(_JP_DIGIT_TRANS).replace('　',' ')
    normalized=normalized.replace('‐','-').replace('‑','-').replace('–','-').replace('―','-')
    # Some real estate sites write 荏原4-2-3 (chome-block-house).
    p=re.compile(r'(?:(?:'+re.escape(pref)+r')\s*)?'+re.escape(city)+r'\s*'+re.escape(stem)
        +r'\s*'+re.escape(chome)+r'(?:丁目\s*|-\s*)'
        +r'(?P<block>\d{1,4})\s*(?:番(?:地)?(?:の)?\s*|[-－]\s*)'
        +r'(?P<unit>\d{1,4})(?:号)?(?!\d)')
    addresses={}
    for mat in p.finditer(normalized):
        # A registered land parcel number is not automatically a residential address.
        after=normalized[mat.end():mat.end()+24]
        before=normalized[max(0,mat.start()-24):mat.start()]
        if re.search(r'^\s*[（(]\s*地番',after) or re.search(r'(?:地番|登記地番)\s*[:：]?\s*$',before):continue
        block=str(int(mat.group('block')));unit=str(int(mat.group('unit')))
        if block=='0' or unit=='0':continue
        full=_normalize_japanese_address(pref+city+town+block+'-'+unit)
        addresses[full]=None
        if len(addresses)>=15:break
    return list(addresses)


def _v136_registry_confirmed(web,external_address,map_point,munis,region_code=None,position_kind='',
                             independently_corrobated_center=False):
    """Verify a published *exact* building number against real registry rows.

    Exact room pins use the unchanged 70m limit. A published SUUMO map-center
    can be used ONLY after two independent named-building PRIMARY address fields
    agree; address numbers are never inferred from distance alone. The
    verified registry point, not the imprecise map center, becomes the saved pin.
    """
    center_mode=(position_kind=='map_center' and independently_corrobated_center)
    if (position_kind not in ('room_marker','image_pin') and not center_mode) or not map_point:return None
    region=_registry_verified_point(web,map_point,munis,region_code)
    if not region:return None
    region=dict(region,munis=munis)
    code=str(region['code']);_,pref,city=munis[code]
    expected_prefix=_normalize_japanese_address(address_key(pref+city+region['town']))
    candidate_key=_normalize_japanese_address(address_key(external_address))
    if not _detailed_address(candidate_key) or not candidate_key.startswith(expected_prefix):return None
    towns=_registry_manifest(web,region)
    wanted=address_key(region['town'])
    matching=[]
    for entry in towns:
        if not isinstance(entry,dict):continue
        place=normal(entry.get('oaza_cho'))+normal(entry.get('chome'))
        info=(entry.get('csv_ranges') or {}).get('住居表示')
        if address_key(place)!=wanted or not isinstance(info,dict):continue
        try:offset,size=int(info['start']),int(info['length'])
        except (TypeError,KeyError,ValueError):continue
        if 0<=offset and 0<size<=ADDRESS_REGISTRY_MAX_TOWN_RANGE:matching.append((place,offset,size))
    if len(matching)!=1:return None
    place,offset,size=matching[0]
    key=(code,offset,size)
    with web.cache_lock:
        cache=getattr(web,'address_registry_town_cache',None)
        if cache is None:cache={};web.address_registry_town_cache=cache
        locks=getattr(web,'address_registry_town_locks',None)
        if locks is None:locks={};web.address_registry_town_locks=locks
        gate=locks.setdefault(key,threading.Lock())
    with gate:
        with web.cache_lock:rows=cache.get(key)
        if rows is None:
            raw=_registry_range_text(web,region,offset,size)
            if raw is None:return None
            rows=_registry_read_rows(raw)
            if not rows:return None
            with web.cache_lock:
                if len(cache)>=4:cache.pop(next(iter(cache)))
                cache[key]=rows
    matches=[]
    for number,point in rows:
        known=_normalize_japanese_address(address_key(pref+city+place+number))
        if known!=candidate_key:continue
        distance=meters(map_point,point)
        if distance<=(_V144_MAP_CENTER_MAX_REGISTRY_DISTANCE_M if center_mode else _V136_MAX_REGISTRY_DISTANCE_M):
            matches.append((distance,point))
    if not matches:return None
    distance,point=min(matches,key=lambda x:x[0])
    return dict(address=candidate_key,reference_point=list(point),distance_m=distance,
        official_region={k:region[k] for k in ('code','pref','town','label')},
        registry_method='デジタル庁アドレス・ベース・レジストリ (Geolonia v2)',
        verification='external_exact_building_address_plus_official_registry')


# V137: a named property's own location field is stronger than a page-wide
# address match. Large estate-library pages often mention nearby buildings and
# their addresses; those must NEVER be treated as this building's address.
_V137_PRIMARY_MAX_METERS = 50.0
_V137_LOCATION_FIELD = re.compile(
    r'(?:\u6240\u5728\u5730(?:\s*[\uff08(]\s*\u4f4f\u6240\s*[\uff09)])?|'
    r'\u4f4f\u6240|\u4f4f\u5c45\u8868\u793a)\s*[:\uff1a]?')
_V137_GENERIC_BUILDING = re.compile(r'(?:\u99c5|\u7dda).*?(?:\u968e\u5efa|\u7bc9\d+\u5e74)')


def _v137_primary_building_addresses(soup,identity,region):
    """Exact address from the named building's own primary location field.

    Requires a matching H1, an explicitly labelled location/address field,
    only one address in the first property's main section, and a complete
    block and residence number. Related property lists and footer matches do
    not qualify. The official registry and pin are verified separately.
    """
    if soup is None or not isinstance(region,dict):return []
    name=normal((identity or {}).get('building_name'))
    token=_v133_building_token(name)
    if len(token)<5 or _V137_GENERIC_BUILDING.search(name):return []
    for heading in soup.find_all('h1',limit=8):
        heading_token=_v133_building_token(heading.get_text(' ',strip=True))
        if token not in heading_token:continue
        parts=[];size=0
        for node in heading.next_elements:
            if getattr(node,'name',None)=='h1':break
            if not isinstance(node,str) or getattr(node,'parent',None) is None:continue
            if getattr(node.parent,'name','') in ('script','style','noscript','template'):continue
            piece=normal(node)
            if not piece:continue
            # The property overview is near the first heading. Never scan the
            # full document: related buildings are commonly listed later.
            if size+len(piece)>5500:break
            parts.append(piece);size+=len(piece)+1
        content=' '.join(parts)
        related=re.search(r'(?:\u5468\u8fba|\u8fd1\u96a3|\u4ed8\u8fd1)\u306e?(?:\u7269\u4ef6|\u30de\u30f3\u30b7\u30e7\u30f3|\u5efa\u7269|\u8cc3\u8cb8)|\u985e\u4f3c\u7269\u4ef6', content)
        if related:content=content[:related.start()]
        matches=[]
        for label in _V137_LOCATION_FIELD.finditer(content):
            # A labelled field, not a random full address elsewhere on a page.
            text=content[label.end():label.end()+180]
            candidates=_v136_external_addresses(text,region)
            for address in candidates:
                if address not in matches:matches.append(address)
            if len(matches)>1:return []
        if len(matches)==1:return matches
    return []


# V138: Public references for specific buildings observed in the user's failed
# SUUMO log. These are URL seeds ONLY, NEVER pre-filled addresses or coordinates.
# Every run must actually fetch the page, verify the named building, and match
# its full street address to an official house-number point and a SUUMO room pin.
# A URL may disappear; the standard generic search path remains available.
_V138_REFERENCE_URLS = (
    ('ガーラ・ヒルズ不動前','https://www.daikyo-anabuki.co.jp/building/TO00099418'),
    ('シェル品川戸越','https://www.arrival-net.co.jp/property/shell/shinagawa_togoshi/'),
    ('FLレジデンス品川I','https://www.roompia.jp/chintai/B403908251/'),
    ('PASEO武蔵小山II パセオ武蔵小山2','https://www.kokyuchintai.com/outline/14811/'),
    ('ミトラス荏原','https://www.shamaison.com/tokyo/area/13109/11514090/'),
    ('ラウレア大井町 ペット共生','https://www.nichiwa-realestate.co.jp/bkndetail/2851576023/'),
    # Public, named building profiles observed in failed Shinagawa-ward URLs.
    # Only URLs are seeded; no addresses, points or prices are embedded.
    ('SOLASIA residence 武蔵小山','https://www.milford-chintai.com/chintai/B55507703413592410000033436/'),
    ('SOLASIA residence 武蔵小山','https://lifullhomes-index.jp/buildings/b-48545496/'),
    ('ラヴィエール洗足','https://lifullhomes-index.jp/buildings/b-47627543/'),
    ('パークシティ武蔵小山 ザ タワー','https://www.daikyo-anabuki.co.jp/building/TO00117194'),
    ('パークシティ武蔵小山 ザ タワー','https://towers.select/tokyo/shinagawa-ku/park-city-musashikoyama-the-tower/'),
    ('シティタワー武蔵小山','https://www.daikyo-anabuki.co.jp/building/TO00119976'),
    ('シティタワー武蔵小山','https://lifullhomes-index.jp/buildings/b-44150350/'),
    # v148: first-hand named-property descriptions for v145 real failed URLs.
    # These are URLs ONLY; never save an address until the live named page,
    # exact SUUMO room map and government ABR number agree.
    ('Maison de Takaya','https://www.mitsui-chintai.co.jp/rf/tatemono/10818'),
    ('Maison de Takaya','https://lifullhomes-index.jp/buildings/b-48078267/'),
    ('ORSUS戸越銀座','https://www.arrival-net.co.jp/article/orsus/togoshi_ginza/outline.html'),
    ('ORSUS戸越銀座','https://www.kkf.co.jp/g/tky/orsus-togoshiginza/'),
    ('ルーブル目黒不動前','https://lifullhomes-index.jp/buildings/b-40751666/'),
    ('スパシエヴァロル品川荏原','https://library.smtrc.jp/mansion/757320.html'),
    ('ブランズ西小山','https://www.livable.co.jp/chintai/L826100153/'),
    ('the togoshiginza(ザトゴシギンザ)','https://www.property-bank.co.jp/bldg178990396/'),
)

# v149: URLs are seeds only. Address and official number are fetched and verified live.
_V149_REFERENCE_URLS = (('\u30a8\u30df\u30cd\u30f3\u30b9\u9ad8\u8f2a\u53f0', 'https://www.mitsui-chintai.co.jp/rf/tatemono/69515'), ('\u30a8\u30df\u30cd\u30f3\u30b9\u9ad8\u8f2a\u53f0', 'https://lifullhomes-index.jp/buildings/b-39619726/'), ('\u30a8\u30df\u30cd\u30f3\u30b9\u9ad8\u8f2a\u53f0', 'https://landwork-estate.co.jp/chintai/%E3%82%A8%E3%83%9F%E3%83%8D%E3%83%B3%E3%82%B9%E9%AB%98%E8%BC%AA%E5%8F%B0/'), ('AXAS\u30ec\u30b8\u30c7\u30f3\u30b9\u76ee\u9ed2\u6771', 'https://www.mitsui-chintai.co.jp/rf/tatemono/72816'), ('AXAS\u30ec\u30b8\u30c7\u30f3\u30b9\u76ee\u9ed2\u6771', 'https://www.srhome.co.jp/room/38755/'), ('AXAS RESIDENCE \u76ee\u9ed2\u6771', 'https://www.mitsui-chintai.co.jp/rf/tatemono/72816'), ('AXAS RESIDENCE \u76ee\u9ed2\u6771', 'https://www.srhome.co.jp/room/38755/'), ('\u30b3\u30f3\u30d5\u30a9\u30fc\u30eb\u6c60\u7530\u5c71', 'https://realestate.yahoo.co.jp/catalog/detail/41f0e4e8008bed6ded13518cb3cd40b4159d0ea5/'), ('\u30ac\u30ea\u30b7\u30a2\u5fa1\u6bbf\u5c71(\u30ac\u30ea\u30b7\u30a2\u30b4\u30c6\u30f3\u30e4\u30de)', 'https://lifullhomes-index.jp/buildings/b-40752253/'), ('\u30ac\u30ea\u30b7\u30a2\u5fa1\u6bbf\u5c71(\u30ac\u30ea\u30b7\u30a2\u30b4\u30c6\u30f3\u30e4\u30de)', 'https://asuxia.co.jp/outline/21298/'), ('SolanaTakanawadai', 'https://asuxia.co.jp/bukken/334403/'), ('GRAN PASEO\u5cf6\u6d25\u5c71East', 'https://www.arrival-net.co.jp/property/granpaseo/shimazuyama/outline.php'), ('GRAN PASEO\u5cf6\u6d25\u5c71East', 'https://lifullhomes-index.jp/buildings/b-46589198/'), ('\u30ac\u30ea\u30b7\u30a2\u5fa1\u6bbf\u5c71', 'https://lifullhomes-index.jp/buildings/b-40752253/'), ('\u30ac\u30ea\u30b7\u30a2\u5fa1\u6bbf\u5c71', 'https://asuxia.co.jp/outline/21298/'), ('\u30b0\u30e9\u30f3\u30d1\u30bb\u30aa\u5cf6\u6d25\u5c71', 'https://www.arrival-net.co.jp/property/granpaseo/shimazuyama/outline.php'), ('\u30b0\u30e9\u30f3\u30d1\u30bb\u30aa\u5cf6\u6d25\u5c71', 'https://lifullhomes-index.jp/buildings/b-46589198/'))
_V138_REFERENCE_URLS = _V138_REFERENCE_URLS + _V149_REFERENCE_URLS

_V138_REFERENCE_ALIASES = {
    'paseo武蔵小山iiパセオ武蔵小山2': ('パセオ武蔵小山Ⅱ','PASEO武蔵小山II'),
    'thetogoshiginzaザトゴシギンザ': ('the togoshiginza',),
}


def _v140_building_identity_token(value):
    """Normalize only confirmed advertising suffixes, never the identity core."""
    token=_v133_building_token(value)
    for suffix in ('ペット共生','ペット共生型','ペット可','ペット相談'):
        st=_v133_building_token(suffix)
        if token.endswith(st) and len(token)>len(st)+5:token=token[:-len(st)]
    return token


def _v149_references_only(identity):
    name=normal((identity or {}).get('building_name'))
    key=_v133_building_token(name)
    if len(key)<5 or _V137_GENERIC_BUILDING.search(name):return []
    urls=[]
    for label,url in _V149_REFERENCE_URLS:
        if _v133_building_token(label)==key and url not in urls:urls.append(url)
    return urls


def _v138_reference_urls(identity):
    """Do not send generic station/floor pseudo-names to unrelated buildings."""
    name=normal((identity or {}).get('building_name'))
    token=_v133_building_token(name)
    if len(token)<5 or _V137_GENERIC_BUILDING.search(name):return []
    core=_v140_building_identity_token(name)
    return [url for known,url in _V138_REFERENCE_URLS
            if _v133_building_token(known)==token or
            (len(core)>=7 and _v140_building_identity_token(known)==core)]


def _v138_profile_primary_address(soup,identity,region,source_url):
    """Extract the NAMED BUILDING's own address field from verified page structure.

    This function does not validate a residence: _v136_registry_confirmed does
    that separately using an exact official address record plus the actual room
    marker. Related-property cards and searches are not accepted as evidence.
    """
    host=urlparse(source_url).hostname or ''
    if host not in ('www.daikyo-anabuki.co.jp','www.arrival-net.co.jp',
                    'www.roompia.jp','www.kokyuchintai.com','www.shamaison.com',
                    'www.nichiwa-realestate.co.jp','www.milford-chintai.com',
                    'lifullhomes-index.jp','towers.select','www.kkf.co.jp',
                    'www.mitsui-chintai.co.jp','www.livable.co.jp','library.smtrc.jp',
                    'www.property-bank.co.jp'):
        return []
    if soup is None or not isinstance(region,dict):return []
    name=normal((identity or {}).get('building_name'))
    token=_v133_building_token(name)
    if len(token)<5 or _V137_GENERIC_BUILDING.search(name):return []
    aliases=(name,)+_V138_REFERENCE_ALIASES.get(token,())
    alias_tokens=[_v133_building_token(x) for x in aliases if len(_v133_building_token(x))>=5]
    if not alias_tokens:return []
    # First-page identification is required. A name only in related-properties
    # lower on the page is insufficient.
    primary_scope=(soup.find('main') or soup) if host=='www.nichiwa-realestate.co.jp' else soup
    prefix=' '.join(primary_scope.stripped_strings)[:2600]
    if not any(a in _v133_building_token(prefix[:1500]) for a in alias_tokens):return []
    heading_names=[_v133_building_token(x.get_text(' ',strip=True)) for x in soup.find_all('h1',limit=6)]
    # Every specialized parser below requires the *main building* H1. A nearby
    # listing containing the desired name only in its recommendations cannot qualify.
    named_h1=next((x for x in soup.find_all('h1',limit=6)
                   if any(a in _v133_building_token(x.get_text(' ',strip=True)) for a in alias_tokens)),None)
    if host=='www.daikyo-anabuki.co.jp':
        # Profiles publish the building's one address as p.address OR a plain p
        # in the introductory <ul>, before the first property overview <h2>.
        if named_h1 is None:return []
        found=[]
        for node in named_h1.next_elements:
            if getattr(node,'name',None)=='h2':break
            if getattr(node,'name',None)=='p':
                found+=_v136_external_addresses(node.get_text(' ',strip=True),region)
            if len(set(found))>1:return []
        return list(dict.fromkeys(found)) if len(set(found))==1 else []
    if host=='www.mitsui-chintai.co.jp':
        if named_h1 is None:return []
        matches=[]
        for table in soup.find_all('table',limit=12):
            for tr in table.find_all('tr',limit=50):
                th=tr.find('th');td=tr.find('td')
                if th is not None and td is not None and '所在地' in normal(th.get_text(' ',strip=True)):
                    matches+=_v136_external_addresses(td.get_text(' ',strip=True),region)
        for dl in soup.find_all('dl',limit=45):
            dt=dl.find('dt');dd=dl.find('dd')
            if dt is not None and dd is not None and '所在地' in normal(dt.get_text(' ',strip=True)):
                matches+=_v136_external_addresses(dd.get_text(' ',strip=True),region)
        return list(dict.fromkeys(matches)) if len(set(matches))==1 else []
    if host=='www.property-bank.co.jp':
        if named_h1 is None:return []
        matches=[]
        for tr in soup.find_all('tr',limit=220):
            th=tr.find('th');td=tr.find('td')
            if th and td and normal(th.get_text(' ',strip=True)).replace(' ','') in ('住所','所在地'):
                matches+=_v136_external_addresses(td.get_text(' ',strip=True),region)
        return list(dict.fromkeys(matches)) if len(set(matches))==1 else []
    if host=='www.livable.co.jp':
        if named_h1 is None:return []
        matches=[]
        for dl in soup.find_all('dl',limit=80):
            dt=dl.find('dt');dd=dl.find('dd')
            if dt and dd and '所在地' in normal(dt.get_text(' ',strip=True)):
                matches+=_v136_external_addresses(dd.get_text(' ',strip=True),region)
        return list(dict.fromkeys(matches)) if len(set(matches))==1 else []
    if host=='lifullhomes-index.jp':
        # Live building index HTML: <h1>name</h1><p>full address</p>.
        # The immediate sibling, not addresses in room cards or recommendations.
        if named_h1 is None:return []
        sibling=named_h1.find_next_sibling()
        if sibling is None or sibling.name!='p':return []
        found=_v136_external_addresses(sibling.get_text(' ',strip=True),region)
        return found if len(set(found))==1 else []
    if host=='www.milford-chintai.com':
        # First building overview, exact labelled <th>所在地</th><td>ADDRESS</td>.
        if named_h1 is None:return []
        found=[]
        for table in soup.find_all('table',limit=3):
            for tr in table.find_all('tr',limit=15):
                th=tr.find('th');td=tr.find('td')
                if th and td and normal(th.get_text(' ',strip=True)).replace(' ','')=='所在地':
                    found+=_v136_external_addresses(td.get_text(' ',strip=True),region)
            if found:break
        return list(dict.fromkeys(found)) if len(set(found))==1 else []
    if host=='towers.select':
        # The published building fact table explicitly labels a residence
        # address. Never use unlabeled street numbers or a second building.
        if named_h1 is None:return []
        found=[]
        for tr in soup.find_all('tr',limit=180):
            th=tr.find('th');td=tr.find('td')
            if not th or not td:continue
            if '所在地' in normal(th.get_text(' ',strip=True)) and '住居表示' in normal(th.get_text(' ',strip=True)):
                found+=_v136_external_addresses(td.get_text(' ',strip=True),region)
        return list(dict.fromkeys(found)) if len(set(found))==1 else []
    # Arrival's live page has an explicit strong '住所：' + adjacent DD, not
    # necessarily an H1; require its building name preceding that field.
    if host=='www.arrival-net.co.jp':
        found=[]
        for dl in soup.find_all('dl',limit=12):
            heading=dl.find(['strong','dt'])
            if heading is None or not re.search(r'住所|所在地|住居表示',heading.get_text(' ',strip=True)):continue
            dd=dl.find('dd')
            if dd is None:continue
            found+=_v136_external_addresses(dd.get_text(' ',strip=True),region)
            if len(found)>2:break
        return list(dict.fromkeys(found)) if len(set(found))==1 else []
    if host=='www.nichiwa-realestate.co.jp':
        # Real listing confirmed on 2026-10-10, including `物件名` and `所在地`
        # DL fields in its MAIN element. Never inspect nearby-property cards.
        main=soup.find('main') or soup
        headings=[_v133_building_token(h.get_text(' ',strip=True)) for h in main.find_all(['h1','h2'],limit=10)]
        if not any(any(a in h for a in alias_tokens) for h in headings):return []
        published_names=[];addresses=[]
        for dl in main.find_all('dl',limit=45):
            heading=dl.find(['strong','dt'])
            dd=dl.find('dd')
            if heading is None or dd is None:continue
            label=normal(heading.get_text(' ',strip=True)).replace(' ','')
            if label=='物件名':published_names.append(_v140_building_identity_token(dd.get_text(' ',strip=True)))
            if label in ('所在地','住所','住居表示'):
                addresses.extend(_v136_external_addresses(dd.get_text(' ',strip=True),region))
        expected_core=_v140_building_identity_token(name)
        if not published_names or expected_core not in published_names:return []
        return list(dict.fromkeys(addresses)) if len(set(addresses))==1 else []
    # Other vetted building-profile pages must have a single, labelled full
    # address in the first primary section, before lists of related properties.
    early=prefix[:2300]
    cutoff=re.search(r'周辺(?:の|物件)|近隣(?:の|物件)|こちらの物件も|同じ駅の物件',early)
    if cutoff:early=early[:cutoff.start()]
    found=[]
    for match in re.finditer(r'(?:所在地|住居表示|住所)\s*[:：]?\s*',early):
        found.extend(_v136_external_addresses(early[match.end():match.end()+140],region))
        if len(set(found))>1:return []
    return list(dict.fromkeys(found)) if len(set(found))==1 else []



_V142_RELATED_HEADING = re.compile(r'(?:周辺|近隣|類似|おすすめ|関連)(?:の|物件|マンション|建物|賃貸|一覧)|(?:新着|人気)物件')
_V142_ADDRESS_LABEL = re.compile(r'^(?:所在地(?:\(住所\))?|住所|住居表示|現住所|物件所在地|所在地住所)\s*[:：]?$')

def _v142_structured_primary_address(soup,identity,region):
    """Require a named H1 and a labelled, unique primary building address.

    Never take the agency office, floor plan, neighboring building, or search
    snippet as the subject property's address. Official registry validation
    remains a separate, mandatory step after this extraction.
    """
    name=normal((identity or {}).get('building_name'))
    core=_v140_building_identity_token(name)
    if len(core)<7 or _V137_GENERIC_BUILDING.search(name) or soup is None:return []
    main=soup.find('main') or soup
    match=None
    for h in main.find_all('h1',limit=6):
        hcore=_v140_building_identity_token(h.get_text(' ',strip=True))
        if hcore and (core==hcore or (len(core)>=9 and (hcore.startswith(core) or core.startswith(hcore)))):
            match=h;break
    if match is None:return []
    found=[];visited=0
    for node in match.next_elements:
        if not getattr(node,'name',None):continue
        visited+=1
        if visited>600:break
        if node.name=='h1':break
        if node.name in ('h2','h3') and _V142_RELATED_HEADING.search(normal(node.get_text(' ',strip=True))):break
        addr_text=None
        if node.name=='tr':
            head=node.find(['th'],recursive=False);value=node.find(['td'],recursive=False)
            if head and value and _V142_ADDRESS_LABEL.fullmatch(normal(head.get_text(' ',strip=True)).replace(' ','')):
                addr_text=value.get_text(' ',strip=True)
        elif node.name=='dl':
            head=node.find(['dt','strong'],recursive=False);value=node.find('dd',recursive=False)
            if head and value and _V142_ADDRESS_LABEL.fullmatch(normal(head.get_text(' ',strip=True)).replace(' ','')):
                addr_text=value.get_text(' ',strip=True)
        if addr_text:
            found+=_v136_external_addresses(addr_text,region)
            if len(set(found))>1:return []
    return list(dict.fromkeys(found)) if len(set(found))==1 else []


def _v142_identity_jsonld_addresses(soup,identity,region):
    """Only structured exact-building address where schema name == H1 identity.

    JSON-LD outside the building's main heading never qualifies on its own.
    """
    name=normal((identity or {}).get('building_name'))
    core=_v140_building_identity_token(name)
    if len(core)<7 or _V137_GENERIC_BUILDING.search(name) or soup is None:return []
    h1=soup.find('h1')
    if not h1 or core!=_v140_building_identity_token(h1.get_text(' ',strip=True)):return []
    matches=[]
    for script in soup.find_all('script',attrs={'type':re.compile(r'application/ld\+json',re.I)},limit=8):
        raw=(script.string or script.get_text(' ',strip=False) or '')
        if len(raw)>100_000:continue
        try:payload=json.loads(raw)
        except (ValueError,TypeError):continue
        roots=payload if isinstance(payload,list) else [payload]
        stack=list(roots);count=0
        while stack and count<40:
            node=stack.pop();count+=1
            if not isinstance(node,dict):continue
            if isinstance(node.get('@graph'),list):stack.extend(node['@graph'][:30])
            kind=node.get('@type') or ''
            if isinstance(kind,list):kind=' '.join(map(str,kind))
            if not re.search(r'(?:Building|Apartment|Residence|Accommodation|Place)',str(kind),re.I):continue
            if _v140_building_identity_token(node.get('name'))!=core:continue
            address=node.get('address')
            if isinstance(address,dict):
                addr=str(address.get('streetAddress') or '')
                locality=str(address.get('addressLocality') or '')
                area=str(address.get('addressRegion') or '')
                address=''.join((area,locality,addr)) if addr else ''
            elif not isinstance(address,str):address=''
            matches.extend(_v136_external_addresses(address,region))
    return list(dict.fromkeys(matches)) if len(set(matches))==1 else []


def _v142_identity_result_urls(links):
    """Discard redirects/contact pages that cannot provide a property address."""
    out=[]
    for link in links:
        parsed=urlparse(str(link or ''))
        if parsed.scheme!='https' or parsed.hostname not in _EXTERNAL_IDENTITY_HOSTS:continue
        if re.search(r'/(?:contact[_/-]|login|signin|search[/?]|inquiry|entryform)',parsed.path,re.I):continue
        if re.search(r'(?:\.pdf|\.jpg|\.png|\.webp)$',parsed.path,re.I):continue
        if link not in out:out.append(link)
    return out


def _v149_named_primary_address(soup, identity, region, source_url):
    if soup is None:return []
    allowed=[name for name,url in _V149_REFERENCE_URLS if source_url==url]
    if not allowed:return []
    requested=_v133_building_token((identity or {}).get('building_name'))
    names={_v133_building_token(name) for name in allowed}
    if requested not in names:return []
    titles=[normal(soup.title.get_text(' ',strip=True))] if soup.title else []
    headings=[h.get_text(' ',strip=True) for h in soup.find_all('h1',limit=5)]
    # A page about another building must not be accepted based on a sidebar.
    matching=[h for h in headings if any(name in _v133_building_token(h) for name in names)]
    if not matching and not any(name in _v133_building_token(t) for t in titles for name in names):
        return []
    candidates=[]
    for tr in soup.find_all('tr',limit=120):
        th=tr.find('th');td=tr.find('td')
        if th is not None and td is not None and re.search(r'^(?:所在地|住所|住居表示|物件所在地)',normal(th.get_text(' ',strip=True))):
            candidates.extend(_v136_external_addresses(td.get_text(' ',strip=True),region))
    for dl in soup.find_all('dl',limit=75):
        dt=dl.find('dt');dd=dl.find('dd')
        if dt is not None and dd is not None and re.search(r'^(?:所在地|住所|住居表示|物件所在地)',normal(dt.get_text(' ',strip=True))):
            candidates.extend(_v136_external_addresses(dd.get_text(' ',strip=True),region))
    # A verified named-building index may have the full address in a paragraph
    # immediately after the H1, without a separate address label.
    for h in soup.find_all('h1',limit=5):
        if not any(name in _v133_building_token(h.get_text(' ',strip=True)) for name in names):continue
        p=h.find_next_sibling('p')
        if p is not None:candidates.extend(_v136_external_addresses(p.get_text(' ',strip=True),region))
        break
    if not candidates:
        for h in soup.find_all('h1',limit=5):
            if not any(name in _v133_building_token(h.get_text(' ',strip=True)) for name in names):continue
            chunks=[];size=0
            for node in h.next_elements:
                if getattr(node,'name',None)=='h1':break
                if not isinstance(node,str) or getattr(node,'parent',None) is None:continue
                if getattr(node.parent,'name','') in ('script','style','template'):continue
                value=normal(node)
                if not value:continue
                size+=len(value)+1
                if size>3600:break
                chunks.append(value)
            paragraph=' '.join(chunks)
            boundary=re.search(r'(?:関連物件|周辺物件|この物件を取り扱う店舗|会社概要|お問い合わせ)',paragraph)
            if boundary:paragraph=paragraph[:boundary.start()]
            for match in re.finditer(r'(?:所在地|住居表示|住所)\s*[:：]?\s*',paragraph):
                candidates.extend(_v136_external_addresses(paragraph[match.end():match.end()+160],region))
            break
    choices=list(dict.fromkeys(candidates))
    return choices if len(choices)==1 else []

def _v136_building_address_pages(web,links,identity,region_code,munis,map_point,position_kind):
    """Match independent sources to one exact registered address, not a nearby guess.

    An official property owner's site needs one validated publication; other
    sites require agreement by two independent domains OR extra room facts.
    HTML and Google/Yahoo snippets are not persisted in app memory.
    """
    try:region=_registry_verified_point(web,map_point,munis,region_code)
    except (AppError,ValueError,TypeError):return None
    if not region or position_kind not in ('room_marker','image_pin','map_center'):return None
    region=dict(region,munis=munis)
    candidates={}
    for target in _v142_identity_result_urls(links)[:12]:
        host=urlparse(target).hostname or ''
        if host not in _EXTERNAL_IDENTITY_HOSTS:continue
        with web.cache_lock:
            blocked_until=getattr(web,'v140_external_host_cooldown',{}).get(host,0.)
        if time.monotonic()<blocked_until or _v144_source_pause_remaining(host)>0:
            # One audit event per blocked external host, not once per room.
            with web.cache_lock:
                noted=getattr(web,'v141_noted_blocked_hosts',None)
                if noted is None:noted=set();web.v141_noted_blocked_hosts=noted
                first=host not in noted
                if first:noted.add(host)
            if first:trace(web,'v140_external_host_cooldown',{'host':host,'decision':'next_source'},stage='address.fallback')
            continue
        # Cache only extracted facts, never entire remote HTML. Many SUUMO rooms
        # share one building; this prevents redundant external page downloads.
        page_key=(target,_v133_building_token(identity.get('building_name')),address_key(region['town']))
        with web.cache_lock:
            fact_cache=getattr(web,'v136_external_page_facts',None)
            if fact_cache is None:fact_cache={};web.v136_external_page_facts=fact_cache
            facts=fact_cache.get(page_key)
        if facts is None:
            try:
                if not web.permitted(target):continue
                if host=='www.homes.co.jp':web.local.stage1_identity_only=True
                try:reply=web.fetch(target)
                finally:
                    if host=='www.homes.co.jp':web.local.stage1_identity_only=False
                soup=BeautifulSoup(reply.text,'html.parser')
                page=' '.join(soup.stripped_strings)
            except SearchCancelled:raise
            except (AppError,ValueError,TypeError) as exc:
                status=(getattr(exc,'diagnostic',{}) or {}).get('status')
                if status in (403,405,429):
                    _v144_source_pause(host,status)
                    with web.cache_lock:
                        cooldown=getattr(web,'v140_external_host_cooldown',None)
                        if cooldown is None:cooldown={};web.v140_external_host_cooldown=cooldown
                        cooldown[host]=time.monotonic()+(600 if status in (403,405) else 1800)
                trace(web,'external_identity_page_failed',{'url':target,'exception_type':type(exc).__name__,
                    'status':status,'next_source':True},'WARNING','address.fallback')
                continue
            score,evidence=_identity_match_score(page,identity)
            special=(_v149_named_primary_address(soup,identity,region,target)
                     or _v138_profile_primary_address(soup,identity,region,target)
                     or _v142_structured_primary_address(soup,identity,region)
                     or _v142_identity_jsonld_addresses(soup,identity,region))
            if special and 'building_name' not in evidence:
                # A vetted URL does not prove a match by itself: the actual
                # publisher page must show this exact name near its primary
                # address field. _v138_profile_primary_address checks it.
                score+=4;evidence=list(evidence)+['building_name']
            primary=(special or _v137_primary_building_addresses(soup,identity,region)) if 'building_name' in evidence else []
            # Main-building address replaces, rather than mixes with, nearby
            # building addresses repeated in recommendations on the same page.
            options=(primary or _v136_external_addresses(page,region)) if 'building_name' in evidence else []
            facts=(score,tuple(evidence),tuple(options),tuple(primary))
            with web.cache_lock:
                if len(fact_cache)>=120:fact_cache.pop(next(iter(fact_cache)))
                fact_cache[page_key]=facts
        score,evidence,options,primary=facts
        if 'building_name' not in evidence or not options:continue
        for addr in options:
            row=candidates.setdefault(addr,dict(hosts=set(),urls=[],score=0,owner=False,primary_hosts=set()))
            row['hosts'].add(host);row['score']=max(row['score'],score)
            if target not in row['urls']:row['urls'].append(target)
            row['owner']=row['owner'] or host in _V136_OWNER_SITES
            if addr in primary:row['primary_hosts'].add(host)
    approved=[]
    for address,row in candidates.items():
        # Map centers are NOT building locations. Two independent pages must
        # identify this very building in their own PRIMARY location field.
        # Search snippets, owner-office addresses, or two rooms of one site
        # are not independent evidence. Do not relax exact-pin criteria.
        primary=bool(row['primary_hosts'])
        center_mode=(position_kind=='map_center')
        if center_mode:
            if len(row['primary_hosts'])<2 or len(row['hosts'])<2:continue
        elif not (row['owner'] or len(row['hosts'])>=2 or row['score']>=8 or primary):continue
        result=_v136_registry_confirmed(web,address,map_point,munis,region_code,position_kind,
                                       independently_corrobated_center=center_mode)
        if result:
            # One independent publisher is enough ONLY if its own building
            # overview explicitly identifies this address, the number is in
            # the registry, and the property marker is within 50 metres.
            # Otherwise the v136 multisource/extra-evidence gate still applies.
            old_gate=row['owner'] or len(row['hosts'])>=2 or row['score']>=8
            if center_mode:
                # Specific verification ID prevents downstream code from
                # silently interpreting an address point as a room marker.
                result['verification']='v144_two_independent_primary_addresses_plus_official_registry'
                result['pin_precision']='official_residential_address_point'
            if not center_mode and not old_gate and (not primary or result['distance_m']>_V137_PRIMARY_MAX_METERS):
                trace(web,'v137_primary_address_outside_pin',{'building_name':normal(identity.get('building_name')),
                    'distance_m':round(result['distance_m'],1),'limit_m':_V137_PRIMARY_MAX_METERS},
                    'WARNING','address.fallback')
                continue
            result.update(external_hosts=sorted(row['hosts']),external_urls=row['urls'][:4],
                          identity_evidence=['exact_h1_building_name','primary_location_field',
                                             'exact_registry_house_number'] if primary else
                          ['building_name','exact_registry_house_number'],
                          stage='search_identity')
            if primary and not center_mode:result['verification']='v137_primary_building_location_plus_official_registry'
            if center_mode:
                result['identity_evidence']=['two_independent_primary_building_name_matches',
                                             'two_identical_full_addresses','exact_official_registry_house_number',
                                             'SUUMO_published_map_center_only']
            approved.append(result)
    if len(approved)!=1:
        if len(approved)>1:
            trace(web,'v136_multiple_verified_addresses',{'building_name':normal(identity.get('building_name')),
                   'count':len(approved),'decision':'reject_ambiguous'},'WARNING','address.fallback')
        elif candidates:
            trace(web,'v136_external_registry_unconfirmed',{'building_name':normal(identity.get('building_name')),
                   'address_candidates':len(candidates),'domains':len({h for row in candidates.values() for h in row['hosts']})},'WARNING','address.fallback')
        return None
    result=approved[0]
    if len(result.get('external_hosts',()))>=2:
        trace(web,'v141_independent_building_sources_agreed',{'building_name':normal(identity.get('building_name')),
               'address':result['address'],'sources':result['external_hosts'],
               'distance_m':round(result['distance_m'],2)},stage='address.fallback')
    trace(web,'v136_exact_building_address_verified',{'building_name':normal(identity.get('building_name')),
          'address':result['address'],'distance_m':round(result['distance_m'],1),
          'sources':result['external_hosts']},stage='address.fallback')
    return result

def _v134_stage1_api_result_links(web,query):
    """Stage 1 search via an authorized API, no anonymous search-page scraping.

    Explicit owner consent is mandatory (API requests may be billable); return
    *only URLs* of original third-party pages. API snippets never verify addresses.
    This function does not change the strict address/identity tests that follow.
    """
    config=getattr(web,'stage1_search',{}) or {}
    provider=config.get('provider','off')
    if provider not in ('brave','google_legacy') or not config.get('api_key'):
        return []
    gate=getattr(web,'stage1_api_lock',None)
    if gate is None:
        web.stage1_api_lock=threading.RLock();web.stage1_api_uses=0
        web.stage1_api_cache={};web.stage1_api_disabled=False;gate=web.stage1_api_lock
    with gate:
        if query in web.stage1_api_cache:
            return list(web.stage1_api_cache[query])
        if web.stage1_api_disabled:
            return []
        limit=max(0,int(config.get('max_requests',0) or 0))
        if web.stage1_api_uses>=limit:
            trace(web,'stage1_api_budget_exhausted',{'provider':provider,'requests':web.stage1_api_uses,'limit':limit},stage='address.fallback')
            return []
        web.check_cancel()
        # Per-controller serialized request pacing; never rotate identities on 429.
        previous=float(getattr(web,'stage1_api_last_request',0.) or 0.)
        delay=max(0.,1.25-(time.monotonic()-previous)) if previous else 0.
        if delay:web.pause(delay)
        web.stage1_api_last_request=time.monotonic()
        # Count before sending; a rejected or timed-out call still consumes budget.
        web.stage1_api_uses+=1
        try:
            if provider=='brave':
                url='https://api.search.brave.com/res/v1/web/search'
                headers={'X-Subscription-Token':config['api_key'],'Accept':'application/json'}
                params={'q':query,'count':10,'country':'JP','search_lang':'ja'}
            else:
                # Google Custom Search JSON API: ONLY previously enabled clients.
                # Google closed new signups in 2026; no undocumented endpoints.
                url='https://www.googleapis.com/customsearch/v1'
                headers={'Accept':'application/json'}
                params={'key':config['api_key'],'cx':config['engine_id'],'q':query,'num':10,'gl':'jp','hl':'ja'}
            # The isolated request intentionally NEVER passes a secret through
            # PublicWeb.fetch, its URL logger, request trace, or audit exceptions.
            reply=requests.get(url,params=params,headers=headers,timeout=(4,12),allow_redirects=False)
            status=int(reply.status_code)
            if status!=200:
                if status in (401,403,429):web.stage1_api_disabled=True
                trace(web,'stage1_api_unavailable',{'provider':provider,'status':status,
                     'requests':web.stage1_api_uses,'disabled':web.stage1_api_disabled},'WARNING','address.fallback')
                return []
            if len(reply.content)>600_000:
                trace(web,'stage1_api_response_too_large',{'provider':provider},'WARNING','address.fallback')
                return []
            body=reply.json()
            rows=(body.get('web') or {}).get('results') if provider=='brave' else body.get('items')
            if not isinstance(rows,list):rows=[]
            links=[]
            for row in rows[:10]:
                if not isinstance(row,dict):continue
                target=str(row.get('url' if provider=='brave' else 'link') or '').strip()
                parsed=urlparse(target)
                if parsed.scheme!='https' or parsed.hostname not in _EXTERNAL_IDENTITY_HOSTS or parsed.username or parsed.password:
                    continue
                if target not in links:links.append(target)
            if len(web.stage1_api_cache)>=100:web.stage1_api_cache.pop(next(iter(web.stage1_api_cache)))
            web.stage1_api_cache[query]=list(links)
            trace(web,'stage1_search_api_result',{'provider':provider,'approved':True,
                 'total_results':len(rows),'approved_original_links':len(links),'requests':web.stage1_api_uses},stage='address.fallback')
            return links
        except SearchCancelled:raise
        except (requests.RequestException,ValueError,TypeError) as exc:
            # Never log exception messages: requests exceptions may embed the API
            # key in the request URL, and no token may enter diagnostic output.
            trace(web,'stage1_api_network_failure',{'provider':provider,'exception_type':type(exc).__name__},'WARNING','address.fallback')
            return []


# V147: Independent public building profiles for high-volume *failed* SUUMO URLs.
# These are URL seeds and map-locality gates, NOT hardcoded addresses to save.
# Every source must be fetched, verified to name this building, and its labelled
# residential address must match an exact government ABR house-number row.  The
# stored building coordinate comes ONLY from SUUMO's own room-marker/registry.
_V147_PROFILES = (
    dict(name='ロイジェント戸越銀座', station='戸越銀座駅', floors=8,
         approximate_map_pin=(35.6199143389183,139.713267503558), radius_m=22.,
         primary_hosts=('www.winspro.co.jp','www.arrival-net.co.jp'), sources=(
            'https://www.winspro.co.jp/db/%E3%83%AD%E3%82%A4%E3%82%B8%E3%82%A7%E3%83%B3%E3%83%88%E6%88%B8%E8%B6%8A%E9%8A%80%E5%BA%A7/',
            'https://www.arrival-net.co.jp/property/roygent/togoshi_ginza/')),
    dict(name='ラティエラ武蔵小山レジデンス', station='武蔵小山駅', floors=19,
         approximate_map_pin=(35.6165023956921,139.70663706572), radius_m=22.,
         primary_hosts=('www.mec-h.com','www.mitsui-chintai.co.jp'), sources=(
            'https://www.mec-h.com/building/musashikoyama/outline',
            'https://www.mitsui-chintai.co.jp/rf/tatemono/74606')),
)


def _v147_profile_published_addresses(soup,profile,region):
    """Named primary BUILDING content and a labelled full residential address."""
    if soup is None:return []
    expected=_v133_building_token(profile['name'])
    title=normal(soup.title.get_text(' ',strip=True)) if soup.title else ''
    meta=soup.find('meta',attrs={'property':'og:title'})
    title+=' '+normal(meta.get('content')) if meta else ''
    prefix=' '.join(soup.stripped_strings)[:5500]
    top_labels=' '.join(normal(x.get('alt')) for x in soup.find_all('img',limit=18))
    # The building name must identify the PAGE itself, not a lower-page listing.
    if not any(expected in _v133_building_token(q) for q in (title,prefix[:1000],top_labels)):
        return []
    snippets=[]
    # Primary overview table and definition lists; skip any secondary listing.
    for tr in soup.find_all('tr',limit=150):
        heading=tr.find('th');value=tr.find('td')
        if heading and value and re.search(r'^(?:所在地|住所|住居表示)(?:\s*[:：])?$',normal(heading.get_text(' ',strip=True))):
            snippets.append(value.get_text(' ',strip=True))
    for dl in soup.find_all('dl',limit=75):
        head=dl.find('dt');dd=dl.find('dd')
        if head and dd and re.search(r'住所|所在地|住居表示',normal(head.get_text(' ',strip=True))):
            snippets.append(dd.get_text(' ',strip=True))
    # Owner landing pages sometimes render an address as plain paragraphs.
    for m in re.finditer(r'(?:住所|住居表示|所在地)\s*[:：]\s*',prefix[:3800]):
        snippets.append(prefix[m.end():m.end()+170])
    candidate=set()
    for part in snippets:
        for address in _v136_external_addresses(part,region):candidate.add(address)
    if len(candidate)!=1:return []
    return list(candidate)


def _v147_generic_primary_proof(web,identity,map_point,munis,region_code,position_kind):
    """Conservative cross-source exact-building and ABR validation.

    One room's successful proof can be reused for other room pins on the SAME
    building; each reused pin is separately checked against the official point.
    This older independent exact-address fallback deliberately ignores the
    publisher text; the active v150 town-level path uses the published 所在地.
    """
    if position_kind!='room_marker' or not map_point:return None
    label=normal(identity.get('building_name'))
    if not _V137_GENERIC_BUILDING.search(label):return None
    m=re.search(r'(?<!\d)(\d{1,2})階建',label)
    if not m:return None
    age=normal(identity.get('raw_age'))
    if not ('新築' in label or '新築' in age or '築1年' in label or '築1年' in age):return None
    for profile in _V147_PROFILES:
        if int(m.group(1))!=profile['floors'] or profile['station'] not in label:continue
        if meters(map_point,profile['approximate_map_pin'])>profile['radius_m']:continue
        key=(str(region_code),profile['name'])
        with web.cache_lock:
            cache=getattr(web,'v147_proven_buildings',None)
            if cache is None:cache={};web.v147_proven_buildings=cache
            prior=cache.get(key)
        if prior is not None:
            ref=prior.get('reference_point')
            if ref and meters(map_point,ref)<=50 and meters(map_point,prior['original_pin'])<=profile['radius_m']:
                result=dict(prior['result']);result['distance_m']=meters(map_point,ref)
                result['verification']='v147_same_building_proven_registry_reuse'
                trace(web,'v147_verified_building_reused',{'building_name':profile['name'],
                      'distance_m':round(result['distance_m'],2)},stage='address.fallback')
                return result
            # Fail safe: never inherit the proof beyond the verified building.
            return None
        region=_registry_verified_point(web,map_point,munis,region_code)
        if not region:return None
        region=dict(region,munis=munis)
        expected_name=profile['name']
        sources={}
        for url in profile['sources']:
            host=urlparse(url).hostname or ''
            if host not in _EXTERNAL_IDENTITY_HOSTS:continue
            try:
                if not web.permitted(url):continue
                reply=web.fetch(url)
                html_soup=BeautifulSoup(reply.text,'html.parser')
                addresses=_v147_profile_published_addresses(html_soup,profile,region)
                if len(addresses)==1:sources.setdefault(addresses[0],set()).add(host)
            except SearchCancelled:raise
            except (AppError,requests.RequestException,ValueError,TypeError) as exc:
                trace(web,'v147_profile_http_error',{'host':host,'type':type(exc).__name__},
                      'WARNING','address.fallback')
        approved=[]
        for address,hosts in sources.items():
            if not any(h in hosts for h in profile['primary_hosts']) and len(hosts)<2:continue
            proof=_v136_registry_confirmed(web,address,map_point,munis,region_code,'room_marker')
            if not proof or proof.get('distance_m',999)>50:continue
            proof['verification']='v147_published_primary_exact_address_plus_official_registry'
            proof['external_hosts']=sorted(hosts)
            proof['external_urls']=[u for u in profile['sources'] if urlparse(u).hostname in hosts]
            proof['identity_evidence']=['map_exact_room_marker','generic_station_story_year',
                                        'independent_named_primary_building_page','ABR_exact_address_number']
            approved.append(proof)
        if len(approved)!=1:
            trace(web,'v147_profile_unverified',{'building_name':expected_name,'sources':len(sources),
                 'registry_matches':len(approved)},'WARNING','address.fallback')
            return None
        chosen=approved[0]
        with web.cache_lock:
            cache[key]={'result':dict(chosen),'reference_point':list(chosen['reference_point']),
                        'original_pin':list(map_point)}
        trace(web,'v147_exact_address_verified',{'building_name':profile['name'],
              'address':chosen['address'],'independent_sources':chosen['external_hosts'],
              'registry_distance_m':round(chosen['distance_m'],2)},stage='address.fallback')
        return chosen
    return None


def _cross_source_address_from_identity(web,identity,map_point,munis,region_code=None,
                                        position_kind='',expected_towns=None):
    """Stage 1 uses corroborated building address; stage 2 is strict map registry."""
    name=normal(identity.get('building_name'))
    blocked=False;robots_blocked=False;result_links=[];search_errors=[]
    if _V137_GENERIC_BUILDING.search(name):
        independently_proven=_v147_generic_primary_proof(web,identity,map_point,munis,
                                                          region_code,position_kind)
        if independently_proven:return independently_proven
    if len(name)>=3 and not _V137_GENERIC_BUILDING.search(name):
        # First retry the previously failed, real SUUMO building against
        # documented public building profiles. No address is hardcoded.
        references=_v138_reference_urls(identity)
        if references:
            trace(web,'v138_verified_source_urls_attempt',{'building_name':name,
                  'sources':references},stage='address.fallback')
            exact=_v136_building_address_pages(web,references,identity,region_code,munis,map_point,position_kind)
            if exact:
                trace(web,'v138_failed_url_now_address_verified',{'building_name':name,
                      'address':exact['address'],'source_urls':exact.get('external_urls',[])},stage='address.fallback')
                return exact
        # Search once per building/town per controller, not once per apartment room.
        # Query '住所' rather than the floorplan, which often excludes an owner's
        # property-outline page from web search results.
        query='"'+name+'" 東京都 住所'
        cache_key=(str(region_code or ''),_v133_building_token(name))
        # A per-building app-local lock prevents concurrent room workers from
        # issuing the same Google/Yahoo queries simultaneously.
        with web.cache_lock:
            gates=getattr(web,'v136_building_search_locks',None)
            if gates is None:gates={};web.v136_building_search_locks=gates
            gate=gates.setdefault(cache_key,threading.Lock())
            if len(gates)>200:
                for key,old_gate in list(gates.items()):
                    if key!=cache_key and not old_gate.locked():
                        gates.pop(key,None)
                        if len(gates)<=180:break
        with gate:
            with web.cache_lock:
                cache=getattr(web,'v136_building_search_cache',None)
                if cache is None:cache={};web.v136_building_search_cache=cache
                cached=cache.get(cache_key)
            if cached is not None:
                result_links=list(cached)
                trace(web,'v136_building_search_cache_hit',{'building_name':name,'links':len(result_links)},stage='address.fallback')
            else:
                result_links.extend(_v134_stage1_api_result_links(web,query))
                search_urls=(
                    'https://www.google.com/search?q='+quote(query),
                    'https://search.yahoo.co.jp/search?p='+quote(query),
                )
                for search_url in (() if result_links else search_urls):
                    host=urlparse(search_url).hostname
                    with web.cache_lock:
                        until=getattr(web,'identity_search_429',{}).get(host,0)
                    until=max(until,time.monotonic()+_v144_source_pause_remaining(host))
                    if not getattr(web,'stage1_direct',True):
                        blocked=True;robots_blocked=True
                        trace(web,'identity_search_direct_disabled',{'host':host,'next_stage':'address.stage2'},stage='address.fallback')
                        continue
                    if time.monotonic()<until:
                        blocked=True
                        trace(web,'identity_search_skipped_429',{'host':host,'reason':'cooldown','next_stage':'address.stage2'},stage='address.fallback')
                        continue
                    try:
                        trace(web,'stage1_direct_search_attempt',{'host':host,
                              'proxy_configured':bool(getattr(web,'stage1_http_proxies',[]))},stage='address.fallback')
                        reply=web.fetch(search_url)
                        search_title=response_title(reply.text)
                        if (urlparse(reply.url).path.startswith('/sorry/')
                                or re.search(r'(?:unusual traffic|verify you are human|captcha|automated requests|通常とは異なるトラフィック)',
                                             search_title+' '+reply.text[:5000],re.I)):
                            blocked=True
                            with web.cache_lock:
                                breakers=getattr(web,'identity_search_429',None)
                                if breakers is None:breakers={};web.identity_search_429=breakers
                                breakers[host]=max(breakers.get(host,0.),time.monotonic()+1800)
                            _v144_source_pause(host,429)
                            trace(web,'stage1_search_challenge',{'host':host,'status':reply.status_code,
                                  'decision':'stop_this_host_for_30min'},'WARNING','address.fallback')
                            continue
                        soup=BeautifulSoup(reply.text,'html.parser')
                        result_links.extend(x for x in _external_result_links(soup,reply.url) if x not in result_links)
                    except SearchCancelled:raise
                    except AppError as exc:
                        status=(getattr(exc,'diagnostic',{}) or {}).get('status')
                        search_errors.append({'host':host,'status':status,'exception_type':type(exc).__name__})
                        if status in (403,429):
                            blocked=True
                            _v144_source_pause(host,status)
                            with web.cache_lock:
                                breakers=getattr(web,'identity_search_429',None)
                                if breakers is None:breakers={};web.identity_search_429=breakers
                                breakers[host]=max(breakers.get(host,0.),time.monotonic()+(3600 if status==429 else 900))
                    if len(result_links)>=10:break
                with web.cache_lock:
                    if len(cache)>=120:cache.pop(next(iter(cache)))
                    cache[cache_key]=list(result_links[:12])
        # Search engines often return dead contact/marketing URLs. Treat them
        # as weak clues, not mandatory or repeated property-content requests.
        result_links=_v142_identity_result_urls(result_links)
        exact=_v136_building_address_pages(web,result_links,identity,region_code,munis,map_point,position_kind)
        if exact:return exact
    if _V137_GENERIC_BUILDING.search(name):
        trace(web,'v140_generic_building_identity',{'building_name':name,
            'decision':'skip_unreliable_web_search_use_official_registry'},stage='address.fallback')
    trace(web,'address_stage2_start',{'reason':'robots_disallowed' if robots_blocked else 'search_429' if blocked else 'no_verified_identity',
          'position_kind':position_kind,'primary_errors':search_errors,'search_result_links':len(result_links)},
          stage='address.stage2')
    registry=_registry_precise_address_from_map(web,map_point,position_kind,munis,region_code,expected_towns,identity)
    if registry:
        registry['stage']='official_registry'
        return registry
    trace(web,'external_identity_address_unresolved',{'building_name':name,
          'search_errors':search_errors,'result_links':result_links[:8],
          'candidate_count':0,'stage2':'official_registry_unresolved'},'WARNING','address.fallback')
    return None



def _v142_verified_building_key(identity,region_code):
    name=normal((identity or {}).get('building_name'))
    token=_v140_building_identity_token(name)
    if len(token)<9 or _V137_GENERIC_BUILDING.search(name) or not region_code:return None
    return str(region_code),token


def _v142_reuse_exact_building(web,identity,point,kind,munis,region_code,expected_towns):
    """Reuse ONLY an independently & officially verified address, same building.

    A room marker in a second listing must be within 20m of the source marker;
    actual official address number and reverse town must also remain identical.
    """
    key=_v142_verified_building_key(identity,region_code)
    if not key or kind!='room_marker':return None
    with web.cache_lock:
        row=getattr(web,'v142_verified_buildings',{}).get(key)
    if not row or meters(point,row['source_point'])>20:return None
    ref=row.get('reference_point')
    if not ref or meters(point,ref)>_V136_MAX_REGISTRY_DISTANCE_M:return None
    try:town=reverse(web,point,munis,force=True)
    except AppError:return None
    if not town or town['code']!=str(region_code):return None
    if not any(town_matches(t,town['town']) for t in (expected_towns or [town['town']])):return None
    if address_key(town['town'])!=address_key(row['town']):return None
    result={k:(list(v) if isinstance(v,list) else v) for k,v in row['result'].items()}
    result['distance_m']=meters(point,ref)
    result['verification']='v142_same_building_independent_registry_reuse'
    trace(web,'v142_reuse_verified_building',{'building_name':normal(identity.get('building_name')),
        'address':result.get('address'),'pin_distance_m':round(meters(point,row['source_point']),2)},stage='address.fallback')
    return result


def _v142_remember_exact_building(web,identity,point,kind,region_code,inferred):
    if kind!='room_marker' or not isinstance(inferred,dict):return
    key=_v142_verified_building_key(identity,region_code)
    reference=inferred.get('reference_point')
    official=inferred.get('official_region') or {}
    if not key or not reference or official.get('code')!=str(region_code):return
    if inferred.get('verification') not in (
        'external_exact_building_address_plus_official_registry',
        'v137_primary_building_location_plus_official_registry'):
        return
    if not _detailed_address(inferred.get('address')):return
    with web.cache_lock:
        cache=getattr(web,'v142_verified_buildings',None)
        if cache is None:cache={};web.v142_verified_buildings=cache
        if len(cache)>=200:cache.pop(next(iter(cache)),None)
        cache[key]={'source_point':list(point),'reference_point':list(reference),
                    'town':official['town'],'result':dict(inferred)}

def suumo_location_from_kankyo_image(web,detail_soup,detail_url,bounds,munis,expected_towns=None,region_code=None,identity=None):
    """Resolve SUUMO map position then infer address, with identity-only fallback."""
    identity=dict(identity or {})
    jnc=suumo_jnc_key(detail_url);bc=suumo_bc_id(detail_url)
    cache_key=('bc:'+bc) if bc else ('jnc:'+jnc if jnc else 'detail:'+detail_url)
    with web.cache_lock:cached=getattr(web,'suumo_location_cache',{}).get(cache_key)
    if cached:
        trace(web,'suumo_location_cache_hit',{'detail_url':detail_url,'cache_key':cache_key,'address':cached.get('inferred_address','')},stage='location')
        return dict(cached)
    targets=suumo_kankyo_urls(detail_soup,detail_url)
    if not targets:raise AppError('SUUMOの地図・周辺環境URLを確認できません。')
    fetched=[];lat=lng=None;method='';source='';failures=[];position_kind=''
    # 1) Exact published room marker.
    for number,target in enumerate(targets,1):
        web.check_cancel()
        if not web.permitted(target):failures.append({'url':target,'reason':'robots_not_permitted'});continue
        try:reply=web.fetch(target)
        except AppError as exc:failures.append({'url':target,'reason':str(exc)});continue
        soup=BeautifulSoup(reply.text,'html.parser');fetched.append((target,soup))
        point,meta=_suumo_room_marker_points(web,soup,target)
        if point:
            # Published marker coordinates are evidence, but a BC link may refer
            # to another room. Reject an obviously different ward/town and try
            # the next published SUUMO map before declaring the listing missing.
            try:marker_region=reverse(web,point,munis,force=True)
            except AppError:marker_region=None
            wrong_code=bool(marker_region and region_code and marker_region.get('code')!=str(region_code))
            wrong_town=bool(marker_region and expected_towns and not any(
                town_matches(t,marker_region.get('town','')) for t in expected_towns))
            if wrong_code or wrong_town:
                failures.append({'url':target,'reason':'published_marker_wrong_search_region',
                    'actual_code':marker_region.get('code'),'actual_town':marker_region.get('town')})
                trace(web,'v142_map_marker_wrong_area',{'url':target,'expected_code':region_code,
                    'actual_code':marker_region.get('code'),'actual_town':marker_region.get('town'),
                    'decision':'next_published_kankyo_map'},'WARNING','location')
                continue
            lat,lng=point;source=target;position_kind='room_marker';method='SUUMO地図・周辺環境の物件マーカー座標→詳細住所推定'
            if number>1:trace(web,'suumo_map_fallback_success',{'detail_url':detail_url,'map_url':target,'attempt':number,'attempted_urls':targets[:number]},stage='location')
            break
        failures.append({'url':target,'reason':'room_marker_not_unique_or_missing','meta':meta})
    # 2) Georeferenced static map image pin.
    if lat is None:
        for target,soup in fetched:
            candidates=map_image_candidates(web,soup,target);distinct={p[:2] for p in candidates if p[3]>=5}
            if len(distinct)==1:
                lat,lng=next(iter(distinct));source=target;position_kind='image_pin';method='SUUMO地図画像ピン認識→詳細住所推定';break
    # 3) Published Google-map center. This fixes listings with no js-gmapData and no
    # unique static-image pin; it is a hint, not proof of the exact building position.
    if lat is None:
        center_candidates=[]
        for target,soup in fetched:
            point,meta=_suumo_map_center_points(soup,target)
            if point:center_candidates.append((point,target,meta))
            else:failures.append({'url':target,'reason':'map_center_missing_or_ambiguous','meta':meta})
        unique={x[0] for x in center_candidates}
        if len(unique)==1:
            (lat,lng),source,meta=center_candidates[0];position_kind='map_center';method='SUUMO地図・周辺環境のGoogle地図中心座標→詳細住所照合'
            trace(web,'suumo_map_center_fallback',{'detail_url':detail_url,'map_url':source,'point':[lat,lng],'meta':meta},stage='location')
        elif len(unique)>1:
            # Prefer a center repeated across multiple property-specific map pages.
            counts={}
            for point,target,meta in center_candidates:counts[point]=counts.get(point,0)+1
            ranked=sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))
            if len(ranked)==1 or ranked[0][1]>ranked[1][1]:
                (lat,lng),_=ranked[0];source=next(t for p,t,m in center_candidates if p==(lat,lng));position_kind='map_center';method='SUUMO地図・周辺環境のGoogle地図中心座標→詳細住所照合'
    if lat is None:
        trace(web,'suumo_marker_unresolved',{'detail_url':detail_url,'candidate_map_urls':targets,'failures':failures,
              'reason':'物件マーカー・地図画像ピン・Google地図中心座標のいずれも取得できない'},'WARNING','location');return None
    if not in_rectangle((lat,lng),bounds):
        trace(web,'published_point_outside_bounds',{'map_url':source,'point':[lat,lng],'bounds':list(bounds),'retained':True,'reason':'検索範囲外でも照合を継続'},stage='location')

    # Exact marker/image pin may use the strict expected-town JHJ path. A map-center
    # hint can be hundreds of metres off, so do not reject it merely because reverse
    # geocoding returns another neighboring town; cross-source identity handles that.
    strict_towns=expected_towns if position_kind in ('room_marker','image_pin') else None
    external=None
    inferred=None
    if position_kind in ('room_marker','image_pin') and _v149_references_only(identity):
        external=_v136_building_address_pages(web,_v149_references_only(identity),identity,
                                               region_code,munis,(lat,lng),position_kind)
        if external:
            inferred=external
            trace(web,'v149_named_address_first',{'url':detail_url,'address':external['address'],
                 'verification':'live_named_publisher_plus_official_registry'},stage='address.fallback')
    if not inferred:
        inferred=map_point_to_residential_address(web,(lat,lng),munis,expected_code=region_code,expected_towns=strict_towns)
    if not inferred:
        external=_v142_reuse_exact_building(web,identity,(lat,lng),position_kind,
                                            munis,region_code,expected_towns)
        if not external:
            external=_cross_source_address_from_identity(web,identity,(lat,lng),munis,region_code,
                                                         position_kind=position_kind,expected_towns=expected_towns)
        inferred=external
    if external:
        _v142_remember_exact_building(web,identity,(lat,lng),position_kind,region_code,external)
    if not inferred:return None
    address=inferred['address']
    if not _detailed_address(address):return None
    external_used=bool(external)
    location={'latitude':lat,'longitude':lng,
              'location_method':(('SUUMO物件マーカー→OSM建物名・住所と公的住所番号を照合' if external.get('verification')=='osm_exact_named_building_plus_address_registry' else 'SUUMO物件地図の正確なマーカー→デジタル庁住所基盤データ照合' if external.get('stage')=='official_registry' else 'SUUMO物件マーカー→独立建物住所→公的住居表示番号と座標を照合' if external.get('verification') in ('external_exact_building_address_plus_official_registry','v137_primary_building_location_plus_official_registry','v142_same_building_independent_registry_reuse') else 'SUUMO物件固有地図→建物名等を独立ソース照合→GSI住所確認') if external_used else method),
              'map_address':address,'inferred_address':address,
              'address_match':(('建物名と番地を独立情報で照合（OSM＋住所レジストリ）' if external.get('verification')=='osm_exact_named_building_plus_address_registry' else '公式住所点との近接・一意性を確認（推定）' if external.get('stage')=='official_registry' else '外部掲載の建物名・詳細住所と公的住所番号・位置を照合' if external.get('verification') in ('external_exact_building_address_plus_official_registry','v137_primary_building_location_plus_official_registry','v142_same_building_independent_registry_reuse') else '独立ソースで建物同一性を照合しGSIで番地確認') if external_used else '地図座標から番地・住居番号相当まで推定'),
              'coordinate_precision':'building' if external_used else 'listing_map',
              'position_source_url':source,
              'address_precision':(('住居表示住所の公式点・近接一意性（建物一致は推定）' if external.get('stage')=='official_registry' else '外部掲載の住居表示住所＋公的住所番号・位置照合（推定）' if external.get('verification') in ('external_exact_building_address_plus_official_registry','v137_primary_building_location_plus_official_registry','v142_same_building_independent_registry_reuse') else '独立複数ソース照合＋GSI住所検索') if external_used else 'GSI住居表示・街区符号/基礎番号'),
              'address_distance_m':round(float(inferred.get('distance_m') or 0),2),
              'suumo_map_hint_latitude':lat,'suumo_map_hint_longitude':lng,
              'suumo_map_position_kind':position_kind,
              'address_reference_point':inferred.get('reference_point') if external_used else None}
    if external_used:
        location['address_identity_sources']=external.get('external_hosts',[]) or ([ADDRESS_REGISTRY_HOST] if external.get('stage')=='official_registry' else [])
        location['address_identity_evidence']=external.get('identity_evidence',[])
        if external.get('verification')=='v144_two_independent_primary_addresses_plus_official_registry':
            ref=external.get('reference_point')
            if not isinstance(ref,(list,tuple)) or len(ref)<2 or not has_point({'latitude':ref[0],'longitude':ref[1]}):return None
            location.update(latitude=float(ref[0]),longitude=float(ref[1]),
                location_method='SUUMO物件地図中心→独立した建物情報2サイトの同一詳細住所→公的住所点',
                address_match='独立2サイトの建物名・番地・住居番号と公式住所レジストリの完全一致',
                address_precision='住居表示の公式住所点（建物中心位置・部屋位置ではない）',
                coordinate_precision='official_residential_address_point',
                address_distance_m=round(float(external['distance_m']),2),
                verification='v144_two_independent_primary_addresses_plus_official_registry')
            trace(web,'v144_map_center_full_address_verified',{
                'detail_url':detail_url,'address':address,
                'source_hosts':external.get('external_hosts',[]),
                'map_center_distance_m':location['address_distance_m'],
                'coordinate_kind':'official_residential_address_point',
                'decision':'eligible_for_database_save_not_yet_saved'},stage='address.fallback')
    with web.cache_lock:
        if len(web.suumo_location_cache)>=400:web.suumo_location_cache.pop(next(iter(web.suumo_location_cache)))
        web.suumo_location_cache[cache_key]=dict(location)
    trace(web,'location_ok',{'source':'SUUMO物件固有地図','map_hint':[lat,lng],'map_derived_address':address,'external_identity_used':external_used,'location':location},stage='location')
    return location

def suumo_collect(web,region,bounds,munis,emit):
    """SUUMO: map towns -> town page/filter -> detail -> kankyo map -> compact save."""
    prepared=suumo_prepare_region(web,region);seen_urls=set();seen_ids=set()
    # The town search returns every chome. Save the full town, including off-screen properties.
    collection_towns=[town_base_name(region.get('town',''))]
    for group_number,layouts in enumerate(SUUMO_LAYOUT_GROUPS,1):
        filter_params=suumo_group_params(prepared,layouts,1)
        current_url=prepared['url'];current_params=filter_params;seen_pages=set();group_candidates=0;page_number=1
        emit('message',f'SUUMO条件{group_number}｜マンション｜築15年以内｜'+ '・'.join(layouts))
        while current_url:
            reply=web.fetch(current_url,params=current_params if current_params else None);soup=BeautifulSoup(reply.text,'html.parser')
            buildings=suumo_result_buildings(soup)
            if not buildings:
                if suumo_explicit_zero_results(soup):
                    trace(web,'empty',{'provider':'SUUMO','group':list(layouts),'page':page_number,
                          'url':reply.url,'reason':'公開検索結果に0件の明示表示'},stage='pagination')
                    break
                trace(web,'parse',{'provider':'SUUMO','group':list(layouts),'page':page_number,
                      'url':reply.url,'page_title':normal(soup.title.get_text(' ',strip=True)) if soup.title else '',
                      'reason':'物件カードも明示的な0件表示もない'},'ERROR','pagination')
                raise AppError(f'SUUMO 条件{group_number}・{page_number}ページ目の一覧を確認できません。')
            signature=hashlib.sha256(str(buildings).encode()).hexdigest()
            if signature in seen_pages:
                raise AppError('SUUMOのページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature);page_candidates=[];page_skipped=0;skip_examples=[]

            for building in buildings:
                building_name_hint=_v140_card_building_name(building)
                # Do not read the list-page address. Town scope has already been fixed by the SUUMO town selector.
                rooms=building.select('tr.js-cassette_link')
                if not rooms:
                    rooms=[x for x in building.select('tr') if x.select_one('a[href*="/chintai/"]')]
                for room in rooms:
                    links=[]
                    for a in room.select('a[href]'):
                        label=normal(a.get_text(' ',strip=True));url=urljoin('https://suumo.jp',a.get('href','')).split('#')[0]
                        if safe_url(url,'SUUMO') and ('詳細を見る' in label or re.search(r'/chintai/(?:bc_|jnc_)',urlparse(url).path)):
                            links.append((0 if '詳細を見る' in label else 1,url))
                    if not links:continue
                    url=min(links,key=lambda x:x[0])[1]
                    if url in seen_urls:continue
                    seen_urls.add(url)
                    group_candidates+=1
                    property_id=suumo_property_id(url)
                    if property_id and property_id in seen_ids:continue
                    if property_id:seen_ids.add(property_id)
                    with web.cache_lock:
                        claimed=getattr(web,'claimed_ids',None)
                        if claimed is None:claimed=set();web.claimed_ids=claimed
                        if property_id and property_id in claimed:continue
                        if property_id:claimed.add(property_id)
                    obtained=getattr(web,'acquired_ids',{}).get(property_id)
                    if obtained and recent_acquisition(obtained):
                        page_skipped+=1
                        if len(skip_examples)<3:skip_examples.append({'url':url,'property_id':property_id,'last_success_at':obtained})
                        emit('skipped',1);continue
                    page_candidates.append((url,building_name_hint))
            emit('candidate',len(page_candidates)+page_skipped)

            scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
            def detail_work(item):
                while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
                try:return detail_work_unbounded(item)
                finally:web.detail_slots.release()
            def detail_work_unbounded(item):
                web.check_cancel()
                index,(url,card_hint)=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                emit('message',f'条件{group_number} 詳細を見る {index}/{len(page_candidates)}件');emit('detail',1)
                try:
                    begin_listing(web,'SUUMO',url)
                    if not web.permitted(url):raise AppError('SUUMO詳細ページの自動取得が許可されていません。')
                    detail_reply=web.fetch(url);detail=BeautifulSoup(detail_reply.text,'html.parser');fields=suumo_detail_fields(detail)
                    original_name=fields.get('building_name','')
                    fields['building_name']=_v140_choose_suumo_name(original_name,card_hint)
                    if original_name!=fields['building_name']:
                        trace(web,'v140_suumo_card_building_name',{'url':url,'old':original_name,
                            'name':fields['building_name'],'proof':'SUUMO same-cassette title'},stage='address.fallback')
                    # SUUMO's labelled 所在地 is now the preferred town/chome source.
                    layout=parsed_layout(fields['raw_layout'],rough=False)
                    if parsed_layout(fields['raw_layout']) not in layouts:
                        reject_listing(web,'SUUMO',url,'detail.layout','condition' if layout else 'missing_data','間取りが検索条件外、または読み取れない',{'raw':fields['raw_layout'],'parsed':layout},list(layouts));emit('rejected',1);trace(web,'layout',{'url':url,'observed':fields['raw_layout'],'parsed':layout,'requested_group':list(layouts),'reason':'detail_layout_not_in_group'},'WARNING');return
                    if 'マンション' not in fields['building_type']:
                        reject_listing(web,'SUUMO',url,'detail.building_type','condition' if fields['building_type'] else 'missing_data','建物種別がマンションと確認できない',fields['building_type'],'マンション');emit('rejected',1);trace(web,'structure',{'url':url,'building_type':fields['building_type'],'reason':'not_mansion'},'WARNING');return
                    age,ym=age_info(fields['raw_age'])
                    if age is None or age>15:
                        reject_listing(web,'SUUMO',url,'detail.age','missing_data' if age is None else 'condition','築年数が15年以内と確認できない',{'raw':fields['raw_age'],'years':age},'15年以内');emit('rejected',1);trace(web,'age',{'url':url,'raw_age':fields['raw_age'],'age':age,'reason':'detail_age_not_confirmed_within_15'},'WARNING');return
                    rent=optional_amount(fields['raw_rent'])
                    if not rent:
                        reject_listing(web,'SUUMO',url,'detail.rent','missing_data','家賃を読み取れない',{'raw':fields['raw_rent'],'parsed':rent},'家賃（円）>0');emit('rejected',1);trace(web,'parse',{'url':url,'missing':'detail_rent','visible_head':fields['visible'][:400]},'WARNING');return

                    try:location=_v150_suumo_location(web,detail,url,bounds,munis,region,fields)
                    except AppError as exc:
                        location=None;trace(web,'location_pending',{'url':url,'message':str(exc),'source':'SUUMO掲載所在地・固有地図'},'WARNING','location')
                    address=location.get('inferred_address','') if location else ''
                    if not town_chome_key(address):
                        reject_listing(web,'SUUMO',url,'address.town_chome','location_failure',
                            'SUUMO所在地・物件地図とも対象の町丁目・正式町名を確認できない',location,
                            '掲載所在地または物件マーカーから町丁目・正式町名を特定');emit('rejected',1);return

                    # Rent, layout, inferred address and acquisition time survive Database.save_units().
                    row=partial_listing('SUUMO',url,'SUUMO掲載募集',address,layout,rent,None,None,region,bounds,location,None,age,ym,'')
                    if not row:reject_listing(web,'SUUMO',url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・地図で使える町丁目・物件URL');emit('rejected',1);return
                    row['property_id']=suumo_property_id(url)
                    row['address_source']=location['location_method'];row['address_quality_version']=4
                    emit('unit',row)
                except (AppError,ValueError,TypeError) as exc:
                    trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection');emit('rejected',1);emit('issue','SUUMO詳細確認｜'+str(exc));trace(web,'detail_optional_error',{'url':url,'message':str(exc)},'WARNING','collector')

            for item,_,error in bounded_results(list(enumerate(page_candidates,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
                if error:
                    url=item[1][0] if isinstance(item,tuple) and len(item)>1 and isinstance(item[1],tuple) else ''
                    trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'detail.worker',
                        'category':'acquisition_failure','reason':'parallel detail worker exception','exception_type':type(error).__name__,
                        'observed':{},'expected':'listing detail processing completes','evidence':[]},'ERROR','rejection')
                    emit('rejected',1);emit('issue','SUUMO detail worker | '+type(error).__name__)

            next_url=_suumo_next_url(soup,reply.url,filter_params)
            trace(web,'page_end',{'provider':'SUUMO','group':list(layouts),'page':page_number,'buildings':len(buildings),
                  'new_candidates':len(page_candidates),'already_acquired':page_skipped,'skip_examples':skip_examples,'next_url':next_url or ''},stage='pagination')
            if not next_url:break
            current_url=next_url;current_params=None;page_number+=1
        if not group_candidates:trace(web,'empty',{'provider':'SUUMO','region':region.get('label'),'group':list(layouts),'reason':'条件一覧に候補なし'},stage='collector')
    try:
        suumo_house_collect(web,region,bounds,munis,emit)
    except SearchCancelled:
        raise
    except (AppError,ValueError,TypeError) as exc:
        # Not complete: retain the failure and retry this town in a future run;
        # do not misreport house/other as a successful empty result.
        detail={'provider':'SUUMO','region':region.get('label',''),
                'category':'一戸建て・その他','reason':str(exc),
                'exception_type':type(exc).__name__,
                'diagnostic':getattr(exc,'diagnostic',{})}
        trace(web,'suumo_house_incomplete',detail,'ERROR','pagination')
        emit('issue','SUUMO一戸建て・その他の一覧確認失敗｜'+str(exc))
        emit('incomplete',1)


def suumo_house_collect(web,region,bounds,munis,emit):
    """SUUMO third building-type category: house/other, <=40y, >=50 sqm."""
    prepared=suumo_prepare_region(web,region);filter_params=dict(prepared['base_params']);filter_params.update(SUUMO_HOUSE_PARAMS)
    current_url=prepared['url'];current_params=filter_params;seen_pages=set();seen_urls=set();seen_ids=set();list_type_by_url={};list_name_by_url={};list_age_by_url={};page_number=1;found=0
    collection_towns=[town_base_name(region.get('town',''))];scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    emit('message','SUUMO条件4｜一戸建て・その他（アパート除外）｜築40年以内｜50㎡以上')
    trace(web,'suumo_building_type_filter',{'selected':'一戸建て・その他','ts':filter_params['ts'],
        'excluded':'アパート','age_years_checked_in_detail':HOUSE_MAX_AGE,
        'min_area_sqm':HOUSE_MIN_AREA},stage='search_conditions')
    while current_url:
        reply=web.fetch(current_url,params=current_params if current_params else None);current_params=None;soup=BeautifulSoup(reply.text,'html.parser')
        buildings=suumo_result_buildings(soup)
        if not buildings:
            if suumo_explicit_zero_results(soup):
                trace(web,'empty',{'provider':'SUUMO','group':'一戸建て・その他','page':page_number,
                      'url':reply.url,'reason':'公開検索結果に0件の明示表示'},stage='pagination')
                break
            trace(web,'parse',{'provider':'SUUMO','group':'一戸建て・その他','page':page_number,
                  'url':reply.url,'page_title':normal(soup.title.get_text(' ',strip=True)) if soup.title else '',
                  'reason':'物件カードも明示的な0件表示もない','requested_type_code':filter_params['ts']},'ERROR','pagination')
            raise AppError(f'SUUMO 一戸建て・その他・{page_number}ページ目の一覧を確認できません。')
        signature=hashlib.sha256(str(buildings).encode()).hexdigest()
        if signature in seen_pages:raise AppError('SUUMO一戸建てのページ送りが同じ一覧を返しました。全ページを確認できていません。')
        seen_pages.add(signature);page_candidates=[];page_skipped=0;skip_examples=[]
        for building in buildings:
            building_type_on_card=suumo_house_card_type(building)
            card_name=_v140_card_building_name(building)
            # SUUMO card's published 築年月 / 築N年, never infer age from map.
            card_age=''
            for age_node in building.select('.cassetteitem_detail-col3,.cassetteitem_content-body'):
                val=normal(age_node.get_text(' ',strip=True))
                match=re.search(r'(?:築\s*\d{1,3}\s*年|(?:19|20)\d{2}年\s*\d{1,2}月|新築)',val)
                if match:card_age=match.group(0);break
            rooms=building.select('tr.js-cassette_link') or [x for x in building.select('tr') if x.select_one('a[href*="/chintai/"]')]
            for room in rooms:
                links=[]
                for a in room.select('a[href]'):
                    label=normal(a.get_text(' ',strip=True));url=urljoin('https://suumo.jp',a.get('href','')).split('#')[0]
                    if safe_url(url,'SUUMO') and ('詳細を見る' in label or re.search(r'/chintai/(?:bc_|jnc_)',urlparse(url).path)):
                        links.append((0 if '詳細を見る' in label else 1,url))
                if not links:continue
                url=min(links,key=lambda x:x[0])[1]
                list_type_by_url[url]=building_type_on_card
                list_name_by_url[url]=card_name
                list_age_by_url[url]=card_age
                if url in seen_urls:continue
                seen_urls.add(url);property_id=suumo_property_id(url)
                if property_id and property_id in seen_ids:continue
                if property_id:seen_ids.add(property_id)
                with web.cache_lock:
                    claimed=getattr(web,'claimed_ids',None)
                    if claimed is None:claimed=set();web.claimed_ids=claimed
                    if property_id and property_id in claimed:continue
                    if property_id:claimed.add(property_id)
                obtained=getattr(web,'acquired_ids',{}).get(property_id)
                if obtained and recent_acquisition(obtained):
                    page_skipped+=1
                    if len(skip_examples)<3:skip_examples.append({'url':url,'property_id':property_id,'last_success_at':obtained})
                    emit('skipped',1);continue
                page_candidates.append(url)
        emit('candidate',len(page_candidates)+page_skipped);found+=len(page_candidates)
        def detail_work(item):
            while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
            try:
                index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                emit('message',f'SUUMO一戸建て 詳細を見る {index}/{len(page_candidates)}件');emit('detail',1)
                try:
                    begin_listing(web,'SUUMO',url)
                    if not web.permitted(url):raise AppError('SUUMO一戸建て詳細ページの自動取得が許可されていません。')
                    detail_reply=web.fetch(url);detail=BeautifulSoup(detail_reply.text,'html.parser');fields=suumo_detail_fields(detail)
                    fields['building_name']=_v140_choose_suumo_name(fields.get('building_name',''),list_name_by_url.get(url,''))
                    if age_info(fields['raw_age'])[0] is None and age_info(list_age_by_url.get(url,''))[0] is not None:
                        fields['raw_age']=list_age_by_url[url]
                        trace(web,'v142_house_card_age',{'url':url,'source':'same_suumo_cassette',
                            'raw_age':fields['raw_age']},stage='collector')
                    layout=parsed_layout(fields['raw_layout'],rough=False)
                    if not layout:
                        reject_listing(web,'SUUMO',url,'detail.layout','missing_data','一戸建ての間取りを読み取れない',fields['raw_layout'],'間取り');emit('rejected',1);return
                    type_text=normal(fields.get('building_type')) or list_type_by_url.get(url,'')
                    if not suumo_house_other_type(type_text):
                        reject_listing(web,'SUUMO',url,'detail.building_type',
                            'condition' if type_text else 'missing_data',
                            '建物種別が「一戸建て・その他」と確認できない（アパート除外）',
                            type_text,'一戸建て・その他');emit('rejected',1);return
                    age,ym=age_info(fields['raw_age'])
                    if age is None or age>HOUSE_MAX_AGE:
                        reject_listing(web,'SUUMO',url,'detail.age','missing_data' if age is None else 'condition','築年数が40年以内と確認できない',{'raw':fields['raw_age'],'years':age},'40年以内');emit('rejected',1);return
                    area=_identity_area(fields.get('raw_area'))
                    if area is None or area<HOUSE_MIN_AREA:
                        reject_listing(web,'SUUMO',url,'detail.area','missing_data' if area is None else 'condition','面積が50㎡以上と確認できない',{'raw':fields.get('raw_area'),'sqm':area},'50㎡以上');emit('rejected',1);return
                    rent=optional_amount(fields['raw_rent'])
                    if not rent:
                        reject_listing(web,'SUUMO',url,'detail.rent','missing_data','家賃を読み取れない',{'raw':fields['raw_rent'],'parsed':rent},'家賃（円）>0');emit('rejected',1);return
                    try:location=_v150_suumo_location(web,detail,url,bounds,munis,region,fields)
                    except AppError as exc:
                        location=None;trace(web,'location_pending',{'url':url,'message':str(exc),'source':'SUUMO一戸建て地図・周辺環境'},'WARNING','location')
                    address=location.get('inferred_address','') if location else ''
                    if not town_chome_key(address):
                        reject_listing(web,'SUUMO',url,'address.town_chome','location_failure',
                            '一戸建ての掲載所在地・物件マーカーから町丁目・正式町名を特定できない',location,
                            '地図上の町丁目');emit('rejected',1);return
                    row=partial_listing('SUUMO',url,'SUUMO一戸建て掲載募集',address,layout,rent,None,area,region,bounds,location,'一戸建て',age,ym,'',dwelling_type='house')
                    if not row:
                        reject_listing(web,'SUUMO',url,'listing.validation','invalid_data','一戸建ての保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'area':area,'address':address},'家賃・間取り・50㎡以上・地図で使える町丁目');emit('rejected',1);return
                    row['property_id']=suumo_property_id(url);row['address_source']=location['location_method'];row['address_quality_version']=4;emit('unit',row)
                except (AppError,ValueError,TypeError) as exc:
                    trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'house.detail','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__},'ERROR','rejection')
                    emit('rejected',1);emit('issue','SUUMO一戸建て詳細確認｜'+str(exc))
            finally:web.detail_slots.release()
        for item,_,error in bounded_results(list(enumerate(page_candidates,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
            if error:
                url=item[1] if isinstance(item,tuple) and len(item)>1 else ''
                trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'house.detail.worker','category':'acquisition_failure','reason':'parallel detail worker exception','exception_type':type(error).__name__},'ERROR','rejection')
                emit('rejected',1);emit('issue','SUUMO一戸建て detail worker | '+type(error).__name__)
        next_url=_suumo_next_url(soup,reply.url,filter_params)
        trace(web,'page_end',{'provider':'SUUMO','group':'一戸建て','page':page_number,'buildings':len(buildings),'new_candidates':len(page_candidates),'already_acquired':page_skipped,'skip_examples':skip_examples,'next_url':next_url or ''},stage='pagination')
        if not next_url:break
        current_url=next_url;page_number+=1
    if not found:trace(web,'empty',{'provider':'SUUMO','region':region.get('label'),'group':'一戸建て・その他','reason':'築40年以内・50㎡以上の候補なし'},stage='collector')


def provider_city_name(region,munis):
    row=munis.get(str(region.get('code',''))) if isinstance(munis,dict) else None
    if isinstance(row,(list,tuple)) and len(row)>=3:return str(row[2])
    label=normal(region.get('label',''));town=normal(region.get('town',''))
    prefix=label[:-len(town)] if town and label.endswith(town) else label
    return re.sub(r'^(?:東京都|神奈川県|千葉県|埼玉県)','',prefix)


def town_base_name(town):
    value=normal(town).replace('　','').replace(' ','')
    value=re.sub(r'(?:[0-9０-９一二三四五六七八九十]+丁目.*)$','',value)
    return value or normal(town)


def provider_root(provider,region):
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('追加取得元は東京都・神奈川県・千葉県・埼玉県で利用できます。')
    roots={
        'スマイティ':f'https://sumaity.com/chintai/{alias}/',
        'カナリー':f'https://web.canary-app.jp/chintai/{alias}/areas/',
        'アットホーム':f'https://www.athome.co.jp/chintai/{alias}/',
        'CHINTAI':f'https://www.chintai.net/{alias}/',
        'Comfy':'https://comfy.maison/',
        'アパマンショップ':f'https://www.apamanshop.com/{alias}/',
    }
    if provider not in roots:raise AppError(provider+'の公開検索入口を確認できません。')
    return roots[provider]


def associated_label_text(soup,field):
    texts=[]
    fid=field.get('id')
    if fid:
        label=soup.find('label',attrs={'for':fid})
        if label:texts.append(label.get_text(' ',strip=True))
    parent=field.find_parent('label')
    if parent:texts.append(parent.get_text(' ',strip=True))
    for node in (field.previous_sibling,field.next_sibling):
        if getattr(node,'get_text',None):texts.append(node.get_text(' ',strip=True))
        elif isinstance(node,str):texts.append(node)
    if field.name=='option':texts.append(field.get_text(' ',strip=True))
    texts.extend([str(field.get('aria-label') or ''),str(field.get('data-label') or ''),str(field.get('title') or '')])
    return ' '.join(x for x in texts if x)


def discover_choice(soup,label,name_hint=None):
    wanted=address_key(label)
    best=None
    for field in soup.select('input[value],option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '') or ''
        if name_hint and not re.search(name_hint,name,re.I):continue
        value=str(field.get('value','')).strip()
        if not value:continue
        observed=address_key(associated_label_text(soup,field))
        if not observed:continue
        score=3 if observed==wanted else 2 if wanted and wanted in observed else 1 if observed and observed in wanted else 0
        if score and (best is None or score>best[0]):best=(score,name,value)
    if best:return best[1],best[2]
    raw=str(soup)
    escaped=re.escape(normal(label))
    uuid=r'([0-9a-f]{8}-[0-9a-f-]{27,36})'
    patterns=[
        rf'["\'](?:name|label|title)["\']\s*:\s*["\']{escaped}["\'].{{0,300}}?["\'](?:id|value)["\']\s*:\s*["\']{uuid}["\']',
        rf'["\'](?:id|value)["\']\s*:\s*["\']{uuid}["\'].{{0,300}}?["\'](?:name|label|title)["\']\s*:\s*["\']{escaped}["\']',
    ]
    for pat in patterns:
        m=re.search(pat,raw,re.I|re.S)
        if m:return ('cityIds' if '区' in label or '市' in label or '郡' in label else 'areaIds'),m.group(1)
    return None


def find_named_link(soup,base_url,label,host=None,prefer=()):
    wanted=address_key(label);choices=[]
    for a in soup.select('a[href]'):
        href=urljoin(base_url,html.unescape(str(a.get('href') or '')))
        u=urlparse(href)
        if u.scheme!='https' or host and u.hostname!=host:continue
        observed=address_key(a.get_text(' ',strip=True))
        if not observed:continue
        score=0
        if observed==wanted:score=100
        elif wanted and (observed.startswith(wanted) or wanted in observed):score=80
        elif observed and observed in wanted:score=60
        if not score:continue
        score+=sum(5 for p in prefer if re.search(p,u.path+'?'+u.query,re.I))
        if re.search(r'/souba/|/rent/|/sitemap/|/theme/',u.path):score-=20
        choices.append((score,href))
    return max(choices,key=lambda x:x[0])[1] if choices else None


def provider_region_request(web,provider,region,munis,requested):
    provider=canonical_provider(provider);city=provider_city_name(region,munis);town=town_base_name(region.get('town',''))
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('対象都県を確認できません。')
    cache_key=('region_request',provider,str(region.get('code')),town,requested if provider in ('カナリー',) else None)
    with web.provider_lock:
        cached=web.provider_cache.get(cache_key)
    if cached:return dict(cached)

    if provider=='CHINTAI':
        # CHINTAI exposes municipality pages and town links; never substitute the city page for a town search.
        city_url=f'https://www.chintai.net/{alias}/area/{region["code"]}/list/'
        if not web.permitted(city_url):raise AppError('CHINTAIの地域ページの自動取得が許可されていません。')
        soup=BeautifulSoup(web.fetch(city_url).text,'html.parser')
        region_url=find_named_link(soup,city_url,town,'www.chintai.net',('area','list'))
        if not region_url:
            raise AppError('CHINTAIで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='スマイティ':
        root=provider_root(provider,region);host=PROVIDER_HOST[provider]
        if not web.permitted(root):raise AppError('スマイティの地域検索入口の自動取得が許可されていません。')
        root_soup=BeautifulSoup(web.fetch(root).text,'html.parser')
        city_url=find_named_link(root_soup,root,city,host,('chintai','list','area'))
        if not city_url:raise AppError('スマイティで'+city+'の公開地域ページを確認できません。')
        if not web.permitted(city_url):raise AppError('スマイティの市区町村ページの自動取得が許可されていません。')
        city_soup=BeautifulSoup(web.fetch(city_url).text,'html.parser')
        region_url=find_named_link(city_soup,city_url,town,host,('chintai','zip','area'))
        if not region_url:
            raise AppError('スマイティで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='アットホーム':
        # AtHome may reject automated list requests. Discover only public links; do not invent internal endpoints.
        market_index=f'https://www.athome.co.jp/chintai/souba/{alias}/city/'
        if not web.permitted(market_index):raise AppError('アットホームの公開地域入口の自動取得が許可されていません。')
        market_soup=BeautifulSoup(web.fetch(market_index).text,'html.parser')
        market_city=find_named_link(market_soup,market_index,city,'www.athome.co.jp',('souba',))
        if not market_city:raise AppError('アットホームで'+city+'の公開地域ページを確認できません。')
        slug_match=re.search(r'/chintai/souba/'+re.escape(alias)+r'/([^/]+)/?',urlparse(market_city).path)
        if not slug_match:raise AppError('アットホームの市区町村URLを確認できません。')
        list_url=f'https://www.athome.co.jp/chintai/{alias}/{slug_match.group(1)}/list/'
        if not web.permitted(list_url):raise AppError('アットホームの物件一覧の自動取得が許可されていません。')
        list_soup=BeautifulSoup(web.fetch(list_url).text,'html.parser')
        region_url=find_named_link(list_soup,list_url,town,'www.athome.co.jp',('chintai','list','area'))
        if not region_url:
            raise AppError('アットホームで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='アパマンショップ':
        # Current public area URLs use the municipality-code suffix (e.g. 13109 -> /tokyo/109/).
        suffix=str(region.get('code',''))[-3:]
        city_url=f'https://www.apamanshop.com/{alias}/{suffix}/'
        if not web.permitted(city_url):raise AppError('アパマンショップの市区町村一覧の自動取得が許可されていません。')
        # No stable public town-only URL is assumed. Candidate cards are filtered by their explicit address before detail fetch.
        request={'url':city_url,'params':None,'precise':False,'filter_context_town':True}

    elif provider=='カナリー':
        root=provider_root(provider,region)
        if not web.permitted(root):raise AppError('カナリーの地域選択ページの自動取得が許可されていません。')
        soup=BeautifulSoup(web.fetch(root).text,'html.parser')
        city_choice=discover_choice(soup,city,r'city')
        if not city_choice:raise AppError('カナリーで'+city+'の地域IDを公開ページから確認できません。')
        city_name,city_value=city_choice
        params={city_name:city_value,'isTotalRent':'false','page':'1','prefectureAlias':alias,'searchType':'1','sortType':'5'}
        if requested in LAYOUTS:params['layouts']=requested
        selected=BeautifulSoup(web.fetch(root,params=params).text,'html.parser')
        town_choice=discover_choice(selected,town,r'(area|town|district)')
        if not town_choice:
            raise AppError('カナリーで'+town+'の町域IDを確認できません。市区町村全体を代用しません。')
        params[town_choice[0]]=town_choice[1]
        request={'url':root,'params':params,'precise':True}

    elif provider=='Comfy':
        # Comfy's public location URLs combine a Kanto group, municipality codes and the town master ID.
        # Build a candidate only from the public Geolonia/ABR town master, then fetch it and verify the page title/text.
        muni=munis.get(str(region.get('code',''))) if isinstance(munis,dict) else None
        if not isinstance(muni,(list,tuple)) or len(muni)<3:
            raise AppError('Comfyの地域URL確認に必要な市区町村情報がありません。')
        pref_name=str(muni[1]);city_name=str(muni[2]);code=str(region.get('code',''))
        town_data=catalog_json(web,'https://japanese-addresses-v2.geoloniamaps.com/api/ja/'+quote(pref_name,safe='')+'/'+quote(city_name,safe='')+'.json')
        exact=[];base=[]
        wanted=address_key(region.get('town',''));wanted_base=address_key(town)
        for row in town_data.get('data',[]):
            if not isinstance(row,dict):continue
            mid=str(row.get('machiaza_id') or '')
            if len(mid)<4 or not mid[:4].isdigit():continue
            label=''.join(str(row.get(k) or '') for k in ('oaza_cho','chome','koaza'))
            key=address_key(label);oaza=address_key(row.get('oaza_cho',''))
            if key==wanted:exact.append(mid[:4])
            if oaza==wanted_base:base.append(mid[:4])
        town_code=next(iter(dict.fromkeys(exact or base)),None)
        if not town_code:
            raise AppError('Comfy用の町域コードを公開住所データから確認できません。')
        # Designated-city wards use their parent city code in the first segment; Tokyo special wards do not.
        parent=code
        if str(region.get('pref')) in ('11','12','14') and '市' in city_name and '区' in city_name and len(code)==5:
            parent=code[:3]+'00'
        region_url=f'https://comfy.maison/location/3-{parent}-{code[-3:]}-{town_code}'
        if not web.permitted(region_url):raise AppError('Comfyの町域ページの自動取得が許可されていません。')
        reply=web.fetch(region_url);page=normal(BeautifulSoup(reply.text,'html.parser').get_text(' ',strip=True))
        if address_key(city_name) not in address_key(page) or wanted_base not in address_key(page):
            raise AppError('Comfyの公開location URLを検証できません。未確認の地域コードは使用しません。')
        request={'url':region_url,'params':None,'precise':True}

    else:raise AppError(provider+'の検索方式がありません。')

    with web.provider_lock:
        if len(web.provider_cache)>=96:web.provider_cache.pop(next(iter(web.provider_cache)))
        web.provider_cache[cache_key]=dict(request)
    trace(web,'provider_region_discovered',{'provider':provider,'region':region.get('label'),'city':city,'town':town,'request':request},stage='search_conditions')
    return request

def generic_next_page(soup,current_url,provider):
    host=PROVIDER_HOST.get(provider);candidates=[]
    for a in soup.select('a[href]'):
        label=normal(a.get_text(' ',strip=True)).upper()
        if not (re.search(r'次(?:の)?(?:\d+件|ページ|へ)|NEXT',label,re.I) or label in ('›','»','>','≫')):continue
        target=urljoin(current_url,html.unescape(str(a.get('href') or '')));u=urlparse(target)
        if u.scheme=='https' and u.hostname==host and target!=current_url:candidates.append(target)
    return candidates[0] if candidates else None


def _candidate_context(anchor,max_levels=7):
    parts=[];node=anchor
    for _ in range(max_levels):
        node=getattr(node,'parent',None)
        if node is None:break
        if getattr(node,'name',None) in ('body','html'):break
        try:text=node.get_text(' ',strip=True)
        except Exception:text=''
        if text:parts.append(text)
        if getattr(node,'name',None) in ('article','li','tr'):break
    return normal(' '.join(parts))


def detail_link_candidates(soup,base_url,provider,town=None,require_town_context=False):
    provider=canonical_provider(provider);host=PROVIDER_HOST[provider];out=[];seen=set()
    strong={
        'スマイティ':r'^/chintai/[^/]*_prop/prop_\d+/?$',
        'カナリー':r'^/chintai/(?:rooms?|buildings?|properties?)/[0-9a-f-]+/?$',
        'アットホーム':r'^/chintai/\d+/?$',
        'CHINTAI':r'^/detail/bk-[A-Za-z0-9_-]+/?$',
        'Comfy':r'^/(?:properties|rooms|rentals)/[^/]+/?$',
        'アパマンショップ':r'^/(?:tokyo|kanagawa|saitama|chiba)/\d{2,3}/b\d+/\d+/?$',
    }.get(provider,r'')
    wanted=address_key(town or '')
    for a in soup.select('a[href]'):
        url=urljoin(base_url,html.unescape(str(a.get('href') or ''))).split('#')[0];u=urlparse(url)
        if url in seen or u.scheme!='https' or u.hostname!=host or not safe_url(url,provider):continue
        label=normal(a.get_text(' ',strip=True));context=_candidate_context(a);path=u.path
        if re.search(r'/souba/|/sitemap/|/theme/|/rent/|/areas?/?$|/stations?/',path,re.I):continue
        is_strong=bool(strong and re.search(strong,path,re.I))
        contextual=bool(re.search(r'詳細|物件|部屋|賃貸',label)) and bool(re.search(r'\d+(?:\.\d+)?\s*万円|\d{4,}\s*円',context))
        if not (is_strong or contextual):continue
        if require_town_context and wanted:
            if not (town_matches(town,context) or wanted in address_key(context)):
                continue
        seen.add(url);out.append(url)
    return out

def generic_labeled(soup,labels):
    value=labeled(soup,tuple(labels))
    if value:return value
    wanted={normal(x).replace(' ','') for x in labels}
    for row in soup.select('tr'):
        cells=row.find_all(['th','td'],recursive=False)
        if len(cells)>=2 and normal(cells[0].get_text(' ',strip=True)).replace(' ','') in wanted:
            return cells[1].get_text(' ',strip=True)
    for dt in soup.select('dt'):
        if normal(dt.get_text(' ',strip=True)).replace(' ','') in wanted:
            dd=dt.find_next_sibling('dd')
            if dd:return dd.get_text(' ',strip=True)
    return ''


def json_listing_fields(soup):
    out={}
    for script in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.get_text())
        except Exception:continue
        for node in json_nodes(data):
            if not isinstance(node,dict):continue
            if not out.get('title') and node.get('name') and node.get('@type') not in ('Organization','RealEstateAgent','LocalBusiness'):
                out['title']=str(node.get('name'))
            address=node.get('address')
            if isinstance(address,dict) and not out.get('address'):
                out['address']=''.join(str(address.get(k,'') or '') for k in ('addressRegion','addressLocality','streetAddress'))
            offer=node.get('offers')
            if isinstance(offer,dict) and not out.get('rent'):out['rent']=offer.get('price')
            size=node.get('floorSize')
            if isinstance(size,dict) and not out.get('area'):out['area']=size.get('value')
            props=node.get('additionalProperty')
            if isinstance(props,list):
                for item in props:
                    if not isinstance(item,dict):continue
                    name=normal(item.get('name'));value=item.get('value')
                    if name in ('間取り','間取'):out.setdefault('layout',value)
                    elif name in ('建物構造','構造'):out.setdefault('structure',value)
                    elif name in ('築年月','築年数'):out.setdefault('age',value)
                    elif name in ('管理費等','管理費','共益費'):out.setdefault('fees',value)
    return out


def regex_after_label(text,labels,value_pattern):
    for label in labels:
        m=re.search(re.escape(label)+r'\s*[:：]?\s*('+value_pattern+r')',text,re.I)
        if m:return m.group(1).strip()
    return ''


def generic_detail(web,provider,url,region,bounds,munis,requested):
    begin_listing(web,provider,url)
    if not web.permitted(url):raise AppError(provider+'の詳細ページの自動取得が許可されていません。')
    reply=web.fetch(url);soup=BeautifulSoup(reply.text,'html.parser');visible=' '.join(soup.stripped_strings);structured=json_listing_fields(soup)
    raw_layout=structured.get('layout') or generic_labeled(soup,('間取り','間取')) or regex_after_label(visible,('間取り','間取'),r'(?:ワンルーム|\d+(?:S?LDK|S?DK|SK|LK|K|L|R))')
    layout=resolve_layout(web,raw_layout,requested,url)
    if layout is None:return reject_listing(web,provider,url,'detail.layout','condition' if parsed_layout(raw_layout) else 'missing_data','間取りが検索条件外、または読み取れない',raw_layout,requested or list(LAYOUTS))
    address=structured.get('address') or generic_labeled(soup,('所在地','住所','物件所在地','所在地住所'))
    if not address:
        m=re.search(r'(東京都|神奈川県|千葉県|埼玉県)[^|｜\n]{2,80}?(?=(?:交通|最寄|駅徒歩|間取り|賃料|家賃|築|$))',visible)
        if m:address=m.group(0).strip()
    if address and not town_matches(region['town'],address):
        trace(web,'address',{'provider':provider,'town':region['town'],'listing_address':address,'url':url},'WARNING','parser');return reject_listing(web,provider,url,'address.town_match','condition','掲載住所が対象町域と一致しない',address,region['town'])
    title=structured.get('title') or (soup.select_one('h1').get_text(' ',strip=True) if soup.select_one('h1') else provider+'掲載募集')
    raw_rent=structured.get('rent') or generic_labeled(soup,('賃料','家賃'))
    if not raw_rent:raw_rent=regex_after_label(visible,('賃料','家賃'),r'\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円')
    rent=optional_amount(raw_rent)
    raw_fees=structured.get('fees') or generic_labeled(soup,('管理費等','管理費・共益費','管理費','共益費'))
    if not raw_fees:raw_fees=regex_after_label(visible,('管理費等','管理費・共益費','管理費','共益費'),r'(?:なし|無料|－|-|\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円)')
    fees=optional_amount(raw_fees)
    raw_area=structured.get('area') or generic_labeled(soup,('専有面積','面積'))
    if not raw_area:raw_area=regex_after_label(visible,('専有面積','面積'),r'\d+(?:\.\d+)?\s*(?:m2|m²|㎡|平米)')
    area_match=re.search(r'\d+(?:\.\d+)?',normal(raw_area));area=float(area_match.group(0)) if area_match else None
    structure=structured.get('structure') or generic_labeled(soup,('建物構造','構造','種別/構造'))
    raw_age=structured.get('age') or generic_labeled(soup,('築年月','築年数','築年'))
    if not raw_age:
        raw_age=regex_after_label(visible,('築年月','築年数','築年'),r'(?:新築|築\s*\d{1,3}年|(?:19|20)\d{2}年\s*\d{1,2}月)')
    age,ym=age_info(raw_age)
    floor=generic_labeled(soup,('所在階','階建 / 階','階建/階','階数','階'))
    try:location=locate(web,soup,reply.text,address,bounds,munis,source_url=url)
    except AppError as exc:location=None;trace(web,'location',{'provider':provider,'url':url,'message':str(exc)},'WARNING','location')
    row=partial_listing(provider,url,title,address or region['label'],layout,rent,fees,area,region,bounds,location,structure or None,age,ym,floor)
    if row:trace(web,'detail_fields',{'provider':provider,'url':url,'layout':layout,'rent':rent,'fees':fees,'area':area,'address':address,'missing_fields':row.get('missing_fields',[])},stage='parser')
    if not row:return reject_listing(web,provider,url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'raw_rent':raw_rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・住所・物件URL')
    return row


def public_portal_collect(web,region,bounds,munis,emit,provider):
    provider=canonical_provider(provider);requested=region.get('search_layout')
    if requested is not None and requested not in ALL_TARGET_LAYOUTS:raise AppError(provider+'の間取り指定を確認してください。')
    request=provider_region_request(web,provider,region,munis,requested)
    cache_key=('listing_links',provider,str(region.get('code')),address_key(region.get('town','')),requested if provider=='カナリー' and requested in ALL_TARGET_LAYOUTS else 'all')
    with web.provider_lock:cached=web.provider_cache.get(cache_key)
    links=list(cached) if isinstance(cached,list) else []
    if not links:
        current=request['url'];params=dict(request.get('params') or {});seen_pages=set();links_seen=set();page=1
        while current:
            emit('message',f'{provider} 公開一覧 {page}ページ目')
            if not web.permitted(current):raise AppError(provider+'の一覧ページの自動取得が許可されていません。')
            reply=web.fetch(current,params=params or None);params={}
            soup=BeautifulSoup(reply.text,'html.parser');signature=hashlib.sha256(reply.content).hexdigest()
            if signature in seen_pages:raise AppError(provider+'のページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature)
            found=[u for u in detail_link_candidates(soup,reply.url,provider,region.get('town'),bool(request.get('filter_context_town'))) if u not in links_seen]
            for u in found:links_seen.add(u);links.append(u)
            emit('candidate',len(found))
            next_url=generic_next_page(soup,reply.url,provider)
            trace(web,'page_end',{'provider':provider,'region':region['label'],'page':page,'new_candidates':len(found),'next_url':next_url},stage='pagination')
            if not next_url:break
            current=next_url;page+=1
        with web.provider_lock:
            if len(web.provider_cache)>=96:web.provider_cache.pop(next(iter(web.provider_cache)))
            web.provider_cache[cache_key]=list(links)
    if not links:
        raise AppError(provider+'で'+region['label']+'の募集詳細URLを公開一覧から確認できません。')

    scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    def detail_work(item):
        while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
        try:
            i,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
            emit('message',f'{provider} 掲載項目の確認 {i}/{len(links)}件');emit('detail',1)
            try:row=generic_detail(web,provider,url,region,bounds,munis,requested)
            except Exception as exc:
                trace(web,'detail_optional_error',{'provider':provider,'url':url,'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '詳細追加確認失敗'},'WARNING','collector')
                trace(web,'listing_failed',{'provider':provider,'url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection')
                emit('rejected',1);emit('issue',provider+'｜'+(str(exc) if isinstance(exc,AppError) else '詳細ページを確認できません。'));return 0
            if row:emit('unit',row);return 1
            emit('rejected',1);return 0
        finally:web.detail_slots.release()

    accepted=0
    for item,value,error in bounded_results(list(enumerate(links,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
        if error:
            url=item[1] if isinstance(item,tuple) and len(item)>1 else ''
            trace(web,'listing_failed',{'provider':provider,'url':url,'decision':'failed','failed_stage':'detail.worker',
                'category':'acquisition_failure','reason':'parallel detail worker exception','exception_type':type(error).__name__,
                'observed':{},'expected':'listing detail processing completes','evidence':[]},'ERROR','rejection')
            emit('rejected',1);emit('issue',provider+' detail worker | '+type(error).__name__)
        elif value:accepted+=int(value)
    trace(web,'provider_collect_finish',{'provider':provider,'region':region['label'],'layout':requested,'detail_links':len(links),'accepted':accepted,'detail_workers':DETAIL_PAGE_WORKERS},stage='collector')


def sumaity_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'スマイティ')


def canary_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'カナリー')


def athome_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'アットホーム')


def chintai_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'CHINTAI')


def comfy_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'Comfy')


def apaman_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'アパマンショップ')


PROVIDER_COLLECTORS={
    'HOME’S':homes_collect,'SUUMO':suumo_collect,'スマイティ':sumaity_collect,'カナリー':canary_collect,
    'アットホーム':athome_collect,'CHINTAI':chintai_collect,'Comfy':comfy_collect,'アパマンショップ':apaman_collect,
}



def search_all(db,conditions,screen,state=None):
    if state is None: state=st.session_state
    # SUUMO-only release: ignore stale multi-provider requests from stored settings.
    conditions['providers']=['SUUMO']
    bounds=tuple(conditions['bounds']);started=time.monotonic()
    audit=getattr(state,'audit',None) or AuditLog(conditions,(getattr(db,'key',''),));state.audit=audit;db.audit=audit
    DIAG_CONTEXT.audit=audit;DIAG_CONTEXT.scope={}
    search=dict(id=audit.search_id,status='started',
                started_at=utc_now(),finished_at=None,conditions=conditions,summary={})
    units={};saved=set();issues=[];logs=[];acquisition_pending={};q=None;provider_fallbacks=[]
    candidate=detail=rejected=skipped=incomplete=issue_events=0
    def log(message):
        audit.add('progress',diagnosis_code(message) if 'エラー' in message or '失敗' in message else 'progress','INFO',{'message':message})
        logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        screen['log'].code('\n'.join(logs[-15:]),language=None)
    screen['status'].info('検索実行中｜保存先の接続・新しいテーブルを確認しています')
    try:
        if not getattr(db,'collection_checked',False):db.check();db.collection_checked=True
        # The automatic controller already checkpoints the current town.
        # Do not duplicate enormous region / municipality dictionaries in a
        # second 'started' search row; keep the finished result as history.
        if conditions.get('mode')!='automatic_collection':
            db.save_search({**search,'conditions':{k:v for k,v in conditions.items()
                if k in ('bounds','providers','mode','ward_code','town_codes')}})
    except Exception as exc:
        message=str(exc) if isinstance(exc,AppError) else type(exc).__name__+': '+str(exc)
        audit.add('database','save','ERROR',{'stage':'search.preflight','message':message,'traceback':traceback.format_exc()})
        search['status']='failed'
        search['summary']={'confirmed':0,'saved':0,'processed':0,'errors':0,'already_acquired':0,
                           'issues':['保存先の初期確認に失敗：'+message], 'incomplete_tasks':1}
        state.new_units=[];state.new_saved_keys=[];state.new_search=search
        screen['status'].info('保存先の初期確認に失敗：'+message)
        return search
    log('保存先確認OK。新しい検索を開始しました。')
    web=getattr(state,'shared_web',None) or PublicWeb();web.audit=audit
    web.address_points=getattr(db,'address_points',None)
    web.http_cache.clear();web.failed_detail_urls.clear();web.unavailable_hosts.clear();web.claimed_ids=set()
    if hasattr(state,'request_starts'):web.host_last_request=state.request_starts
    if hasattr(state,'cancel_event'):web.cancel_event=state.cancel_event
    if hasattr(web,'configure'):web.configure(getattr(db,'web_config',{}))
    def region_update(stage,done,total,count,errors):
        if done and done==total:audit.add('geography','region_progress','INFO',{'stage':stage,'done':done,'total':total,'regions':count,'failures':errors})
        audit.persist(db)
        if stage.startswith('地名取得先を切替'): log(stage)
        screen['bar'].progress(.05+.15*done/max(1,total),text=f'{stage} {done}/{total}')
        screen['status'].info(f'検索実行中｜{stage} {done}/{total}｜{count}地域｜取得失敗 {errors}件｜経過 {int(time.monotonic()-started)}秒')
    try:
        if 'SUUMO' in conditions['providers']:
            unavailable_until=float(getattr(db,'acquisition_ledger_disabled_until',0) or 0)
            if time.monotonic()<unavailable_until:
                web.acquired_ids={}
                issue='取得済みID参照は前回のHTTP 5xxで一時停止中です（最大60分）。物件検索・保存確認は継続します。'
                issues.append(issue)
                audit.add('database','acquisition_ledger_circuit_open','WARNING',
                    {'remaining_seconds':round(unavailable_until-time.monotonic()),'fallback':'fetch_and_check_save'})
                log(issue)
            else:
                try:
                    web.acquired_ids=db.load_recent_acquisition_ids()
                    db.acquisition_ledger_disabled_until=0
                except AppError as exc:
                    diagnostic=getattr(exc,'diagnostic',{}) or {}
                    status=int(diagnostic.get('status') or 0)
                    if status not in (500,502,503,504):raise
                    # Optional de-duplication ledger only; database write failures
                    # are never swallowed or counted as successful saves.
                    web.acquired_ids={}
                    db.acquisition_cache=None
                    db.acquisition_ledger_disabled_until=time.monotonic()+3600
                    issue='Supabase取得済みIDの参照に失敗（HTTP '+str(status)+'）。この検索では重複スキップを使わず収集します。'
                    issues.append(issue)
                    audit.add('database','acquisition_ledger_unavailable','ERROR',{'message':str(exc),
                        'diagnostic':diagnostic,'fallback':'re-fetch_with_database_save_validation',
                        'next_ledger_probe_seconds':3600})
                    log(issue)
        screen['status'].info('検索実行中｜地域の町名リストを取得しています')
        if conditions.get('auto_regions') and conditions.get('auto_munis'):
            regions,munis,region_errors=conditions['auto_regions'],conditions['auto_munis'],0
        elif conditions.get('ward_code'):
            regions,munis,region_errors=suumo_ward_regions(web,conditions['ward_code'])
        else:
            regions,munis,region_errors=regional_tasks(web,bounds,region_update)
        if conditions.get('ward_code'):
            regions=select_ward_towns(regions,conditions.get('town_codes',[]))
        if conditions.get('mode')=='automatic_collection':
            conditions['auto_regions']=regions;conditions['auto_munis']=munis
        if region_errors:
            issues.append(f'地域判定で{region_errors}地点を確認できませんでした。')
            audit.add('geography','region_lookup_failed','WARNING',{'failures':region_errors,'bounds':list(bounds)})
        search['conditions']['region_source']='SUUMO区の公開町名選択フォーム' if conditions.get('ward_code') else 'Geolonia町丁目一覧' if getattr(web,'reverse_unavailable',False) else '国土地理院地名判定'
        log('地名取得完了｜'+search['conditions']['region_source']+'｜'+str(len(regions))+'地域')
        search['conditions']['regions']=[r['label'] for r in regions]
        state.new_regions=[r['label'] for r in regions]
        conditions['providers']=list(dict.fromkeys(canonical_provider(p) for p in conditions['providers']))
        if 'HOME’S' in conditions['providers']:
            homes_ok,homes_fallback=homes_preflight_or_fallback(web,regions)
            if not homes_ok:
                conditions['providers']=[p for p in conditions['providers'] if p!='HOME’S']
                if 'SUUMO' not in conditions['providers']:conditions['providers'].insert(0,'SUUMO')
                if homes_fallback:provider_fallbacks.append(dict(homes_fallback))
                log('HOMESはHTTP 403のため、この実行では停止してSUUMOのみで続行します。')
                audit.add('provider_fallback','homes_http403_fallback','WARNING',homes_fallback or {'provider':'HOME’S','reason':'HTTP 403','decision':'SUUMO only'})
        search['conditions']['providers_after_fallback']=list(conditions['providers'])
        suumo_groups={};homes_groups={}
        if 'SUUMO' in conditions['providers']:
            for group in suumo_area_groups(regions,munis):suumo_groups[(str(group['code']),address_key(group['suumo_town']))]=group
            search['conditions']['suumo_areas']=[{'municipality_code':g['code'],'city':g.get('city_name',''),'town':g['suumo_town'],'visible_towns':list(g.get('visible_towns',[]))} for g in suumo_groups.values()]
            audit.add('search_conditions','suumo_area_list','INFO',{'areas':search['conditions']['suumo_areas']})
        if 'HOME’S' in conditions['providers']:
            for group in homes_area_groups(regions,munis):homes_groups[(str(group['code']),address_key(group['homes_town']))]=group
            search['conditions']['homes_areas']=[{'municipality_code':g['code'],'city':g.get('city_name',''),'town':g['homes_town'],'visible_towns':list(g.get('visible_towns',[]))} for g in homes_groups.values()]
            audit.add('search_conditions','homes_area_list','INFO',{'areas':search['conditions']['homes_areas']})
        available={}
        generic_providers=[]
        for provider in conditions['providers']:
            if provider not in PROVIDER_COLLECTORS:
                issues.append(provider+'｜未対応の取得元です。');audit.add('search_conditions','provider_unavailable','ERROR',{'provider':provider,'message':'collector not registered'});continue
            if provider=='SUUMO':
                available[provider]=['GROUPED']
                audit.add('search_conditions','provider_enabled','INFO',{'provider':'SUUMO','mansion':{'max_age':15,'layout_groups':[list(x) for x in SUUMO_LAYOUT_GROUPS]},'house':{'max_age':40,'min_area_sqm':50},'filter_mode':'city -> town -> mansion groups + detached house -> detail -> kankyo map'})
                continue
            if provider=='HOME’S':
                available[provider]=['GROUPED']
                audit.add('search_conditions','provider_enabled','INFO',{'provider':'HOME’S','mansion':{'max_age':15,'layout_groups':[list(x) for x in HOMES_LAYOUT_GROUPS]},'house':{'max_age':40,'min_area_sqm':50},'filter_mode':'city -> 町域 -> mansion groups + kodate -> detail -> 地図を見る'})
                continue
            available[provider]=['ALL'];generic_providers.append(provider)
            audit.add('search_conditions','provider_enabled','INFO',{'provider':provider,'layouts':list(ALL_TARGET_LAYOUTS),'filter_mode':'one public town-list crawl + explicit detail layout verification'})
        jobs=[];provider_targets=[]
        for provider in conditions['providers']:
            if provider not in available:continue
            targets=list(suumo_groups.values()) if provider=='SUUMO' else list(homes_groups.values()) if provider=='HOME’S' else regions
            provider_targets.append((provider,targets))
        for number in range(max((len(targets) for _,targets in provider_targets),default=0)):
            for provider,targets in provider_targets:
                if number<len(targets):jobs.append((targets[number],provider))
        if conditions.get('mode')=='automatic_collection':
            if not jobs:raise AppError('自動収集する町が見つかりません。対象範囲を変更してください。')
            requested_label=normalized_automatic_task_label(conditions.get('auto_task_label'))
            requested_identity=normal(conditions.get('auto_task_identity'))
            # Prefer a machine-readable code/town/provider identity; old runs
            # still match the legacy human-readable label with normalized separators.
            matches=[]
            for i,(region,provider) in enumerate(jobs):
                actual_identity=automatic_task_identity(region.get('code'),region.get('suumo_town') or region.get('town'),provider)
                actual_label=normalized_automatic_task_label(region['label']+'｜'+PROVIDER_DISPLAY.get(provider,provider))
                if (requested_identity and requested_identity==actual_identity or
                    not requested_identity and requested_label and requested_label==actual_label):
                    matches.append((i,region,provider))
            if len(matches)==1:
                index=matches[0][0]
                conditions['auto_total_tasks']=len(jobs);conditions['auto_task_index']=index
                jobs=[jobs[index]]
            elif requested_label or requested_identity:
                raise AppError('自動収集の町・取得元を一致させられません。対象を再取得してから再開してください：'+(requested_label or requested_identity))
            else:
                index=int(conditions.get('auto_task_index',0))%len(jobs)
                conditions['auto_total_tasks']=len(jobs);conditions['auto_task_index']=index
                conditions['auto_task_label']=jobs[index][0]['label']+'｜'+PROVIDER_DISPLAY.get(jobs[index][1],jobs[index][1])
                jobs=[jobs[index]]
        db.job_new_keys=set()
        q=queue.Queue();active={};done=0;save_buffer={};save_failed_keys=set();last_flush=time.monotonic()
        def work(index,region,provider):
            if web.cancel_event.is_set():return
            DIAG_CONTEXT.audit=audit;DIAG_CONTEXT.scope={'task':index,'region':region['label'],'town':region['town'],'municipality_code':region['code'],'provider':provider}
            def emit(kind,value):
                if kind!='unit':web.check_cancel()
                if kind in ('message','unit','skipped','detail','rejected','candidate','incomplete','issue'):
                    state.last_activity_at=time.monotonic()
                    if kind=='message':state.last_collection_stage=str(value)[:170]
                # Numeric progress counters are already preserved in page_end/final summaries.
                # Avoid two diagnostic events per acquired-ID skip; large runs otherwise spend
                # substantial time serializing logs rather than collecting data.
                if kind not in ('candidate','detail','rejected','skipped','incomplete'):
                    audit_value=compact_saved_listing(value) if kind=='unit' and isinstance(value,dict) else value
                    if kind=='unit' and isinstance(audit_value,dict) and value.get('property_id'):audit_value={**audit_value,'property_id':value['property_id']}
                    audit.add('collector','accepted' if kind=='unit' else kind,'ERROR' if kind=='issue' else 'INFO',{'event':kind,'value':audit_value})
                q.put((index,kind,value))
            try:
                if provider=='HOME’S':
                    disabled=homes_runtime_disabled(web)
                    if disabled:
                        emit('provider_fallback',disabled);return
                emit('message',provider+'｜'+region['label']+'｜検索を開始')
                PROVIDER_COLLECTORS[provider](web,dict(region,search_layout=None),bounds,munis,emit)
            except SearchCancelled:return
            except Exception as exc:
                if provider=='HOME’S' and is_homes_http403_error(exc):
                    fallback=disable_homes_for_runtime(web,exc,getattr(exc,'diagnostic',{}).get('url','') if isinstance(getattr(exc,'diagnostic',{}),dict) else '')
                    emit('provider_fallback',fallback or {'provider':'HOME’S','reason':'HTTP 403','message':'HOME’Sを停止しSUUMOのみで続行'})
                    return
                trace(web,diagnosis_code(str(exc)),{'exception_type':type(exc).__name__,'traceback':traceback.format_exc()},'ERROR','collector_exception')
                emit('issue',provider+'｜'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
                emit('incomplete',1)
        with futures.ThreadPoolExecutor(max_workers=max(1,min(SEARCH_TASK_WORKERS,len(jobs)))) as pool:
            pending={};next_job=0
            def fill_jobs():
                nonlocal next_job
                while next_job<len(jobs) and len(pending)<SEARCH_TASK_WORKERS and not web.cancel_event.is_set():
                    pending[pool.submit(work,next_job,*jobs[next_job])]=next_job;next_job+=1
            fill_jobs()
            completed=set()
            while pending or not q.empty() or save_buffer:
                ready,_=futures.wait(pending,timeout=.35,return_when=futures.FIRST_COMPLETED) if pending else (set(),set())
                if web.cancel_event.is_set():
                    for f in pending:f.cancel()
                audit.persist(db)
                batch=[]
                while True:
                    try: index,kind,value=q.get_nowait()
                    except queue.Empty: break
                    label=jobs[index][0]['label']+'｜'+jobs[index][1]
                    if kind=='message' and index not in completed: active[index]=label+'｜'+str(value)
                    elif kind=='candidate': candidate+=int(value)
                    elif kind=='detail': detail+=int(value)
                    elif kind=='rejected': rejected+=int(value)
                    elif kind=='skipped': skipped+=int(value)
                    elif kind=='incomplete': incomplete+=int(value)
                    elif kind=='issue':
                        issue_events+=1
                        issues.append(label+'｜'+str(value));log(issues[-1])
                    elif kind=='provider_fallback':
                        item=dict(value) if isinstance(value,dict) else {'provider':'HOME’S','reason':'HTTP 403','message':str(value)}
                        if not any(x.get('provider')=='HOME’S' and x.get('reason')==item.get('reason') for x in provider_fallbacks):provider_fallbacks.append(item)
                        log('HOMESはHTTP 403のため停止｜SUUMOのみで続行')
                    elif kind=='unit':
                        identity=compact_listing_key(value) or value['key'];value=dict(value,key=identity)
                        units[identity]=merge_listing(units.get(identity),value)
                        batch.append(units[identity])
                for row in batch:
                    key=compact_listing_key(row);save_buffer[key]=newest_observation(save_buffer.get(key),row)
                # Persist accepted units promptly.  Detail workers can spend time on map/GSI
                # resolution, so waiting for 100 rows/3 seconds made the UI look as though
                # completed properties were not being saved.  This changes only Supabase
                # batching, not source-site request frequency.
                batch=list(save_buffer.values()) if save_buffer and (len(save_buffer)>=25 or time.monotonic()-last_flush>=1 or web.cancel_event.is_set() or (len(ready)==len(pending) and next_job==len(jobs))) else []
                if batch:
                    save_buffer.clear();last_flush=time.monotonic()
                    state.new_units=list(units.values())
                    errors_now=rejected+len(save_failed_keys-saved)
                    processed_now=len(saved)+errors_now
                    state.live_metrics={'processed':processed_now,'saved':len(saved),'errors':errors_now,'skipped':skipped}
                    screen['status'].info(f'検索実行中｜処理 {processed_now}件｜保存 {len(saved)}件｜未保存 {errors_now}件｜スキップ {skipped}件')
                    try:
                        for offset in range(0,len(batch),200):
                            chunk=batch[offset:offset+200];saved.update(db.save_units(chunk));state.new_last_saved_at=utc_now()
                            for row in chunk:save_failed_keys.discard(compact_listing_key(row))
                            for row in chunk:
                                if row.get('property_id'):acquisition_pending[row['property_id']]=row
                            db.save_acquisition_ids(chunk)
                            for row in chunk:acquisition_pending.pop(row.get('property_id'),None)
                            for row in chunk:
                                if row.get('property_id'):web.acquired_ids[row['property_id']]=row['fetched_at']
                    except Exception as exc:
                        for row in batch:save_failed_keys.add(compact_listing_key(row))
                        issues.append(str(exc) if isinstance(exc,AppError) else type(exc).__name__);log('保存エラー｜'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
                    # Browser session holds unconfirmed rows separately; no claim of persistence.
                    state.new_units=list(units.values())
                    state.new_saved_keys=list(saved)
                    save_failed_keys.difference_update(saved)
                for f in ready:
                    index=pending.pop(f);completed.add(index);active.pop(index,None);done+=1
                    if not f.cancelled():
                        try:f.result()
                        except SearchCancelled:pass
                    log(f'{done}/{len(jobs)}完了｜'+jobs[index][0]['label']+'｜'+jobs[index][1])
                fill_jobs()
                save_failed_keys.difference_update(saved)
                errors_now=rejected+len(save_failed_keys-saved)
                processed_now=len(saved)+errors_now
                state.live_metrics={'processed':processed_now,'saved':len(saved),'errors':errors_now,'skipped':skipped}
                metrics=f'処理 {processed_now}件｜保存 {len(saved)}件｜未保存 {errors_now}件｜スキップ {skipped}件'
                screen['bar'].progress(.2+.75*done/max(1,len(jobs)),text=metrics)
                screen['status'].info('検索実行中｜'+metrics)
        search['status']='cancelled' if web.cancel_event.is_set() else 'partial' if issues else 'completed'
        search['summary']={'confirmed':len(units),'saved':len(saved),'candidate':candidate,'detail':detail,'rejected':rejected,'already_acquired':skipped,'incomplete_tasks':incomplete,
                           'processed':len(saved)+rejected,'errors':rejected,
                           'retained_with_missing_fields':sum(bool(r.get('missing_fields')) for r in units.values()),'price_unknown':sum(not r.get('rent') for r in units.values()),'issues':issues[-100:],'elapsed':round(time.monotonic()-started,3),'tasks':len(jobs),
                           'provider_fallbacks':provider_fallbacks,'homes_http403_fallback':bool(provider_fallbacks)}
    except SearchCancelled:
        search['status']='cancelled'
        search['summary']={'confirmed':len(units),'saved':len(saved),'processed':len(saved)+rejected,'errors':rejected,'already_acquired':skipped,'issues':issues,'elapsed':round(time.monotonic()-started,3)}
    except Exception as exc:
        audit.add('search',diagnosis_code(str(exc)),'ERROR',{'stage':'search_all','exception_type':type(exc).__name__,
            'message':str(exc) if isinstance(exc,AppError) else f'検索エラー（{type(exc).__name__}）','traceback':traceback.format_exc()})
        search['status']='failed';issues.append(str(exc) if isinstance(exc,AppError) else f'検索エラー（{type(exc).__name__}）')
        search['summary']={'confirmed':len(units),'saved':len(saved),'processed':len(saved)+rejected,'errors':rejected,'already_acquired':skipped,'issues':issues,'elapsed':round(time.monotonic()-started,3)}
    # On a consumer exception the executor has joined its workers. Preserve all
    # accepted rows still in the queue before final persistence, including stop.
    if q is not None:
        while True:
            try:_,kind,value=q.get_nowait()
            except queue.Empty:break
            if kind=='unit':
                identity=compact_listing_key(value) or value['key'];value=dict(value,key=identity)
                units[identity]=merge_listing(units.get(identity),value)
            elif kind=='detail':detail+=int(value)
            elif kind=='rejected':rejected+=int(value)
            elif kind=='skipped':skipped+=int(value)
            elif kind=='issue':
                issue_events+=1
                issues.append(str(value))
    # Workers have finished and the event queue is drained before this final save.
    # Cancellation only stops acquisition; database writes remain enabled.
    remaining=[r for r in units.values() if compact_listing_key(r) not in saved]
    if remaining or acquisition_pending:
        screen['status'].info('最終保存中｜取得済みデータの保存を確認しています')
    for offset in range(0,len(remaining),60):
        chunk=remaining[offset:offset+60]
        try:
            saved.update(db.save_units(chunk));state.new_last_saved_at=utc_now()
            for row in chunk:
                if row.get('property_id'):acquisition_pending[row['property_id']]=row
        except Exception as exc:
            audit.add('database','stop_save_failed','ERROR',{'stage':'stop.final_save' if web.cancel_event.is_set() else 'search.final_save','exception_type':type(exc).__name__,'rows':len(chunk)})
            issues.append('最終保存での物件保存失敗：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    if acquisition_pending:
        try:
            db.save_acquisition_ids(list(acquisition_pending.values()));acquisition_pending.clear()
        except Exception as exc:
            audit.add('database','save','ERROR',{'stage':'search.final_acquisition_ids','exception_type':type(exc).__name__,
                'pending_ids':len(acquisition_pending),'message':str(exc) if isinstance(exc,AppError) else type(exc).__name__})
            issues.append('最終保存での取得済みID保存失敗：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    unsaved=sum(compact_listing_key(r) not in saved for r in units.values())
    # Final UI accounting follows the user's simple model exactly.  A candidate that
    # reaches detail processing is either saved or an error; candidates skipped before
    # detail are counted only as skipped.
    terminal_observed=rejected+unsaved
    # A cancelled worker may have emitted `detail` but not a final outcome.
    # Such a partial attempt is not a confirmed address error / failed save.
    unfinished=max(0,int(detail)-len(saved)-terminal_observed) if web.cancel_event.is_set() else 0
    final_errors=max(0,int(detail)-len(saved)-unfinished)
    final_processed=len(saved)+final_errors
    final_candidate=int(skipped)+final_processed
    if web.cancel_event.is_set() and (unfinished or int(candidate)!=final_candidate):
        audit.add('accounting','v140_cancelled_candidates','INFO',{
            'discovered_candidates':candidate,'detail_started':detail,'saved':len(saved),'skipped':skipped,
            'terminal_rejected':rejected,'unsaved':unsaved,'unfinished_on_stop':unfinished,
            'not_started':max(0,int(candidate)-int(detail)-int(skipped)),
            'reason':'収集停止時の未着手・実行中を保存失敗と混同しない'})
    elif terminal_observed!=final_errors or int(candidate)!=final_candidate:
        audit.add('accounting','candidate_accounting_mismatch','ERROR',{
            'discovered_candidates':candidate,'detail_started':detail,'saved':len(saved),'skipped':skipped,
            'terminal_rejected':rejected,'unsaved':unsaved,'ui_processed':final_processed,'ui_errors':final_errors,
            'accounted_candidates':final_candidate,'reason':'候補・処理・保存・スキップと内部イベントの件数差を検出'})
    state.live_metrics={'processed':final_processed,'saved':len(saved),'errors':final_errors,'skipped':skipped}
    search['summary'].update(confirmed=len(units),elapsed=round(time.monotonic()-started,3),new_saved=len(saved & getattr(db,'job_new_keys',set())),updated_saved=len(saved-getattr(db,'job_new_keys',set())),processed=final_processed,errors=final_errors,candidate=final_candidate,discovered_candidate=candidate,already_acquired=skipped,rejected=rejected,detail=detail,unfinished_on_stop=unfinished,last_saved_at=getattr(state,'new_last_saved_at',None),saved=len(saved),unsaved=unsaved,acquisition_ids_pending=len(acquisition_pending),issues=issues[-100:],provider_fallbacks=provider_fallbacks,homes_http403_fallback=bool(provider_fallbacks))
    if unsaved:
        audit.add('database','unsaved_listings','ERROR',{'stage':'stop.final_save' if web.cancel_event.is_set() else 'search.final_save','unsaved':unsaved,'retained_in_server_memory':True})
        for row in units.values():
            key=compact_listing_key(row)
            if key in saved:continue
            audit.add('database','listing_failed','ERROR',{
                'provider':row.get('provider'),'url':row.get('listing_url') or row.get('url') or '',
                'property_id':row.get('property_id'),'decision':'failed','failed_stage':'database.final_save',
                'category':'save_failure','reason':'Supabaseへの保存を最終再試行後も確認できない',
                'observed':{'listing_key':key},'expected':'Supabase保存確認','evidence':[]})
    audit.add('map','data_ready','INFO',{'confirmed_units':len(units),'colored_points':sum(has_point(r) for r in units.values()),'render_deferred_until_saved_data_load':False})
    screen['status'].info(f'検索終了｜処理 {final_processed}件｜保存 {len(saved)}件｜未保存 {final_errors}件｜スキップ {skipped}件'+
                          (f'｜中断時の未完了 {unfinished}件' if unfinished else ''))
    audit.add('search','finish','INFO',{'status':search['status'],'summary':search['summary']})
    try:
        audit.persist(db,force=True)
    except Exception as exc:
        diagnostic_error=type(exc).__name__+': '+str(exc)
        search['summary']['diagnostic_storage_error']=diagnostic_error
        search['summary']['issues']=list(search['summary'].get('issues') or [])+['詳細ログの保存に失敗：'+diagnostic_error]
    search['summary']['diagnostics']={'search_id':audit.search_id,'events':audit.count,'persisted_events':audit.persisted_events,
                                     'persisted_failures':audit.persisted_failures,'pending_failures':len(audit.pending_failures),
                                     'storage_error':audit.storage_error}
    if audit.storage_error:
        search['summary']['diagnostic_storage_error']=audit.storage_error
        search['summary']['issues']=list(search['summary'].get('issues') or [])+['重要ログの永続保存に失敗：'+audit.storage_error]
    search['finished_at']=utc_now()
    try:
        stored_search=dict(search)
        stored_search['summary']=dict(search.get('summary') or {})
        stored_search['summary']['issues']=list((stored_search['summary'].get('issues') or [])[-8:])
        stored_search['conditions']={k:v for k,v in search.get('conditions',{}).items()
            if k in ('bounds','providers','mode','ward_code','town_codes','auto_task_index',
                     'auto_task_label','auto_task_identity','region_source','providers_after_fallback')}
        db.save_search(stored_search)
    except AppError as exc:
        audit.add('database','save','ERROR',{'stage':'search.history','message':str(exc),'status':search.get('status')})
        try:audit.persist(db,force=True)
        except Exception:pass
        issues.append('検索履歴｜'+str(exc));search['summary']['issues']=issues
        if search['status']!='cancelled':search['status']='partial'
    screen['bar'].progress(1.,text='検索処理が終了しました')
    state.new_units=list(units.values())
    state.new_search=search
    state.new_saved_keys=list(saved)
    return search


# Detailed diagnostics are independent of UI and retained in Supabase search-log chunks.
DIAG_CONTEXT=threading.local()

DIAG_ADVICE={
 'landmark_geocoder_failed':('施設名ジオコーダーの通信・解析失敗','failure_phase・exception_chain・HTTP状態を確認し、元の通信例外まで追跡する。'),
 'landmark_endpoint_fallback':('施設検索先の障害による切替','接続先別の実測時間と応答有無を比較する。サーバー待ちと要求回数制限を区別する。'),
 'landmark_search_failed':('施設名検索の通信・応答解析に失敗','検索語・失敗工程・HTTP状態・応答抜粋・例外を確認して原因を切り分ける。'),
 'landmark_address_failed':('施設の住所照合に失敗','座標と国土地理院の応答形式・地域コードを確認する。'),
 'landmark_no_candidates':('施設名に一致する候補がない','通信成功時の0件と、不正な施設データの全件除外を確認する。'),
 'landmark_log_save_failed':('施設名検索ログの保存に失敗','ダウンロードでログを確保し、保存先への接続・権限を確認する。'),
 'timeout':('接続または応答の時間超過','同じURLをブラウザで確認し、サービス障害とStreamlitからの通信制限を切り分ける。正常時の応答時間を測ってからタイムアウトと同時接続数を調整する。'),
 'http_403':('公開先がアクセスを拒否','通信試行ログの経路番号を確認する。プロキシ設定・認証・HTTPS接続と経路切替後のHTTP状態を比較する。403の解消は成功応答で確認する。'),
 'http_429':('取得先の要求回数制限','同じ自治体・ページの重複要求を減らし、キャッシュと取得間隔を導入する。Retry-Afterがあればその値に従う。'),
 'http_404':('対象のURL・データが存在しない','掲載終了とAPI・タイルURL変更を区別する。現行の公開ページ・公式仕様とURLを照合する。'),
 'http_5xx':('取得先のサーバーエラー','発生時刻とURLを確認して障害を切り分け、復旧後に失敗したタスクだけを再検証する。'),
 'network':('接続・DNS・TLSの問題の可能性','同一ホストの複数URLと別ホストの結果を比較する。StreamlitログでDNS/TLS・外向き通信を確認する。'),
 'parse':('掲載情報の形式を読み取れない','HTTP成功時のページタイトル・ハッシュ・検出件数と現行HTML/JSONを照合し、項目名・セレクタ・ページ送りを修正して同じ例で再検証する。'),
 'structure':('構造の掲載情報','実際の構造表記とSRC判定を照合する。RC等の正しい除外と表記ゆれの誤判定を分ける。'),
 'age':('築年数の掲載情報','築年月の元表記・解析値・実行日を照合する。築20年超は仕様上の除外であり取得失敗とは扱わない。'),
 'layout':('間取り条件または表記の不一致','間取りの元表記を確認し、空白・全角・括弧等を正規化する。対象外間取りは仕様上の除外とする。'),
 'address':('地名・住所の照合で不一致','丁目の漢数字・数字、町名表記、市区名の変換を照合する。実際の掲載座標との比較で誤除外がないか確認する。'),
 'location':('掲載座標・住所の位置を確認できない','掲載地図・JSON-LD・住所検索の各候補と表示範囲を比較する。番地や掲載座標がない物件を架空座標で補完しない。'),
 'outside':('物件が検索開始時の表示範囲外','ログの固定表示範囲と掲載座標を比較する。範囲内の物件を落とす場合は座標順序・境界の判定を修正する。'),
 'save':('Supabaseの保存または保存確認の失敗','物件確認数と保存確認数を比較し、キー・テーブル・権限・HTTP応答を確認する。未保存物件の再保存で復旧を検証する。'),
 'automatic_collection_failure':('自動収集コントローラーの実行失敗','発生した町・直前検索ID・例外種別を確認し、失敗町から再開する。'),
 'empty':('取得元の一覧に候補がない','地名・自治体コード・検索パラメータをログのURLで照合する。手動検索の件数と比較し、真の0件と検索条件/解析の誤りを区別する。'),
 'location_unverified':('掲載座標はあるが住所照合は未判定','地名照合サービスの失敗ログを確認する。掲載座標と実際の建物位置を手動で照合し、未判定を位置確認済みとして評価しない。'),
 'layout_assumed':('掲載間取りを単独検索の指定値で補完','検索条件・公開フォームの値と結果一覧を照合する。掲載間取りを確認できたら優先し、異なる明示間取りは除外する。補完済みを掲載確認済みとは扱わない。'),
 'request_spacing':('取得元への同時接続と頻度を調整','物件サイトごとの同時通信は2件、通信開始は最低1秒間隔。取得件数・ページ数には上限を設けない。'),
 'session_entry':('同じ経路で公開トップページを確認','応答状態・Cookie件数と再試行結果を比較する。Cookieの値は保存しない。'),
 'session_retry':('公開ページ閲覧後にセッション付き再試行','同じプロキシ・Cookieを使い、実際に訪れたページをRefererとして送る。改善したか後続応答で確認する。'),
 'route_cooldown':('拒否された経路またはRetry-Afterの待機','残り待機時間を確認する。403の経路とURLは60秒休止し、他の設定済み経路があれば試す。'),
 'form_alternative':('別の公開ページで間取り条件を確認','成功した公開ページのフォーム名と値を確認する。条件が確認できなければ物件取得を開始しない。'),
 'http_challenge':('物件本文ではなくアクセス検証画面','実際のHTTP状態・ページタイトル・経路番号を比較する。検証画面を物件ページとして解析しない。'),
 'transport_config':('取得経路の設定','プロキシ未設定ならStreamlit SecretsにRENTAL_HTTP_PROXIESを設定する。物件サイトだけに適用する。'),
 'transport_attempt':('実際の通信試行','経路番号・HTTP状態・本文バイト数を比較し、取得成功した経路を確認する。'),
 'proxy_switch':('別のプロキシ経路へ切替','切替後のHTTP状態を確認する。全経路で403なら取得可能とは表示しない。'),
 'proxy_error':('プロキシ経路の接続失敗','プロキシの契約状態、ホスト・ポート・認証情報、HTTPS CONNECT対応を確認する。'),
 'http_retry':('一時的なHTTPエラーを再試行','Retry-Afterと再試行後の状態を確認する。繰り返す場合は取得元またはプロキシの接続状況を調べる。'),
 'provider_unavailable':('取得元の検索条件を取得できない','通信試行ログを確認する。403が全経路で続く場合はプロキシ接続先と取得元へのアクセス可否を確認する。'),
 'layout_unsupported':('取得元に単独検索条件がない','この間取りは検索未実施。取得元の公開フォームに対応する間取りがあるか確認する。'),
 'partial_retained':('取得できた募集情報を保存対象に保持','未確認項目があっても取得済みの家賃・間取り・地域を反映する。建物座標と町丁目の概算を区別する。'),
 'detail_optional_error':('詳細の追加確認でエラー・一覧情報は保持','一覧から取得できた募集項目とSupabase保存確認を見る。詳細エラーを募集全体の除外理由にしない。'),
 'location_pending':('条件に合う募集だが建物位置が未確認','記録したURLと掲載項目を用いて掲載地図・正確な番地を確認する。地図の250m区画へ町丁目代表点を入れない。位置を確認した後に再取得・保存する。'),
 'map_link_result':('掲載ページから地図ページを追加確認','座標の出所と掲載住所を照合する。町丁目の代表点を建物座標として扱わない。'),
 'http_empty':('HTTP 202の空応答で掲載データが未到着','公開ページを手動で確認し、取得元のアクセス条件と公開API/データ提供を確認する。空本文をHTML解析失敗や間取り欠落と扱わない。'),
 'image_georef_missing':('地図画像の基準座標・縮尺が不明','画像URLや埋め込み地図の中心座標・ズームを確認する。基準のない画像から緯度経度を捏造しない。'),
 'suumo_map_fallback_success':('SUUMOの代替地図経路で物件位置を取得','JNC経路で取れない場合に、同じ募集のcanonical BC地図を確認してtype=roomマーカーを使用する。'),
 'suumo_location_cache_hit':('同一SUUMO物件の確認済み位置を再利用','同じJNC物件の別募集では確認済みの地図位置・住所を再利用し、地図ページと住所推定の重複通信を省く。'),
 'image_pin_result':('地図画像の物件ピン認識結果','認識ピクセル位置・地図基準座標・ズーム・逆投影後の座標を確認する。'),
 'position_pending':('掲載地図の位置再取得が未完了','掲載URL、地図リンク、画像認識・HTTPエラーのイベントを確認する。元の募集データは保持する。'),
 'position_repaired':('掲載地図から位置と住所を更新','position_source_urlと推定住所を比較する。番地を読み取れていない場合は町丁目までの推定として扱う。'),
 'reverse_address_pending':('地図位置は保持・逆算住所は未確認','国土地理院の通信結果と市区町村コード表を確認する。逆算住所の未確認で地図位置を捨てない。'),
 'map_multiple_candidates':('物件の座標候補が競合','候補の出所を照合する。店や地図中心の座標で代用しない。'),
 'map_address_difference':('掲載住所と逆算町字が異なる','掲載地図の座標と逆算住所を別項目で確認する。番地の一致とは扱わない。'),
 'robots_disallowed':('取得先のrobotsルールによる制限','取得先の制限・利用規約を確認し、利用可能な検索APIを優先する。'),
 'osm_rate_limited':('OpenStreetMap APIの利用制限','この収集では問い合わせを中止し、次の地域へ進む。アクセス制限を回避しない。'),
 'unknown':('現時点で理由を特定できない','前後の通信・解析・除外イベントを照合する。再現URLと読取項目を追加してから修正する。原因を断定しない。')}

DIAG_ADVICE.update({'address_tile_empty':('GSIの住所タイルにデータがない','HTTP 404とNoSuchKeyが両方確認された空タイル。取得できた周辺タイルの同一町丁目の住所候補を比較する。その他の通信失敗と区別する。'),'listing_rejected':('物件の除外理由と工程・取得値・期待値','failed_stage/observed/expected/evidenceで条件不一致と読取・住所推定失敗を区別する。'),'listing_failed':('物件の通信・解析処理に失敗','HTTP状態・例外型・直前の工程を確認する。原因未確定を確定扱いしない。'),
 'redirect_offsite':('取得先が許可済みホストから移動','source_host/target_host/target_scheme/redirect_stepを検証する。安全性が未確認の外部サイトを追跡しない。')})

def diagnosis_code(message):
    text=str(message)
    if '取得経路の待機中' in text:return 'route_cooldown'
    if 'アクセス検証画面' in text:return 'http_challenge'
    if 'プロキシ接続失敗' in text:return 'proxy_error'
    if '取得先が別のサイトに移動' in text:return 'redirect_offsite'
    if 'HTTP 202' in text and '0バイト' in text:return 'http_empty'
    for needle,code in [('タイムアウト','timeout'),('HTTP 403','http_403'),('HTTP 429','http_429'),('HTTP 404','http_404')]:
        if needle in text:return code
    if re.search(r'HTTP 5\d\d',text):return 'http_5xx'
    if 'Supabase' in text or '保存' in text or '権限' in text:return 'save'
    if any(x in text for x in ('形式','読み取','JSON','ページ送り')):return 'parse'
    if any(x in text for x in ('接続','通信','DNS')):return 'network'
    return 'unknown'

def diagnostic_failure_event(event):
    """True for acquisition/processing failures that must be durably stored as they occur."""
    if not isinstance(event,dict):return False
    if str(event.get('level') or '').upper() in ('WARNING','ERROR','CRITICAL'):return True
    code=str(event.get('code') or '')
    if code in {
        'listing_rejected','listing_failed','collector_exception','provider_unavailable','unsaved_listings',
        'location_pending','position_pending','reverse_address_pending','address_tile_failed','map_address_unresolved',
        'http_403','http_404','http_429','http_5xx','http_empty','http_challenge','timeout','network','proxy_error','save'
    }:return True
    details=event.get('details') if isinstance(event.get('details'),dict) else {}
    for candidate in (details.get('status'),(details.get('technical_exception') or {}).get('status') if isinstance(details.get('technical_exception'),dict) else None):
        try:
            if int(candidate)>=400:return True
        except (TypeError,ValueError):pass
    return False


class AuditLog:
    def __init__(self,conditions,secrets=()):
        fd,self.path=tempfile.mkstemp(prefix='sumai-log-',suffix='.jsonl');os.close(fd)
        self.lock=threading.RLock();self.persist_lock=threading.Lock();self.count=0;self.offset=0;self.chunk=0;self.last_save=0.;self.persisted_events=0
        self.storage_error=None;self.search_id=hashlib.sha256(os.urandom(32)).hexdigest();self.secrets=tuple(str(v) for v in secrets if v)
        self.search_mode=conditions.get('mode');self.ward_code=conditions.get('ward_code');self.town_codes=list(conditions.get('town_codes') or [])
        self.pending_failures=[];self.persisted_failures=0;self.failure_storage_error=None
        self._persisted_fault_samples=0
        self._persisted_db_fault_samples=0
        self._persisted_start=False
        self.add('search','start','INFO',{'build':BUILD,'bounds':conditions.get('bounds'),'providers':conditions.get('providers'),'filters':{'structure':None,'max_age':None,'layouts':list(LAYOUTS),'suumo':{'mansion':{'max_age':15,'layout_groups':[list(x) for x in SUUMO_LAYOUT_GROUPS]},'house':{'max_age':40,'min_area_sqm':50}}},'property_limit':None})
    def clean(self,value):
        if isinstance(value,dict):return {str(k):('[REDACTED]' if re.search(r'^(?:key|apikey|api_key|token|access_token|authorization|cookie|password|secret|SUPABASE_.*KEY)$',str(k),re.I) else self.clean(v)) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [self.clean(x) for x in value]
        if isinstance(value,str):
            for secret in self.secrets:value=value.replace(secret,'[REDACTED]')
            value=re.sub(r'(https?://)[^/\s@]+@',r'\1[REDACTED]@',value)
            value=re.sub(r'(sb_secret_[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)', '[REDACTED]',value)
            value=re.sub(r'(?i)([?&](?:apikey|key|token|password|secret)=)[^&\s]+',r'\1[REDACTED]',value)
            return value
        if isinstance(value,float) and not math.isfinite(value):return str(value)
        return value
    def add(self,stage,code,level='INFO',details=None):
        cause,advice=DIAG_ADVICE.get(code,('進行・確認結果',''))
        with self.lock:
            self.count+=1
            context=getattr(DIAG_CONTEXT,'scope',{})
            event=self.clean(dict(seq=self.count,time=utc_now(),search_id=self.search_id,stage=stage,code=code,level=level,
                                 context=context,details=details or {},observed=cause,improvement=advice,
                                 _search_mode=self.search_mode,_ward_code=self.ward_code,_town_codes=self.town_codes))
            with open(self.path,'a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
            # Full event detail is already written to local JSONL; don't retain
            # a second in-memory array for the disabled legacy per-error writer.
    def records(self):
        with self.lock:
            with open(self.path,encoding='utf-8') as f:return [json.loads(line) for line in f if line.strip()]
    def release(self):
        """Discard the local *obsolete* JSONL after DB summaries are persisted."""
        with self.lock:
            old_path=self.path
            self.path=''
            if old_path:
                try:os.unlink(old_path)
                except OSError:pass
    def _persist_failures(self,db):
        """Compatibility method: errors are saved in compact diagnostic_log chunks."""
        return
    def persist(self,db,force=False):
        # A background region-update and the finishing worker may request a
        # flush concurrently.  Serialize chunk IDs and cursor progression.
        if not self.persist_lock.acquire(blocking=force):return
        try:return self._persist_locked(db,force)
        finally:self.persist_lock.release()

    def _persist_locked(self,db,force=False):
        """Persist *every* warning/error in bounded JSON chunks, plus start/finish.

        Previous v144 kept only 32 non-DB errors and eight DB errors PER SEARCH,
        losing the majority of real acquisition failures before the next job.
        All failure events are now kept; success-only chatter remains local.
        The cursor advances only after a confirmed DB write.  Every batch is
        strictly bounded by event count and approximate UTF-8 payload size.
        """
        now=time.monotonic()
        if not force and now-self.last_save<20:return
        self.last_save=now
        while True:
            with self.lock:
                if not self.path:return
                with open(self.path,encoding='utf-8') as file:
                    file.seek(self.offset)
                    selected=[];examined=0;selected_bytes=0
                    while examined<1500 and len(selected)<48:
                        before=file.tell()
                        line=file.readline()
                        if not line:break
                        event=json.loads(line)
                        keep=(diagnostic_failure_event(event)
                              or (event.get('stage')=='search' and event.get('code') in ('start','finish')))
                        if keep:
                            length=len(line.encode('utf-8'))
                            if selected and selected_bytes+length>110_000:
                                file.seek(before)
                                break
                            selected.append(event);selected_bytes+=length
                        examined+=1
                    end=file.tell()
            if not examined:
                self.storage_error=None
                return
            if selected:
                row=dict(id=f'{self.search_id}.log.{self.chunk:08d}',status='diagnostic_log',
                    started_at=selected[0]['time'],finished_at=selected[-1]['time'],
                    conditions={'search_id':self.search_id,'build':BUILD,'chunk':self.chunk,'compact':True,
                                'complete_failure_coverage':True},
                    summary={'events':selected})
                try:
                    db._saving_audit=True
                    db.save_search(row)
                except Exception as exc:
                    self.storage_error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                    return
                finally:
                    db._saving_audit=False
                self.chunk+=1
                self.persisted_events+=len(selected)
                self.persisted_failures+=sum(bool(diagnostic_failure_event(e)) for e in selected)
                self._persisted_start=self._persisted_start or any(
                    e.get('stage')=='search' and e.get('code')=='start' for e in selected)
                with self.lock:
                    persisted_seq={int(e.get('seq',-1)) for e in selected}
                    self.pending_failures=[e for e in self.pending_failures if int(e.get('seq',-1)) not in persisted_seq]
            with self.lock:self.offset=end
            if not force:
                self.storage_error=None
                return


def begin_listing(web,provider,url):
    if not hasattr(web,'local'):web.local=threading.local()
    web.local.listing_evidence=[]
    DIAG_CONTEXT.scope={**getattr(DIAG_CONTEXT,'scope',{}),'provider':provider,'listing_url':url}

def reject_listing(web,provider,url,stage,category,reason,observed=None,expected=None):
    evidence=list(getattr(getattr(web,'local',None),'listing_evidence',[]))
    if stage=='map.address_inference':
        stages={'suumo_marker_invalid':'map.marker_json','suumo_marker_unresolved':'map.room_marker',
                'map_multiple_candidates':'map.image_candidates','image_georef_missing':'map.image_metadata',
                'image_pin_error':'map.image_decode','image_pin_result':'map.image_pin',
                'outside':'map.bounds','map_address_difference':'address.town_match',
                'homes_map_image_candidates':'map.image_candidates','map_address_unresolved':'address.residential_candidates'}
        for item in reversed(evidence):
            code=item['code'];detail=item['details']
            if code not in stages:continue
            if code=='image_pin_result' and detail.get('point'):continue
            if code=='homes_map_image_candidates' and (detail.get('usable_candidates') or detail.get('in_bounds_candidates')):continue
            stage=detail.get('failed_stage') or stages[code];observed=detail
            if code=='map_address_unresolved' and detail.get('tile_errors'):stage='address.tiles';category='acquisition_failure'
            reason=detail.get('reason') or {'suumo_marker_unresolved':'物件マーカーを一意に取得できない','homes_map_image_candidates':'範囲内の地図画像ピン候補を取得できない','outside':'物件マーカーが検索範囲外','map_multiple_candidates':'物件ピン候補が複数ある','map_address_difference':'推定住所が対象町域と一致しない'}.get(code,reason)
            if code in ('outside','map_address_difference'):category='condition'
            break
    trace(web,'listing_rejected',{'provider':provider,'url':url,'decision':'excluded','failed_stage':stage,
          'category':category,'reason':reason,'observed':observed,'expected':expected,'evidence':evidence},'WARNING','rejection')
    return None

def exclusion_rows(events):
    return [{'時刻UTC':e.get('time'),'取得元':e.get('details',{}).get('provider'),'物件URL':e.get('details',{}).get('url'),
             '判定':e.get('details',{}).get('decision'),'工程':e.get('details',{}).get('failed_stage'),
             '分類':e.get('details',{}).get('category'),'理由':e.get('details',{}).get('reason'),
             '取得値':json.dumps(e.get('details',{}).get('observed'),ensure_ascii=False),
             '期待値':json.dumps(e.get('details',{}).get('expected'),ensure_ascii=False)}
            for e in events if e.get('code') in ('listing_rejected','listing_failed')]


def acquisition_failure_rows(events):
    """One CSV row per warning/error event, preserving enough evidence for later fixes.

    The same listing intentionally appears on multiple rows when it encounters multiple
    failures (for example HTTP retry -> map marker failure -> address inference failure).
    """
    rows=[]
    for e in events:
        if not diagnostic_failure_event(e):continue
        context=e.get('context') if isinstance(e.get('context'),dict) else {}
        details=e.get('details') if isinstance(e.get('details'),dict) else {}
        technical=details.get('technical_exception') if isinstance(details.get('technical_exception'),dict) else {}
        observed=details.get('observed')
        observed_dict=observed if isinstance(observed,dict) else {}
        status=details.get('status') or technical.get('status') or observed_dict.get('status')
        listing_url=(context.get('listing_url') or details.get('listing_url') or
                     (details.get('url') if '/chintai/' in str(details.get('url') or '') else ''))
        communication_url=details.get('url') or details.get('map_url') or details.get('position_source_url') or ''
        reason=details.get('reason') or details.get('message') or details.get('exception_type') or e.get('observed') or ''
        evidence=details.get('evidence') if isinstance(details.get('evidence'),list) else []
        rows.append({
            '時刻UTC':e.get('time'),'検索ID':e.get('search_id'),'通番':e.get('seq'),'収集モード':e.get('_search_mode'),
            '取得元':context.get('provider') or details.get('provider'),'地域':context.get('region'),'町名':context.get('town'),
            '物件URL':listing_url,'通信URL':communication_url,
            '工程':e.get('stage'),'失敗工程':details.get('failed_stage') or e.get('stage'),'分類':details.get('category'),
            'エラーコード':e.get('code'),'レベル':e.get('level'),'HTTP状態':status,
            '理由':reason,'例外':details.get('exception_type') or technical.get('exception_type') or '',
            '取得値':json.dumps(observed,ensure_ascii=False,separators=(',',':')) if observed is not None else '',
            '期待値':json.dumps(details.get('expected'),ensure_ascii=False,separators=(',',':')) if details.get('expected') is not None else '',
            '根拠':json.dumps(evidence,ensure_ascii=False,separators=(',',':')) if evidence else '',
            '詳細':json.dumps(details,ensure_ascii=False,separators=(',',':')),
        })
    return rows



def candidate_failure_rows(events):
    """One terminal row per failed listing candidate; this count matches the UI listing-error count."""
    latest={}
    for e in events:
        if e.get('code') not in ('listing_rejected','listing_failed'):continue
        details=e.get('details') if isinstance(e.get('details'),dict) else {}
        context=e.get('context') if isinstance(e.get('context'),dict) else {}
        url=context.get('listing_url') or details.get('url') or details.get('listing_url') or ''
        search_id=str(e.get('search_id') or '')
        if not url:continue
        key=(search_id,url)
        latest[key]={
            '時刻UTC':e.get('time'),'検索ID':search_id,'取得元':context.get('provider') or details.get('provider'),
            '地域':context.get('region'),'町名':context.get('town'),'物件URL':url,
            '処理結果':'エラー','失敗工程':details.get('failed_stage') or e.get('stage'),
            '分類':details.get('category'),'エラーコード':e.get('code'),
            '理由':details.get('reason') or details.get('message') or e.get('observed') or '',
            '取得値':json.dumps(details.get('observed'),ensure_ascii=False,separators=(',',':')),
            '期待値':json.dumps(details.get('expected'),ensure_ascii=False,separators=(',',':')),
            '根拠':json.dumps(details.get('evidence') or [],ensure_ascii=False,separators=(',',':')),
        }
    return list(latest.values())

def csv_bytes_from_rows(rows,fieldnames=None):
    output=io.StringIO()
    fields=fieldnames or (list(rows[0]) if rows else ['時刻UTC','検索ID','通番','取得元','物件URL','工程','エラーコード','理由'])
    writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def trace(web,code,details=None,level='INFO',stage='parser'):
    local=getattr(web,'local',None)
    if local is not None and hasattr(local,'listing_evidence') and code not in ('listing_rejected','listing_failed') and (level in ('WARNING','ERROR') or code in ('reverse_result','image_pin_result','homes_map_image_candidates')):
        local.listing_evidence.append({'code':code,'stage':stage,'details':details or {}})
        local.listing_evidence=local.listing_evidence[-20:]
    audit=getattr(web,'audit',None) or getattr(DIAG_CONTEXT,'audit',None)
    if audit:audit.add(stage,code,level,details)


def diagnostic_report(events):
    counts={};examples={}
    for e in events:
        if e.get('code') in DIAG_ADVICE:
            c=e['code'];counts[c]=counts.get(c,0)+1;examples.setdefault(c,e)
    lines=['住まいコンパス 詳細作業ログ・改善レポート',f"アプリ版：{next((e.get('details',{}).get('build') for e in events if e.get('code')=='start'),BUILD)}｜イベント数：{len(events)}",
           '事実と改善案を分けています。改善案は調査・修正候補であり、原因の確定や修正済みを意味しません。',
           '取得精度の評価には正解データとの照合が必要です。このログの確認/保存件数だけでは正確性・網羅性を保証しません。','', '原因別の観測と次の改善：']
    for code,count in sorted(counts.items(),key=lambda x:-x[1]):
        cause,advice=DIAG_ADVICE[code];lines.extend([f'・{cause}：{count}イベント',f'  改善候補：{advice}',f'  根拠例：{json.dumps(examples[code].get("details",{}),ensure_ascii=False)}'])
    lines.extend(['','改善後の確認手順：同じ固定範囲・取得元で再検索し、地名数、候補数、詳細確認数、理由別除外数、座標未判定数、保存確認数を比較する。',
                  '手動検索で見つかる実在物件のURLを数件用意し、このログで取得・除外・未検出の経路を確認する。','', '詳細イベント（UTC・通番・取得元/地名・通信/解析/除外・改善案）：'])
    for e in events:lines.append(json.dumps(e,ensure_ascii=False))
    return ('\n'.join(lines)+'\n').encode('utf-8-sig')



def log_metrics(events):
    counts={}
    for e in events:
        code=e.get('code','unknown');counts[code]=counts.get(code,0)+1
    finishes=[e for e in events if e.get('code')=='finish']
    summary=finishes[-1].get('details',{}).get('summary',{}) if finishes else {}
    for key in ('candidate','detail','rejected','confirmed','saved'):counts['件数:'+key]=summary.get(key,0)
    return counts


def compare_logs(old,new):
    a,b=log_metrics(old),log_metrics(new)
    return [{'項目':key,'前回':a.get(key,0),'今回':b.get(key,0),'差分':b.get(key,0)-a.get(key,0)} for key in sorted(set(a)|set(b))]

def diagnostic_downloads(events,prefix='current'):
    """Render log downloads immediately. Error CSV is unified elsewhere."""
    if not events:
        st.caption('詳細ログはまだありません。')
        return
    export_key=(events[0].get('search_id'),len(events),events[-1].get('seq'),'direct-log-v2')
    export=st.session_state.get(prefix+'_log_export')
    if not isinstance(export,dict) or export.get('format')!='direct-log-v2' or export.get('key')!=export_key:
        export={'format':'direct-log-v2','key':export_key,'txt':diagnostic_report(events),
                'json':json.dumps({'schema':1,'events':events},ensure_ascii=False,separators=(',',':')).encode('utf-8')}
        export['preview']=export['txt'].decode('utf-8-sig').split('詳細イベント')[0]
        st.session_state[prefix+'_log_export']=export
    c1,c2=st.columns(2)
    with c1:
        st.download_button('詳細ログ TXT',export['txt'],'sumai_work_log.txt','text/plain',
                           key=prefix+'_txt',on_click='ignore',use_container_width=True)
    with c2:
        st.download_button('詳細ログ JSON',export['json'],'sumai_work_log.json','application/json',
                           key=prefix+'_json',on_click='ignore',use_container_width=True)
    with st.expander('作業ログの原因別集計と改善案'):
        st.text(export['preview'])
        st.caption('全イベントはダウンロードに含まれます。画面には最新20イベントを表示します。')
        st.dataframe([{'時刻UTC':e['time'],'工程':e['stage'],'理由':e['code'],'取得元・地名':str(e.get('context',{})),
                       '詳細':str(e.get('details',{})),'改善案':e.get('improvement','')} for e in events[-20:]],hide_index=True)

def physical_units(rows):
    """Keep each acquired listing; do not merge by address, price, room size or location."""
    return list(rows)


def unique_map_position_count(rows):
    """Count visually distinct coordinate positions, not listing records or marker layers."""
    return len({(round(float(r['latitude']),6),round(float(r['longitude']),6)) for r in rows if has_point(r)})

def visible_colored_dot_count(rows):
    """KPI: visually distinguishable colored rent dots. Exact coordinate overlaps count as one dot."""
    return len({(round(float(r['latitude']),6),round(float(r['longitude']),6))
                for r in rows if has_point(r) and r.get('rent')})


DISPLAY_REASONS={
 'invalid':'保存レコードのURL・家賃等が不正',
 'bounds':'現在の地図範囲外（位置不明なら検索範囲の重なりで判定）',
 'location_missing':'位置未確認：取得情報は保持・地図配置は保留',
 'address':'保存済みの地図由来住所を読み込み時に再座標化して表示',
 'unpriced':'家賃未確認：1募集の灰色の点として表示',
 'town':'1募集を1点として表示（町丁目の位置・建物位置未確認）',
 'building':'1募集を掲載地図等から取得した座標の1点として表示'}


def display_pipeline(units,bounds,load=None,include_records=False):
    """All layouts and all prices, one point per listing; no spatial or price aggregation."""
    rows=[];records=[];points=[];counts={k:0 for k in DISPLAY_REASONS}
    for r in units:
        if not valid_unit(r):reason='invalid'
        elif not listing_in_bounds(r,bounds):reason='bounds'
        else:
            rows.append(r)
            if not has_point(r):reason='location_missing'
            else:
                points.append((r['latitude'],r['longitude']))
                reason='unpriced' if not r.get('rent') else 'town' if r.get('coordinate_precision')=='town' else 'address' if r.get('coordinate_precision')=='address' else 'building'
        counts[reason]+=1
        if include_records:records.append({'key':r.get('key'),'title':r.get('title'),'provider':r.get('provider'),'listing_url':r.get('listing_url'),
            'layout':r.get('layout'),'dwelling_type':listing_dwelling_type(r),'rent':r.get('rent'),'fees':r.get('fees'),'monthly':monthly_price(r) if r.get('rent') else None,
            'region':r.get('region_label') or r.get('address'),'latitude':r.get('latitude'),'longitude':r.get('longitude'),
            'coordinate_precision':r.get('coordinate_precision'),'location_method':r.get('location_method'),'inferred_address':r.get('inferred_address'),'position_source_url':r.get('position_source_url'),
            'reason_code':reason,'reason':DISPLAY_REASONS[reason],'merged_into':None})
    report={'schema':2,'build':BUILD,'time':utc_now(),'filters':{'bounds':bounds,'layouts':None,'monthly_limit':None,'structure':None,'age':None},
        'aggregation':False,'load':load,'stages':{'loaded':len(units),'in_bounds':len(rows),'individual_points':len(points)},
        'reason_counts':counts,'map':{'markers_total':len(points),'colored_points':counts['town']+counts['address']+counts['building'],
        'gray_points':counts['unpriced'],'unique_positions':len(set(points)),
        'colored_unique_positions':visible_colored_dot_count(rows),
        'overlapping_points':len(points)-len(set(points)),
        'renderer':'Canvas','aggregation':False,'unconfirmed_positions':sum(not has_point(r) or r.get('coordinate_precision') in ('town','address') for r in rows)}, 'records':records,
        'improvements':['位置未確認の場合は掲載URL・住所・掲載地図の読取結果を確認して座標取得を改善する。取得済み募集は捨てない。',
            '町丁目しか分からない募集はその位置を明記する。同じ座標の点は重なるが、件数をまとめたり実在しない位置へ散らしたりしない。',
            '家賃未確認の場合は一覧・詳細の金額の読取箇所を確認する。管理費未確認なら読み取れた家賃で色分けする。']}
    return rows,rows,[],report


def display_diagnostic_downloads(report,units=None,bounds=None,load=None):
    stages=report['stages'];mapping=report['map']
    st.caption(f"地図の〇 {mapping['colored_unique_positions']}個｜読み込んだ募集 {stages['loaded']}件｜現在の範囲 {stages['in_bounds']}件｜座標あり {mapping['markers_total']}件｜位置未確認 {mapping['unconfirmed_positions']}件")
    st.caption(f"地図の〇＝家賃色を付けて地図上で区別できる座標位置。完全に同じ座標へ重なる募集は1個として数えます。家賃帯対象 {mapping['colored_points']}件｜家賃未確認 {mapping['gray_points']}件｜重なり {mapping['overlapping_points']}件。")
    with st.expander('表示が少ない原因・全募集の診断ログ'):
        st.dataframe([{'理由':DISPLAY_REASONS[k],'募集件数':v} for k,v in report['reason_counts'].items()],hide_index=True)
        token=(id(units),len(units or []),tuple(bounds or ()))
        if st.button('表示診断ログを準備',key='prepare_display_diagnostics'):
            complete=display_pipeline(units or [],bounds,load,include_records=True)[3]
            lines=['住まいコンパス 1募集1点の表示診断',json.dumps({k:v for k,v in complete.items() if k!='records'},ensure_ascii=False,indent=2),'募集ごとの判定：']
            lines.extend(json.dumps(r,ensure_ascii=False) for r in complete['records'])
            output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(complete['records'][0]) if complete['records'] else ['key','reason']);writer.writeheader();writer.writerows(complete['records'])
            st.session_state['_mobile_diag_export']={'token':token,'txt':'\n'.join(lines).encode('utf-8-sig'),
                'json':json.dumps(complete,ensure_ascii=False,indent=2).encode('utf-8'),
                'csv':output.getvalue().encode('utf-8-sig')}
        export=st.session_state.get('_mobile_diag_export')
        if export and export.get('token')==token:
            st.download_button('表示原因ログをTXTでダウンロード',export['txt'],'sumai_display_log.txt','text/plain',key='display_txt',on_click='ignore')
            st.download_button('表示原因ログをJSONでダウンロード',export['json'],'sumai_display_log.json','application/json',key='display_json',on_click='ignore')
            st.download_button('全募集の表示判定をCSVでダウンロード',export['csv'],'sumai_display_decisions.csv','text/csv',key='display_csv',on_click='ignore')
        for advice in report['improvements']:st.write('・'+advice)



def rent_color(price):
    """Use the same 3万～45万 continuous scale as the active town map."""
    return town_rent_gradient(price)


def mesh(rows,center,radius,interpolate=False,bounds=None):
    """No cell averages or town aggregation: each listing has its own point."""
    return []



class MonotoneBase(MacroElement):
    """Desaturate only the base tile pane; rent colors remain untouched."""
    _template=Template("""{% macro script(this, kwargs) %}
    {{ this._parent.get_name() }}.getPane('monotoneBase').style.filter = 'grayscale(1)';
    {{ this._parent.get_name() }}.getPane('monotoneBase').style.webkitFilter = 'grayscale(1)';
    {{ this._parent.get_name() }}.getContainer().style.backgroundColor = '#ececec';
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='MonotoneBase'


class MapReferenceLabels(MacroElement):
    """Fault-isolated, deferred station labels: station JS must never block Leaflet map creation.

    The map and the rent SVG remain usable even if station-label rendering fails.
    Only labels in the viewport are materialized; nothing is fetched from the DB.
    """
    _template=Template("""{% macro script(this, kwargs) %}
    // Station labels are optional decoration. Run after the base map and tile layer
    // have had time to mount, and contain exceptions inside this callback.
    window.setTimeout(function () {
      try {
        var map = {{ this._parent.get_name() }};
        if (!map || !window.L || !map.getPane('referenceLabelPane')) return;
        var stations = {{ this.station_payload }};
        var wards = {{ this.ward_payload }};
        if (map._sumaiStationOverlay && map._sumaiStationOverlay.dispose) {
          map._sumaiStationOverlay.dispose();
        }
        var stationLayer = L.layerGroup().addTo(map);
        var wardLayer = L.layerGroup().addTo(map);
        var timer = null, disposed = false, lastKey = '';
        function htmlEscape(v) {
          return String(v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
        }
        function redraw() {
          timer = null;
          if (disposed || !map._loaded || !map.getContainer().isConnected) return;
          try {
            var zoom = map.getZoom(), bounds = map.getBounds(), size = map.getSize();
            if (!size.x || !size.y) return;
            var key = [zoom,bounds.getSouth().toFixed(4),bounds.getWest().toFixed(4),
              bounds.getNorth().toFixed(4),bounds.getEast().toFixed(4),size.x,size.y].join('|');
            if (key === lastKey) return;
            var nextStations = [], nextWards = [];
            var taken = Object.create(null);
            if (zoom <= 13) {
              wards.forEach(function(item) {
                if (bounds.contains([item.lat,item.lng])) nextWards.push(item);
              });
            }
            if (zoom >= 11) {
              var limit = zoom <= 11 ? 14 : zoom === 12 ? 22 : zoom === 13 ? 32 :
                zoom === 14 ? 44 : zoom === 15 ? 55 : 68;
              stations.forEach(function(item) {
                if (nextStations.length >= limit || !bounds.contains([item.lat,item.lng])) return;
                if (zoom <= 11 && item.priority !== 0) return;
                if (zoom === 12 && item.priority > 1) return;
                var pt = map.latLngToContainerPoint([item.lat,item.lng]);
                if (pt.x < 0 || pt.x > size.x || pt.y < 0 || pt.y > size.y) return;
                var name = /駅前$|駅$/.test(item.name) ? item.name : item.name+'駅';
                var width = name.length*12+18;
                var cellX=Math.floor(pt.x/45),cellY=Math.floor(pt.y/33);
                var span=Math.max(1,Math.ceil(width/45));
                for (var d=-span;d<=span;d++){
                  if (taken[(cellX+d)+':'+cellY]) return;
                }
                for(var d=-span;d<=span;d++) taken[(cellX+d)+':'+cellY]=true;
                nextStations.push({lat:item.lat,lng:item.lng,name:name});
              });
            }
            // Compute first, then replace only this overlay. Never alter base tiles,
            // rent SVG, map viewport, or Streamlit session state from this layer.
            stationLayer.clearLayers();
            wardLayer.clearLayers();
            nextWards.forEach(function(item) {
              L.marker([item.lat,item.lng],{pane:'referenceLabelPane',interactive:false,keyboard:false,
                icon:L.divIcon({className:'',iconSize:[0,0],html:
                  '<div style="white-space:nowrap;transform:translate(-50%,-50%);font:900 14px sans-serif;color:#202020;text-shadow:-2px -2px white,2px -2px white,-2px 2px white,2px 2px white;">'+htmlEscape(item.name)+'</div>'})
              }).addTo(wardLayer);
            });
            nextStations.forEach(function(item) {
              L.marker([item.lat,item.lng],{pane:'referenceLabelPane',interactive:false,keyboard:false,
                icon:L.divIcon({className:'',iconSize:[0,0],html:
                  '<div style="white-space:nowrap;transform:translate(-50%,-112%);display:inline-block;background:rgba(255,255,255,.96);padding:2px 4px;border:1px solid #555;border-radius:4px;font:800 12px sans-serif;color:#171717;">'+htmlEscape(item.name)+'</div>'})
              }).addTo(stationLayer);
            });
            lastKey = key;
          } catch (drawError) {
            // Decorative label failure must not break Leaflet or the rent layer.
            if (window.console && console.warn) console.warn('Station labels skipped:',drawError);
          }
        }
        function schedule() {
          if (disposed || timer !== null) return;
          timer = window.setTimeout(redraw,120);
        }
        function dispose() {
          disposed=true;
          if (timer !== null) {window.clearTimeout(timer);timer=null;}
          map.off('moveend zoomend resize',schedule);
          map.off('unload',dispose);
          document.removeEventListener('visibilitychange',resume);
          window.removeEventListener('pageshow',resume);
          stationLayer.clearLayers();wardLayer.clearLayers();
          if(map.hasLayer(stationLayer)) map.removeLayer(stationLayer);
          if(map.hasLayer(wardLayer)) map.removeLayer(wardLayer);
        }
        function resume() {
          if (document.visibilityState !== 'hidden') {lastKey='';schedule();}
        }
        map._sumaiStationOverlay={dispose:dispose};
        map.on('moveend zoomend resize',schedule);
        map.on('unload',dispose);
        document.addEventListener('visibilitychange',resume);
        window.addEventListener('pageshow',resume);
        schedule();
      } catch (initError) {
        if (window.console && console.warn) console.warn('Station labels disabled:',initError);
      }
    },400);
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='MapReferenceLabels'
        wards=[{'name':name,'lat':point[0],'lng':point[1]} for name,point in WARD_LABELS.items()]
        major=set(MAJOR_STATION_LABELS)
        points=[{'name':name,'lat':point[0],'lng':point[1],
                 'priority':0 if name in major else 1 if name in STATIONS else 2}
                for name,point in STATION_MAP_POINTS.items()]
        points.sort(key=lambda item:(item['priority'],item['name']))
        self.ward_payload=json.dumps(wards,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
        self.station_payload=json.dumps(points,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')

class MobileScrollControl(MacroElement):
    """Allow direct touch pan/pinch by default, with optional page-scroll mode."""
    _template=Template("""{% macro script(this, kwargs) %}
    (function () {
      if (!window.matchMedia || !window.matchMedia('(pointer:coarse)').matches) return;
      var map = {{ this._parent.get_name() }};
      var manipulating = false, button;
      function setMode(manipulate) {
        manipulating = manipulate;
        if (manipulating) {
          map.dragging.enable();
          if (map.touchZoom) map.touchZoom.enable();
          map.getContainer().style.touchAction = 'none';
        } else {
          map.dragging.disable();
          if (map.touchZoom) map.touchZoom.disable();
          map.getContainer().style.touchAction = 'pan-y';
        }
        if (button) {
          button.textContent = manipulating ? 'ページをスクロール' : '地図を指で動かす';
          button.setAttribute('aria-pressed', manipulating ? 'true' : 'false');
        }
      }
      var touchControl = L.control({position:'topright'});
      touchControl.onAdd = function () {
        var div = L.DomUtil.create('div','leaflet-bar');
        div.style.cssText = 'background:white;border-radius:7px;overflow:hidden;box-shadow:0 1px 5px #777;';
        button = L.DomUtil.create('button','',div);
        button.type = 'button';
        button.style.cssText = 'min-height:40px;padding:4px 10px;border:0;background:white;color:#203f39;font-size:13px;font-weight:600;';
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);
        L.DomEvent.on(button,'click',function (e) {
          L.DomEvent.stop(e);
          setMode(!manipulating);
        });
        return div;
      };
      touchControl.addTo(map);
      setMode(true);
    })();
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='MobileScrollControl'


def rental_map(rows,center,radius,cells,facilities):
    import folium
    m=folium.Map(location=center,zoom_start=15,tiles=None,control_scale=True,prefer_canvas=True,
                 zoom_control=True)
    folium.map.CustomPane('monotoneBase',z_index=200,pointer_events=False).add_to(m)
    MonotoneBase().add_to(m)
    folium.TileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png',
        attr='国土地理院',name='駅名・地名のモノトーン地図',max_zoom=18,pane='monotoneBase').add_to(m)
    folium.map.CustomPane('rentIndividualPane',z_index=410,pointer_events=False).add_to(m)
    # Reference labels live above the rent dots.  Rent dots remain translucent so the
    # underlying roads, station names and ward geography stay legible even in dense areas.
    folium.map.CustomPane('referenceLabelPane',z_index=625,pointer_events=False).add_to(m)
    MapReferenceLabels().add_to(m)
    MobileScrollControl().add_to(m)
    rental_features(rows,cells,facilities).add_to(m)
    return m


def listing_card(r,persisted):
    price=f"{float(r.get('rent') or 0)/10000:g}万円 / 月" if r.get('rent') else '家賃未確認'
    dwelling='一戸建て' if listing_dwelling_type(r)=='house' else 'マンション'
    return '<div class="unit"><h3>'+html.escape(r.get('address') or '住所未確認')+'</h3><b>'+price+'</b> · '+html.escape(dwelling)+' · '+html.escape(r.get('layout') or '間取り未確認')+'<p>住所は地図からの推定値です。<br>データ取得日時：'+html.escape(acquisition_time_jst(r.get('fetched_at')))+'</p></div>'


class IndividualRentPoints(MacroElement):
    """Transparent, grouped SVG paths for all rent dots; no per-listing DOM layers.

    Deliberately avoid HTML Canvas on Android: desynchronized GPU compositing of
    transparent canvases can turn the whole tile map black. Each price/precision
    combination is one SVG path. Viewport selection is browser-side, not a Python
    map rerun, and moveend rather than move drives costly geometry updates.
    """
    _template=Template("""{% macro script(this, kwargs) %}
    (function () {
      var map = {{ this._parent._parent.get_name() }};
      var data = {{ this.payload }};
      var pane = map.getPane('rentIndividualPane');
      if (!pane) return;
      if (map._sumaiRentalSVG) {
        var prior = map._sumaiRentalSVG;
        if (typeof prior.cleanup === 'function') prior.cleanup();
        else {
          map.off('moveend zoomend resize viewreset', prior.schedule);
          if (prior.svg.parentNode) prior.svg.parentNode.removeChild(prior.svg);
        }
      }
      var ns = 'http://www.w3.org/2000/svg';
      var svg = document.createElementNS(ns,'svg');
      svg.setAttribute('class','sumai-rent-svg');
      svg.setAttribute('aria-hidden','true');
      svg.setAttribute('focusable','false');
      svg.style.cssText = 'position:absolute;top:0;left:0;overflow:hidden;pointer-events:none;background:transparent!important;opacity:1;';
      pane.appendChild(svg);
      var grid = Object.create(null);
      var CELL = 0.01;
      for (var i=0; i<data.length; i++) {
        var d = data[i];
        var key = Math.floor(d[0]/CELL)+':'+Math.floor(d[1]/CELL);
        if (!grid[key]) grid[key]=[];
        grid[key].push(d);
      }
      var scheduled = false;
      function paint() {
        scheduled = false;
        if (!svg.isConnected) return;
        var size = map.getSize();
        if (size.x<=0 || size.y<=0) return;
        svg.setAttribute('width',size.x);
        svg.setAttribute('height',size.y);
        svg.setAttribute('viewBox','0 0 '+size.x+' '+size.y);
        svg.style.width=size.x+'px';
        svg.style.height=size.y+'px';
        L.DomUtil.setPosition(svg,map.containerPointToLayerPoint([0,0]));
        var bounds = map.getBounds().pad(0.04);
        var south=Math.floor(bounds.getSouth()/CELL),north=Math.floor(bounds.getNorth()/CELL);
        var west=Math.floor(bounds.getWest()/CELL),east=Math.floor(bounds.getEast()/CELL);
        var cells=(north-south+1)*(east-west+1),visible=[];
        if (cells>0 && cells<=3000) {
          for (var lat=south;lat<=north;lat++) {
            for (var lng=west;lng<=east;lng++) {
              var bucket=grid[lat+':'+lng];
              if (bucket) for(var j=0;j<bucket.length;j++)visible.push(bucket[j]);
            }
          }
        } else visible=data;
        // All properties stay independent. Multiple positions share one SVG path
        // per rent/precision group, greatly reducing DOM and memory pressure.
        var paths=Object.create(null),metas=Object.create(null),R=5.8;
        for (var k=0;k<visible.length;k++) {
          var item=visible[k];
          if (item[0]<bounds.getSouth() || item[0]>bounds.getNorth() ||
              item[1]<bounds.getWest() || item[1]>bounds.getEast()) continue;
          var xy=map.latLngToContainerPoint([item[0],item[1]]);
          if (xy.x< -R || xy.x>size.x+R || xy.y< -R || xy.y>size.y+R)continue;
          var group=item[2]+'|'+(item[3]?'approx':'exact');
          // Circles as closed subpaths. Round only screen pixels, never listing coordinates.
          var x=xy.x.toFixed(1),y=xy.y.toFixed(1);
          var arc='M'+(xy.x+R).toFixed(1)+' '+y+'a'+R+' '+R+' 0 1 0 '+(-2*R)+' 0a'+R+' '+R+' 0 1 0 '+(2*R)+' 0Z';
          (paths[group]||(paths[group]=[])).push(arc);
          metas[group]=item;
        }
        // Replacing <= 24 SVG path nodes only at moveend avoids touch lag.
        while(svg.firstChild) svg.removeChild(svg.firstChild);
        for (var name in paths) {
          var path=document.createElementNS(ns,'path');
          path.setAttribute('d',paths[name].join(''));
          path.setAttribute('fill',metas[name][2]);
          path.setAttribute('fill-opacity','0.70');
          path.setAttribute('stroke','#ffffff');
          path.setAttribute('stroke-opacity','0.62');
          path.setAttribute('stroke-width',metas[name][3]?'1.35':'0.75');
          if(metas[name][3])path.setAttribute('stroke-dasharray','2 2');
          svg.appendChild(path);
        }
      }
      function schedule(){
        if(scheduled || !svg.isConnected)return;
        scheduled=true;
        requestAnimationFrame(paint);
      }
      map.on('moveend zoomend resize viewreset',schedule);
      map.whenReady(schedule);
      function svgResume() {
        if (document.visibilityState && document.visibilityState !== 'visible') return;
        if (map._loaded) map.invalidateSize({pan:false,animate:false});
        schedule();
      }
      document.addEventListener('visibilitychange',svgResume);
      window.addEventListener('pageshow',svgResume);
      function cleanup(){
        map.off('moveend zoomend resize viewreset',schedule);
        map.off('unload',cleanup);
        document.removeEventListener('visibilitychange',svgResume);
        window.removeEventListener('pageshow',svgResume);
        if (svg.parentNode) svg.parentNode.removeChild(svg);
      }
      map._sumaiRentalSVG={svg:svg,schedule:schedule,cleanup:cleanup};
      map.on('unload',cleanup);
    })();
    {% endmacro %}""")
    def __init__(self,rows):
        super().__init__();self._name='IndividualRentPoints';items=[]
        for r in rows:
            if not has_point(r):continue
            items.append([float(r['latitude']),float(r['longitude']),
                rent_color(monthly_price(r)) if r.get('rent') else '#777777',
                r.get('coordinate_precision') in ('town','address')])
        self.payload=json.dumps(items,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


class ReplaceRentalFeatureLayer(MacroElement):
    """Remove superseded Folium feature groups after mobile-map updates."""
    _template=Template("""{% macro script(this, kwargs) %}
    (function () {
      var map = {{ this._parent._parent.get_name() }};
      var group = {{ this._parent.get_name() }};
      if (map._sumaiOldFeatureGroup && map._sumaiOldFeatureGroup !== group) {
        map.removeLayer(map._sumaiOldFeatureGroup);
      }
      map._sumaiOldFeatureGroup = group;
    })();
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='ReplaceRentalFeatureLayer'


def rental_features(rows,cells,facilities):
    import folium
    m=folium.FeatureGroup(name='1募集1点・家賃帯')
    IndividualRentPoints(rows).add_to(m)
    for f in facilities:
        folium.CircleMarker((f['lat'],f['lng']),radius=6,color='#fff',fill=True,fill_color='#555555',fill_opacity=1,tooltip=html.escape(f['name'])).add_to(m)
    ReplaceRentalFeatureLayer().add_to(m)
    return m


def commute(origin,destination):
    edges={name:[] for name in STATIONS}
    for line,stations in LINES.items():
        for a,b in zip(stations,stations[1:]):
            minutes=meters(STATIONS[a],STATIONS[b])*1.15/1000/40*60
            edges[a].append((b,line,minutes));edges[b].append((a,line,minutes))
    # Choose fewer train changes before considering minutes.
    todo=[(0,0.0,origin,'',())];seen=set()
    while todo:
        changes,minutes,node,last,path=heapq.heappop(todo)
        if (node,last) in seen: continue
        seen.add((node,last))
        if node==destination: return dict(minutes=minutes,transfers=changes,path=path)
        for next_node,line,duration in edges[node]:
            change=int(bool(last) and last!=line)
            heapq.heappush(todo,(changes+change,minutes+duration+5*change,next_node,line,path+((node,next_node,line),)))
    raise AppError('登録路線では経路を計算できません。')


def fetch_facilities(center,kind):
    tags={'スーパー':'shop=supermarket','コンビニ':'shop=convenience','公園':'leisure=park','病院':'amenity~"hospital|clinic"'}
    query=f'[out:json][timeout:20];nwr[{tags[kind]}](around:1000,{center[0]},{center[1]});out center tags;'
    try:
        r=requests.post('https://overpass-api.de/api/interpreter',data={'data':query},timeout=(4,25),allow_redirects=False)
        r.raise_for_status();data=r.json()
    except (requests.RequestException,ValueError): raise AppError('周辺施設を取得できません。時間を置いて再試行してください。') from None
    out=[]
    for el in data.get('elements',[]):
        p=el.get('center') or el;name=el.get('tags',{}).get('name')
        if name and 'lat' in p and 'lon' in p: out.append(dict(name=name,lat=p['lat'],lng=p['lon']))
    return out


def csv_bytes(rows):
    keys=['property_id','rent','layout','address','dwelling_type','fetched_at'];file=io.StringIO();writer=csv.DictWriter(file,fieldnames=keys);writer.writeheader()
    for row in rows:
        compact=compact_saved_listing(row)
        if compact:writer.writerow(compact)
    return file.getvalue().encode('utf-8-sig')


def read_csv(content):
    try:
        rows=[]
        for raw in csv.DictReader(io.StringIO(content.decode('utf-8-sig'))):
            row={'property_id':normal(raw.get('property_id')) or None,'rent':int(raw['rent']),'layout':normal(raw['layout']),'address':normal(raw['address']),
                 'dwelling_type':normalized_dwelling_type(raw.get('dwelling_type') or 'mansion'),'fetched_at':normal(raw.get('fetched_at')) or None}
            compact=compact_saved_listing(row)
            if not compact:raise ValueError()
            key=compact_listing_key(compact);rows.append({'key':key,'title':compact['address'],**compact,'fetched_at':compact.get('fetched_at'),'fees':0,'loaded_from_compact_storage':True})
        if not rows:raise ValueError()
        return rows
    except (UnicodeError,ValueError,KeyError,TypeError,csv.Error):raise AppError('家賃・間取り・住所と、任意の取得日時（fetched_at）列を持つCSVを指定してください。') from None


def geocode_saved_address(web,address):
    # Town/chome is a polygon label, never a unit-level point. Avoid false pins.
    if saved_address_requires_reacquisition(address):return None
    data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json()
    if not isinstance(data,list):return None
    wanted=address_key(address);candidates=[]
    for item in data:
        try:
            lng,lat=map(float,item['geometry']['coordinates']);title=normal(item.get('properties',{}).get('title',''))
            if not (34<=lat<=37 and 138<=lng<=141):continue
            score=3 if address_key(title)==wanted else 2 if wanted in address_key(title) or address_key(title) in wanted else 1
            candidates.append((score,len(title),lat,lng,title))
        except (KeyError,ValueError,TypeError):continue
    if not candidates:return None
    _,_,lat,lng,title=max(candidates,key=lambda x:(x[0],x[1]))
    return lat,lng,title


def geocode_saved_address_cached(web,address):
    if saved_address_requires_reacquisition(address):return None
    memory=getattr(web,'address_points',None)
    cached=cached_saved_position(memory,address)
    if cached:return cached
    if not memory:return geocode_saved_address(web,address)
    key=normal(address)
    with memory['lock']:
        flight=memory['flights'].get(key);owner=flight is None
        if owner:flight=futures.Future();memory['flights'][key]=flight
    if not owner:
        while not flight.done():web.pause(.05)
        try:return flight.result()
        except SearchCancelled:
            web.check_cancel()
            with memory['lock']:
                if memory['flights'].get(key) is flight:memory['flights'].pop(key,None)
            return geocode_saved_address_cached(web,address)
    try:
        point=geocode_saved_address(web,address)
        remember_saved_position(memory,address,point);flight.set_result(point);return point
    except BaseException as exc:
        flight.set_exception(exc);raise
    finally:
        with memory['lock']:
            if memory['flights'].get(key) is flight:memory['flights'].pop(key,None)


def hydrated_listing(row,point):
    if saved_address_requires_reacquisition(row.get('address')):
        return dict(row,latitude=None,longitude=None,coordinate_precision='town',
                    location_method='町丁目のみ確認：建物の座標を表示しない')
    if not point:return dict(row,coordinate_precision='unknown',location_method='保存住所の地図配置を確認できない')
    lat,lng,title=point
    return dict(row,latitude=lat,longitude=lng,coordinate_precision='address',location_method='保存済み地図由来住所を読み込み時に住所検索',map_address=title,address_match='保存住所から再配置')


def hydrate_saved_units(rows,bounds,cache=None,web=None,notify=None):
    cache=cache if isinstance(cache,dict) else {};addresses=list(dict.fromkeys(r['address'] for r in rows if r.get('address')));web=web or PublicWeb()
    def locate_address(address):
        web.check_cancel()
        if cache.get(address):return cache[address]
        return geocode_saved_address_cached(web,address)
    pending=[]
    for address in addresses:
        web.check_cancel()
        if saved_address_requires_reacquisition(address):
            cache[address]=None
            if notify:notify(address,None,None)
            continue
        memory=getattr(web,'address_points',None)
        point=cache.get(address) or (cached_saved_position(memory,address) if memory else None)
        if point:
            cache[address]=point
            if notify:notify(address,point,None)
        else:pending.append(address)
    for address,point,error in bounded_results(pending,locate_address,workers=2,stop_event=web.cancel_event):
        if address is None:continue
        cache[address]=point if not error else None
        if notify:notify(address,cache[address],error)
    # The callers already update their own rows in notify(); avoid building
    # another 12,000 full dictionaries solely to discard the returned list.
    return rows,cache


@st.cache_resource
def saved_load_snapshot_registry():
    # Per-server snapshot, immutable after publication; no Supabase secret stored.
    return {},threading.RLock()


class SavedLoadJob:
    """DB rows become available independently of geocoding; no Streamlit in worker."""
    def __init__(self,db,bounds,cache=None,force_full=False):
        self.db=db;self.bounds=bounds;self.token=hashlib.sha256(os.urandom(32)).hexdigest();self.server_key=manual_registry_key(db)
        snapshots,snapshot_lock=saved_load_snapshot_registry()
        with snapshot_lock:
            baseline=snapshots.get(self.server_key)
        self.baseline=None if force_full or not isinstance(baseline,dict) or baseline.get('build')!=BUILD else baseline
        self.mode='delta' if self.baseline is not None else 'full'
        self.last_activity_at=time.monotonic()
        self.lock=threading.RLock();self.cancel_event=threading.Event();self.rows={};self.cache={}  # Session points lack timestamps; reuse only the TTL-controlled server cache.
        self.phase='database';self.finished=False;self.error='';self.complete_db=False;self.pages=0;self.total_addresses=0;self.done_addresses=0;self.placed=0;self.persistent_cache_hits=0;self.geocoded_addresses=0;self.diagnostic={};self.started=time.monotonic();self.started_at_utc=utc_now();self.ended=None
        self.catchup_rounds=0;self.catchup_rows=0;self.catchup_changes=0;self.latest_checked_at=None;self.storage_record_audit=[]
        self._map_metrics=(0,0);self._map_metrics_at=0.;self._map_metrics_count=-1;self._map_metrics_phase=''
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-saved-loader')
    def snapshot(self):
        with self.lock:
            end=self.ended if self.ended is not None else time.monotonic();diag=dict(self.diagnostic or {})
            # Do not scan every saved listing on every 8-second UI poll.
            # The choropleth is independent of listing coordinates; do not
            # allocate large coordinate sets merely to update a progress label.
            if self._map_metrics_count!=len(self.rows):
                self._map_metrics=(0,0)
                self._map_metrics_count=len(self.rows)
            unique_dots,colored_dots=self._map_metrics if self.phase!='database' else (0,0)
            return {'token':self.token,'phase':self.phase,'finished':self.finished,'error':self.error,'complete_db':self.complete_db,
                'rows':len(self.rows),'pages':self.pages,'addresses':self.total_addresses,'done_addresses':self.done_addresses,'placed':self.placed,
                'unique_positions':unique_dots,
                'visible_colored_dots':colored_dots,
                'raw_records':int(diag.get('raw_records') or 0),'accepted_records':int(diag.get('accepted_records') or 0),
                'property_id_records':int(diag.get('property_id_records') or 0),'legacy_records':int(diag.get('legacy_records') or 0),
                'legacy_shadowed':int(diag.get('legacy_shadowed') or 0),'legacy_unmatched':int(diag.get('legacy_unmatched') or 0),
                'persistent_cache_hits':self.persistent_cache_hits,'geocoded_addresses':self.geocoded_addresses,
                'catchup_rounds':self.catchup_rounds,'catchup_rows':self.catchup_rows,'catchup_changes':self.catchup_changes,
                'latest_checked_at':self.latest_checked_at,'elapsed':round(end-self.started,1),'stopping':self.cancel_event.is_set(),
                'mode':self.mode,'idle_seconds':round(time.monotonic()-self.last_activity_at,1)}
    def export(self):
        # Row dictionaries are snapshots: callers receive an outer list only.
        # The processing worker always replaces rows rather than modifying them
        # in place, and map display treats each row as read-only.
        with self.lock:return list(self.rows.values()),dict(self.cache),dict(self.diagnostic)
    def export_storage_record_audit(self):
        # Build diagnostic rows only when explicitly requested.
        with self.lock:
            return [{'Supabase行ID':r.get('storage_id'),'物件ID':r.get('property_id') or '',
                     '家賃':r.get('rent'),'間取り':r.get('layout'),
                     '住所':r.get('address'),'取得日時':r.get('fetched_at') or '',
                     '分類':'物件ID確認済み・地図表示対象' if r.get('property_id') else '旧形式IDなし・地図表示対象'}
                    for r in self.rows.values()]
    def request_stop(self):self.cancel_event.set()
    def run_delta(self,web):
        previous=self.baseline
        with self.lock:
            self.phase='delta'
            # Rows are immutable snapshots: share the individual row dictionaries,
            # and copy only the map index that will receive delta replacements.
            self.rows=dict(previous['rows'])
            self.cache=dict(previous['cache'])
            self.storage_record_audit=[]
            self.diagnostic=dict(previous.get('diagnostic') or {})
            self.complete_db=True
            self.total_addresses=len({r.get('address') for r in self.rows.values() if r.get('address')})
            self.placed=sum(has_point(r) for r in self.rows.values())
        seen_audit={str(r.get('storage_id')) for r in self.rows.values() if r.get('storage_id')}
        old_keys=set(self.rows)
        marker=previous['as_of']
        # Overlap catches writes racing with the previous snapshot publication.
        dt=datetime.fromisoformat(marker.replace('Z','+00:00'))-timedelta(seconds=15)
        since=dt.isoformat(timespec='seconds')
        new_addresses=set()
        delta_seen=set()
        for step in range(2):
            web.check_cancel()
            changed=self.db.load_units_since(since if step==0 else self.started_at_utc,cancel_event=self.cancel_event)
            self.last_activity_at=time.monotonic()
            delta_seen.update(str(r.get('storage_id') or '') for r in changed)
            with self.lock:
                self.catchup_rounds+=1
                self.catchup_rows+=len(changed)
            if not changed and step==0:
                break
            for r in changed:
                address=r.get('address') or ''
                if address and address not in self.cache:new_addresses.add(address)
                point=self.cache.get(address) or cached_saved_position(web.address_points,address)
                hydrated=hydrated_listing(r,point)
                with self.lock:
                    self.rows[r['key']]=hydrated
                rid=r.get('storage_id')
                if rid:seen_audit.add(rid)
            with self.lock:
                self.catchup_changes=len(set(self.rows)-old_keys)
        if new_addresses:
            for address in new_addresses:
                remembered=self.cache.get(address) or cached_saved_position(web.address_points,address)
                if remembered:self.cache[address]=remembered
            missing=[a for a in new_addresses if not self.cache.get(a)]
            points=self.db.load_address_points(missing,self.cancel_event) if missing else {}
            self.cache.update(points)
            missing=[a for a in missing if a not in points]
            if missing:
                representatives={}
                for r in self.rows.values():
                    if r.get('address') in missing and r['address'] not in representatives:
                        representatives[r['address']]=r
                newly_found={}
                def found(address,point,error):
                    self.last_activity_at=time.monotonic()
                    self.done_addresses+=1
                    if point:newly_found[address]=point
                hydrate_saved_units(list(representatives.values()),self.bounds,self.cache,web,found)
                if newly_found:
                    try:self.db.save_address_points(newly_found)
                    except AppError as exc:self.diagnostic['delta_cache_save_error']=str(exc)
            with self.lock:
                self.persistent_cache_hits=len(points)
                for key,r in list(self.rows.items()):
                    if r.get('address') in new_addresses:
                        self.rows[key]=hydrated_listing(r,self.cache.get(r['address']))
        # Previous legacy rows can be shadowed by newer ID-backed observations.
        represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in self.rows.values() if r.get('property_id')}
        with self.lock:
            self.rows={key:r for key,r in self.rows.items() if r.get('property_id') or compact_listing_key(r) not in represented}
            self.placed=sum(has_point(r) for r in self.rows.values())
            self.total_addresses=len({r.get('address') for r in self.rows.values() if r.get('address')})
            self.done_addresses=self.total_addresses
            self.latest_checked_at=utc_now()
            self.diagnostic.update(load_mode='incremental',delta_rows=len(delta_seen),returned=len(self.rows))
            self.phase='complete'

    def run(self):
        web=PublicWeb();web.cancel_event=self.cancel_event;web.address_points=getattr(self.db,'address_points',None)
        def page(rows,diag):
            with self.lock:
                for row in rows:
                    point=self.cache.get(row['address']) or cached_saved_position(web.address_points,row['address'])
                    if point:self.cache[row['address']]=point
                    self.rows[row['key']]=hydrated_listing(row,point)
                self.pages+=1;self.placed=sum(has_point(r) for r in self.rows.values());self.diagnostic=diag
                self.last_activity_at=time.monotonic()
            pressure=cloud_memory_pressure()
            if memory_needs_guard(pressure):
                self.error=('メモリ保護により一時停止：'+str(pressure['used_mib'])+'/'+
                            str(pressure['limit_mib'])+' MiB。読込済み物件は保持しています。')
                self.cancel_event.set()
        def row_signature(row):
            return (row.get('property_id'),row.get('rent'),row.get('layout'),row.get('address'),listing_dwelling_type(row),row.get('fetched_at'))
        def remove_legacy_duplicates(rows):
            rows=list(rows)
            represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in rows if r.get('property_id')}
            rows=[r for r in rows if r.get('property_id') or compact_listing_key(r) not in represented]
            return {compact_listing_key(r):r for r in rows if compact_listing_key(r)}
        try:
            if self.baseline is not None:
                try:
                    self.run_delta(web)
                finally:
                    # Release the previous complete snapshot as soon as the delta
                    # job has copied its outer indexes. The registry keeps only
                    # the newest published snapshot.
                    self.baseline=None
                return
            stored=self.db.load_units(None,on_progress=page,cancel_event=self.cancel_event)
            with self.lock:
                self.diagnostic=dict(getattr(self.db,'last_load_diagnostic',self.diagnostic) or self.diagnostic)
                self.storage_record_audit=[]
                known_storage_ids={str(r.get('storage_id')) for r in stored if r.get('storage_id')}
            addresses=list(dict.fromkeys(r['address'] for r in stored));known_addresses=set(addresses);by_address={a:[] for a in addresses}
            # Persistent cache is loaded in one paged DB pass.  This avoids repeating
            # GSI address searches on every click after the first successful placement.
            persistent={}
            for address in addresses:
                hit=self.cache.get(address) or cached_saved_position(web.address_points,address)
                if hit:self.cache[address]=hit
            needs_lookup=[address for address in addresses if address not in self.cache]
            try:persistent=self.db.load_address_points(needs_lookup,self.cancel_event)
            except AppError as exc:
                with self.lock:self.diagnostic['persistent_address_cache_load_error']=str(exc)
            for address,point in persistent.items():
                self.cache[address]=point
                remember_saved_position(web.address_points,address,point)
            with self.lock:
                self.persistent_cache_hits=len(persistent)
                self.rows={r['key']:hydrated_listing(r,self.cache.get(r['address']) or cached_saved_position(web.address_points,r['address'])) for r in stored}
                self.complete_db=True;self.phase='positions';self.total_addresses=len(addresses);self.placed=sum(has_point(r) for r in self.rows.values())
                for row in stored:by_address[row['address']].append(row['key'])
            newly_geocoded={}
            def positioned(address,point,error):
                with self.lock:
                    self.done_addresses+=1
                    for key in by_address[address]:
                        old=self.rows[key];new=hydrated_listing(old,point);self.rows[key]=new
                        self.placed+=int(has_point(new))-int(has_point(old))
                    self.cache[address]=point
                    if point and address not in persistent:newly_geocoded[address]=point;self.geocoded_addresses+=1
            hydrate_saved_units(stored,self.bounds,self.cache,web,positioned)
            if newly_geocoded:
                try:
                    self.db.save_address_points(newly_geocoded)
                    with self.lock:self.diagnostic['persistent_address_cache_saved']=len(newly_geocoded)
                except AppError as exc:
                    with self.lock:self.diagnostic['persistent_address_cache_save_error']=str(exc)

            # Refresh barrier: listings can be committed while the full DB scan and
            # address preparation are running. Query only rows upserted since this job
            # started and merge them before declaring the load complete. Repeat briefly
            # so writes that land during the first catch-up are also visible.
            with self.lock:self.phase='refreshing'
            for _ in range(3):
                web.check_cancel()
                delta=self.db.load_units_since(self.started_at_utc,cancel_event=self.cancel_event)
                with self.lock:
                    self.catchup_rounds+=1;self.catchup_rows=max(self.catchup_rows,len(delta))
                    before={k:row_signature(v) for k,v in self.rows.items()}
                if delta:
                    new_storage=[r for r in delta if r.get('storage_id') and str(r.get('storage_id')) not in known_storage_ids]
                    if new_storage:
                        with self.lock:
                            self.diagnostic['raw_records']=int(self.diagnostic.get('raw_records') or 0)+len(new_storage)
                            self.diagnostic['accepted_records']=int(self.diagnostic.get('accepted_records') or 0)+len(new_storage)
                            self.diagnostic['property_id_records']=int(self.diagnostic.get('property_id_records') or 0)+sum(bool(r.get('property_id')) for r in new_storage)
                            self.diagnostic['legacy_records']=int(self.diagnostic.get('legacy_records') or 0)+sum(not bool(r.get('property_id')) for r in new_storage)
                            # No duplicate per-record audit dictionaries in RAM.
                            pass
                        known_storage_ids.update(str(r.get('storage_id')) for r in new_storage)
                    delta_addresses=list(dict.fromkeys(r['address'] for r in delta if r.get('address')))
                    new_addresses=[a for a in delta_addresses if a not in known_addresses]
                    persistent_delta={}
                    if new_addresses:
                        try:persistent_delta=self.db.load_address_points(new_addresses,self.cancel_event)
                        except AppError as exc:
                            with self.lock:self.diagnostic['catchup_address_cache_load_error']=str(exc)
                        for address,point in persistent_delta.items():
                            self.cache[address]=point;remember_saved_position(web.address_points,address,point)
                        unresolved=[]
                        for address in new_addresses:
                            point=self.cache.get(address) or cached_saved_position(web.address_points,address)
                            if point:
                                self.cache[address]=point
                                with self.lock:self.done_addresses+=1
                            else:
                                representative=next((r for r in delta if r.get('address')==address),None)
                                if representative:unresolved.append(representative)
                        with self.lock:self.total_addresses+=len(new_addresses)
                        catchup_geocoded={}
                        def catchup_positioned(address,point,error):
                            with self.lock:
                                self.done_addresses+=1;self.cache[address]=point
                                if point and address not in persistent_delta:catchup_geocoded[address]=point;self.geocoded_addresses+=1
                        if unresolved:hydrate_saved_units(unresolved,self.bounds,self.cache,web,catchup_positioned)
                        if catchup_geocoded:
                            try:self.db.save_address_points(catchup_geocoded)
                            except AppError as exc:
                                with self.lock:self.diagnostic['catchup_address_cache_save_error']=str(exc)
                        known_addresses.update(new_addresses)
                    hydrated_delta=[hydrated_listing(r,self.cache.get(r['address']) or cached_saved_position(web.address_points,r['address'])) for r in delta]
                    with self.lock:
                        for row in hydrated_delta:self.rows[row['key']]=row
                        self.rows=remove_legacy_duplicates(self.rows.values())
                        self.placed=sum(has_point(r) for r in self.rows.values())
                        self.total_addresses=len({r.get('address') for r in self.rows.values() if r.get('address')})
                        self.done_addresses=max(self.done_addresses,self.total_addresses)
                with self.lock:
                    after={k:row_signature(v) for k,v in self.rows.items()}
                    changed=sum(before.get(k)!=v for k,v in after.items())+sum(k not in after for k in before)
                    self.catchup_changes+=changed;self.latest_checked_at=utc_now()
                if not delta or changed==0:break
            with self.lock:self.phase='complete'
        except SearchCancelled:
            with self.lock:self.phase='stopped'
        except Exception as exc:
            with self.lock:self.error=str(exc) if isinstance(exc,AppError) else '読み込みエラー（'+type(exc).__name__+'）';self.phase='failed'
        finally:
            if self.phase=='complete' and not self.cancel_event.is_set():
                snapshots,snapshot_lock=saved_load_snapshot_registry()
                with self.lock:
                    # Publish references to finished immutable dictionaries, not
                    # additional 12k-record copies. Delta jobs copy only the outer
                    # index before making changes; this snapshot remains unchanged.
                    published={'build':BUILD,'as_of':self.started_at_utc,
                        'rows':self.rows,'cache':self.cache,
                        'diagnostic':dict(self.diagnostic)}
                with snapshot_lock:snapshots[self.server_key]=published
            with self.lock:
                self.diagnostic.update(geocoded_total=self.placed,returned=len(self.rows),database_complete=self.complete_db,
                                       persistent_address_cache_hits=self.persistent_cache_hits,newly_geocoded_addresses=self.geocoded_addresses,
                                       catchup_rounds=self.catchup_rounds,catchup_rows=self.catchup_rows,catchup_changes=self.catchup_changes,latest_checked_at=self.latest_checked_at)
                self.ended=time.monotonic();self.finished=True


@st.cache_resource
def saved_load_registry():return {},threading.RLock()


def active_saved_load():
    jobs,lock=saved_load_registry();token=st.session_state.get('saved_load_token');key=st.session_state.get('saved_load_key')
    with lock:
        job=jobs.get(key)
        current_key=st.session_state.get('current_storage_key',key)
        return job if job and job.token==token and current_key==key else None


def start_saved_load(bounds,cache=None,db=None,force_full=False):
    db=db or Database();key=manual_registry_key(db);jobs,lock=saved_load_registry()
    with lock:
        current=jobs.get(key)
        if current and not current.snapshot()['finished']:job=current
        else:
            job=SavedLoadJob(db,bounds,cache,force_full=force_full);jobs[key]=job;job.thread.start()
    st.session_state.saved_load_token=job.token;st.session_state.saved_load_key=key
    st.session_state.pop('saved_load_export',None)
    st.session_state.pop('saved_record_audit_export',None)
    st.session_state.pop('saved_load_applied_token',None)
    st.session_state.pop('new_notice',None)
    return job


def restore_saved_display():
    """Restore rendered saved units after mobile WebSocket/session reconnect.

    The completed snapshot is server-local and contains only already-loaded rows
    (no Supabase request). In-flight workers are reattached by the storage key.
    After a process restart the snapshot disappears, so a tiny URL marker allows
    a single asynchronous DB restoration instead of silently displaying nothing.
    """
    state=st.session_state
    key=state.get('current_storage_key')
    if not key or state.get('_saved_display_restore_checked')==key:
        return
    state['_saved_display_restore_checked']=key
    # Do not overwrite explicit manual searches or already restored UI state.
    if state.get('new_map_loaded'):
        return
    jobs,job_lock=saved_load_registry()
    snapshots,snapshot_lock=saved_load_snapshot_registry()
    with job_lock:
        job=jobs.get(key)
    with snapshot_lock:
        published=snapshots.get(key)
    if job is not None:
        state.saved_load_key=key
        state.saved_load_token=job.token
    if isinstance(published,dict) and published.get('rows'):
        # Reuse the complete address/coordinate mapping; no second geocoding.
        state.new_units=list(published['rows'].values())
        state.new_saved_keys=list(published['rows'])
        state.address_point_cache=dict(published.get('cache') or {})
        state.new_load_diagnostic=dict(published.get('diagnostic') or {})
        state.new_map_loaded=True
        state.pop('_mobile_map_payload_signature',None)
        state.pop('new_map',None)
        state.saved_map_restore_message='保存済み物件をサーバーの読込済みデータから復元しました。'
        if job and job.snapshot().get('phase')=='complete':
            state.saved_load_applied_token=job.token
        return
    if job is not None:
        # A previous browser session may have left the worker loading the DB.
        # Recover partial records as soon as possible without starting another job.
        rows,cache,diag=job.export()
        if rows:
            state.new_units=rows
            state.new_saved_keys=[r['key'] for r in rows]
            state.address_point_cache=cache
            state.new_load_diagnostic=diag
            state.new_map_loaded=True
            state.pop('_mobile_map_payload_signature',None)
            state.pop('new_map',None)
            state.saved_map_restore_message='前の画面で読み込み済みの物件を再表示しました。'
            if job.snapshot().get('phase')=='complete':
                state.saved_load_applied_token=job.token
        return
    # Streamlit Cloud process restarts clear cache_resource. In that case the
    # browser URL remembers that saved data had been loaded, and a single DB
    # worker reloads the persisted rows without requiring another tap.
    try:
        request_restore=str(st.query_params.get('saved_map',''))=='1'
    except (AttributeError,TypeError,ValueError):
        request_restore=False
    if request_restore and not state.get('_saved_restore_db_started'):
        # The map now needs ONLY town summaries, not 12,000+ individual rows.
        # Avoid 3 simultaneous copies (job, session, snapshot) at boot.
        state['_saved_restore_db_started']=True
        state.saved_map_restore_message='地図は町丁目集計から表示します。個別物件は「保存物件の一覧を読み込む」で確認できます。'
        try:st.query_params.pop('saved_map',None)
        except (AttributeError,TypeError,ValueError):pass


def manual_search_running():
    jobs,lock=job_registry();token=st.session_state.get('new_job_token');server_key=st.session_state.get('manual_server_key')
    with lock:
        job=jobs.get(token)
        if job and not job.finished:return True
        return any(getattr(j,'server_key',None)==server_key and not j.finished for j in jobs.values()) if server_key else False


def apply_saved_load(job):
    if active_saved_load() is not job or manual_search_running():return False
    rows,cache,diag=job.export();state=st.session_state
    for stale in ('_mobile_units_export','_mobile_diag_export','saved_load_export','saved_record_audit_export','_mobile_listing_select_ready','_mobile_listing_table_ready'):
        state.pop(stale,None)
    state.address_point_cache=cache;state.new_units=rows;state.new_saved_keys=[r['key'] for r in rows]
    state.new_search=None;state.new_map_loaded=True;state.new_load_diagnostic=diag;state.pop('new_job_token',None)
    try:st.query_params['saved_map']='1'
    except (AttributeError,TypeError,ValueError):pass
    # Individual saved listings are for the list/CSV only.  The map uses the
    # independently persisted town averages, so do not do O(n) dot counting.
    state.new_notice=f'一覧読込完了：{len(rows):,}件'
    return True


@st.fragment(run_every='8s')
def saved_load_progress():
    """Compact progress for the optional listing/CSV view, NOT the map."""
    job=active_saved_load()
    if not job:return
    snap=job.snapshot();searching=manual_search_running()
    applied=st.session_state.get('saved_load_applied_token')==job.token
    if snap['finished'] and snap['phase']=='complete' and not applied and not searching:
        if apply_saved_load(job):
            st.session_state.saved_load_applied_token=job.token
            st.rerun()
    if snap['phase'] in ('database','delta','refreshing'):
        st.caption(f"一覧読込中：{snap['rows']:,}件｜{snap['elapsed']}秒")
    elif snap['phase']=='complete':
        st.caption(f"読込完了：{snap['rows']:,}件")
    elif snap['phase']=='failed':
        st.error(f"一覧の読込失敗：{snap['rows']:,}件まで確認")
    elif snap['phase']=='stopped':
        st.caption(f"読込停止：{snap['rows']:,}件まで確認")
    else:
        st.caption(f"一覧を準備中：{snap['rows']:,}件｜{snap['elapsed']}秒")
    if snap.get('error'):st.error(snap['error'])
    if not applied and snap['rows'] and not searching:
        if st.button('途中までの物件を一覧に表示',key='apply_saved_load'):
            if apply_saved_load(job):
                if snap['finished'] and snap['phase']=='complete':
                    st.session_state.saved_load_applied_token=job.token
                st.rerun()
    if not snap['finished'] and st.button('読込を中止',key='stop_saved_load',disabled=snap['stopping']):
        job.request_stop()


def capture_viewport():
    data=st.session_state.get('new_map',{})
    bounds=viewport_bounds(data)
    if bounds:
        # Keep only the search bounds.  Feeding the browser's center/zoom straight back
        # into st_folium on every move caused repeated setView operations and visible
        # snap/jank while dragging or zooming a map with thousands of markers.
        st.session_state.new_bounds=bounds
    else:
        st.session_state.new_bounds=None


def remember_search():
    state=st.session_state
    if automatic_busy():
        state.new_search={'status':'failed','summary':{'issues':['自動収集を停止してから手動検索を開始してください。'],'saved':0,'confirmed':0}}
        return
    if state.get('manual_search_scope','地図の表示範囲')=='区・町名を選択':
        code=state.get('manual_ward','13116')
        selected=list(state.get('new_providers',[]))
        providers=[canonical_provider(p) for p in selected]
        providers=[p for p in dict.fromkeys(providers) if p=='SUUMO']
        if not providers:
            state.new_search={'status':'failed','summary':{'issues':['区・町名検索ではSUUMOを取得元に選択してください。'],'saved':0,'confirmed':0}}
            return
        state.new_request={'bounds':[34,138,37,141],'providers':providers,'ward_code':code,
                           'town_codes':list(state.get('manual_selected_'+code,[])), 'mode':'ward_search',
                           'display':{'layouts':'all','monthly_limit':None,'aggregation':False}}
        state.new_map_loaded=True
        return
    selected=list(state.get('new_providers',[]))
    providers=[p for p in dict.fromkeys(canonical_provider(p) for p in selected) if p=='SUUMO']
    bounds=state.get('new_bounds')
    state.new_preferences={'new_providers':selected}
    state.new_map_loaded=True
    if not providers or not bounds:
        state.new_search={'status':'failed','summary':{'issues':['地図の表示範囲と取得元を確認してください。'],'saved':0,'confirmed':0}}
        return
    state.new_request=dict(bounds=list(bounds),providers=providers,display={'layouts':'all','monthly_limit':None,'aggregation':False})



class WorkerState:
    def __init__(self):
        self.new_units=[];self.new_search=None;self.new_saved_keys=[];self.new_regions=[];self.live_metrics={'processed':0,'saved':0,'errors':0,'skipped':0};self.last_activity_at=time.monotonic();self.last_collection_stage='検索の準備中'

class SearchJob:
    def __init__(self,db,conditions):
        self.lock=threading.RLock();self.state=WorkerState();self.cancel_event=threading.Event();self.state.cancel_event=self.cancel_event;self.audit=AuditLog(conditions,(getattr(db,'key',''),));self.state.audit=self.audit;self.started_at=utc_now();self.updated_at=self.started_at;self.progress=0.;self.message='バックグラウンド検索を開始しています';self.text='';self.finished=False
        self.thread=threading.Thread(target=self.run,args=(db,conditions),daemon=True,name='housing-search')
    def run(self,db,conditions):
        job=self
        class Display:
            def progress(self,value,text=''):
                with job.lock: job.progress=value
            def info(self,value):
                with job.lock: job.message=value;job.updated_at=utc_now()
            def code(self,value,language=None):
                with job.lock: job.text=value
        screen={k:Display() for k in ('bar','status','log')}
        try: search_all(db,conditions,screen,self.state)
        except Exception as exc:
            full_traceback=traceback.format_exc()
            frames=traceback.extract_tb(exc.__traceback__)
            at=next((f for f in reversed(frames) if f.filename.endswith('.py')),frames[-1] if frames else None)
            location=(at.name+':'+str(at.lineno)) if at else 'unknown'
            message=str(exc) if isinstance(exc,AppError) else type(exc).__name__+': '+str(exc)
            reason='検索処理が異常終了｜発生箇所 '+location+': '+message
            self.audit.add('search',diagnosis_code(message),'ERROR',{
                'message':message,'exception_type':type(exc).__name__,
                'exception_location':location,'traceback':full_traceback})
            self.state.new_search={'status':'failed','conditions':conditions,'summary':{
                'issues':[reason], 'exception_type':type(exc).__name__,
                'exception_location':location,'saved':len(self.state.new_saved_keys),
                'confirmed':len(self.state.new_units),
                'processed':int((self.state.live_metrics or {}).get('processed',0)),
                'errors':int((self.state.live_metrics or {}).get('errors',0)),
                'already_acquired':int((self.state.live_metrics or {}).get('skipped',0))}}
        finally:
            try:self.audit.persist(db,force=True)
            except Exception:pass
            with self.lock: self.finished=True
            # The automatic controller releases its update only after the whole
            # selected collection stops. Manual searches can refresh promptly.
            if conditions.get('mode')!='automatic_collection':
                notify_town_collection_finished(db)
    def request_stop(self):
        self.cancel_event.set()
        with self.lock:self.message='中止要求を受け付けました。新しい取得を止め、通信終了後に取得済みデータを保存・確認して終了します。'
    def snapshot(self,include_units=True):
        with self.lock:
            result=dict(progress=self.progress,message=self.message,log=self.text,finished=self.finished,stopping=self.cancel_event.is_set(),
                        updated_at=getattr(self,'updated_at',None),last_saved_at=getattr(self.state,'new_last_saved_at',None),
                        live_metrics=dict(getattr(self.state,'live_metrics',{}) or {}),result=self.state.new_search,
                        unit_count=len(self.state.new_units),saved_count=len(self.state.new_saved_keys),
                        idle_seconds=max(0,int(time.monotonic()-getattr(self.state,'last_activity_at',time.monotonic()))),
                        collection_stage=str(getattr(self.state,'last_collection_stage','')))
            # The automatic collector polls every few seconds but needs only
            # counts, not hundreds of copied listing references each time.
            if include_units:
                result['units']=list(self.state.new_units)
                result['saved']=list(self.state.new_saved_keys)
            return result

class PositionRepairJob(SearchJob):
    def __init__(self,db,rows,bounds,saved):
        self.lock=threading.RLock();self.cancel_event=threading.Event();self.state=WorkerState();self.state.cancel_event=self.cancel_event;self.state.new_units=[dict(r) for r in rows];self.state.new_saved_keys=list(saved)
        self.conditions={'bounds':list(bounds),'mode':'published_map_position_repair','providers':list({r['provider'] for r in rows})}
        self.audit=AuditLog(self.conditions,(getattr(db,'key',''),));self.state.audit=self.audit
        self.progress=0.;self.message='掲載物件の地図から位置と住所を再取得しています';self.text='';self.finished=False;self.updated=0
        self.thread=threading.Thread(target=self.run,args=(db,self.conditions),daemon=True,name='housing-map-position')
    def run(self,db,conditions):
        rows=[dict(r) for r in self.state.new_units];saved=set(self.state.new_saved_keys);issues=[];updated=0;attempted=0
        history={'id':self.audit.search_id,'status':'started','conditions':conditions,'summary':{},'started_at':utc_now(),'finished_at':None}
        web=PublicWeb();web.audit=self.audit;web.cancel_event=self.cancel_event
        if hasattr(web,'configure'):web.configure(getattr(db,'web_config',{}))
        try:
            try:db.save_search(history)
            except Exception as exc:issues.append('位置確認の開始履歴を保存できません');self.audit.add('database','save','WARNING',{'stage':'position_start_history','exception_type':type(exc).__name__})
            try:munis=municipalities(web)
            except Exception as exc:munis={};self.audit.add('location','reverse_address_pending','WARNING',{'reason':'市区町村コード表を取得できない','exception_type':type(exc).__name__})
            targets=[i for i,r in enumerate(rows) if needs_position_repair(r)]
            for done,i in enumerate(targets,1):
                web.check_cancel()
                old=rows[i];attempted+=1;point=None
                with self.lock:self.message=f'物件地図の位置確認 {done}/{len(targets)}件｜改善 {updated}件'
                self.audit.add('location','position_repair_start','INFO',{'listing_url':old['listing_url'],'old_point':[old.get('latitude'),old.get('longitude')],'old_precision':old.get('coordinate_precision')})
                try:
                    if not web.permitted(old['listing_url']):raise AppError('掲載物件の自動取得ルールで取得できません。')
                    reply=web.fetch(old['listing_url']);soup=BeautifulSoup(reply.text,'html.parser')
                    address=labeled(soup,('所在地','住所','物件所在地')) or old.get('address','')
                    point=locate(web,soup,reply.text,address,conditions['bounds'],munis,source_url=old['listing_url'])
                    if point and point.get('coordinate_precision')=='listing_map':
                        rows[i]=merge_listing(old,dict(old,**point,address=address))
                        try:
                            saved.update(db.save_units([rows[i]]));updated+=1
                            self.audit.add('location','position_repaired','INFO',{'listing_url':old['listing_url'],'new_position':point,'saved':True})
                        except Exception as exc:saved.discard(old['key']);issues.append('位置更新の保存を確認できません');self.audit.add('database','save','ERROR',{'listing_url':old['listing_url'],'exception_type':type(exc).__name__})
                        self.audit.add('location','position_extracted','INFO',{'listing_url':old['listing_url'],'new_position':point,'saved':old['key'] in saved})
                    else:self.audit.add('location','position_pending','WARNING',{'listing_url':old['listing_url'],'reason':'掲載地図の座標または画像の基準位置を取得できない','retained':True})
                except Exception as exc:
                    issues.append(str(exc) if isinstance(exc,AppError) else '掲載地図の読取失敗')
                    self.audit.add('location','position_pending','WARNING',{'listing_url':old['listing_url'],'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '掲載地図の読取失敗','retained':True})
                with self.lock:
                    self.state.new_units=list(rows);self.state.new_saved_keys=list(saved);self.progress=done/max(1,len(targets));self.updated=updated
                    self.text=f'確認 {done}/{len(targets)}件｜位置改善 {updated}件｜全募集 {len(rows)}件を保持'
                self.audit.persist(db)
            history.update(status='partial' if issues or any(needs_position_repair(r) for r in rows) else 'completed',finished_at=utc_now(),
                summary={'confirmed':len(rows),'saved':len(saved),'position_attempted':attempted,'position_updated':updated,
                    'position_pending':sum(needs_position_repair(r) for r in rows),'issues':list(dict.fromkeys(issues)),'individual_positions':len({(r.get('latitude'),r.get('longitude')) for r in rows if has_point(r)})})
            self.audit.add('location','position_repair_finish','INFO',history['summary']);self.audit.persist(db,force=True)
            db.save_search(history)
        except SearchCancelled:
            history.update(status='cancelled',finished_at=utc_now(),summary={'confirmed':len(rows),'saved':len(saved),'position_attempted':attempted,'position_updated':updated,'issues':issues})
            try:db.save_search(history)
            except Exception:pass
        except Exception as exc:
            history.update(status='partial',finished_at=utc_now(),summary={**history.get('summary',{}),'confirmed':len(rows),'saved':len(saved),'issues':[str(exc) if isinstance(exc,AppError) else '位置更新の保存・ログ処理に失敗しました']})
            self.audit.add('location','position_pending','ERROR',{'exception_type':type(exc).__name__,'retained':len(rows)})
        finally:
            try:self.audit.persist(db,force=True)
            except Exception:pass
            with self.lock:self.state.new_search=history;self.finished=True


def needs_position_repair(row):
    if saved_address_requires_reacquisition(row.get('address')):return False
    return not has_point(row) or row.get('coordinate_precision')=='town' or str(row.get('location_method') or '').startswith('住所検索')


def start_position_repair(rows,bounds,db=None):
    if not rows or not bounds or not any(needs_position_repair(r) for r in rows):return
    jobs,lock=job_registry();state=st.session_state
    token=state.setdefault('new_job_token',hashlib.sha256(os.urandom(32)).hexdigest())
    with lock:
        old=jobs.get(token)
        if old and not old.snapshot()['finished']:return
        db=db or Database();db.web_config=rental_network_settings()
        job=PositionRepairJob(db,rows,bounds,state.get('new_saved_keys',[]));jobs[token]=job;job.created=time.monotonic();job.thread.start()
    state.new_search=None


@st.cache_resource
def job_registry():
    return {},threading.RLock()

def active_job():
    jobs,lock=job_registry()
    with lock:
        old=jobs.get(st.session_state.get('new_job_token'))
        if old and not old.snapshot()['finished']:return old
        server_key=st.session_state.get('manual_server_key')
        if server_key:
            for token,job in jobs.items():
                if getattr(job,'server_key',None)==server_key and not job.snapshot()['finished']:
                    st.session_state.new_job_token=token;return job
        return old

def manual_registry_key(db):
    return hashlib.sha256((str(getattr(db,'url',''))+'|'+str(db.namespace)).encode()).hexdigest()

def restore_manual_search():
    if st.session_state.get('manual_job_checked'):return
    st.session_state.manual_job_checked=True
    if active_job():return
    try:
        key=manual_registry_key(Database());st.session_state.manual_server_key=key
    except Exception:return
    jobs,lock=job_registry()
    with lock:
        for token,job in jobs.items():
            if getattr(job,'server_key',None)==key and not job.snapshot()['finished']:
                st.session_state.new_job_token=token;st.session_state.new_map_loaded=True
                snap=job.snapshot();st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
                return

def start_search(conditions):
    if automatic_busy():raise AppError('自動収集を停止してから手動検索を開始してください。')
    jobs,lock=job_registry()
    token=st.session_state.setdefault('new_job_token',hashlib.sha256(os.urandom(32)).hexdigest())
    with lock:
        old=jobs.get(token)
        if old and not old.snapshot()['finished']: return
        # Start with an empty map and reflect this search as rows arrive.
        st.session_state.new_map_loaded=True
        st.session_state.new_units=[]
        st.session_state.new_saved_keys=[]
        # Secrets are read on the Streamlit thread, before launching a pure Python worker.
        db=Database();db.web_config=rental_network_settings()
        server_key=manual_registry_key(db);st.session_state.manual_server_key=server_key
        for running_token,running in jobs.items():
            if getattr(running,'server_key',None)==server_key and not running.snapshot()['finished']:
                st.session_state.new_job_token=running_token;return
        job=SearchJob(db,conditions);job.server_key=server_key;jobs[token]=job
        # Release finished jobs from other sessions after two hours.
        for key,value in list(jobs.items()):
            if key!=token and value.finished and time.monotonic()-getattr(value,'created',time.monotonic())>7200:
                jobs.pop(key,None)
                try:value.audit.release()
                except (AttributeError,OSError):pass
        job.created=time.monotonic();job.thread.start()
    st.session_state.new_search=None

def snapshot_count_metrics(snapshot):
    """Return the four user-facing counts from current or pre-v75 worker snapshots."""
    snapshot=snapshot if isinstance(snapshot,dict) else {}
    live=snapshot.get('live_metrics') if isinstance(snapshot.get('live_metrics'),dict) else {}
    keys=('processed','saved','errors','skipped')
    if any(k in live for k in keys):
        return {k:max(0,int(live.get(k,0) or 0)) for k in keys}
    result=snapshot.get('result') if isinstance(snapshot.get('result'),dict) else {}
    summary=result.get('summary') if isinstance(result.get('summary'),dict) else {}
    if summary:
        saved=max(0,int(summary.get('saved',0) or 0));errors=max(0,int(summary.get('errors',summary.get('rejected',0)) or 0));skipped=max(0,int(summary.get('already_acquired',0) or 0))
        return {'processed':saved+errors,'saved':saved,'errors':errors,'skipped':skipped}
    message=str(snapshot.get('message') or '')
    m=re.search(r'処理\s*(\d+)件.*?保存\s*(\d+)件.*?(?:未保存|エラー)\s*(\d+)件.*?スキップ\s*(\d+)件',message)
    if m:return dict(zip(keys,map(int,m.groups())))
    # Compatibility with older running collectors: '詳細' means detail processing
    # started; '除外' is a completed failure.  Only completed outcomes are shown.
    saved_m=re.search(r'(?:保存確認|保存)\s*(\d+)(?:件)?',message);error_m=re.search(r'(?:未保存|除外|エラー)\s*(\d+)(?:件)?',message);skip_m=re.search(r'(?:取得済みスキップ|スキップ)\s*(\d+)(?:件)?',message)
    saved=int(saved_m.group(1)) if saved_m else 0;errors=int(error_m.group(1)) if error_m else 0;skipped=int(skip_m.group(1)) if skip_m else 0
    return {'processed':saved+errors,'saved':saved,'errors':errors,'skipped':skipped}


@st.fragment(run_every='10s')
def background_progress():
    """Render live worker progress and buttons inside the fragment-owned container."""
    job=active_job()
    if job is None or job.snapshot()['finished']:
        controller=get_automatic_collection()
        auto=controller.snapshot() if controller else None
        if auto and auto['running']:
            current=auto.get('current',{})
            live=snapshot_count_metrics(current)
            live_text=f"処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜未保存 {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件"
            st.progress(min(.99,max(0.,auto['progress'])),text=live_text)
            st.caption('現在｜'+live_text)
            if auto.get('message') and live_text not in str(auto.get('message')):st.caption(auto['message'])
            if current:st.caption('処理更新：'+acquisition_time_jst(current.get('updated_at'))+'｜最終物件保存：'+acquisition_time_jst(current.get('last_saved_at')))
            if st.button('自動収集を中止（取得済みデータを保存）',key='auto_stop_map',disabled=auto['stopping']):
                try:controller.request_stop()
                except AppError as exc:st.error('中止は要求しましたが、停止設定の保存に失敗しました：'+str(exc))
            return
        if job is None:return
    snap=job.snapshot()
    if not snap['finished'] or st.session_state.get('finished_snapshot_applied')!=job.audit.search_id:
        st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
        if snap['finished']:st.session_state.finished_snapshot_applied=job.audit.search_id
    if snap['finished']:
        st.session_state.new_search=snap['result']
    def refresh_map():
        st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
        st.rerun()
    def render():
        st.caption('処理更新：'+acquisition_time_jst(snap.get('updated_at'))+'｜最終物件保存：'+acquisition_time_jst(snap.get('last_saved_at')))
        if snap['finished']:
            result=snap.get('result') or {};summary=result.get('summary',{})
            st.info(f"検索終了｜処理 {int(summary.get('processed',0))}件｜保存 {int(summary.get('saved',0))}件｜未保存 {int(summary.get('errors',0))}件｜スキップ {int(summary.get('already_acquired',0))}件")
            if summary.get('unsaved'):st.error('未保存 '+str(summary['unsaved'])+'件。保存に失敗したデータはサーバーのメモリに保持しています。作業ログを確認してください。')
            if summary.get('acquisition_ids_pending'):st.error('取得済みIDの保存未確認 '+str(summary['acquisition_ids_pending'])+'件。次回は再取得対象になります。')
            if st.button('取得済みデータを地図へ反映',key='refresh_finished_map'):refresh_map()
            return
        live=snapshot_count_metrics(snap)
        live_text=f"処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜未保存 {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件"
        progress_text='バックグラウンドで地図位置・住所を確認しています' if isinstance(job,PositionRepairJob) else live_text
        st.progress(min(.99,snap['progress']),text=progress_text)
        if not isinstance(job,PositionRepairJob):st.caption('現在｜'+live_text)
        if snap.get('message') and (isinstance(job,PositionRepairJob) or live_text not in str(snap.get('message'))):st.caption(('中断処理中｜' if snap.get('stopping') else '')+str(snap['message']))
        st.caption('画面を閉じたり他のアプリへ移っても、サーバー稼働中は取得・保存を継続します。')
        st.caption(f"地図操作優先：自動再描画を停止中｜取得済み {len(snap['units'])}件。検索・保存は継続します。")
        if st.button('取得済みデータを地図へ反映',key='refresh_live_map'):refresh_map()
        if st.button('取得を中止（取得済みデータを保存）',key='stop_background_job',disabled=bool(snap.get('stopping'))):
            job.request_stop()
    render()


@st.fragment(run_every='20s')
def background_status():
    """Render current-search diagnostics with download buttons always available."""
    job=active_job()
    if job is None:return
    snap=job.snapshot()
    st.caption(f"作業ログ {job.audit.count}イベント｜Supabase保存確認 {job.audit.persisted_events}イベント")
    try:
        cache=st.session_state.get('diagnostic_export_cache')
        cache_key=(job.audit.search_id,int(job.audit.count))
        if st.button('詳細ログを更新してTXT・JSONを準備',key='prepare_current_log'):
            cache={'key':cache_key,'events':job.audit.records(),'prepared_at':utc_now()}
            st.session_state.diagnostic_export_cache=cache
        if isinstance(cache,dict) and cache.get('events'):
            diagnostic_downloads(cache['events'],'current')
        else:
            c1,c2=st.columns(2)
            with c1:st.download_button('詳細ログ TXT',b'','sumai_work_log.txt','text/plain',key='current_txt_pending',disabled=True)
            with c2:st.download_button('詳細ログ JSON',b'','sumai_work_log.json','application/json',key='current_json_pending',disabled=True)
    except Exception as exc:
        st.error('詳細ログを出力できませんでした：'+type(exc).__name__)
    if job.audit.failure_storage_error:
        st.error('取得エラーのSupabase都度保存を確認できません。保存待ちを保持して再試行します：'+job.audit.failure_storage_error)
    if job.audit.storage_error:
        st.error('作業ログのSupabase保存を確認できません。この画面のTXT/JSONはサーバーに残るログから直接出力します。'+job.audit.storage_error)
    if not snap['finished']:
        if snap['log']:st.code(snap['log'],language=None)
        st.caption('画面を操作しても検索と保存は継続します。サーバーの休止・再起動では実行が終了します。')

def capture_map_fragment():
    had_bounds=bool(st.session_state.get('new_bounds'))
    capture_viewport()
    if not had_bounds and st.session_state.get('new_bounds'):
        st.session_state['map_initial_bounds_ready']=True

def capture_map_fragment_v115():
    """Copy real browser bounds when the mounted Folium map reports them.

    Never force a full rerun from a map callback: doing so during initial
    mounting previously unmounted the iframe before Leaflet had initialized.
    """
    current=st.session_state.get('new_map_v115_'+str(st.session_state.get('_v115_map_reset_count',0)))
    if not isinstance(current,dict):
        return
    bounds=viewport_bounds(current)
    if bounds:
        st.session_state['new_bounds']=bounds
        st.session_state['new_map']=current
        st.session_state['new_view_center']=bounds_center(bounds)


def mobile_map_content_signature(rows,facilities):
    """Hash compact visible points without retaining a second listing copy."""
    digest=hashlib.blake2s(digest_size=12)
    for r in rows:
        if not has_point(r):continue
        digest.update((str(r.get('key') or '')+'|'+str(r['latitude'])+'|'+str(r['longitude'])+'|'
                       +str(r.get('rent'))+'|'+str(r.get('fees'))+'|'
                       +str(r.get('coordinate_precision'))+';').encode('utf-8'))
    for f in facilities:
        digest.update((str(f.get('name'))+'|'+str(f.get('lat'))+'|'+str(f.get('lng'))+';').encode('utf-8'))
    return (len(rows),len(facilities),digest.hexdigest())


@st.fragment
def interactive_rental_map(pins,cells,facilities):
    from streamlit_folium import st_folium
    """Reliable map mount: base map and rental data are rendered atomically.

    Do not share one mutable Folium.Map through cache_resource across Streamlit
    sessions. Do not insert dynamic feature_group_to_add into a map whose JS
    instance may have been disposed by an Android browser's tab suspension.
    """
    state=st.session_state
    window=state.get('new_bounds')
    if window and len(window)==4:
        south,west,north,east=map(float,window)
        previous=state.get('_mobile_map_data_window')
        if (previous and len(previous)==4 and
            previous[0]+.002 <= south and previous[1]+.002 <= west and
            north <= previous[2]-.002 and east <= previous[3]-.002):
            view=previous
        else:
            lat_mid=(south+north)*.5;lng_mid=(west+east)*.5
            center_lat=(math.floor(lat_mid/.015)+.5)*.015
            center_lng=(math.floor(lng_mid/.015)+.5)*.015
            dy=max(.022,north-south)*1.25
            dx=max(.022,east-west)*1.25
            view=(center_lat-dy,center_lng-dx,center_lat+dy,center_lng+dx)
            state['_mobile_map_data_window']=view
        visible=[r for r in pins if has_point(r) and
                 view[0] <= float(r['latitude']) <= view[2] and
                 view[1] <= float(r['longitude']) <= view[3]]
        scope=tuple(round(x,5) for x in view)
        center=bounds_center(window)
    else:
        center=state.get('new_view_center',DEFAULT_CENTER)
        lat,lng=center
        visible=[r for r in pins if has_point(r) and
                 abs(float(r['latitude'])-lat)<.025 and abs(float(r['longitude'])-lng)<.035]
        scope=(round(lat,4),round(lng,4),'initial')

    signature=(BUILD,scope,mobile_map_content_signature(visible,facilities))
    # One bounded Folium object per session. On changes replace it in full; the
    # base tile layer cannot be left uninitialized while dynamic SVG loads.
    cached=state.get('_v115_map_snapshot')
    if not isinstance(cached,dict) or cached.get('signature')!=signature:
        m=rental_map(visible,center,1500,cells,facilities)
        state['_v115_map_snapshot']={'signature':signature,'map':m}
    else:
        m=cached['map']
    # Always provide the full, complete map. The component no longer depends on
    # feature_group_to_add or the on_change -> forced st.rerun feedback loop.
    data=st_folium(m,key='new_map_v115_'+str(state.get('_v115_map_reset_count',0)),height=480,use_container_width=True,
                   returned_objects=['bounds'],on_change=capture_map_fragment_v115)
    # Capture a new browser viewport even if a component callback is delayed.
    if isinstance(data,dict):
        current_bounds=viewport_bounds(data)
        if current_bounds:
            state['new_bounds']=current_bounds
            state['new_view_center']=bounds_center(current_bounds)
            state['new_map']=data
    # Reset button is deliberately outside the map iframe; it remains available
    # even if the iframe fails to initialize after a mobile-tab restore.
    if st.button('地図が表示されない場合は再初期化',key='reset_mobile_map_v115'):
        state.pop('_v115_map_snapshot',None)
        state.pop('_mobile_map_data_window',None)
        state.pop('new_map',None)
        # Keep last valid viewport for the initial zoom after re-mounting.
        state['_v115_map_reset_count']=int(state.get('_v115_map_reset_count',0))+1
        st.rerun()


def cloud_memory_pressure():
    """cgroup-wide memory, reclaimable cache and current Python RSS in MiB."""
    try:
        with open('/sys/fs/cgroup/memory.max',encoding='ascii') as f:raw=f.read().strip()
        if not raw.isdecimal():return None
        limit=int(raw)
        if not 0<limit<1<<46:return None
        with open('/sys/fs/cgroup/memory.current',encoding='ascii') as f:used=int(f.read().strip())
        stats={}
        try:
            with open('/sys/fs/cgroup/memory.stat',encoding='ascii') as f:
                for line in f:
                    k,v=line.strip().split()
                    if k in ('anon','file','inactive_file','slab_reclaimable'):stats[k]=int(v)
        except (OSError,ValueError):pass
        rss=0
        try:
            with open('/proc/self/status',encoding='ascii') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        rss=int(line.split()[1])*1024;break
        except (OSError,ValueError):pass
        mb=1024*1024
        return {'used_mib':round(used/mb),'limit_mib':round(limit/mb),
                'ratio':used/limit,'rss_mib':round(rss/mb),
                'cache_mib':round(stats.get('file',0)/mb),
                'inactive_mib':round(stats.get('inactive_file',0)/mb),
                'anon_mib':round(stats.get('anon',0)/mb),
                'effective_mib':round(max(0,used-stats.get('inactive_file',0))/mb),
                'effective_ratio':max(0,used-stats.get('inactive_file',0))/limit}
    except (OSError,ValueError,ZeroDivisionError):return None



def memory_needs_guard(pressure):
    """Only block jobs for genuinely tight cgroup memory, not clean inactive cache.

    Linux generally reclaims inactive_file under pressure. Still keep a strict
    absolute cgroup guard to avoid running with almost no remaining headroom.
    """
    if not pressure:return False
    if pressure.get('effective_ratio',pressure.get('ratio',0))>=0.80:return True
    return pressure.get('ratio',0)>=0.96




# v130: Resource isolation policy.
# The application must never change shared OS file caches, cgroup memory,
# Conda/uv package caches, installed libraries, or other applications' files.
# Only memory owned by this Python process may be reclaimed.
def _v126_trim_heap():
    """Release unused heap arenas owned by this Python process only."""
    try:
        import gc
        gc.collect()
        import ctypes
        libc=ctypes.CDLL(None)
        trim=getattr(libc,'malloc_trim',None)
        if trim is not None:
            trim.argtypes=[ctypes.c_size_t]
            trim.restype=ctypes.c_int
            trim(0)
        return True
    except (ImportError,OSError,AttributeError,TypeError,ValueError):
        return False


def _v126_work_finished(reason, file_path=None):
    """Clean up the application's Python heap after its own worker completes."""
    return _v126_trim_heap()


# v124: On-demand, bounded, private memory diagnosis. No subprocess, no DB read,
# no names of files, addresses, URLs, CLI parameters, passwords or credentials.
# The kernel 'file' counter includes tmpfs / shared memory and is NOT synonymous
# with a set of text files or with fully reclaimable page cache.
MEMORY_DIAGNOSTIC_FIELDS = (
    'anon','file','kernel','kernel_stack','pagetables','sec_pagetables',
    'percpu','sock','shmem','file_mapped','file_dirty','file_writeback',
    'swapcached','anon_thp','file_thp','shmem_thp',
    'inactive_anon','active_anon','inactive_file','active_file',
    'unevictable','slab_reclaimable','slab_unreclaimable','slab',
)


def _v124_read_numeric(path, limit=32768):
    try:
        with open(path,'r',encoding='ascii',errors='replace') as handle:
            return handle.read(limit)
    except (OSError,UnicodeError):
        return ''


def _v124_parse_kv(text, kilobytes=False):
    result={}
    for line in text.splitlines():
        fields=line.replace(':',' ').split()
        if len(fields)<2:continue
        try:value=int(fields[1])
        except ValueError:continue
        result[fields[0]]=value*(1024 if kilobytes else 1)
    return result


def _v124_process_category(pid):
    comm=_v124_read_numeric('/proc/'+str(pid)+'/comm',256).strip().lower()
    if ('chrome' in comm or 'chromium' in comm or 'browser' in comm):return 'Chromium/ブラウザー'
    if 'python' in comm:return 'Python'
    if 'streamlit' in comm:return 'Streamlit'
    if not comm:return '不明'
    return 'その他のプロセス'


def _v124_process_rows():
    """Read cgroup member processes without exposing process arguments."""
    raw=_v124_read_numeric('/sys/fs/cgroup/cgroup.procs',32768)
    pids=[]
    for word in raw.split():
        if word.isdecimal():pids.append(int(word))
    pids=list(dict.fromkeys(pids+[os.getpid()]))[:256]
    rows=[]
    for pid in pids:
        status=_v124_parse_kv(_v124_read_numeric('/proc/'+str(pid)+'/status',16384),kilobytes=True)
        if not status:continue
        rows.append({'pid':pid,'kind':_v124_process_category(pid),
                     'rss':status.get('VmRSS',0),'anon':status.get('RssAnon',0),
                     'file':status.get('RssFile',0),'shmem':status.get('RssShmem',0),
                     'swap':status.get('VmSwap',0)})
    rows.sort(key=lambda row:row['rss'],reverse=True)
    # Only the largest ten PSS calls (can be slow on some Linux kernels).
    for row in rows[:10]:
        smaps=_v124_parse_kv(_v124_read_numeric('/proc/'+str(row['pid'])+'/smaps_rollup',8192),kilobytes=True)
        row['pss']=smaps.get('Pss')
        row['pss_anon']=smaps.get('Pss_Anon')
        row['pss_file']=smaps.get('Pss_File')
        row['pss_shmem']=smaps.get('Pss_Shmem')
    return {'observed':len(rows),'truncated':len(pids)>=256,'top':rows[:10],
            'rss_sum':sum(r['rss'] for r in rows)}


def _v124_temp_folder_totals(path, max_entries=2500, depth=2):
    """Read names only for classification, never retain or print them.

    File sizes describe disk/tmpfs occupancy, NOT necessarily resident RAM.
    Avoid symlinks and bounded traversal prevents pathological directories from
    blocking the collection fragment.
    """
    categories={'町丁目境界DB':0,'町丁目集計DB':0,'アプリログ':0,
                'その他のSQLite':0,'その他のファイル':0}
    count=0;truncated=False;dirs=[(path,0)]
    while dirs:
        folder,level=dirs.pop()
        try:
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    # Do not descend into, inspect, or count other apps' temp files.
                    # Only this app's reserved sumai-* / sumai_* names are in scope.
                    if level==0 and not entry.name.startswith(('sumai-', 'sumai_')):
                        continue
                    if count>=max_entries:truncated=True;break
                    count+=1
                    try:
                        if entry.is_symlink():continue
                        if entry.is_dir(follow_symlinks=False):
                            if level<depth:dirs.append((entry.path,level+1))
                            continue
                        if not entry.is_file(follow_symlinks=False):continue
                        size=entry.stat(follow_symlinks=False).st_size
                        name=entry.name.lower()
                        if name.startswith('sumai-town-boundary-v') or ('sumai-town-boundary-' in entry.path and name.endswith('.sqlite')):
                            category='町丁目境界DB'
                        elif 'sumai-town-agg-' in entry.path or name=='latest.sqlite':
                            category='町丁目集計DB'
                        elif name.startswith('sumai-log-'):
                            category='アプリログ'
                        elif name.endswith(('.sqlite','.sqlite-wal','.sqlite-shm','.db','.db-wal','.db-shm')):
                            category='その他のSQLite'
                        else:category='その他のファイル'
                        categories[category]+=max(0,size)
                    except OSError:continue
        except OSError:pass
        if truncated:break
    return {'file_entries_scanned':count,'incomplete':truncated,'bytes':categories}


def memory_diagnostic_v124():
    """One tiny point-in-time snapshot. No network or Supabase operations."""
    result={'timestamp_jst':datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST'),
            'build':BUILD}
    root='/sys/fs/cgroup/'
    stat=_v124_parse_kv(_v124_read_numeric(root+'memory.stat'))
    result['cgroup']={
        'current':_v124_parse_kv('current '+_v124_read_numeric(root+'memory.current').strip()).get('current'),
        'max':_v124_parse_kv('max '+_v124_read_numeric(root+'memory.max').strip()).get('max'),
        'swap_current':_v124_parse_kv('swap '+_v124_read_numeric(root+'memory.swap.current').strip()).get('swap'),
        'events':_v124_parse_kv(_v124_read_numeric(root+'memory.events')),
        'stat':{k:stat[k] for k in MEMORY_DIAGNOSTIC_FIELDS if k in stat},
    }
    result['processes']=_v124_process_rows()
    result['temp']={
        '/tmp':_v124_temp_folder_totals('/tmp'),
        '/dev/shm':_v124_temp_folder_totals('/dev/shm',max_entries=1200),
    }
    try:
        result['app_threads']=threading.active_count()
    except RuntimeError:result['app_threads']=None
    return result


def format_memory_diagnostic_v124(samples):
    mib=1024*1024
    def n(x):return '取得不可' if x is None else f'{x/mib:,.1f} MiB'
    out=['住まいコンパス｜サーバーメモリ診断',
         '個人情報を含む物件・住所・ファイル名・URL・認証情報・コマンド引数は記録しません。',
         '単位：MiB = 1,048,576 bytes。ファイル容量とメモリ常駐量は別の指標です。',
         'cgroup の file には tmpfs/shmem などを含み、全額が解放可能なキャッシュとは限りません。',
         'inactive_file は回収可能量の目安ですが、実際の解放可能量を保証しません。','']
    for number,s in enumerate(samples,1):
        cg=s['cgroup'];stat=cg['stat'];proc=s['processes']
        out += [f'【測定 {number}/{len(samples)}】 {s["timestamp_jst"]} / {s["build"]}',
                '■ メモリ：コンテナ全体',
                '  使用中 '+n(cg['current'])+' / 上限 '+n(cg['max']),
                '  スワップ '+n(cg['swap_current']),
                '  内訳（Linux memory.stat）']
        descriptions={
            'anon':'匿名メモリ（Python等の主なヒープ）',
            'file':'ファイル関連合計（tmpfs等を含む）',
            'inactive_file':'非アクティブなファイルページ',
            'active_file':'アクティブなファイルページ',
            'shmem':'共有メモリ・tmpfs関連（fileと重複し得る）',
            'file_mapped':'プロセスにマップされたファイル',
            'file_dirty':'未書き込みのファイルページ',
            'file_writeback':'書き込み待ちページ',
            'slab_reclaimable':'回収可能とされるカーネルslab',
            'slab_unreclaimable':'回収困難なカーネルslab',
            'slab':'カーネルslab合計',
            'kernel':'カーネル用メモリ',
            'pagetables':'ページテーブル',
            'kernel_stack':'カーネルスタック',
            'sock':'ネットワークソケット',
            'inactive_anon':'非アクティブな匿名ページ',
            'active_anon':'アクティブな匿名ページ',
            'unevictable':'退避できないページ',
            'anon_thp':'匿名巨大ページ',
            'file_thp':'ファイル巨大ページ',
            'shmem_thp':'共有巨大ページ',
            'swapcached':'スワップキャッシュ',
            'percpu':'CPUごとのカーネルメモリ',
            'sec_pagetables':'補助ページテーブル',
        }
        for key in MEMORY_DIAGNOSTIC_FIELDS:
            if key in stat:out.append(f'    {descriptions.get(key,key)} [{key}]: {n(stat[key])}')
        events=cg.get('events') or {}
        if events:out.append('  メモリ制限イベント：'+', '.join(f'{k}={v}' for k,v in sorted(events.items())))
        out += ['■ プロセス（cgroup所属：RSS合計は共有ページを重複計上する場合があります）',
                f'  確認できたプロセス {proc["observed"]}件（上位10件）｜一覧取得制限 {"あり" if proc["truncated"] else "なし"}',
                f'  RSS単純合計 {n(proc["rss_sum"])}']
        for p in proc['top']:
            line=(f'  PID {p["pid"]} / {p["kind"]} / RSS {n(p["rss"])} / '
                  f'匿名 {n(p["anon"])} / ファイル {n(p["file"])} / '
                  f'共有 {n(p["shmem"])} / swap {n(p["swap"])}')
            if p.get('pss') is not None:
                line+=f' / PSS {n(p["pss"])}'
                if p.get('pss_anon') is not None:line+=f' / PSS匿名 {n(p["pss_anon"])}'
                if p.get('pss_file') is not None:line+=f' / PSSファイル {n(p["pss_file"])}'
            out.append(line)
        out += [f'■ アプリ専用の一時ファイル容量（RAM使用量ではありません）']
        for folder,v in s['temp'].items():
            out.append(f'  {folder}：{v["file_entries_scanned"]}件を調査'+('（件数上限につき一部のみ）' if v['incomplete'] else ''))
            for key,size in v['bytes'].items():out.append(f'    {key}：{n(size)}')
        out.append(f'  Pythonスレッド数：{s.get("app_threads") if s.get("app_threads") is not None else "取得不可"}')
        out.append('')
    if len(samples)>1:
        a=samples[0]['cgroup'];b=samples[-1]['cgroup'];out+=['■ 最初と最後の変化']
        for name,k in [('コンテナ使用量','current'),('匿名メモリ','anon'),('ファイル関連','file'),('共有メモリ','shmem'),('非アクティブファイル','inactive_file')]:
            av=a.get(k) if k=='current' else a['stat'].get(k)
            bv=b.get(k) if k=='current' else b['stat'].get(k)
            if isinstance(av,int) and isinstance(bv,int):out.append(f'  {name}：{(bv-av)/mib:+,.1f} MiB')
    return ('\n'.join(out)+'\n').encode('utf-8-sig')


# v130: Full-server file inspection was removed. It scanned unrelated files,
# raised memory usage during measurement, and did not identify cgroup ownership.
# Lightweight read-only process/container counters remain available below.

def render_memory_diagnostic_v124():
    """Small opt-in expander; no sampling on every Streamlit fragment refresh."""
    with st.expander('メモリ診断（必要なときだけ）',expanded=False):
        st.caption('現在の内訳を記録してTXTを出力します。待機中・収集中など最大3回を比較できます。')
        if st.button('現在のメモリを記録',key='v124_memory_sample',use_container_width=True):
            try:
                current=memory_diagnostic_v124()
                old=st.session_state.get('_v124_memory_samples') or []
                st.session_state['_v124_memory_samples']=(old+[current])[-3:]
            except Exception:
                st.error('計測できませんでした。サーバーの診断情報を取得できるか確認してください。')
        history=st.session_state.get('_v124_memory_samples') or []
        if history:
            last=history[-1];cg=last['cgroup'];stat=cg['stat'];mb=1024*1024
            def metric(v):return '—' if not isinstance(v,int) else f'{v/mb:,.0f} MiB'
            a,b=st.columns(2)
            a.metric('コンテナ',metric(cg['current']))
            b.metric('匿名メモリ',metric(stat.get('anon')))
            a,b=st.columns(2)
            a.metric('ファイル関連',metric(stat.get('file')))
            b.metric('うち共有メモリ',metric(stat.get('shmem')))
            st.caption('記録 '+str(len(history))+'/3回｜直近 '+last['timestamp_jst']+'。ファイル関連には共有メモリ等も含みます。')
            st.download_button('メモリ診断TXTをダウンロード',
                               format_memory_diagnostic_v124(history),
                               'sumai_memory_diagnostic_v124.txt','text/plain',
                               key='v124_memory_download',use_container_width=True,on_click='ignore')
        st.caption('物件データ・秘密鍵・ファイル名・URLは出力しません。')


# Keep searches moving when one town has too many listings or stops making progress.
# A skipped town is NEVER marked complete: it remains in incomplete_task_indices.
AUTO_TOWN_IDLE_SECONDS = 180  # 3 minutes without actual collector events
AUTO_TOWN_MAX_SECONDS = 1200  # 20 minutes total per town, independent of screen status

AUTO_COLLECTION_ID='automatic.collection.settings'

def normalized_automatic_task_label(value):
    """Normalize pre-v97 stored ASCII | versus present full-width ｜ task labels."""
    return re.sub(r'\s*[|｜]\s*', '｜', normal(value))


def automatic_task_identity(code,town,provider):
    """Stable municipality/town/provider identity across UI text changes."""
    return str(code)+'|'+address_key(town_base_name(town))+'|'+canonical_provider(provider)


def automatic_task_plan(settings):
    """Town-by-town, and strictly ward-by-ward, in the user's selection order."""
    if not isinstance(settings,dict):return []
    regions=settings.get('auto_regions') or [];munis=settings.get('auto_munis') or {}
    if not regions:return []
    wards=automatic_ward_codes(settings)
    providers=[canonical_provider(p) for p in (settings.get('providers') or AUTOMATIC_REGION_PROVIDERS)]
    providers=[p for p in dict.fromkeys(providers) if p in AUTOMATIC_REGION_PROVIDERS]
    target_sets=[]
    for provider in providers:
        groups=suumo_area_groups(regions,munis) if provider=='SUUMO' else homes_area_groups(regions,munis)
        target_sets.append((provider,groups))
    plan=[]
    # Finishing all towns of the first ward is required before starting the
    # second ward. No provider/ward interleaving is allowed across that border.
    for ward in wards:
        ward_groups=[(provider,[g for g in groups if str(g.get('code'))==ward])
                     for provider,groups in target_sets]
        for number in range(max((len(groups) for _,groups in ward_groups),default=0)):
            for provider,groups in ward_groups:
                if number>=len(groups):continue
                g=groups[number];town=g.get('suumo_town') if provider=='SUUMO' else g.get('homes_town')
                plan.append({'index':len(plan),'ward_code':ward,'provider':provider,
                             'label':(g.get('label') or ('東京都'+str(g.get('city_name') or '')+str(town or g.get('town') or '')))+'｜'+PROVIDER_DISPLAY.get(provider,provider),
                             'town':town or g.get('town') or '',
                             'identity':automatic_task_identity(g.get('code'),town or g.get('town'),provider)})
    return plan


def advance_automatic_task(summary,index,total,success):
    """Attempt each town once; keep incomplete towns pending for the next run."""
    if total<1:raise ValueError('empty plan')
    idx=int(index)%total
    complete={int(i) for i in summary.get('cycle_completed_indices',[])
              if str(i).isdigit() and 0<=int(i)<total}
    attempted={int(i) for i in summary.get('cycle_attempted_indices',[])
               if str(i).isdigit() and 0<=int(i)<total} | complete
    failed={int(i) for i in summary.get('incomplete_task_indices',[])
            if str(i).isdigit() and 0<=int(i)<total}
    attempted.add(idx)
    if success:
        complete.add(idx);failed.discard(idx)
    else:
        failed.add(idx)
    summary['cycle_attempted_indices']=sorted(attempted)
    summary['cycle_completed_indices']=sorted(complete)
    summary['incomplete_task_indices']=sorted(failed)
    remaining=[(idx+step)%total for step in range(1,total+1)
               if (idx+step)%total not in attempted]
    if remaining:
        summary['next_task_index']=remaining[0]
        summary.pop('cycle_reset_pending',None)
        return 'next'
    if failed:
        summary['next_task_index']=min(failed)
        summary.pop('cycle_reset_pending',None)
        return 'paused_incomplete'
    summary['next_task_index']=0
    summary['last_cycle_completed_at']=utc_now()
    summary['cycle_reset_pending']=True
    return 'cycle_complete'


def reconcile_automatic_task_progress(summary, old_plan, new_plan, just_completed_label=None):
    """Transfer completed, attempted and incomplete jobs by town/provider label.

    Numeric task indices cannot be reused if source/region plans change. Pending
    incomplete work must survive rebuilding the plan after a server restart.
    """
    old_labels=list(summary.get('task_labels') or [task['label'] for task in old_plan])
    def old_labels_for(field, fallback=()):
        return {normalized_automatic_task_label(old_labels[int(i)])
                for i in summary.get(field, fallback)
                if str(i).isdigit() and 0<=int(i)<len(old_labels)}
    previous_done=summary.get('cycle_completed_indices')
    if previous_done is None:
        previous_done=list(range(max(0,int(summary.get('next_task_index',0) or 0))))
    completed_labels=old_labels_for('cycle_completed_indices',previous_done)
    attempted_labels=old_labels_for('cycle_attempted_indices')|completed_labels
    incomplete_labels=old_labels_for('incomplete_task_indices')
    if just_completed_label:
        completed_labels.add(normalized_automatic_task_label(just_completed_label))
        attempted_labels.add(normalized_automatic_task_label(just_completed_label))
    new_labels=[normalized_automatic_task_label(task['label']) for task in new_plan]
    complete=[i for i,label in enumerate(new_labels) if label in completed_labels]
    attempted=[i for i,label in enumerate(new_labels) if label in attempted_labels]
    incomplete=[i for i,label in enumerate(new_labels) if label in incomplete_labels and i not in complete]
    summary['task_labels']=new_labels
    summary['total_tasks']=len(new_labels)
    summary['cycle_completed_indices']=complete
    summary['cycle_attempted_indices']=attempted
    summary['incomplete_task_indices']=incomplete
    if new_labels and len(complete)==len(new_labels):
        if not summary.get('cycle_reset_pending'):
            summary['last_cycle_completed_at']=utc_now()
        summary['cycle_reset_pending']=True
        next_index=0
    else:
        summary.pop('cycle_reset_pending',None)
        attempted_set=set(attempted)
        # First finish never-attempted towns; then revisit the incomplete ones.
        next_index=next((i for i in range(len(new_labels)) if i not in attempted_set),None)
        if next_index is None:
            next_index=next((i for i in incomplete),0)
    summary['next_task_index']=next_index
    summary['current_task_index']=next_index
    summary['current_task_label']=new_labels[next_index] if new_labels else ''
    return next_index


def _diagnostic_event_key(event):
    return (str(event.get('search_id') or ''),str(event.get('seq') or ''),
            str(event.get('stage') or ''),str(event.get('code') or ''))


def _diagnostic_fetch_page(db,params,requested=200):
    """Read small PostgREST pages; a transient 5xx lowers payload, not data fidelity."""
    limits=list(dict.fromkeys((requested,max(50,requested//2),25)))
    latest=None
    for attempt,limit in enumerate(limits):
        current=dict(params,limit=limit)
        try:
            rows=db.call('GET',SEARCH_TABLE,current)
            if not isinstance(rows,list):raise AppError('Supabase error-page response is not a list')
            return rows,limit
        except AppError as exc:
            latest=exc
            diagnostic=getattr(exc,'diagnostic',{}) or {}
            status=diagnostic.get('status')
            code=diagnostic.get('code')
            transient=(status in (500,502,503,504) or code=='57014' or bool(re.search(r'HTTP 5\d\d',str(exc))))
            if not transient or attempt==len(limits)-1:raise
            time.sleep(.35*(attempt+1))
    raise latest


def load_persisted_error_events_fast(db):
    """Read one error record per event, using keyset pagination and bounded payloads."""
    events=[];seen=set();last_id=''
    while True:
        params={'namespace':'eq.'+db.namespace,'status':'eq.diagnostic_error',
                'and':'(id.gte.failure.,id.lt.failure/)',
                'select':'id,summary','order':'id.asc'}
        if last_id:params['id']='gt.'+last_id
        rows,limit=_diagnostic_fetch_page(db,params)
        if not rows:break
        for row in rows:
            event=(row.get('summary') or {}).get('event')
            if not isinstance(event,dict) or not diagnostic_failure_event(event):continue
            key=_diagnostic_event_key(event)
            if key in seen:continue
            seen.add(key);events.append(event)
        new_last=str(rows[-1].get('id') or '')
        if not new_last or new_last<=last_id:
            raise AppError('Supabase error CSV pagination cursor did not advance')
        last_id=new_last
        if len(rows)<limit:break
    events.sort(key=lambda e:(str(e.get('time') or ''),str(e.get('search_id') or ''),int(e.get('seq') or 0)))
    return events


def load_legacy_diagnostic_error_events(db,current_events):
    """Backfill older chunk-only diagnostics without reloading the new per-error rows."""
    events=list(current_events);seen={_diagnostic_event_key(e) for e in events};last_id=''
    while True:
        params={'namespace':'eq.'+db.namespace,'status':'eq.diagnostic_log',
                'select':'id,summary','order':'id.asc'}
        if last_id:params['id']='gt.'+last_id
        rows,limit=_diagnostic_fetch_page(db,params,requested=100)
        if not rows:break
        for row in rows:
            for event in ((row.get('summary') or {}).get('events') or []):
                if not diagnostic_failure_event(event):continue
                key=_diagnostic_event_key(event)
                if key in seen:continue
                seen.add(key);events.append(event)
        new_last=str(rows[-1].get('id') or '')
        if not new_last or new_last<=last_id:
            raise AppError('Supabase historic error CSV pagination cursor did not advance')
        last_id=new_last
        if len(rows)<limit:break
    events.sort(key=lambda e:(str(e.get('time') or ''),str(e.get('search_id') or ''),int(e.get('seq') or 0)))
    return events


class AutomaticFailureExport:
    """Refresh the unified error CSV in the background; the UI never waits for preparation."""
    def __init__(self,db):
        self.db=db;self.lock=threading.RLock();self.running=False;self.loaded=False;self.error='';self.phase='idle'
        self.rows=[];self.row_count=0;self.csv=csv_bytes_from_rows([]);self.updated_at=None;self.updated_monotonic=0.;self.last_attempt_monotonic=0.;self.thread=None
    def refresh(self,max_age=60):
        with self.lock:
            if self.running:return
            now=time.monotonic()
            # Do not hammer a failing Supabase endpoint on every Streamlit rerun.
            if self.last_attempt_monotonic and now-self.last_attempt_monotonic<max_age:return
            self.last_attempt_monotonic=now
            self.running=True;self.error='';self.phase='current_errors'
            self.thread=threading.Thread(target=self._run,daemon=True,name='housing-error-export')
            self.thread.start()
    def _run(self):
        try:
            fast_events=load_persisted_error_events_fast(self.db)
            fast_rows=acquisition_failure_rows(fast_events)
            with self.lock:
                self.rows=[];self.row_count=len(fast_rows);self.csv=csv_bytes_from_rows(fast_rows);self.updated_at=utc_now()
                self.updated_monotonic=time.monotonic();self.loaded=True;self.error='';self.phase='historical_backfill'
            # Older builds stored errors only inside diagnostic-log chunks. Merge those
            # after the current per-error rows are already downloadable.
            events=load_legacy_diagnostic_error_events(self.db,fast_events)
            rows=acquisition_failure_rows(events)
            with self.lock:
                self.rows=[];self.row_count=len(rows);self.csv=csv_bytes_from_rows(rows);self.updated_at=utc_now()
                self.updated_monotonic=time.monotonic();self.loaded=True;self.error='';self.phase='complete'
        except Exception as exc:
            with self.lock:
                self.error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                if self.loaded:self.phase='partial'
        finally:
            with self.lock:self.running=False
    def snapshot(self):
        with self.lock:
            return {'running':self.running,'loaded':self.loaded,'error':self.error,'phase':self.phase,'rows':[], 'row_count':self.row_count,
                    'csv':self.csv,'updated_at':self.updated_at}


@st.cache_resource
def automatic_failure_export_registry():
    return {},threading.RLock()


def get_automatic_failure_export(max_age=60,refresh=True):
    db=Database();key=manual_registry_key(db)+'|'+BUILD
    jobs,lock=automatic_failure_export_registry()
    with lock:
        job=jobs.get(key)
        if job is None:
            job=AutomaticFailureExport(db);jobs[key]=job
    # Historical-error export can be expensive.  Never start/refresh it while the
    # collector is actively fetching listings; the existing cached CSV stays usable.
    if refresh:job.refresh(max_age=max_age)
    return job


class AutomaticCollection:
    """One low-concurrency collection loop per saved namespace; no Streamlit UI in its thread."""
    def __init__(self,db,settings,summary=None):
        settings=dict(settings)
        settings['providers']=['SUUMO']
        settings.pop('homes_disabled_reason',None)
        settings.pop('homes_disabled_at',None)
        validate_automatic_settings(settings);validate_automatic_summary(summary or {})
        self.db=db;self.settings=dict(settings);self.settings['interval_minutes']=0;self.summary=dict(summary or {});self.summary.pop('next_run_at',None);self.build=BUILD
        # Only a *new* controller reaches here. A persisted 'running' checkpoint
        # without a live worker means the server died in the middle of the town.
        # Do not pretend that the incomplete town was fully searched.
        if self.summary.get('current_status')=='running' and self.summary.get('task_labels'):
            labels=list(self.summary['task_labels'])
            index=int(self.summary.get('current_task_index',self.summary.get('next_task_index',0)) or 0)%len(labels)
            advance_automatic_task(self.summary,index,len(labels),False)
            self.summary['last_interrupted_town']=labels[index]
            self.summary['last_interrupted_at']=utc_now()
            self.summary['current_status']='interrupted'
        self.lock=threading.RLock();self.io_lock=threading.Lock();self.stop_event=threading.Event()
        self.request_starts={};self.shared_web=PublicWeb();self.shared_web.cancel_event=self.stop_event
        self.last_job=None;self.current=None;self.message='検索候補の町名を確認しています';self.progress=0.;self.error=''
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-automatic-collection')
    def ensure_plan(self):
        wards=automatic_ward_codes(self.settings)
        with self.lock:
            cached=self.settings.get('auto_regions') or []
            cached_wards={str(r.get('code')) for r in cached}
            if cached and self.settings.get('auto_munis') and set(wards).issubset(cached_wards):
                plan=automatic_task_plan(self.settings)
                if plan:
                    if self.summary.get('task_labels')!=[p['label'] for p in plan]:
                        reconcile_automatic_task_progress(self.summary,[],plan)
                    else:
                        self.summary['total_tasks']=len(plan)
                    return plan
        self.shared_web.cancel_event=self.stop_event
        if hasattr(self.shared_web,'configure'):self.shared_web.configure(getattr(self.db,'web_config',{}))
        town_filters=automatic_ward_town_filters(self.settings)
        all_regions=[];all_munis={}
        # Only one ward's area catalog is fetched at a time. The visible
        # progress and a cooperative stop are preserved between wards.
        for ordinal,ward in enumerate(wards,1):
            if self.stop_event.is_set():return []
            with self.lock:
                self.message=f'町名一覧の取得 {ordinal}/{len(wards)}区｜{TOKYO_WARDS[ward]}'
            regions,munis,_=suumo_ward_regions(self.shared_web,ward)
            regions=select_ward_towns(regions,town_filters.get(ward,[]))
            if not regions:raise AppError(TOKYO_WARDS[ward]+'の検索対象の町がありません。')
            all_regions.extend(regions)
            if isinstance(munis,dict):all_munis.update(munis)
        with self.lock:
            self.settings['auto_regions']=all_regions;self.settings['auto_munis']=all_munis
            plan=automatic_task_plan(self.settings)
            if not plan:raise AppError('自動収集する町が見つかりません。')
            if self.summary.get('task_labels')!=[p['label'] for p in plan]:
                reconcile_automatic_task_progress(self.summary,[],plan)
            else:
                self.summary['total_tasks']=len(plan)
            if 'cycle_completed_indices' not in self.summary:
                nxt=int(self.summary.get('next_task_index',0) or 0)%len(plan)
                self.summary['cycle_completed_indices']=list(range(nxt))
            self.summary.setdefault('cycle_number',1)
            self.message=f'選択 {len(wards)}区｜検索候補 {len(plan)}町を確認しました。'
        self.persist_best_effort('region_plan')
        return plan
    def persist_best_effort(self,stage):
        """Do not let a failed *controller checkpoint* mask actual listing DB results.

        The SearchJob separately probes/search-saves and confirms each listing write;
        failed listing writes must never be recorded as saved.
        """
        try:
            self.persist()
        except AppError as exc:
            diagnostic=dict(getattr(exc,'diagnostic',{}) or {})
            item={'stage':stage,'time':utc_now(),'error':str(exc)[:480],
                  'db_diagnostic':diagnostic}
            with self.lock:
                self.summary['checkpoint_error']=item
                job=self.current or self.last_job
            if job is not None:
                job.audit.add('database','automatic_checkpoint_failed','ERROR',item)
            return False
        with self.lock:self.summary.pop('checkpoint_error',None)
        return True
    def persist(self):
        with self.io_lock:
            with self.lock:
                record={'id':AUTO_COLLECTION_ID,'status':'automatic_collection_settings','started_at':self.settings.get('created_at',utc_now()),
                        'finished_at':None,'conditions':json.loads(json.dumps({k:v for k,v in self.settings.items() if k not in ('auto_regions','auto_munis')})), 'summary':json.loads(json.dumps(self.summary))}
            self.db.save_search(record)
    def snapshot(self):
        with self.lock:
            job=self.current
            result=dict(settings=dict(self.settings),summary=dict(self.summary),message=self.message,error=self.error,build=getattr(self,'build',None),
                        running=self.thread.is_alive(),stopping=self.stop_event.is_set(),progress=self.progress)
        if job:
            snap=job.snapshot(include_units=False);result.update(message=snap['message'],progress=snap['progress'],current=snap)
        return result
    def request_stop(self):
        with self.lock:
            self.stop_event.set()
            self.settings['enabled']=False;self.message='停止要求を受け付けました。取得済みデータを保存して終了します。'
            job=self.current
        if job:job.request_stop()
        self.persist()
    def run(self):
        try:
            self.ensure_plan()
            while not self.stop_event.is_set():
                pressure=cloud_memory_pressure()
                if memory_needs_guard(pressure):
                    with self.lock:
                        self.error=('サーバーメモリの使用率が80%に達したため、自動収集を一時停止しました。'+
                                    '保存済み物件と未着手の町は保持しています。')
                        self.settings['enabled']=False
                    self.stop_event.set()
                    self.persist_best_effort('memory_budget')
                    break
                with self.lock:
                    if self.stop_event.is_set():break
                    plan=automatic_task_plan(self.settings)
                    if not plan:raise AppError('自動収集する町が見つかりません。')
                    total=len(plan);index=int(self.summary.get('next_task_index',0) or 0)%total
                    if self.summary.pop('cycle_reset_pending',False):
                        self.summary['cycle_completed_indices']=[]
                        self.summary['cycle_attempted_indices']=[]
                        self.summary['incomplete_task_indices']=[]
                        self.summary['cycle_number']=int(self.summary.get('cycle_number',1) or 1)+1
                    self.summary['task_labels']=[p['label'] for p in plan];self.summary['total_tasks']=total
                    self.summary['current_task_index']=index;self.summary['current_task_label']=plan[index]['label']
                    current_task=plan[index]
                    ward=current_task['ward_code']
                    # Pass only the active ward to SearchJob. Its local town
                    # index is NOT the global controller progress index.
                    active_regions=[r for r in (self.settings.get('auto_regions') or [])
                                    if str(r.get('code'))==ward]
                    if not active_regions:raise AppError('選択区の収集対象を確認できません：'+TOKYO_WARDS[ward])
                    conditions={'bounds':[34,138,37,141],'ward_code':ward,'providers':['SUUMO'],
                                'mode':'automatic_collection','auto_task_index':index,'auto_task_label':current_task['label'],
                                'auto_task_identity':current_task['identity'],
                                'auto_controller_index':index,
                                'auto_regions':active_regions,'auto_munis':self.settings.get('auto_munis') or {},
                                # Each ward has already been filtered in ensure_plan.
                                'town_codes':[]}
                    self.summary['last_started_at']=utc_now();self.summary['current_status']='running'
                    self.message='検索中｜'+plan[index]['label']
                    job=SearchJob(self.db,conditions)
                    # Keep the last search id even if the collector fails before
                    # search_all writes a finished search summary.
                    self.summary['last_search_id']=job.audit.search_id
                    if hasattr(job,'state'):job.state.request_starts=self.request_starts;job.state.shared_web=self.shared_web
                    self.current=job
                self.persist_best_effort('before_town')
                # The collector may legitimately have many pages. Time out a *town*,
                # not the whole collection; the saved rows remain safely in Supabase.
                job.state.last_activity_at=time.monotonic()
                started_town=time.monotonic()
                watchdog_finished=threading.Event()
                timeout_reason=[]
                def watch_town():
                    while not watchdog_finished.wait(1):
                        if self.stop_event.is_set() or job.cancel_event.is_set():return
                        running_for=time.monotonic()-started_town
                        idle_for=time.monotonic()-job.state.last_activity_at
                        memory=cloud_memory_pressure()
                        if memory_needs_guard(memory):
                            timeout_reason.append('サーバーメモリ保護により中断（'+str(memory['used_mib'])+
                                                  '/'+str(memory['limit_mib'])+' MiB）')
                            self.error=timeout_reason[-1]+'。保存済み物件は保持しています。'
                            self.stop_event.set()
                            job.request_stop()
                            return
                        if running_for>=AUTO_TOWN_MAX_SECONDS or idle_for>=AUTO_TOWN_IDLE_SECONDS:
                            reason=('町ごとの最大実行時間' if running_for>=AUTO_TOWN_MAX_SECONDS
                                    else '取得処理の進捗停止')
                            timeout_reason.append(reason+f'（実行{int(running_for)}秒・無進捗{int(idle_for)}秒）')
                            job.audit.add('automatic','town_timeout','WARNING',
                                          {'town':conditions['auto_task_label'],'reason':reason,
                                           'running_seconds':int(running_for),'idle_seconds':int(idle_for),
                                           'decision':'retry_later_not_completed'})
                            job.request_stop()
                            return
                watcher=threading.Thread(target=watch_town,daemon=True,name='housing-town-watchdog')
                watcher.start()
                try:
                    job.run(self.db,conditions)
                finally:
                    watchdog_finished.set()
                    watcher.join(timeout=2)
                snap=job.snapshot(include_units=False);result=snap.get('result') or {};status=result.get('status','failed')
                if timeout_reason and not self.stop_event.is_set() and status!='failed':
                    result['status']='partial'
                    stats=result.setdefault('summary',{})
                    stats['incomplete_tasks']=max(1,int(stats.get('incomplete_tasks') or 0))
                    stats['issues']=list(stats.get('issues') or [])+[timeout_reason[0]+'。未確認として次の町へ進みます。']
                    status='partial'
                incomplete=bool(result.get('summary',{}).get('incomplete_tasks'))
                with self.lock:
                    previous_job=self.last_job
                    self.last_job=job;self.current=None;self.progress=1.;self.summary['last_finished_at']=utc_now();self.summary['last_status']=status
                    self.summary['current_status']='stopped';self.summary['last_task']=conditions.get('auto_task_label',self.summary.get('current_task_label','地域判定'))
                    # Keep a compact controller checkpoint, not full error details.
                    self.summary['last_summary']=dict(result.get('summary',{}) or {})
                    self.summary['last_summary']['issues']=list((
                        self.summary['last_summary'].get('issues') or [])[-5:])
                    self.summary['saved_observations']=int(self.summary.get('saved_observations',0))+int(snap['saved_count'])
                    stats=result.get('summary',{})
                    for name,source in (('new_saved_observations','new_saved'),('updated_saved_observations','updated_saved'),('failed_save_observations','unsaved'),('processed_observations','processed'),('error_observations','errors'),('detail_observations','detail'),('skipped_observations','already_acquired'),('rejected_observations','rejected')):
                        self.summary[name]=int(self.summary.get(name,0))+int(stats.get(source,0))
                    self.summary['statistics_started_at']=self.summary.get('statistics_started_at') or self.summary['last_started_at']
                    self.summary['collection_seconds']=float(self.summary.get('collection_seconds',0))+float(stats.get('elapsed',0))
                    self.summary['confirmed_observations']=int(self.summary.get('confirmed_observations',0))+int(snap['unit_count'])
                    # SearchJob rewrites auto_task_index for its *single* ward;
                    # the queue checkpoint must use the original GLOBAL index.
                    total=len(plan)
                    actual_index=int(conditions.get('auto_controller_index',index) or 0)%total
                    success=status=='completed' and not incomplete and not self.stop_event.is_set()
                    self.summary.pop('next_run_at',None)
                    if status=='cancelled' or self.stop_event.is_set():
                        self.message='取得・保存処理を中止しました。'
                    elif status=='failed':
                        # Database/service failures are not page failures: stop rather
                        # than hammering every town while persistent storage is down.
                        self.settings['enabled']=False;self.stop_event.set()
                        last_issues=stats.get('issues') or []
                        self.error='検索処理が失敗しました。'+(str(last_issues[-1])[:320] if last_issues else '詳細ログを確認してください。')
                        self.message='保存先または検索処理が失敗したため停止しました。'
                    else:
                        outcome=advance_automatic_task(self.summary,actual_index,total,success)
                        if success:
                            self.summary['completed_runs']=int(self.summary.get('completed_runs',0))+1
                            # Keep the whole multi-ward plan. Saving only this
                            # SearchJob's active ward would erase subsequent wards.
                        if outcome=='paused_incomplete':
                            self.settings['enabled']=False;self.stop_event.set()
                            self.error='未確認の町が'+str(len(self.summary.get('incomplete_task_indices',[])))+'件残っています。取得できた町は保持し、次回は未確認の町から再開します。'
                            self.message='他の町の確認を終えました。未確認の町は再試行待ちです。'
                        elif outcome=='cycle_complete':
                            # A complete pass is finite; do not automatically rescan
                            # every town while the mobile screen is in the background.
                            self.settings['enabled']=False;self.stop_event.set()
                            self.message='全町の確認が完了しました。収集を終了します。再取得は開始ボタンから行えます。'
                        elif not success:
                            self.message='一戸建て等の未確認ページを記録して、次の町へ進みます。'
                        else:
                            self.message='保存完了。次の町へ進みます。'
                # The controller retains the last job for TXT/JSON diagnostics.
                # Its thousands of temporary listing rows are already stored in
                # Supabase and counted above, so release them before the next town.
                try:
                    with job.lock:
                        job.state.new_units=[]
                        job.state.new_saved_keys=[]
                        job.state.new_regions=[]
                        job.state.shared_web=None
                except (AttributeError,TypeError):
                    pass
                # Keep only the latest town's full TXT/JSON file. Historical
                # diagnostic summaries are already persisted in Supabase.
                if previous_job is not None and previous_job is not job:
                    try:previous_job.audit.release()
                    except (AttributeError,OSError):pass
                self.shared_web.http_cache.clear()
                self.shared_web.jhj_tile_cache.clear()
                self.shared_web.jhj_tile_cached_at.clear()
                self.shared_web.suumo_location_cache.clear()
                self.shared_web.address_result_cache.clear()
                self.shared_web.failed_detail_urls.clear()
                del snap
                if not self.persist_best_effort('after_town'):
                    # Without a confirmed progress checkpoint a subsequent town
                    # could be repeated on restart. Pause, retain all confirmed listings.
                    with self.lock:
                        self.settings['enabled']=False
                        self.stop_event.set()
                        self.error='町の検索結果は保持していますが、自動収集の進捗をSupabaseへ保存できません。DB診断を確認してください。'
                        self.message='進捗保存未確認のため停止しました。物件ごとの保存確認は別途記録されています。'
                # A town's listing objects and response caches have just been
                # dropped. Return unused Python arenas and clean file pages.
                _v126_work_finished('town-collection')
        except Exception as exc:
            with self.lock:
                active_job=self.current or self.last_job
                self.error=str(exc) if isinstance(exc,AppError) else '自動収集エラー（'+type(exc).__name__+'）'
                self.message='自動収集が停止しました。設定画面から再開してください。'
                self.summary['last_status']='failed';self.settings['enabled']=False
            try:
                audit=active_job.audit if active_job is not None else AuditLog({
                    'bounds':[34,138,37,141],'ward_code':self.settings.get('ward_code'),'town_codes':self.settings.get('town_codes',[]),
                    'providers':list(self.settings.get('providers') or AUTOMATIC_REGION_PROVIDERS),'mode':'automatic_collection'},(getattr(self.db,'key',''),))
                audit.add('automatic','automatic_collection_failure','ERROR',{'message':self.error,'exception_type':type(exc).__name__,'traceback':traceback.format_exc()})
                audit.persist(self.db,force=True)
            except Exception:pass
            try:self.persist()
            except Exception:pass
        finally:
            with self.lock:self.current=None
            # Flush all pending town-map changes once the automatic collection
            # has stopped, including cancellation and partial results.
            notify_town_collection_finished(self.db)

@st.cache_resource
def automatic_registry():
    return {},threading.RLock()

def automatic_registry_key(db):
    return hashlib.sha256((db.url+'|'+db.namespace).encode()).hexdigest()

def get_automatic_collection():
    controller=st.session_state.get('automatic_collection_controller')
    if not controller:return None
    registry,lock=automatic_registry()
    with lock:
        key=automatic_registry_key(controller.db)
        controller=registry.get(key,controller)
        # Streamlit can keep an old object even after app.py was replaced. An old
        # *stopped* controller is safe to replace without discarding search progress.
        # Never replace a still-running controller while its worker could write.
        if getattr(controller,'build',None)!=BUILD and not controller.snapshot().get('running'):
            old_snapshot=controller.snapshot()
            previous_settings=dict(old_snapshot.get('settings') or {})
            new_settings=dict(previous_settings)
            new_settings['providers']=['SUUMO']
            new_settings.pop('homes_disabled_reason',None)
            new_settings.pop('homes_disabled_at',None)
            summary=dict(old_snapshot.get('summary') or {})
            old_plan=automatic_task_plan(previous_settings)
            new_plan=automatic_task_plan(new_settings)
            if new_plan and (old_plan!=new_plan or
                             summary.get('task_labels')!=[task['label'] for task in new_plan]):
                reconcile_automatic_task_progress(summary,old_plan,new_plan)
            fresh_db=Database()
            fresh_db.web_config=rental_network_settings()
            fresh=AutomaticCollection(fresh_db,new_settings,summary)
            fresh.last_job=getattr(controller,'last_job',None) or getattr(controller,'current',None)
            fresh.message='停止した旧版の自動収集を現行版へ切り替えました。検索は「開始・再開」で再開してください。'
            registry[key]=fresh
            controller=fresh
        st.session_state.automatic_collection_controller=controller
    return controller

def automatic_busy():
    controller=get_automatic_collection()
    return bool(controller and controller.snapshot()['running'])

def restore_automatic_collection():
    # One lookup when a browser session starts. It never runs on map pan or progress polls.
    if st.session_state.get('automatic_settings_checked'):return
    st.session_state.automatic_settings_checked=True
    try:
        db=Database();db.web_config=rental_network_settings();registry,lock=automatic_registry();key=automatic_registry_key(db)
        with lock:
            controller=registry.get(key)
            if controller:
                # A stopped job still owns the last audit file and the persisted summary.
                # Do not discard it merely because its worker thread has exited.
                st.session_state.automatic_collection_controller=controller
                return
            rows=db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+db.namespace,'id':'eq.'+AUTO_COLLECTION_ID,'select':'conditions,summary','limit':1})
            if not isinstance(rows,list):raise AppError('自動収集設定を読み取れません。')
            if not rows:return
            settings=rows[0].get('conditions') or {};summary=rows[0].get('summary') or {}
            settings['providers']=['SUUMO']
            settings.pop('homes_disabled_reason',None)
            settings.pop('homes_disabled_at',None)
            validate_automatic_settings(settings);validate_automatic_summary(summary)
            st.session_state.automatic_saved_settings=settings;st.session_state.automatic_saved_summary=summary
            controller=AutomaticCollection(db,settings,summary);registry[key]=controller
            st.session_state.automatic_collection_controller=controller
            # Restore progress but never start an automatic job merely because
            # an old DB checkpoint still says enabled. Explicit Start resumes.
            if settings.get('enabled'):
                controller.settings['enabled']=False
                controller.message='前回の進捗を復元しました。収集は停止中です。開始・再開ボタンで再開できます。'
    except Exception as exc:st.session_state.automatic_settings_error=str(exc) if isinstance(exc,AppError) else '自動収集設定の復元に失敗しました（'+type(exc).__name__+'）'

def validate_automatic_settings(settings):
    if not isinstance(settings,dict):raise AppError('自動収集設定の形式が不正です。')
    wards=automatic_ward_codes(settings)
    if settings.get('ward_code')!=wards[0]:
        raise AppError('自動収集の先頭区設定が一致しません。')
    legacy_codes=settings.get('town_codes',[])
    if not isinstance(legacy_codes,list) or any(
        not isinstance(code,str) or not re.fullmatch(re.escape(wards[0])+r'\d{3}',code)
        for code in legacy_codes
    ):raise AppError('対象町名の設定が不正です。')
    automatic_ward_town_filters(settings)
    providers=[canonical_provider(p) for p in (settings.get('providers') or [])]
    if providers!=['SUUMO']:raise AppError('SUUMO-only automatic collection requires SUUMO.')

def validate_automatic_summary(summary):
    if not isinstance(summary,dict):raise AppError('自動収集の進捗形式が不正です。')
    for key in ('next_task_index','total_tasks','completed_runs','saved_observations','confirmed_observations'):
        try:
            if key in summary and int(summary[key])<0:raise ValueError()
        except (ValueError,TypeError):raise AppError('自動収集の進捗が不正です：'+key) from None
    if not isinstance(summary.get('last_summary',{}),dict):raise AppError('自動収集の前回結果が不正です。')

def start_automatic_collection(ward_codes,interval_minutes=0,town_codes=None,*,restart_all=False):
    # A fresh pass resets the selected wards' checkpoint only. The original
    # rental_listing and rental_acquisition records are never deleted.
    wards=automatic_ward_codes({'ward_codes':ward_codes})
    if restart_all:
        filters={ward:[] for ward in wards}
    elif isinstance(town_codes,dict):
        filters={ward:list(dict.fromkeys(town_codes.get(ward,[]))) for ward in wards}
    else:
        # Backwards-compatible one-ward callers.
        filters={ward:(list(dict.fromkeys(town_codes or [])) if ward==wards[0] else []) for ward in wards}
    settings={'enabled':True,'ward_code':wards[0],'ward_codes':wards,
              'providers':list(AUTOMATIC_REGION_PROVIDERS),'interval_minutes':0,
              'created_at':utc_now(),'town_codes':list(filters[wards[0]]),
              'town_codes_by_ward':filters}
    validate_automatic_settings(settings)
    db=Database();db.web_config=rental_network_settings();registry,lock=automatic_registry();key=automatic_registry_key(db)
    with lock:
        old=registry.get(key)
        if old and old.thread.is_alive():raise AppError('自動収集が稼働中です。停止してから範囲を変更してください。')
        old_snapshot=old.snapshot() if old else {}
        prior=old_snapshot.get('settings',st.session_state.get('automatic_saved_settings',{}))
        previous_summary=old_snapshot.get('summary',st.session_state.get('automatic_saved_summary',{}))
        # Provider availability is not part of the town scope. A previously saved
        # SUUMO-only run must keep its completed towns when HOME'S is re-probed.
        try:
            prior_wards=automatic_ward_codes(prior)
            prior_filters=automatic_ward_town_filters(prior)
        except AppError:
            prior_wards=[];prior_filters={}
        same_scope=(prior_wards==wards and prior_filters==filters)
        summary=dict(previous_summary) if same_scope and not restart_all else {}
        if same_scope and not restart_all and summary.get('layout_collection_revision')!='studio_1r_1k_1dk_v174':
            # Layout coverage expanded: formerly completed towns did not search
            # 1R/1K/1DK. Revisit each town once without deleting saved listings.
            for progress_key in ('cycle_completed_indices','cycle_attempted_indices',
                                 'incomplete_task_indices'):
                summary[progress_key]=[]
            summary['next_task_index']=0
            summary.pop('cycle_reset_pending',None)
        summary['layout_collection_revision']='studio_1r_1k_1dk_v174'
        if same_scope and not restart_all:
            # Preserve attempted/failed markers so a restart does not hammer the
            # same incomplete town when other towns were never attempted.
            labels=list(summary.get('task_labels') or [])
            attempted={int(i) for i in (summary.get('cycle_attempted_indices') or []) if str(i).isdigit()}
            remaining=[i for i in range(len(labels)) if i not in attempted]
            pending=[int(i) for i in (summary.get('incomplete_task_indices') or []) if str(i).isdigit()]
            if remaining:summary['next_task_index']=remaining[0]
            elif pending:
                # Starting a new pass gives unresolved towns another attempt.
                summary['cycle_attempted_indices']=list(summary.get('cycle_completed_indices') or [])
                summary['next_task_index']=min(pending)
        for item in ('auto_regions','auto_munis'):
            # Rebuild the town plan from the live ward list on a fresh pass.
            if same_scope and not restart_all and prior.get(item):settings[item]=prior[item]
        controller=AutomaticCollection(db,settings,summary)
        saved=controller.persist_best_effort('restart_from_first' if restart_all else 'start')
        # Do not announce a fresh restart unless its blank progress checkpoint is durable.
        if restart_all and not saved:
            raise AppError('全町の再検索を開始できません。進捗の初期化をSupabaseへ保存できませんでした。')
        registry[key]=controller;st.session_state.automatic_collection_controller=controller
        st.session_state.automatic_settings_error='';controller.thread.start()
    return controller

@st.cache_resource
def automatic_upgrade_registry():
    return set(),threading.RLock()


def schedule_automatic_upgrade(controller):
    if controller is None or getattr(controller,'build',None)==BUILD:return False
    snap=controller.snapshot()
    if not snap.get('running'):return False
    key=automatic_registry_key(controller.db);pending,pending_lock=automatic_upgrade_registry();registry,registry_lock=automatic_registry()
    with pending_lock:
        if key in pending:return True
        pending.add(key)
    desired_settings=dict(snap.get('settings') or {});desired_settings['enabled']=True
    desired_settings['providers']=['SUUMO']
    def handoff():
        try:
            # Stop is cooperative even when the metadata save fails. Wait until the
            # old worker has fully exited before starting a new writer.
            try:controller.request_stop()
            except AppError:pass
            controller.thread.join()
            latest=controller.snapshot();settings=dict(desired_settings);settings['enabled']=True
            summary=dict(latest.get('summary') or {})
            old_plan=automatic_task_plan(latest.get('settings') or {})
            new_plan=automatic_task_plan(settings)
            if new_plan and old_plan!=new_plan:
                reconcile_automatic_task_progress(summary,old_plan,new_plan)
            fresh_db=Database();fresh_db.web_config=rental_network_settings()
            replacement=AutomaticCollection(fresh_db,settings,summary)
            replacement.persist_best_effort('upgrade')
            with registry_lock:registry[key]=replacement
            replacement.thread.start()
        finally:
            with pending_lock:pending.discard(key)
    threading.Thread(target=handoff,daemon=True,name='housing-automatic-upgrade').start()
    return True


def latest_automatic_search_id(db,summary):
    """Look up an actual saved search id, never synthesize audit events."""
    summary=summary if isinstance(summary,dict) else {}
    details=summary.get('last_summary') if isinstance(summary.get('last_summary'),dict) else {}
    diagnostics=details.get('diagnostics') if isinstance(details.get('diagnostics'),dict) else {}
    for candidate in (summary.get('last_search_id'),diagnostics.get('search_id')):
        if isinstance(candidate,str) and re.fullmatch(r'[0-9a-f]{64}',candidate):return candidate
    # Older releases did not persist last_search_id into the controller summary.
    rows=db.call('GET',SEARCH_TABLE,{
        'namespace':'eq.'+db.namespace,
        'status':'in.(started,completed,partial,failed,cancelled)',
        'conditions->>mode':'eq.automatic_collection',
        'select':'id','order':'started_at.desc','limit':1})
    for row in (rows if isinstance(rows,list) else []):
        value=row.get('id')
        if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value):return value
    return None


def load_saved_search_audit_small_pages(db,search_id):
    """Read saved audit chunks with bounded payloads even when large reads get HTTP 500."""
    if not isinstance(search_id,str) or not re.fullmatch(r'[0-9a-f]{64}',search_id):
        raise AppError('保存済みの検索IDを確認できません。')
    out=[];last_id=''
    while True:
        for size in (8,2,1):
            params={'namespace':'eq.'+db.namespace,'status':'eq.diagnostic_log',
                    'id':('gt.'+last_id if last_id else 'like.'+search_id+'.log.*'),
                    'select':'id,summary','order':'id.asc','limit':size}
            try:
                # After the first page, the keyset filter still needs the search-id
                # namespace to avoid streaming other searches' unrelated records.
                if last_id:params['and']=f'(id.like.{search_id}.log.*,id.gt.{last_id})';params.pop('id')
                rows=db.call('GET',SEARCH_TABLE,params)
                if not isinstance(rows,list):raise AppError('詳細ログの応答形式が不正です。')
                break
            except AppError as exc:
                if size==1 or not re.search(r'HTTP 5\d\d',str(exc)):raise
        if not rows:break
        for row in rows:
            if str(row.get('id') or '').startswith(search_id+'.log.'):
                out.extend((row.get('summary') or {}).get('events') or [])
        new_last=str(rows[-1].get('id') or '')
        if not new_last or new_last<=last_id:raise AppError('詳細ログのページ取得位置を更新できません。')
        last_id=new_last
        if len(rows)<size:break
    return out


class SavedAutomaticLogLoader:
    """Asynchronously restore a prior search's persisted TXT/JSON audit events."""
    def __init__(self,db):
        self.db=db;self.lock=threading.RLock();self.search_id=None;self.running=False
        self.loaded=False;self.events=[];self.error='';self.thread=None

    def start(self,summary):
        with self.lock:
            if self.running:return
            self.running=True;self.error=''
            self.thread=threading.Thread(target=self._run,args=(dict(summary or {}),),
                                         daemon=True,name='housing-saved-audit-load')
            self.thread.start()

    def _run(self,summary):
        try:
            search_id=latest_automatic_search_id(self.db,summary)
            if not search_id:raise AppError('保存済み自動収集の検索IDが見つかりません。')
            with self.lock:
                if search_id!=self.search_id:
                    self.events=[];self.loaded=False
                self.search_id=search_id
            events=load_saved_search_audit_small_pages(self.db,search_id)
            if not events:raise AppError('該当する検索の詳細ログがSupabaseにありません。保存失敗または検索直後の可能性があります。')
            with self.lock:
                self.events=events;self.loaded=True;self.error=''
        except Exception as exc:
            with self.lock:self.error=str(exc) if isinstance(exc,AppError) else type(exc).__name__+': '+str(exc)
        finally:
            with self.lock:self.running=False

    def snapshot(self):
        with self.lock:
            return {'running':self.running,'loaded':self.loaded,'events':self.events,
                    'search_id':self.search_id,'error':self.error}


@st.cache_resource
def saved_automatic_log_loader_registry():
    return {},threading.RLock()


def get_saved_automatic_log_loader():
    db=Database();key=automatic_registry_key(db);registry,lock=saved_automatic_log_loader_registry()
    with lock:
        if key not in registry:registry[key]=SavedAutomaticLogLoader(db)
        return registry[key]


def automatic_log_download_panel(controller,busy,summary):
    """Show the TXT/JSON section even without a running or cached controller."""
    log_job=None
    if controller:
        with controller.lock:log_job=controller.current or controller.last_job
    events=[];source=''
    if log_job is not None:
        try:
            cache=st.session_state.get('automatic_log_cache')
            search_id=log_job.audit.search_id;now=time.monotonic()
            # Do not parse and JSON-serialize every historical event on each
            # Streamlit fragment poll. Prepare the full TXT/JSON only on demand.
            if st.button('詳細ログを更新してTXT・JSONを準備',key='prepare_automatic_log'):
                cache={'search_id':search_id,'count':int(log_job.audit.count),'at':now,
                       'events':log_job.audit.records()}
                st.session_state.automatic_log_cache=cache
            if isinstance(cache,dict) and cache.get('search_id')==search_id:
                events=cache.get('events') or [];source='サーバーの作業ログ'
        except Exception as exc:
            st.warning('現在の作業ログを開けません：'+type(exc).__name__)
    if not events:
        cache=st.session_state.get('automatic_log_cache')
        if isinstance(cache,dict) and cache.get('events'):
            events=cache['events'];source='前回表示した作業ログ'
    loader=None
    try:
        loader=get_saved_automatic_log_loader()
        saved=loader.snapshot()
    except Exception as exc:
        st.warning('保存済みログの復元機能に接続できません：'+type(exc).__name__)
        saved={'running':False,'loaded':False,'events':[],'error':''}
    if not events and saved['loaded']:
        events=saved['events'];source='Supabaseから復元した詳細ログ'
    if events:
        st.caption('出力元：'+source+'｜'+str(len(events))+'イベント')
        diagnostic_downloads(events,'automatic')
    else:
        # Keep both controls in the UI after a stopped job / process restart.
        c1,c2=st.columns(2)
        with c1:st.download_button('詳細ログ TXT',b'','sumai_work_log.txt','text/plain',
                                  key='automatic_txt_empty',disabled=True,use_container_width=True)
        with c2:st.download_button('詳細ログ JSON',b'','sumai_work_log.json','application/json',
                                  key='automatic_json_empty',disabled=True,use_container_width=True)
        st.caption('現在のログがメモリ上にありません。保存済みログがある場合は下から復元してください。')
    if loader is not None:
        if st.button('前回の詳細ログをSupabaseから復元',key='restore_automatic_txt_json',
                     disabled=saved['running']):
            loader.start(summary)
            st.rerun()
        if saved['running']:st.caption('保存済みログをバックグラウンドで読込中です。')
        if saved['error']:st.error('保存済みログの復元に失敗：'+saved['error'])
        if saved['loaded'] and events is not saved['events']:
            st.caption('保存済みログは復元済みです。停止後の再起動でも再取得できます。')


def automatic_collection_ward_filter_ui(ward_codes, filters, busy=False):
    """v154: A chosen ward in the primary multiselect always takes priority.

    Multi-ward collection scans ALL towns in each selected ward. The optional
    single-ward town filter is cleared/locked to avoid silently narrowing any
    of those wards by a stale or previously loaded town filter. If no ward is
    chosen above, the lower selector remains an independent single-ward path
    for users who want to narrow that ward to specific towns.
    """
    if ward_codes:
        # This key belongs only to the *enabled* widget. Reset it before the
        # disabled placeholder is rendered, even when changing from single-ward
        # filtering to multi-ward scanning in the same Streamlit session.
        st.session_state['automatic_filter_ward']=None
        st.selectbox('町名を絞る区（任意）', [''], index=0,
                     key='automatic_filter_ward_disabled',disabled=True,
                     format_func=lambda value:'')
        return list(ward_codes),{code:[] for code in ward_codes}

    # The lower selector becomes usable only when the upper ward list is empty.
    # index=None makes the default genuinely blank rather than preselecting a ward.
    ward_code=st.selectbox('町名を絞る区（任意）',list(TOKYO_WARDS),index=None,
                           format_func=lambda code:TOKYO_WARDS[code],
                           key='automatic_filter_ward',disabled=busy)
    if not ward_code:
        return [],{}
    town_codes=ward_town_selector(ward_code,'automatic',filters.get(ward_code,[]),disabled=busy)
    filters[ward_code]=list(town_codes)
    return [ward_code],{ward_code:list(town_codes)}


@st.fragment(run_every='16s')
def automatic_collection_panel():
    st.subheader('アプリ内の自動収集（SUUMO）')
    memory=cloud_memory_pressure()
    if memory:
        st.caption(f"処理と非回収キャッシュの目安 {memory['effective_mib']}/{memory['limit_mib']} MiB" 
                   f"（{memory['effective_ratio']:.0%}）｜コンテナ計上 {memory['used_mib']} MiB" 
                   f"｜Python RSS {memory['rss_mib']} MiB｜非アクティブなファイルページ {memory['inactive_mib']} MiB。"
                   '保護停止は実効80%以上またはコンテナ全体96%以上。')
    st.caption('収集中は画面を閉じても継続します。サーバーの再起動後は自動再開しません。進捗は保存され、開始・再開ボタンで再開できます。')
    controller=get_automatic_collection();snap=controller.snapshot() if controller else None
    # Legacy per-error persistence bridge intentionally disabled. Historical
    # diagnostics can now be discarded without being written back on UI reruns.
    busy=bool(snap and snap['running'])
    st.caption('画面コード版 '+BUILD+'｜自動収集処理版 '+str(snap.get('build') or 'なし' if snap else 'なし'))
    if controller and getattr(controller,'build',None)!=BUILD and busy:
        st.warning('旧版ワーカーが実行中です。新版への切替処理を実施しています。')
        schedule_automatic_upgrade(controller)
    if snap and (snap.get('summary') or {}).get('checkpoint_error'):
        checkpoint=snap['summary']['checkpoint_error']
        st.error('自動収集の進捗保存失敗｜'+str(checkpoint.get('stage'))+'｜'+str(checkpoint.get('error')))
        st.caption('診断：'+json.dumps(checkpoint.get('db_diagnostic') or {},ensure_ascii=False)[:1200])
    saved=snap['settings'] if snap else st.session_state.get('automatic_saved_settings',{})
    # Category choice is an additive shortcut. The final ward multiselect is
    # authoritative: users may remove/add any individual ward afterwards.
    try:default_wards=automatic_ward_codes(saved)
    except AppError:default_wards=[]
    if 'automatic_wards' not in st.session_state:
        # v171: a fresh 23-ward run must not silently inherit an old one-ward
        # selection. Keep the actual scope unchanged while a job is running.
        st.session_state['automatic_wards']=default_wards if busy else list(TOKYO_WARDS)
    if busy and len(default_wards)<len(TOKYO_WARDS):
        st.warning(f'現在の収集設定は{len(default_wards)}/23区です。中止後、23区を選択して開始すると対象を拡大できます。')
    st.session_state.setdefault('automatic_area_categories',[])
    st.session_state.setdefault('automatic_applied_area_categories',[])
    st.multiselect('区のカテゴリー（一括追加）',list(TOKYO_WARD_CATEGORIES),
                   key='automatic_area_categories',disabled=busy,
                   on_change=automatic_add_area_categories)
    with st.expander('区カテゴリーの内訳を確認',expanded=False):
        for label,codes in TOKYO_WARD_CATEGORIES.items():
            st.caption(label+'：'+'・'.join(TOKYO_WARDS[code] for code in codes))
    if st.button('東京23区をすべて選択',key='automatic_select_all_23_v171',disabled=busy):
        st.session_state['automatic_wards']=list(TOKYO_WARDS)
        st.rerun(scope='fragment')
    ward_codes=st.multiselect('自動収集する区（複数選択）',list(TOKYO_WARDS),
                              format_func=lambda code:TOKYO_WARDS[code],
                              key='automatic_wards',disabled=busy)
    missing_codes=[code for code in TOKYO_WARDS if code not in ward_codes]
    st.caption(f'選択した区：{len(ward_codes)}/23区')
    if missing_codes:
        st.warning('23区全体の収集ではありません。未選択：'+ '・'.join(TOKYO_WARDS[code] for code in missing_codes))
    reset=st.session_state.pop('_automatic_reset_town_selection',[])
    if isinstance(reset,str):reset=[reset]
    for code in reset:
        if code in TOKYO_WARDS:
            st.session_state['automatic_selected_'+code]=[]
            st.session_state.pop('automatic_towns_'+code,None)
    if 'automatic_town_filters' not in st.session_state:
        try:st.session_state['automatic_town_filters']=automatic_ward_town_filters(saved)
        except AppError:st.session_state['automatic_town_filters']={}
    filters=st.session_state['automatic_town_filters']
    collection_wards,chosen_filters=automatic_collection_ward_filter_ui(ward_codes,filters,busy)
    st.caption('取得順：'+(' → '.join(TOKYO_WARDS[code] for code in collection_wards) if collection_wards else '区を選択してください'))
    st.caption('SUUMOのみで町ごとに最後のページまで検索します。マンションは築15年以内、ワンルーム/1K/1DK・1LDK/2K/2DK・2LDK/3K/3DK、一戸建て・その他は築40年以内・50㎡以上を対象とします。')
    manual=active_job();manual_busy=bool(manual and not manual.snapshot()['finished'])
    cannot_start=busy or manual_busy or not collection_wards
    if st.button('選択した区で自動収集を開始・再開',key='automatic_start',disabled=cannot_start):
        try:
            controller=start_automatic_collection(collection_wards,town_codes=chosen_filters)
            snap=controller.snapshot();busy=True
            st.session_state.automatic_saved_settings=dict(snap['settings'])
        except AppError as exc:st.error(str(exc))
    if st.button('選択した区で自動収集を開始・再開（最初からすべて）',
                 key='automatic_start_all',disabled=cannot_start or busy):
        try:
            controller=start_automatic_collection(collection_wards,town_codes={},restart_all=True)
            snap=controller.snapshot();busy=True
            st.session_state.automatic_saved_settings=dict(snap['settings'])
            st.session_state.automatic_saved_summary=dict(snap['summary'])
            st.session_state['automatic_town_filters']={}
            st.session_state['_automatic_reset_town_selection']=list(collection_wards)
            st.rerun()
        except AppError as exc:st.error(str(exc))
    st.caption('通常は選択した区・町の進捗を復元します。「最初からすべて」は選択した全区の進捗と町名の絞り込みだけを初期化します。保存済み物件・履歴は削除しません。')
    if st.button('自動収集を中止（取得済みデータを保存）',key='automatic_stop',disabled=not busy or bool(snap and snap['stopping'])):
        try:
            controller.request_stop();snap=controller.snapshot()
            st.session_state.automatic_saved_settings=snap['settings'];st.session_state.automatic_saved_summary=snap['summary']
            st.success('停止を要求しました。保存済みデータは保持します。')
        except AppError as exc:st.error('実行の停止は要求しましたが、停止設定の保存に失敗しました：'+str(exc))
    if snap:
        summary=snap['summary'];st.write(snap['message']);st.progress(min(1.,max(0.,snap['progress'])))
        current=snap.get('current') or {};live=snapshot_count_metrics(current)
        if current:st.caption(f"現在｜処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜未保存 {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件")
        if current and busy:
            st.caption('処理工程：'+str(current.get('collection_stage') or '確認中')+
                       '｜取得処理の最終進捗 '+str(int(current.get('idle_seconds') or 0))+'秒前')
        if (snap.get('summary') or {}).get('last_interrupted_town'):
            st.caption('前回のサーバー中断：'+str(snap['summary']['last_interrupted_town'])+
                       '（未確認として保持し、別の町を優先します）')
        cumulative_processed=int(summary.get('processed_observations',0))
        cumulative_saved=int(summary.get('saved_observations',0))
        cumulative_errors=int(summary.get('error_observations',0))
        cumulative_skipped=int(summary.get('skipped_observations',0))
        st.caption(f'累計｜処理 {cumulative_processed}件｜保存 {cumulative_saved}件｜未保存 {cumulative_errors}件｜スキップ {cumulative_skipped}件')
        st.caption('未保存には住所未確定・検索条件外・通信失敗などを含みます。詳細ログで理由を区別します。住所照合：© OpenStreetMap contributors / ODbL（https://www.openstreetmap.org/copyright）')

        labels=list(summary.get('task_labels') or [p['label'] for p in automatic_task_plan(snap.get('settings') or {})])
        if labels:
            total=len(labels);current_index=int(summary.get('current_task_index',summary.get('next_task_index',0)) or 0)%total
            completed={int(x) for x in summary.get('cycle_completed_indices',[]) if isinstance(x,(int,float)) or str(x).isdigit()}
            completed={i for i in completed if 0<=i<total}
            current_label=labels[current_index] if busy else '停止中'
            selected_wards=automatic_ward_codes(snap.get('settings') or {})
            finished_wards=0
            for ward in selected_wards:
                matching=[i for i,label in enumerate(labels)
                          if label.startswith('東京都'+TOKYO_WARDS[ward])]
                if matching and all(i in completed for i in matching):
                    finished_wards+=1
            st.caption(f'区の進捗：{finished_wards}/{len(selected_wards)}区完了｜取得順は指定した区順')
            st.markdown(f"**検索対象 {total}町｜完了 {len(completed)}町｜未確認 {len(summary.get('incomplete_task_indices', []))}町｜現在 {current_label}**")
            # Mobile browsers became sluggish when a 50+ row interactive dataframe
            # was rebuilt every fragment poll.  Keep the same information as plain text.
            lines=[]
            for i,label in enumerate(labels):
                status='検索中' if busy and i==current_index else '完了' if i in completed else '待機'
                lines.append(f'{status}｜{i+1}/{total}｜{label}')
            with st.expander('検索対象の町一覧',expanded=False):st.code('\n'.join(lines),language=None)
        elif busy:
            st.caption('検索対象の町名をバックグラウンドで確認しています。')

        for issue in summary.get('last_summary',{}).get('issues',[])[:4]:st.error(issue)
        if snap['error']:st.error(snap['error'])
    if st.session_state.get('automatic_settings_error'):st.error(st.session_state.automatic_settings_error)

    st.caption('保存物件の読み込みやCSV出力は、この下の『保存した物件』で操作できます。')


# ==========================================================================
# V116: SMALL-AREA CHOROPLETH. The browser receives AREA SUMMARIES ONLY.
# The original unit records and SUUMO collection logic are unchanged.
# Geometry: 2015 e-Stat small-area outlines, simplified by frogcat.
# Source: https://github.com/frogcat/japan-small-area (see its LICENSE).
# ==========================================================================
TOWN_SUMMARY_ID = 'map.town.average.v116'
# Durable, per-namespace generation marker for verified listing writes. It
# belongs to this application and is tiny (one existing search-table record).
# No OS/cache/sibling-app operations and no new database schema are required.
TOWN_DIRTY_MARKER_ID = 'map.town.dirty.v143'
TOWN_DIRTY_POLL_SECONDS = 60.0

TOWN_GEOJSON_URL = 'https://frogcat.github.io/japan-small-area/13.json'
TOWN_GEOJSON_BACKUP = 'https://raw.githubusercontent.com/frogcat/japan-small-area/master/docs/13.json'
# A separate 2021 municipality boundary is a gray BACKDROP ONLY. It is never
# used to infer or invent rent. The legacy 2015 census town polygons remain
# authoritative for colored town/chome averages.
TOWN_WARD_GEOJSON_URL = ('https://raw.githubusercontent.com/smartnews-smri/japan-topography/'
                          'main/data/municipality/geojson/s0010/N03-21_13_210101.json')
TOWN_WARD_GEOJSON_LIMIT = 2 * 1024 * 1024
TOWN_BOUNDARY_CACHE_PATH = os.path.join(tempfile.gettempdir(),'sumai-town-boundary-v173.sqlite')
TOWN_GEOMETRY_LIMIT = 36 * 1024 * 1024
TOWN_GROUPS = ('group0','group1','group2','house')
# Continuous rent scale: 3万円 (low / 1R) to 45万円 (high).
# Separate hues distinguish compact-home rents; higher-priced colors remain
# green -> yellow -> red -> dark burgundy as in the existing map.
TOWN_GRADIENT = (
    (30000,(208,239,255)),
    (60000,(105,191,233)),
    (90000,(36,121,210)),
    (150000,(24,164,114)),
    (200000,(238,199,27)),
    (275000,(225,59,53)),
    (450000,(51,10,26)),
)


def town_chome_key(text):
    """One canonical key for unchanged legacy rows and new SUUMO town rows.

    The color layer itself requires a matching official polygon.  Abbreviated
    Tokyo notation (要町１ / 東池袋3 / 東五反田4-5-9) is normalized here, at read
    time, without altering the underlying listing JSON in Supabase.
    """
    value=address_key(text).replace(' ','').replace('　','').removeprefix('東京都/')
    value=value.removeprefix('東京都')
    ward=next((w for w in sorted(TOKYO_WARDS.values(),key=len,reverse=True) if value.startswith(w)),None)
    if not ward:return None
    rest=value[len(ward):].lstrip('/・,、 ')
    # Explicit chome has the strongest evidence; retain it even when the full
    # historical address continues with block, lot or apartment number.
    match=re.match(r'^([^/0-9－−ー-]{1,40}?\d{1,2}丁目)',rest)
    if match:return ward+'|'+match.group(1)
    # SUUMO normally abbreviates 一丁目 as １, and earlier schema-3 records
    # have the same suffix.  The map geometry remains the final authority:
    # an unrecognized key cannot color an unrelated ward/town polygon.
    match=re.match(r'^([^/0-9－−ー-]{1,40}?)(\d{1,2})(?=$|[-－−ー]|番)',rest)
    if match:
        # 千代田区の一番町・三番町などは丁目のない正式町名。
        # 後続の街区番号を架空の丁目に変換してはならない。
        if match.group(1).endswith('番町'):return ward+'|'+match.group(1)
        return ward+'|'+match.group(1)+match.group(2)+'丁目'
    # A town with no chome can appear on a map only if the boundary dataset
    # actually contains exactly this key; no street number is guessed.
    if re.fullmatch(r'[^/\d\-－]{2,30}',rest) and not re.search(r'(?:番地?|号)$',rest):return ward+'|'+rest
    return None


def _v150_town_address(raw,region=None,require_chome=True,official_reverse_verified=False):
    """Validate a provider town without fabricating a chome or lot number.

    A published non-chome town (e.g. Chiyoda Ichibancho) is valid when its
    polygon exists OR the listing's own map marker independently resolves to
    that exact official GSI town. A missing asynchronous polygon cache is NOT
    evidence that the official town does not exist. Without either proof, a
    broad address such as Nishishinjuku must not masquerade as a map chome.
    """
    candidate=normal(raw).replace(' ','').replace('　','')
    key=town_chome_key(candidate)
    if not key:return None
    ward,town=key.split('|',1)
    if require_chome and '丁目' not in town:
        # Non-chome source names need independent proof: existing town polygon
        # or the exact official reverse result from THIS listing's map marker.
        if not official_reverse_verified and not _v150_boundary_key_exists(key):return None
    if region:
        code=str(region.get('code') or '')
        if code in TOKYO_WARDS and TOKYO_WARDS[code]!=ward:return None
        expected=normal(region.get('town') or '')
        if expected and not town_matches(expected,town):return None
    return '東京都'+ward+town


def _v150_boundary_key_exists(key):
    """Only query this app's optional read-only polygon index."""
    path=TOWN_BOUNDARY_CACHE_PATH
    if not os.path.isfile(path):return False
    try:
        with sqlite3.connect('file:'+quote(path,safe='/')+'?mode=ro',uri=True,timeout=2) as conn:
            return conn.execute('SELECT 1 FROM areas WHERE town=? LIMIT 1',(key,)).fetchone() is not None
    except (sqlite3.Error,OSError):return False


def _v150_point_in_ring(point,ring):
    lat,lng=point;inside=False
    for i in range(len(ring)):
        ax,ay=ring[i-1];bx,by=ring[i]
        if ((ay>lat)!=(by>lat)) and lng < (bx-ax)*(lat-ay)/(by-ay)+ax:
            inside=not inside
    return inside


def _v150_polygon_confirms(point,key):
    """True/False when loaded geometry can check the marker; None if absent."""
    path=TOWN_BOUNDARY_CACHE_PATH
    if not os.path.isfile(path):return None
    try:
        lat,lng=point
        with sqlite3.connect('file:'+quote(path,safe='/')+'?mode=ro',uri=True,timeout=2) as conn:
            rows=conn.execute("SELECT poly FROM areas WHERE town=? AND south<=? AND north>=? AND west<=? AND east>=?",(key,lat,lat,lng,lng)).fetchall()
        if not rows:return False
        for (raw,) in rows:
            for polygon in json.loads(raw):
                if polygon and _v150_point_in_ring(point,polygon[0]) and not any(
                    _v150_point_in_ring(point,hole) for hole in polygon[1:]):return True
        return False
    except (sqlite3.Error,OSError,ValueError,TypeError,IndexError,ZeroDivisionError):return None


def _v150_suumo_location(web,detail_soup,detail_url,bounds,munis,region,fields):
    """Provider-published town first, map marker -> town second; no JHJ 404."""
    source=fields.get('source_address') or fields.get('address') or ''
    # A published address from another ward/town is an explicit conflict.
    # Never allow a surrounding map to overrule the provider's labelled town.
    if source:
        published=address_key(source).replace(' ','').replace('　','').removeprefix('東京都')
        source_ward=next((w for w in sorted(TOKYO_WARDS.values(),key=len,reverse=True)
                          if published.startswith(w)),None)
        requested_ward=TOKYO_WARDS.get(str(region.get('code') or ''))
        if source_ward and requested_ward:
            expected=normal(region.get('town') or '')
            if source_ward!=requested_ward or (expected and not town_matches(expected,source)):
                trace(web,'v173_published_region_conflict',{
                    'url':detail_url,'published_address':source,'requested_ward':requested_ward,
                    'requested_town':expected,'decision':'not_saved'},'WARNING','location')
                return None
    town_address=_v150_town_address(source,region)
    if town_address:
        trace(web,'v150_published_town_verified',{'url':detail_url,'published_address':source,
            'town_address':town_address,'map_town_key':town_chome_key(town_address),
            'building_position_confirmed':False},stage='location')
        return {'inferred_address':town_address,'latitude':None,'longitude':None,
                'coordinate_precision':'town','location_method':'SUUMO掲載所在地から町丁目を直接確認',
                'map_address':town_address,'address_precision':'town_chome',
                'address_match':'SUUMOの所在地・検索町名・市区コード一致'}
    # Only if SUUMO has not supplied enough town/chome information, inspect
    # the building's *own* public property map. Never use the search centroid.
    failures=[]
    for target in suumo_kankyo_urls(detail_soup,detail_url)[:4]:
        web.check_cancel()
        if not web.permitted(target):
            failures.append('robots_not_permitted');continue
        try:reply=web.fetch(target)
        except AppError as exc:
            failures.append(str(exc)[:110]);continue
        soup=BeautifulSoup(reply.text,'html.parser')
        marker,meta=_suumo_room_marker_points(web,soup,target)
        marker_kind='room_marker'
        if marker is None:
            pins=map_image_candidates(web,soup,target)
            distinct={(round(p[0],7),round(p[1],7)) for p in pins if p[3]>=5}
            if len(distinct)==1:marker=next(iter(distinct));marker_kind='image_pin'
        if marker is None:
            # A Google map CENTER is not a reliable property position; unlike
            # a unique room marker, it must never classify a town by itself.
            failures.append('property_marker_not_unique');continue
        try:place=reverse(web,marker,munis,force=True)
        except AppError as exc:
            failures.append(str(exc)[:110]);continue
        if not place or str(place.get('code'))!=str(region.get('code')):
            failures.append('municipality_mismatch');continue
        if region.get('town') and not town_matches(region['town'],place.get('town','')):
            failures.append('town_mismatch');continue
        ward=TOKYO_WARDS.get(str(region.get('code'))) or ''
        # GSI is independent of SUUMO's town picker. Since the municipality
        # and requested town already matched above, an exact official non-chome
        # result is conclusive even when the polygon cache is not yet built.
        official_address='東京都'+ward+str(place.get('town') or '')
        address=_v150_town_address(official_address,region,official_reverse_verified=True)
        if not address:
            failures.append('reverse_town_below_map_granularity');continue
        verified_key=town_chome_key(address)
        geometry=_v150_polygon_confirms(marker,verified_key)
        if geometry is False:
            # A town that does not have a loaded polygon is NOT evidence that
            # the marker is outside it. Only reject when a polygon for this
            # precise town is actually available and contradicts the marker.
            if '丁目' in verified_key.split('|',1)[1] or _v150_boundary_key_exists(verified_key):
                failures.append('marker_outside_town_polygon');continue
            geometry=None
        trace(web,'v173_map_town_verified',{'url':detail_url,'map_url':target,
            'map_town_key':verified_key,'position_kind':marker_kind,
            'polygon_check':geometry,'polygon_available':_v150_boundary_key_exists(verified_key),
            'official_gsi_town':str(place.get('town') or ''),
            'building_street_number_claimed':False},stage='location')
        return {'inferred_address':address,'latitude':float(marker[0]),
                'longitude':float(marker[1]),'coordinate_precision':'listing_map',
                'location_method':'SUUMO物件マーカー→GSI逆ジオコーダ→町丁目',
                'map_address':address,'address_precision':'town_chome',
                'position_source_url':target,'address_match':'町丁目・市区一致'}
    trace(web,'v150_town_unresolved',{'url':detail_url,
        'source_address':source,'map_fallback_errors':failures[:8],
        'decision':'not_saved_without_map_town'},'WARNING','location')
    return None


def town_layout_group(row):
    if listing_dwelling_type(row)=='house':return 'house'
    layout=parsed_layout(row.get('layout'))
    if layout in DISPLAY_LAYOUT_GROUPS['group0']:return 'group0'
    if layout in DISPLAY_LAYOUT_GROUPS['group1']:return 'group1'
    if layout in DISPLAY_LAYOUT_GROUPS['group2']:return 'group2'
    return None


def town_rent_gradient(price):
    """Continuously interpolated rent colors from 3万円 to 45万円."""
    value=float(price)
    if value<=TOWN_GRADIENT[0][0]:rgb=TOWN_GRADIENT[0][1]
    elif value>=TOWN_GRADIENT[-1][0]:rgb=TOWN_GRADIENT[-1][1]
    else:
        rgb=TOWN_GRADIENT[-1][1]
        for (low, a),(high, b) in zip(TOWN_GRADIENT,TOWN_GRADIENT[1:]):
            if low<=value<=high:
                fraction=(value-low)/(high-low)
                rgb=tuple(round(x+(y-x)*fraction) for x,y in zip(a,b))
                break
    return '#'+''.join(f'{channel:02x}' for channel in rgb)


def town_aggregate_record(raw_rows):
    """Pure, deterministic summary. Rows are (storage_id, compact listing)."""
    latest={}
    examined=0
    for storage_id,listing in raw_rows:
        examined+=1
        compact=compact_saved_listing(listing)
        if compact is None:continue
        group=town_layout_group(compact);town=town_chome_key(compact['address'])
        if not group or not town:continue
        identity=compact.get('property_id') or 'legacy:'+str(storage_id)
        stamp=compact.get('fetched_at') or ''
        if identity not in latest or latest[identity][0]<=stamp:
            latest[identity]=(stamp,group,town,compact['rent'])
    totals={group:{} for group in TOWN_GROUPS}
    for _,group,town,rent in latest.values():
        node=totals[group].setdefault(town,[0,0]);node[0]+=int(rent);node[1]+=1
    return {'schema':1,'groups':totals,'listing_count':len(latest),
            'examined':examined,'unmatched':examined-len(latest),'updated_at':utc_now()}


def town_aggregate_streaming(raw_rows):
    """Bounded-RAM town aggregation; duplicate listings are kept only on disk.

    raw_rows is an iterator of (storage_id, listing JSON). SQLite's index
    replaces the previous unbounded latest={...} Python dictionary. Older
    timestamps cannot overwrite newer property_id observations.
    """
    with tempfile.TemporaryDirectory(prefix='sumai-town-agg-') as folder:
        db=sqlite3.connect(os.path.join(folder,'latest.sqlite'),timeout=15)
        try:
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('PRAGMA cache_size=-1024')
            db.execute('CREATE TABLE latest (identity TEXT PRIMARY KEY, stamp TEXT NOT NULL, grp TEXT NOT NULL, town TEXT NOT NULL, rent INTEGER NOT NULL) WITHOUT ROWID')
            examined=0
            batch=[]
            def flush():
                if not batch:return
                db.executemany("""INSERT INTO latest(identity,stamp,grp,town,rent)
                    VALUES(?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET
                    stamp=excluded.stamp,grp=excluded.grp,town=excluded.town,rent=excluded.rent
                    WHERE excluded.stamp >= latest.stamp""",batch)
                batch.clear()
            for storage_id,listing in raw_rows:
                examined+=1
                compact=compact_saved_listing(listing)
                if compact is None:continue
                group=town_layout_group(compact);town=town_chome_key(compact['address'])
                if not group or not town:continue
                identity=compact.get('property_id') or 'legacy:'+str(storage_id)
                batch.append((identity,compact.get('fetched_at') or '',group,town,int(compact['rent'])))
                if len(batch)>=100:flush()
            flush()
            totals={group:{} for group in TOWN_GROUPS}
            for grp,town,total,count in db.execute('SELECT grp,town,SUM(rent),COUNT(*) FROM latest GROUP BY grp,town'):
                totals[grp][town]=[int(total),int(count)]
            latest_count=int(db.execute('SELECT COUNT(*) FROM latest').fetchone()[0])
            return {'schema':1,'groups':totals,'listing_count':latest_count,
                    'examined':examined,'unmatched':examined-latest_count,'updated_at':utc_now()}
        finally:
            db.close()
            # No OS-wide or page-cache manipulation. The app's private
            # TemporaryDirectory cleanup is performed by its context manager.


# Application-local notifications, not cgroup/OS operations. They contain
# no listing data and require no external polling or additional Supabase writes.
# Multiple browser sessions of this Streamlit process share one worker per DB.
_TOWN_SAVE_CHANGE_LOCK = threading.RLock()
_TOWN_SAVE_CHANGES = {}
TOWN_AUTO_REFRESH_DEBOUNCE = 12.0  # seconds after the first successful save
TOWN_AUTO_REFRESH_MIN_GAP = 180.0  # no full-table scan per saved listing/page


def _town_change_key(db):
    return hashlib.sha256((db.url + '|' + db.namespace).encode()).hexdigest()


def notify_town_summary_saved(db):
    """Mark the map as stale after a verified save, without polling or scanning.

    Recomputing the map is EXCLUSIVELY user-initiated. No additional Supabase
    request is generated per save batch. Existing rental listings are intact.
    """
    key=_town_change_key(db)
    with _TOWN_SAVE_CHANGE_LOCK:
        item=_TOWN_SAVE_CHANGES.setdefault(key, {'sequence':0,'worker':None})
        item['sequence']+=1
        seq=item['sequence']
        worker=item['worker']
    if worker is not None:
        worker.on_listing_saved(seq)
    return None


def notify_town_collection_finished(db):
    """Finishing property collection no longer starts a map recomputation."""
    return None


def attach_town_summary_worker(worker):
    key=_town_change_key(worker.db)
    with _TOWN_SAVE_CHANGE_LOCK:
        entry=_TOWN_SAVE_CHANGES.setdefault(key, {'sequence':0,'worker':None})
        entry['worker']=worker
        sequence=entry['sequence']
    if sequence:
        worker.on_listing_saved(sequence)


class TownSummaryWorker:
    """One namespace snapshot, recomputed only after a manual button click.

    The worker reads the persisted summary on start-up, without scanning
    listings. Manual aggregation runs on a background thread.
    """
    def __init__(self,db):
        self.db=db;self.build=BUILD;self.lock=threading.RLock();self.running=False;self.progress=0;self.total_pages=None;self.total_records=None;self.scanned_records=0;self.page_size=160
        self.ready=False;self.summary=None;self.message='';self.error='';self.thread=None
        self.initialized=False
        self.revision=0
        self.saved_sequence=0
        self.scan_sequence=0
        self.pending=False
        self._urgent_after_scan=False
        self._refresh_timer=None
        self._last_scan_started=0.0
        self._retry_count=0
        self._last_saved_at=None
        # One scan (only when needed) for all browser sessions and new writes.
        self._startup_scan_requested=False
        self._last_marker_probe=0.0
        self._marker_probe_running=False
        self._scan_source_marker=''
    def read_source_marker(self):
        """Read an app-owned one-row dirty marker, never the entire listing set."""
        rows=self.db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.db.namespace,
            'id':'eq.'+TOWN_DIRTY_MARKER_ID,'status':'eq.town_rent_dirty',
            'select':'id,summary','limit':1})
        if not isinstance(rows,list):raise AppError('地図更新通知の読込形式が不正です。')
        payload=(rows[0].get('summary') or {}) if rows else {}
        return str(payload.get('token') or '') if isinstance(payload,dict) else ''
    def poll_source_marker(self,force=False):
        # Kept for compatibility; never schedule background polling.
        return False
    def refresh_on_startup(self):
        # Read existing persisted summary only; do not scan rental listings.
        return False
    def _schedule_refresh_locked(self,urgent=False):
        # No timers or automatic retries. A manual click is always required.
        return False
    def _run_pending_refresh(self):
        return None
    def on_listing_saved(self,sequence):
        with self.lock:
            if sequence<=self.saved_sequence:return
            self.saved_sequence=sequence
            self.pending=True
            self._last_saved_at=utc_now()
    def flush_saved_changes(self):
        # A finished search cannot trigger a map refresh.
        return None
    def initialize(self):
        with self.lock:
            if self.initialized:return
            self.initialized=True
        try:
            records=self.db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.db.namespace,
                'id':'eq.'+TOWN_SUMMARY_ID,'select':'summary','limit':1})
            summary=(records[0].get('summary') if records else None) if isinstance(records,list) else None
            if isinstance(summary,dict) and summary.get('schema')==1 and isinstance(summary.get('groups'),dict):
                with self.lock:self.summary=summary;self.ready=True;self.revision+=1
        except Exception as exc:
            with self.lock:self.error='町丁目集計の保存データを確認できません: '+str(exc)
    def start(self):
        with self.lock:
            if self.running:return False
            if self._refresh_timer is not None:
                self._refresh_timer.cancel();self._refresh_timer=None
            self.running=True;self.progress=0;self.total_pages=None;self.total_records=None;self.scanned_records=0;self.page_size=160;self.error='';self.message='町丁目の家賃を裏で集計中'
            self.scan_sequence=self.saved_sequence
            self.pending=False
            self._urgent_after_scan=False
            self._last_scan_started=time.monotonic()
            self.thread=threading.Thread(target=self.run,daemon=True,name='town-rent-aggregate')
            self.thread.start();return True
    def count_source_records(self):
        # Query one row only; Content-Range gives the exact count, not a scan
        # of all listing JSON. If counting exceeds DB limits, show an unknown
        # total rather than an invented figure.
        params={'namespace':'eq.'+self.db.namespace,'select':'id',
                'status':'eq.rental_listing',
                'and':'(id.gte.listing.,id.lt.listing/)','limit':1}
        headers=dict(self.db.headers);headers['Prefer']='count=exact'
        try:
            response=self.db.session.get(self.db.url+'/rest/v1/'+SEARCH_TABLE,
                    params=params,headers=headers,timeout=(4,12),allow_redirects=False)
            if response.status_code not in (200,206):return None
            text=response.headers.get('Content-Range','')
            match=re.search(r'/(\d+)\s*$',text)
            return int(match.group(1)) if match else None
        except (requests.RequestException,ValueError,OverflowError):return None

    def run(self):
        try:
            last_id='';page=0;page_size=160;started=utc_now()
            # Bind this scan to the marker existing BEFORE pages are read.
            # A marker created during a scan must trigger a later catch-up.
            source_marker=self.read_source_marker()
            total=self.count_source_records()
            with self.lock:
                self.total_records=total
                self.total_pages=math.ceil(total/page_size) if total is not None else None
            # Each Supabase page is released immediately after normalizing its
            # listings into a bounded SQLite spill file. No full Python row list.
            def source_rows():
                nonlocal last_id,page,page_size
                while True:
                    pressure=cloud_memory_pressure()
                    if memory_needs_guard(pressure):
                        raise AppError('サーバーメモリ保護により町丁目集計を中断しました。')
                    params={'namespace':'eq.'+self.db.namespace,'select':'id,summary',
                        'status':'eq.rental_listing','and':'(id.gte.listing.,id.lt.listing/)',
                        'order':'id.asc','limit':page_size}
                    if last_id:params['id']='gt.'+last_id
                    while True:
                        try:
                            got=self.db.call('GET',SEARCH_TABLE,params)
                            break
                        except AppError as exc:
                            diag=getattr(exc,'diagnostic',{}) or {}
                            if (diag.get('code')=='57014' or diag.get('status') in (500,502,503,504)) and page_size>40:
                                page_size=max(40,page_size//2)
                                params['limit']=page_size
                                with self.lock:
                                    self.page_size=page_size
                                    if self.total_records is not None:
                                        remaining=max(0,self.total_records-self.scanned_records)
                                        self.total_pages=self.progress+math.ceil(remaining/page_size)
                                continue
                            raise
                    if not isinstance(got,list):raise AppError('募集データの応答形式が不正です。')
                    if not got:break
                    next_id=str(got[-1].get('id') or '')
                    if not next_id or next_id<=last_id:raise AppError('町丁目集計のページ位置が進みません。')
                    for item in got:
                        body=(item.get('summary') or {}).get('listing')
                        if isinstance(body,dict):yield item.get('id'),body
                    count=len(got)
                    last_id=next_id;page+=1
                    with self.lock:
                        self.progress=page;self.scanned_records+=count;self.page_size=page_size
                        if self.total_records is not None:
                            self.total_pages=page+math.ceil(max(0,self.total_records-self.scanned_records)/page_size)
                        self.message=f'集計中 {page}ページ'
                    # Do not keep response pages alive after each yield cycle.
                    got=None
                    if count<page_size:break
            summary=town_aggregate_streaming(source_rows())
            summary['started_at']=started
            summary['generated_by']=BUILD
            summary['source_marker']=source_marker
            # Persist the new snapshot only after the FULL scan succeeds.
            # Never overwrite a usable snapshot on partial/failed collection.
            self.db.save_search({'id':TOWN_SUMMARY_ID,'status':'town_rent_summary',
                'started_at':summary['updated_at'],'finished_at':summary['updated_at'],
                'conditions':{'schema':1,'source':'rental_listing'},'summary':summary})
            with self.lock:
                self.summary=summary;self.ready=True;self.revision+=1
                self.message='集計完了';self.error='';self._retry_count=0
                # A listing saved DURING the scan might have been missed;
                # it is necessarily carried forward to another refresh.
                if self.saved_sequence>self.scan_sequence:self.pending=True
            # Completed a manual scan. Never queue an automatic catch-up scan.
            _v126_work_finished('town-summary')
        except Exception as exc:
            with self.lock:
                self.pending=True
                self._retry_count+=1
                self.error=str(exc);self.message='集計を更新できませんでした。以前の集計は保持します。'
        finally:
            with self.lock:
                self.running=False
                # Save activity during a scan is flagged as pending for the
                # NEXT manual click; no automatic catch-up/retry.
    def snapshot(self):
        with self.lock:return {'summary':self.summary,'ready':self.ready,'running':self.running,
                               'progress':self.progress,'total_pages':self.total_pages,
                               'total_records':self.total_records,'scanned_records':self.scanned_records,
                               'message':self.message,'error':self.error,'revision':self.revision,
                               'pending':self.pending,'last_saved_at':self._last_saved_at}


@st.cache_resource
def town_summary_registry_v183():
    # A new cache identity is essential: Streamlit preserves st.cache_resource
    # objects over source hot-reloads. The unchanged v131 key reused an old
    # already-initialized worker, permanently skipping new startup refreshes.
    return {},threading.RLock()


def current_town_summary_worker():
    jobs,lock=town_summary_registry_v183();db=Database();key=manual_registry_key(db)+'|town-v183'
    with lock:
        job=jobs.get(key)
        if job is None or (getattr(job,'build',None)!=BUILD and not job.snapshot()['running']):
            # A hot-deployed version must rebuild summaries with its new layout groups.
            # Keep an older, actively running worker until its scan has finished.
            job=TownSummaryWorker(db);jobs[key]=job
    job.initialize()
    attach_town_summary_worker(job)
    return job


def _town_ring_simplify(ring,step=0.00002):
    """Use the same coordinate grid for shared borders, without strided skips.

    v155 sampled long rings independently to ~125 vertices and snapped to
    ~15 m.  Neighbouring town borders consequently diverged into apparent
    empty wedges even when both towns had saved rent observations.  Never
    discard arbitrary intermediate vertices in an administrative boundary.
    """
    out=[]
    previous=None
    for point in ring:
        if not isinstance(point,(list,tuple)) or len(point)<2:continue
        lng,lat=float(point[0]),float(point[1])
        if not(math.isfinite(lng) and math.isfinite(lat)):continue
        snapped=(round(lng/step)*step,round(lat/step)*step)
        if snapped!=previous:
            out.append([round(snapped[0],6),round(snapped[1],6)])
            previous=snapped
    if len(out)<3:return []
    if out[0]!=out[-1]:out.append(out[0])
    return out if len(out)>=4 else []

def town_boundary_geometry(geom):
    if not isinstance(geom,dict):return None
    kind=geom.get('type');coords=geom.get('coordinates') or []
    def polygon(poly):
        rings=[_town_ring_simplify(ring) for ring in poly]
        return [r for r in rings if r]
    if kind=='Polygon':
        rings=polygon(coords)
        return {'type':'Polygon','coordinates':rings} if rings else None
    if kind=='MultiPolygon':
        polys=[polygon(p) for p in coords]
        polys=[p for p in polys if p]
        return {'type':'MultiPolygon','coordinates':polys} if polys else None
    return None


def iter_town_geojson_file(path):
    """Parse a feature collection incrementally; never decode the 36MB JSON as one string."""
    decoder=json.JSONDecoder()
    with open(path,'r',encoding='utf-8-sig') as stream:
        buffer=stream.read(131072)
        while True:
            m=re.search(r'"features"\s*:\s*\[',buffer)
            if m:buffer=buffer[m.end():];break
            if len(buffer)>1024*1024:raise ValueError('GeoJSON features配列が見つかりません。')
            part=stream.read(131072)
            if not part:raise ValueError('GeoJSON features配列が見つかりません。')
            buffer+=part
        while True:
            buffer=buffer.lstrip(' \r\n\t,')
            if buffer.startswith(']'):break
            if not buffer:
                part=stream.read(131072)
                if not part:raise ValueError('GeoJSONが途中で終了しました。')
                buffer=part;continue
            try:item,end=decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if len(buffer)>8*1024*1024:raise ValueError('1区域の境界データが大きすぎます。')
                part=stream.read(131072)
                if not part:raise ValueError('GeoJSONの境界データが不完全です。')
                buffer+=part;continue
            buffer=buffer[end:]
            if isinstance(item,dict):yield item


class TownBoundaryStore:
    """Read-only indexed polygons for both rent-covered and no-data towns."""
    def __init__(self,path):
        self.path=path
        self._town_index=None
        self._town_index_lock=threading.Lock()

    def coverage_keys(self):
        """The database's exact available chome keys, cached once per process."""
        with self._town_index_lock:
            if self._town_index is None:
                with sqlite3.connect('file:'+quote(self.path,safe='/')+'?mode=ro',uri=True,timeout=10) as conn:
                    self._town_index=frozenset(key for (key,) in conn.execute('SELECT DISTINCT town FROM areas'))
            return self._town_index

    def iter_ward_visible(self,bounds):
        """Gray underlay for every Tokyo ward, including areas missing from 2015 polygons."""
        conn=sqlite3.connect('file:'+quote(self.path,safe='/')+'?mode=ro',uri=True,timeout=10)
        try:
            if bounds and len(bounds)==4:
                south,west,north,east=map(float,bounds)
                rows=conn.execute('''SELECT ward,poly FROM ward_areas WHERE south<=? AND north>=? AND west<=? AND east>=?''',
                                  (north,south,east,west))
            else:
                rows=conn.execute('SELECT ward,poly FROM ward_areas')
            for ward,raw in rows:
                if ward not in TOKYO_WARDS.values():continue
                try:yield ward,json.loads(raw)
                except (ValueError,TypeError):continue
        except sqlite3.OperationalError:
            # The town database may be usable even if the additional ward
            # boundary source was unavailable during initial download.
            return
        finally:conn.close()

    def iter_visible(self,bounds,interested=None,include_empty=False):
        if not interested and not include_empty:return
        conn=sqlite3.connect('file:'+quote(self.path,safe='/')+'?mode=ro',uri=True,timeout=10)
        try:
            conn.execute('PRAGMA cache_size=-512')
            if bounds and len(bounds)==4:
                south,west,north,east=map(float,bounds)
                cursor=conn.execute("""SELECT town,poly FROM areas WHERE
                    south<=? AND north>=? AND west<=? AND east>=?""",(north,south,east,west))
            else:
                cursor=conn.execute('SELECT town,poly FROM areas')
            wards=frozenset(TOKYO_WARDS.values())
            for town,raw in cursor:
                if town.partition('|')[0] not in wards:continue
                if not include_empty and town not in interested:continue
                try:yield town,json.loads(raw)
                except (ValueError,TypeError):continue
        finally:conn.close()


class TownBoundaryWorker:
    def __init__(self):
        self.lock=threading.Lock();self.started=False;self.running=False
        self.features=None;self.ready=False;self.error=''
        self.cache_path=TOWN_BOUNDARY_CACHE_PATH
        self.background_error=''
    def start(self):
        with self.lock:
            if self.started:return
            self.started=True;self.running=True
        threading.Thread(target=self.run,daemon=True,name='town-boundaries').start()
    def run(self):
        try:
            # Persistent within the running container; no full-geometry dict.
            if os.path.isfile(self.cache_path) and os.path.getsize(self.cache_path)>8192:
                try:
                    with sqlite3.connect(self.cache_path) as conn:
                        verified=conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0]
                        try:
                            ward_verified=conn.execute('SELECT COUNT(DISTINCT ward) FROM ward_areas').fetchone()[0]
                        except sqlite3.Error:
                            ward_verified=0
                    if verified>0 and ward_verified==len(TOKYO_WARDS):
                        # Only a metadata check was performed; do not evict
                        # the polygon database needed for viewport queries.
                        with self.lock:
                            self.features=TownBoundaryStore(self.cache_path)
                            self.ready=True;self.error=''
                        return
                except (sqlite3.DatabaseError,OSError):
                    # A half-written file must be rebuilt, not reused.
                    pass
            with tempfile.TemporaryDirectory(prefix='sumai-town-boundary-') as folder:
                source=os.path.join(folder,'source.geojson')
                last_exception=None
                downloaded=False
                for source_url in (TOWN_GEOJSON_URL,TOWN_GEOJSON_BACKUP):
                    try:
                        with requests.get(source_url,timeout=(6,25),stream=True,
                                          headers={'User-Agent':'SumaiCompassTownMap/1.0'}) as response:
                            response.raise_for_status()
                            size=0
                            with open(source,'wb') as handle:
                                for chunk in response.iter_content(65536):
                                    if not chunk:continue
                                    size+=len(chunk)
                                    if size>TOWN_GEOMETRY_LIMIT:raise ValueError('境界ファイルが上限を超えました。')
                                    handle.write(chunk)
                        downloaded=True;break
                    except (requests.RequestException,OSError,UnicodeError,ValueError) as exc:
                        last_exception=exc
                if not downloaded:raise ValueError('町丁目境界を取得できません：'+str(last_exception))
                target=os.path.join(folder,'areas.sqlite')
                conn=sqlite3.connect(target,timeout=20)
                try:
                    conn.execute('PRAGMA journal_mode=OFF')
                    conn.execute('PRAGMA synchronous=OFF')
                    conn.execute('PRAGMA temp_store=FILE')
                    conn.execute('PRAGMA cache_size=-1024')
                    conn.execute("""CREATE TABLE areas(town TEXT NOT NULL, south REAL,west REAL,north REAL,east REAL,poly TEXT NOT NULL)""")
                    batch=[]
                    for item in iter_town_geojson_file(source):
                        props=item.get('properties') or {}
                        key=town_chome_key(str(props.get('fullname') or '').replace('/',''))
                        if key is None:continue
                        geometry=town_boundary_geometry(item.get('geometry'))
                        if geometry is None:continue
                        polys=geometry['coordinates'] if geometry['type']=='MultiPolygon' else [geometry['coordinates']]
                        all_points=[p for poly in polys for ring in poly for p in ring]
                        if not all_points:continue
                        batch.append((key,min(p[1] for p in all_points),min(p[0] for p in all_points),
                                      max(p[1] for p in all_points),max(p[0] for p in all_points),
                                      json.dumps(polys,ensure_ascii=False,separators=(',',':'))))
                        if len(batch)>=70:
                            conn.executemany('INSERT INTO areas VALUES(?,?,?,?,?,?)',batch)
                            batch.clear()
                    if batch:conn.executemany('INSERT INTO areas VALUES(?,?,?,?,?,?)',batch)
                    # Source cache stays under the application's own temp directory.
                    # Do not advise global/shared kernel file-cache eviction.
                    count=conn.execute('SELECT COUNT(*) FROM areas').fetchone()[0]
                    if not count:raise ValueError('東京23区の町丁目境界が見つかりません。')
                    conn.execute('CREATE INDEX areas_bbox ON areas(south,north,west,east)')
                    conn.execute('''CREATE TABLE ward_areas(ward TEXT PRIMARY KEY,
                        south REAL, west REAL, north REAL, east REAL, poly TEXT NOT NULL)''')
                    # This source has separate surveyed municipality boundaries
                    # for all 23 wards (N03_007 is the official municipality code).
                    # It is not substituted for town rent observations.
                    try:
                        with requests.get(TOWN_WARD_GEOJSON_URL,timeout=(6,25),stream=True,
                                          headers={'User-Agent':'SumaiCompassTownMap/1.0'}) as response:
                            response.raise_for_status()
                            parts=[];total_bytes=0
                            for chunk in response.iter_content(65536):
                                if not chunk:continue
                                total_bytes+=len(chunk)
                                if total_bytes>TOWN_WARD_GEOJSON_LIMIT:
                                    raise ValueError('23区境界データがサイズ上限を超えました。')
                                parts.append(chunk)
                        source_wards=json.loads(b''.join(parts).decode('utf-8-sig'))
                        if source_wards.get('type')!='FeatureCollection' or not isinstance(source_wards.get('features'),list):
                            raise ValueError('23区境界の応答形式が不正です。')
                        ward_polygons={}
                        for item in source_wards['features']:
                            props=item.get('properties') or {}
                            code=str(props.get('N03_007') or '')
                            if code not in TOKYO_WARDS or normal(props.get('N03_004'))!=TOKYO_WARDS[code]:continue
                            geometry=town_boundary_geometry(item.get('geometry'))
                            if geometry is None:continue
                            polygons=geometry['coordinates'] if geometry['type']=='MultiPolygon' else [geometry['coordinates']]
                            if polygons:ward_polygons.setdefault(TOKYO_WARDS[code],[]).extend(polygons)
                        if len(ward_polygons)!=len(TOKYO_WARDS):
                            raise ValueError(f'23区境界が不足: {len(ward_polygons)}/{len(TOKYO_WARDS)}区')
                        for ward,polys in ward_polygons.items():
                            coords=[pt for poly in polys for ring in poly for pt in ring]
                            conn.execute('INSERT INTO ward_areas VALUES(?,?,?,?,?,?)',
                                         (ward,min(p[1] for p in coords),min(p[0] for p in coords),
                                          max(p[1] for p in coords),max(p[0] for p in coords),
                                          json.dumps(polys,ensure_ascii=False,separators=(',',':'))))
                        conn.execute('CREATE INDEX ward_areas_bbox ON ward_areas(south,north,west,east)')
                    except (requests.RequestException,ValueError,TypeError,KeyError,OSError,sqlite3.Error) as exc:
                        # Optional 23-ward gray fill must never take the town
                        # map offline if this independent source is unavailable.
                        conn.execute('DELETE FROM ward_areas')
                        self.background_error='23区の灰色補完を取得できません: '+str(exc)[:240]
                    conn.commit()
                finally:conn.close()
                os.replace(target,self.cache_path)
            _v126_work_finished('town-boundary')
            with self.lock:
                self.features=TownBoundaryStore(self.cache_path)
                self.ready=True;self.error=self.background_error
        except Exception as exc:
            with self.lock:self.error=str(exc)
        finally:
            with self.lock:self.running=False
    def snapshot(self):
        with self.lock:return self.features,self.ready,self.running,self.error


@st.cache_resource
def town_boundary_registry(v171_geometry_revision="v171"):
    return TownBoundaryWorker()


# v167: one-click provider search from the exact tapped choropleth polygon.
# No third-party API, scraping, background task or Supabase mutation is used
# by this external link.  The website owns the final search semantics.
_V167_WARD_TO_CODE = {name:code for code,name in TOKYO_WARDS.items()}
_V167_SUUMO_CONDITION_LABELS = {
    'group0':'ワンルーム・1K・1DK｜マンション・築15年以内',
    'group1':'1LDK・2K・2DK｜マンション・築15年以内',
    'group2':'2LDK・3K・3DK｜マンション・築15年以内',
    'house':'一戸建て・その他｜50㎡以上（築40年以内は掲載先で確認）',
}
# Verified against SUUMO public town-selection links on 2026-10-23.
# Embed 23 wards so saved rent polygons do not depend on a separate warmup.
_V167_VERIFIED_OZ = {'世田谷区|三宿': '13112058', '世田谷区|三軒茶屋': '13112031', '世田谷区|上北沢': '13112013', '世田谷区|上用賀': '13112016', '世田谷区|上祖師谷': '13112014', '世田谷区|上野毛': '13112015', '世田谷区|上馬': '13112012', '世田谷区|下馬': '13112032', '世田谷区|世田谷': '13112036', '世田谷区|中町': '13112048', '世田谷区|代沢': '13112043', '世田谷区|代田': '13112044', '世田谷区|八幡山': '13112051', '世田谷区|北沢': '13112018', '世田谷区|北烏山': '13112017', '世田谷区|千歳台': '13112045', '世田谷区|南烏山': '13112059', '世田谷区|喜多見': '13112019', '世田谷区|大原': '13112006', '世田谷区|大蔵': '13112005', '世田谷区|太子堂': '13112038', '世田谷区|奥沢': '13112008', '世田谷区|宇奈根': '13112003', '世田谷区|宮坂': '13112060', '世田谷区|尾山台': '13112009', '世田谷区|岡本': '13112007', '世田谷区|弦巻': '13112046', '世田谷区|成城': '13112034', '世田谷区|新町': '13112033', '世田谷区|東玉川': '13112054', '世田谷区|松原': '13112057', '世田谷区|桜': '13112027', '世田谷区|桜上水': '13112030', '世田谷区|桜丘': '13112028', '世田谷区|桜新町': '13112029', '世田谷区|梅丘': '13112004', '世田谷区|池尻': '13112002', '世田谷区|深沢': '13112055', '世田谷区|瀬田': '13112035', '世田谷区|玉堤': '13112042', '世田谷区|玉川': '13112039', '世田谷区|玉川台': '13112040', '世田谷区|玉川田園調布': '13112041', '世田谷区|用賀': '13112061', '世田谷区|砧': '13112020', '世田谷区|祖師谷': '13112037', '世田谷区|等々力': '13112047', '世田谷区|粕谷': '13112010', '世田谷区|経堂': '13112023', '世田谷区|給田': '13112022', '世田谷区|羽根木': '13112053', '世田谷区|船橋': '13112056', '世田谷区|若林': '13112062', '世田谷区|豪徳寺': '13112026', '世田谷区|赤堤': '13112001', '世田谷区|野毛': '13112049', '世田谷区|野沢': '13112050', '世田谷区|鎌田': '13112011', '世田谷区|駒沢': '13112024', '中央区|京橋': '13102004', '中央区|佃': '13102010', '中央区|入船': '13102002', '中央区|八丁堀': '13102032', '中央区|八重洲': '13102037', '中央区|勝どき': '13102003', '中央区|新富': '13102007', '中央区|新川': '13102006', '中央区|日本橋': '13102012', '中央区|日本橋中洲': '13102021', '中央区|日本橋久松町': '13102026', '中央区|日本橋人形町': '13102022', '中央区|日本橋兜町': '13102015', '中央区|日本橋堀留町': '13102027', '中央区|日本橋大伝馬町': '13102013', '中央区|日本橋室町': '13102030', '中央区|日本橋富沢町': '13102020', '中央区|日本橋小伝馬町': '13102018', '中央区|日本橋小網町': '13102017', '中央区|日本橋小舟町': '13102019', '中央区|日本橋本町': '13102029', '中央区|日本橋本石町': '13102028', '中央区|日本橋横山町': '13102031', '中央区|日本橋浜町': '13102024', '中央区|日本橋箱崎町': '13102023', '中央区|日本橋茅場町': '13102016', '中央区|日本橋蛎殻町': '13102014', '中央区|日本橋馬喰町': '13102025', '中央区|明石町': '13102001', '中央区|晴海': '13102034', '中央区|月島': '13102008', '中央区|東日本橋': '13102035', '中央区|湊': '13102036', '中央区|築地': '13102009', '中央区|銀座': '13102005', '中野区|上高田': '13114005', '中野区|上鷺宮': '13114004', '中野区|中央': '13114008', '中野区|中野': '13114009', '中野区|丸山': '13114015', '中野区|南台': '13114016', '中野区|大和町': '13114017', '中野区|弥生町': '13114018', '中野区|新井': '13114001', '中野区|本町': '13114013', '中野区|東中野': '13114012', '中野区|松が丘': '13114014', '中野区|江原町': '13114003', '中野区|江古田': '13114002', '中野区|沼袋': '13114010', '中野区|白鷺': '13114007', '中野区|若宮': '13114019', '中野区|野方': '13114011', '中野区|鷺宮': '13114006', '北区|上中里': '13117011', '北区|上十条': '13117010', '北区|中十条': '13117025', '北区|中里': '13117024', '北区|十条仲原': '13117019', '北区|堀船': '13117030', '北区|岩淵町': '13117006', '北区|岸町': '13117013', '北区|志茂': '13117016', '北区|昭和町': '13117017', '北区|東十条': '13117028', '北区|東田端': '13117029', '北区|栄町': '13117015', '北区|桐ケ丘': '13117014', '北区|浮間': '13117007', '北区|滝野川': '13117020', '北区|王子': '13117008', '北区|王子本町': '13117009', '北区|田端': '13117021', '北区|田端新町': '13117022', '北区|神谷': '13117012', '北区|西が丘': '13117026', '北区|西ケ原': '13117027', '北区|豊島': '13117023', '北区|赤羽': '13117001', '北区|赤羽北': '13117002', '北区|赤羽南': '13117005', '北区|赤羽台': '13117003', '北区|赤羽西': '13117004', '千代田区|一ツ橋': '13101051', '千代田区|一番町': '13101002', '千代田区|三番町': '13101043', '千代田区|九段北': '13101037', '千代田区|九段南': '13101038', '千代田区|二番町': '13101048', '千代田区|五番町': '13101041', '千代田区|六番町': '13101059', '千代田区|内神田': '13101004', '千代田区|四番町': '13101058', '千代田区|外神田': '13101044', '千代田区|富士見': '13101054', '千代田区|岩本町': '13101003', '千代田区|平河町': '13101053', '千代田区|東神田': '13101050', '千代田区|永田町': '13101046', '千代田区|神田三崎町': '13101056', '千代田区|神田佐久間河岸': '13101017', '千代田区|神田佐久間町': '13101018', '千代田区|神田北乗物町': '13101015', '千代田区|神田司町': '13101023', '千代田区|神田和泉町': '13101011', '千代田区|神田多町': '13101022', '千代田区|神田富山町': '13101024', '千代田区|神田小川町': '13101013', '千代田区|神田岩本町': '13101012', '千代田区|神田東松下町': '13101030', '千代田区|神田淡路町': '13101010', '千代田区|神田猿楽町': '13101042', '千代田区|神田神保町': '13101019', '千代田区|神田紺屋町': '13101016', '千代田区|神田練塀町': '13101027', '千代田区|神田美倉町': '13101033', '千代田区|神田西福田町': '13101026', '千代田区|神田錦町': '13101025', '千代田区|神田鍛冶町': '13101014', '千代田区|神田須田町': '13101020', '千代田区|神田駿河台': '13101021', '千代田区|紀尾井町': '13101035', '千代田区|西神田': '13101047', '千代田区|鍛冶町': '13101007', '千代田区|隼町': '13101049', '千代田区|飯田橋': '13101001', '千代田区|麹町': '13101040', '台東区|三ノ輪': '13106030', '台東区|三筋': '13106029', '台東区|上野': '13106007', '台東区|上野桜木': '13106009', '台東区|下谷': '13106017', '台東区|今戸': '13106005', '台東区|元浅草': '13106031', '台東区|入谷': '13106006', '台東区|北上野': '13106011', '台東区|千束': '13106018', '台東区|台東': '13106019', '台東区|寿': '13106015', '台東区|小島': '13106014', '台東区|日本堤': '13106022', '台東区|東上野': '13106027', '台東区|東浅草': '13106026', '台東区|松が谷': '13106028', '台東区|柳橋': '13106033', '台東区|根岸': '13106023', '台東区|橋場': '13106024', '台東区|池之端': '13106004', '台東区|浅草': '13106002', '台東区|浅草橋': '13106003', '台東区|清川': '13106012', '台東区|竜泉': '13106034', '台東区|花川戸': '13106025', '台東区|蔵前': '13106013', '台東区|西浅草': '13106021', '台東区|谷中': '13106032', '台東区|雷門': '13106010', '台東区|駒形': '13106016', '台東区|鳥越': '13106020', '品川区|上大崎': '13109005', '品川区|中延': '13109010', '品川区|二葉': '13109022', '品川区|八潮': '13109025', '品川区|勝島': '13109004', '品川区|北品川': '13109006', '品川区|南品川': '13109024', '品川区|南大井': '13109023', '品川区|大井': '13109002', '品川区|大崎': '13109003', '品川区|小山': '13109007', '品川区|小山台': '13109008', '品川区|平塚': '13109020', '品川区|広町': '13109021', '品川区|戸越': '13109009', '品川区|旗の台': '13109015', '品川区|東中延': '13109019', '品川区|東五反田': '13109017', '品川区|東品川': '13109018', '品川区|東大井': '13109016', '品川区|荏原': '13109001', '品川区|西中延': '13109014', '品川区|西五反田': '13109012', '品川区|西品川': '13109013', '品川区|西大井': '13109011', '品川区|豊町': '13109026', '墨田区|両国': '13107026', '墨田区|亀沢': '13107004', '墨田区|京島': '13107006', '墨田区|八広': '13107023', '墨田区|千歳': '13107013', '墨田区|向島': '13107022', '墨田区|吾妻橋': '13107001', '墨田区|堤通': '13107014', '墨田区|墨田': '13107009', '墨田区|太平': '13107010', '墨田区|押上': '13107003', '墨田区|文花': '13107019', '墨田区|本所': '13107020', '墨田区|東向島': '13107018', '墨田区|東墨田': '13107017', '墨田区|東駒形': '13107016', '墨田区|業平': '13107015', '墨田区|横川': '13107025', '墨田区|横網': '13107024', '墨田区|江東橋': '13107008', '墨田区|石原': '13107002', '墨田区|立川': '13107012', '墨田区|立花': '13107011', '墨田区|緑': '13107021', '墨田区|菊川': '13107005', '墨田区|錦糸': '13107007', '大田区|上池台': '13111012', '大田区|下丸子': '13111020', '大田区|中央': '13111026', '大田区|中馬込': '13111032', '大田区|久が原': '13111017', '大田区|仲六郷': '13111033', '大田区|仲池上': '13111031', '大田区|北千束': '13111014', '大田区|北嶺町': '13111016', '大田区|北糀谷': '13111013', '大田区|北馬込': '13111015', '大田区|千鳥': '13111025', '大田区|南久が原': '13111054', '大田区|南六郷': '13111058', '大田区|南千束': '13111055', '大田区|南蒲田': '13111053', '大田区|南雪谷': '13111057', '大田区|南馬込': '13111056', '大田区|多摩川': '13111024', '大田区|大森中': '13111005', '大田区|大森北': '13111004', '大田区|大森南': '13111009', '大田区|大森本町': '13111008', '大田区|大森東': '13111007', '大田区|大森西': '13111006', '大田区|山王': '13111019', '大田区|新蒲田': '13111022', '大田区|本羽田': '13111052', '大田区|東六郷': '13111049', '大田区|東嶺町': '13111046', '大田区|東矢口': '13111047', '大田区|東糀谷': '13111044', '大田区|東蒲田': '13111043', '大田区|東雪谷': '13111048', '大田区|東馬込': '13111045', '大田区|池上': '13111001', '大田区|田園調布': '13111027', '大田区|田園調布南': '13111029', '大田区|田園調布本町': '13111028', '大田区|矢口': '13111059', '大田区|石川町': '13111002', '大田区|羽田': '13111040', '大田区|萩中': '13111039', '大田区|蒲田': '13111010', '大田区|蒲田本町': '13111011', '大田区|西六郷': '13111038', '大田区|西嶺町': '13111037', '大田区|西糀谷': '13111035', '大田区|西蒲田': '13111034', '大田区|西馬込': '13111036', '大田区|雪谷大塚町': '13111060', '大田区|鵜の木': '13111003', '文京区|千石': '13105009', '文京区|千駄木': '13105010', '文京区|向丘': '13105016', '文京区|大塚': '13105001', '文京区|小日向': '13105006', '文京区|小石川': '13105004', '文京区|弥生': '13105018', '文京区|後楽': '13105005', '文京区|春日': '13105003', '文京区|本郷': '13105015', '文京区|本駒込': '13105014', '文京区|根津': '13105012', '文京区|水道': '13105007', '文京区|湯島': '13105019', '文京区|白山': '13105013', '文京区|目白台': '13105017', '文京区|西片': '13105011', '文京区|関口': '13105008', '文京区|音羽': '13105002', '新宿区|三栄町': '13104040', '新宿区|上落合': '13104032', '新宿区|下宮比町': '13104043', '新宿区|下落合': '13104042', '新宿区|中井': '13104063', '新宿区|中町': '13104066', '新宿区|中落合': '13104064', '新宿区|中里町': '13104065', '新宿区|二十騎町': '13104072', '新宿区|住吉町': '13104049', '新宿区|余丁町': '13104091', '新宿区|信濃町': '13104041', '新宿区|内藤町': '13104062', '新宿区|北山伏町': '13104036', '新宿区|北新宿': '13104034', '新宿区|北町': '13104035', '新宿区|南元町': '13104086', '新宿区|南山伏町': '13104087', '新宿区|南榎町': '13104084', '新宿区|南町': '13104085', '新宿区|原町': '13104074', '新宿区|喜久井町': '13104033', '新宿区|四谷': '13104092', '新宿区|四谷三栄町': '13104129', '新宿区|四谷坂町': '13104127', '新宿区|四谷本塩町': '13104128', '新宿区|大久保': '13104025', '新宿区|大京町': '13104052', '新宿区|天神町': '13104056', '新宿区|富久町': '13104100', '新宿区|山吹町': '13104088', '新宿区|岩戸町': '13104023', '新宿区|左門町': '13104039', '新宿区|市谷仲之町': '13104016', '新宿区|市谷八幡町': '13104017', '新宿区|市谷加賀町': '13104006', '新宿区|市谷台町': '13104013', '新宿区|市谷山伏町': '13104022', '新宿区|市谷左内町': '13104010', '新宿区|市谷本村町': '13104019', '新宿区|市谷柳町': '13104021', '新宿区|市谷田町': '13104012', '新宿区|市谷甲良町': '13104008', '新宿区|市谷砂土原町': '13104009', '新宿区|市谷船河原町': '13104018', '新宿区|市谷薬王寺町': '13104020', '新宿区|弁天町': '13104082', '新宿区|愛住町': '13104001', '新宿区|戸塚町': '13104057', '新宿区|戸山': '13104099', '新宿区|払方町': '13104073', '新宿区|改代町': '13104026', '新宿区|新宿': '13104046', '新宿区|新小川町': '13104045', '新宿区|早稲田南町': '13104097', '新宿区|早稲田町': '13104098', '新宿区|早稲田鶴巻町': '13104096', '新宿区|東五軒町': '13104078', '新宿区|東榎町': '13104076', '新宿区|榎町': '13104024', '新宿区|横寺町': '13104090', '新宿区|歌舞伎町': '13104031', '新宿区|水道町': '13104047', '新宿区|河田町': '13104111', '新宿区|片町': '13104030', '新宿区|白銀町': '13104044', '新宿区|百人町': '13104079', '新宿区|矢来町': '13104089', '新宿区|神楽坂': '13104028', '新宿区|筑土八幡町': '13104054', '新宿区|箪笥町': '13104051', '新宿区|築地町': '13104053', '新宿区|納戸町': '13104067', '新宿区|細工町': '13104037', '新宿区|舟町': '13104081', '新宿区|若宮町': '13104095', '新宿区|若松町': '13104094', '新宿区|若葉': '13104093', '新宿区|荒木町': '13104005', '新宿区|袋町': '13104080', '新宿区|西五軒町': '13104069', '新宿区|西新宿': '13104070', '新宿区|西早稲田': '13104071', '新宿区|西落合': '13104068', '新宿区|赤城下町': '13104002', '新宿区|赤城元町': '13104003', '新宿区|須賀町': '13104048', '新宿区|馬場下町': '13104075', '新宿区|高田馬場': '13104050', '杉並区|上井草': '13115011', '杉並区|上荻': '13115012', '杉並区|上高井戸': '13115013', '杉並区|下井草': '13115018', '杉並区|下高井戸': '13115019', '杉並区|久我山': '13115014', '杉並区|井草': '13115004', '杉並区|今川': '13115006', '杉並区|南荻窪': '13115033', '杉並区|和泉': '13115005', '杉並区|和田': '13115036', '杉並区|善福寺': '13115021', '杉並区|堀ノ内': '13115030', '杉並区|大宮': '13115009', '杉並区|天沼': '13115003', '杉並区|宮前': '13115034', '杉並区|成田東': '13115025', '杉並区|成田西': '13115024', '杉並区|方南': '13115029', '杉並区|本天沼': '13115031', '杉並区|松ノ木': '13115032', '杉並区|松庵': '13115020', '杉並区|桃井': '13115035', '杉並区|梅里': '13115007', '杉並区|永福': '13115008', '杉並区|浜田山': '13115028', '杉並区|清水': '13115017', '杉並区|荻窪': '13115010', '杉並区|西荻北': '13115026', '杉並区|西荻南': '13115027', '杉並区|阿佐谷北': '13115001', '杉並区|阿佐谷南': '13115002', '杉並区|高井戸東': '13115023', '杉並区|高井戸西': '13115022', '杉並区|高円寺北': '13115015', '杉並区|高円寺南': '13115016', '板橋区|三園': '13119049', '板橋区|上板橋': '13119017', '板橋区|中丸町': '13119036', '板橋区|中台': '13119034', '板橋区|中板橋': '13119032', '板橋区|仲宿': '13119033', '板橋区|仲町': '13119035', '板橋区|前野町': '13119048', '板橋区|加賀': '13119016', '板橋区|南常盤台': '13119051', '板橋区|南町': '13119050', '板橋区|双葉町': '13119045', '板橋区|向原': '13119053', '板橋区|四葉': '13119056', '板橋区|坂下': '13119022', '板橋区|大原町': '13119008', '板橋区|大和町': '13119054', '板橋区|大山東町': '13119014', '板橋区|大山町': '13119015', '板橋区|大山西町': '13119013', '板橋区|大山金井町': '13119012', '板橋区|大谷口': '13119009', '板橋区|大谷口上町': '13119010', '板橋区|大谷口北町': '13119011', '板橋区|大門': '13119028', '板橋区|宮本町': '13119052', '板橋区|富士見町': '13119044', '板橋区|小茂根': '13119019', '板橋区|小豆沢': '13119004', '板橋区|常盤台': '13119030', '板橋区|幸町': '13119020', '板橋区|弥生町': '13119055', '板橋区|徳丸': '13119031', '板橋区|志村': '13119025', '板橋区|成増': '13119037', '板橋区|新河岸': '13119026', '板橋区|本町': '13119047', '板橋区|東坂下': '13119042', '板橋区|東山町': '13119043', '板橋区|東新町': '13119029', '板橋区|板橋': '13119006', '板橋区|栄町': '13119021', '板橋区|桜川': '13119023', '板橋区|氷川町': '13119041', '板橋区|泉町': '13119005', '板橋区|清水町': '13119024', '板橋区|熊野町': '13119018', '板橋区|相生町': '13119001', '板橋区|稲荷台': '13119007', '板橋区|舟渡': '13119046', '板橋区|若木': '13119057', '板橋区|蓮根': '13119040', '板橋区|蓮沼町': '13119039', '板橋区|西台': '13119038', '板橋区|赤塚': '13119002', '板橋区|赤塚新町': '13119003', '板橋区|高島平': '13119027', '江戸川区|一之江': '13123001', '江戸川区|一之江町': '13123002', '江戸川区|上一色': '13123051', '江戸川区|上篠崎': '13123056', '江戸川区|下篠崎町': '13123017', '江戸川区|中央': '13123018', '江戸川区|中葛西': '13123019', '江戸川区|二之江町': '13123027', '江戸川区|北小岩': '13123009', '江戸川区|北篠崎': '13123057', '江戸川区|北葛西': '13123008', '江戸川区|南小岩': '13123043', '江戸川区|南篠崎町': '13123044', '江戸川区|南葛西': '13123042', '江戸川区|大杉': '13123050', '江戸川区|宇喜田町': '13123003', '江戸川区|小松川': '13123011', '江戸川区|平井': '13123034', '江戸川区|新堀': '13123020', '江戸川区|春江町': '13123028', '江戸川区|本一色': '13123052', '江戸川区|東小岩': '13123030', '江戸川区|東小松川': '13123031', '江戸川区|東松本': '13123059', '江戸川区|東瑞江': '13123033', '江戸川区|東葛西': '13123029', '江戸川区|松島': '13123040', '江戸川区|松本': '13123053', '江戸川区|松江': '13123039', '江戸川区|江戸川': '13123004', '江戸川区|清新町': '13123046', '江戸川区|瑞江': '13123055', '江戸川区|篠崎町': '13123015', '江戸川区|臨海町': '13123049', '江戸川区|興宮町': '13123005', '江戸川区|船堀': '13123035', '江戸川区|西一之江': '13123021', '江戸川区|西小岩': '13123023', '江戸川区|西小松川町': '13123024', '江戸川区|西瑞江': '13123026', '江戸川区|西篠崎': '13123060', '江戸川区|西葛西': '13123022', '江戸川区|谷河内': '13123058', '江戸川区|鹿骨': '13123013', '江戸川区|鹿骨町': '13123014', '江東区|三好': '13108039', '江東区|亀戸': '13108009', '江東区|住吉': '13108022', '江東区|佐賀': '13108013', '江東区|冬木': '13108035', '江東区|北砂': '13108010', '江東区|千田': '13108024', '江東区|千石': '13108023', '江東区|南砂': '13108038', '江東区|古石場': '13108036', '江東区|塩浜': '13108015', '江東区|大島': '13108008', '江東区|富岡': '13108029', '江東区|常盤': '13108028', '江東区|平野': '13108032', '江東区|扇橋': '13108007', '江東区|新大橋': '13108019', '江東区|新砂': '13108021', '江東区|有明': '13108001', '江東区|木場': '13108011', '江東区|東砂': '13108031', '江東区|東陽': '13108027', '江東区|東雲': '13108017', '江東区|枝川': '13108005', '江東区|森下': '13108041', '江東区|毛利': '13108040', '江東区|永代': '13108004', '江東区|海辺': '13108003', '江東区|深川': '13108033', '江東区|清澄': '13108012', '江東区|潮見': '13108016', '江東区|牡丹': '13108037', '江東区|猿江': '13108014', '江東区|白河': '13108018', '江東区|石島': '13108002', '江東区|福住': '13108034', '江東区|豊洲': '13108030', '江東区|越中島': '13108006', '江東区|辰巳': '13108026', '江東区|門前仲町': '13108042', '江東区|青海': '13108045', '江東区|高橋': '13108025', '渋谷区|上原': '13113001', '渋谷区|代々木': '13113031', '渋谷区|代官山町': '13113018', '渋谷区|元代々木町': '13113030', '渋谷区|円山町': '13113029', '渋谷区|初台': '13113025', '渋谷区|千駄ヶ谷': '13113017', '渋谷区|南平台町': '13113021', '渋谷区|大山町': '13113007', '渋谷区|宇田川町': '13113003', '渋谷区|富ヶ谷': '13113019', '渋谷区|幡ヶ谷': '13113023', '渋谷区|広尾': '13113027', '渋谷区|恵比寿': '13113004', '渋谷区|恵比寿南': '13113006', '渋谷区|恵比寿西': '13113005', '渋谷区|本町': '13113028', '渋谷区|東': '13113026', '渋谷区|松濤': '13113013', '渋谷区|桜丘町': '13113009', '渋谷区|渋谷': '13113012', '渋谷区|猿楽町': '13113011', '渋谷区|神南': '13113016', '渋谷区|神宮前': '13113015', '渋谷区|神山町': '13113008', '渋谷区|神泉町': '13113014', '渋谷区|笹塚': '13113010', '渋谷区|西原': '13113022', '渋谷区|道玄坂': '13113020', '渋谷区|鉢山町': '13113024', '渋谷区|鶯谷町': '13113002', '港区|三田': '13103024', '港区|元赤坂': '13103027', '港区|元麻布': '13103028', '港区|六本木': '13103029', '港区|北青山': '13103008', '港区|南青山': '13103025', '港区|南麻布': '13103026', '港区|台場': '13103030', '港区|愛宕': '13103006', '港区|新橋': '13103016', '港区|東新橋': '13103023', '港区|東麻布': '13103022', '港区|浜松町': '13103021', '港区|海岸': '13103007', '港区|港南': '13103009', '港区|白金': '13103014', '港区|白金台': '13103015', '港区|芝': '13103010', '港区|芝公園': '13103012', '港区|芝大門': '13103013', '港区|芝浦': '13103011', '港区|虎ノ門': '13103018', '港区|西新橋': '13103020', '港区|西麻布': '13103019', '港区|赤坂': '13103001', '港区|高輪': '13103017', '港区|麻布十番': '13103002', '港区|麻布台': '13103003', '港区|麻布狸穴町': '13103005', '目黒区|三田': '13110021', '目黒区|上目黒': '13110005', '目黒区|下目黒': '13110008', '目黒区|中央町': '13110013', '目黒区|中根': '13110015', '目黒区|中町': '13110014', '目黒区|中目黒': '13110016', '目黒区|五本木': '13110007', '目黒区|八雲': '13110026', '目黒区|南': '13110023', '目黒区|原町': '13110017', '目黒区|大岡山': '13110002', '目黒区|大橋': '13110003', '目黒区|平町': '13110011', '目黒区|東が丘': '13110018', '目黒区|東山': '13110019', '目黒区|柿の木坂': '13110004', '目黒区|洗足': '13110010', '目黒区|目黒': '13110024', '目黒区|目黒本町': '13110025', '目黒区|碑文谷': '13110020', '目黒区|祐天寺': '13110027', '目黒区|緑が丘': '13110022', '目黒区|自由が丘': '13110009', '目黒区|青葉台': '13110001', '目黒区|駒場': '13110006', '目黒区|鷹番': '13110012', '練馬区|三原台': '13120046', '練馬区|上石神井': '13120006', '練馬区|上石神井南町': '13120048', '練馬区|下石神井': '13120013', '練馬区|中村': '13120027', '練馬区|中村北': '13120028', '練馬区|中村南': '13120029', '練馬区|光が丘': '13120037', '練馬区|北町': '13120008', '練馬区|南大泉': '13120043', '練馬区|南田中': '13120045', '練馬区|向山': '13120009', '練馬区|土支田': '13120026', '練馬区|大泉学園町': '13120003', '練馬区|大泉町': '13120004', '練馬区|富士見台': '13120041', '練馬区|小竹町': '13120010', '練馬区|平和台': '13120042', '練馬区|早宮': '13120036', '練馬区|旭丘': '13120001', '練馬区|旭町': '13120002', '練馬区|春日町': '13120005', '練馬区|東大泉': '13120039', '練馬区|栄町': '13120011', '練馬区|桜台': '13120012', '練馬区|氷川台': '13120038', '練馬区|田柄': '13120020', '練馬区|石神井台': '13120014', '練馬区|石神井町': '13120015', '練馬区|立野町': '13120021', '練馬区|練馬': '13120034', '練馬区|羽沢': '13120035', '練馬区|西大泉': '13120030', '練馬区|谷原': '13120047', '練馬区|豊玉上': '13120022', '練馬区|豊玉中': '13120024', '練馬区|豊玉北': '13120023', '練馬区|豊玉南': '13120025', '練馬区|貫井': '13120033', '練馬区|錦': '13120032', '練馬区|関町北': '13120017', '練馬区|関町南': '13120049', '練馬区|関町東': '13120050', '練馬区|高松': '13120019', '練馬区|高野台': '13120018', '荒川区|南千住': '13118007', '荒川区|東尾久': '13118004', '荒川区|東日暮里': '13118005', '荒川区|町屋': '13118006', '荒川区|荒川': '13118001', '荒川区|西尾久': '13118002', '荒川区|西日暮里': '13118003', '葛飾区|お花茶屋': '13122003', '葛飾区|亀有': '13122007', '葛飾区|南水元': '13122029', '葛飾区|四つ木': '13122030', '葛飾区|堀切': '13122026', '葛飾区|奥戸': '13122002', '葛飾区|宝町': '13122013', '葛飾区|小菅': '13122008', '葛飾区|新宿': '13122015', '葛飾区|新小岩': '13122011', '葛飾区|東四つ木': '13122024', '葛飾区|東堀切': '13122022', '葛飾区|東新小岩': '13122020', '葛飾区|東水元': '13122023', '葛飾区|東立石': '13122021', '葛飾区|東金町': '13122019', '葛飾区|柴又': '13122009', '葛飾区|水元': '13122027', '葛飾区|白鳥': '13122010', '葛飾区|立石': '13122014', '葛飾区|細田': '13122025', '葛飾区|西亀有': '13122016', '葛飾区|西新小岩': '13122017', '葛飾区|西水元': '13122018', '葛飾区|金町': '13122004', '葛飾区|鎌倉': '13122006', '葛飾区|青戸': '13122001', '葛飾区|高砂': '13122012', '豊島区|上池袋': '13116004', '豊島区|北大塚': '13116005', '豊島区|千川': '13116021', '豊島区|千早': '13116022', '豊島区|南大塚': '13116018', '豊島区|南池袋': '13116017', '豊島区|南長崎': '13116019', '豊島区|巣鴨': '13116007', '豊島区|東池袋': '13116016', '豊島区|池袋': '13116001', '豊島区|池袋本町': '13116002', '豊島区|目白': '13116020', '豊島区|西巣鴨': '13116015', '豊島区|西池袋': '13116014', '豊島区|要町': '13116003', '豊島区|長崎': '13116013', '豊島区|雑司が谷': '13116009', '豊島区|駒込': '13116006', '豊島区|高松': '13116011', '豊島区|高田': '13116010', '足立区|一ツ家': '13121078', '足立区|中央本町': '13121060', '足立区|中川': '13121065', '足立区|伊興': '13121106', '足立区|伊興本町': '13121117', '足立区|佐野': '13121032', '足立区|保塚町': '13121082', '足立区|保木間': '13121081', '足立区|入谷': '13121104', '足立区|入谷町': '13121013', '足立区|六月': '13121098', '足立区|六木': '13121087', '足立区|六町': '13121099', '足立区|加平': '13121023', '足立区|加賀': '13121021', '足立区|北加平町': '13121024', '足立区|千住': '13121040', '足立区|千住中居町': '13121048', '足立区|千住仲町': '13121049', '足立区|千住元町': '13121052', '足立区|千住大川町': '13121043', '足立区|千住宮元町': '13121051', '足立区|千住寿町': '13121045', '足立区|千住旭町': '13121042', '足立区|千住曙町': '13121041', '足立区|千住東': '13121054', '足立区|千住柳町': '13121053', '足立区|千住桜木': '13121055', '足立区|千住橋戸町': '13121050', '足立区|千住河原町': '13121044', '足立区|千住緑町': '13121056', '足立区|千住関屋町': '13121046', '足立区|千住龍田町': '13121047', '足立区|南花畑': '13121084', '足立区|古千谷本町': '13121115', '足立区|堀之内': '13121083', '足立区|大谷田': '13121017', '足立区|宮城': '13121085', '足立区|小台': '13121019', '足立区|島根': '13121035', '足立区|平野': '13121080', '足立区|弘道': '13121028', '足立区|扇': '13121016', '足立区|新田': '13121036', '足立区|日ノ出町': '13121079', '足立区|本木': '13121088', '足立区|本木北町': '13121089', '足立区|本木南町': '13121092', '足立区|本木東町': '13121091', '足立区|本木西町': '13121090', '足立区|東伊興': '13121109', '足立区|東保木間': '13121076', '足立区|東六月町': '13121077', '足立区|東和': '13121062', '足立区|東綾瀬': '13121074', '足立区|柳原': '13121097', '足立区|栗原': '13121027', '足立区|梅島': '13121014', '足立区|梅田': '13121015', '足立区|椿': '13121061', '足立区|江北': '13121029', '足立区|皿沼': '13121033', '足立区|神明': '13121037', '足立区|神明南': '13121038', '足立区|竹の塚': '13121057', '足立区|綾瀬': '13121003', '足立区|興野': '13121018', '足立区|舎人': '13121063', '足立区|花畑': '13121073', '足立区|西伊興': '13121107', '足立区|西保木間': '13121072', '足立区|西加平': '13121071', '足立区|西新井': '13121067', '足立区|西新井本町': '13121069', '足立区|西新井栄町': '13121068', '足立区|西竹の塚': '13121108', '足立区|西綾瀬': '13121066', '足立区|谷中': '13121096', '足立区|谷在家': '13121094', '足立区|足立': '13121002', '足立区|辰沼': '13121059', '足立区|関原': '13121039', '足立区|青井': '13121001', '足立区|鹿浜': '13121034'}


def _v167_public_oz_codes(cache=None):
    """Collect only verified SUUMO oz codes from its existing town form cache."""
    codes=dict(_V167_VERIFIED_OZ)
    if not isinstance(cache,dict):return codes
    for ward_code,record in cache.items():
        if ward_code not in TOKYO_WARDS or not isinstance(record,dict):continue
        for entry in record.get('regions',[]):
            if not isinstance(entry,dict):continue
            town=town_base_name(entry.get('suumo_town') or entry.get('town') or '')
            code=str(((entry.get('suumo_prepared') or {}).get('base_params') or {}).get('oz',''))
            if re.fullmatch(re.escape(ward_code)+r'\d{3}',code) and town:
                codes[TOKYO_WARDS[ward_code]+'|'+town]=code
    return codes


def _v167_suumo_town_search(town_key, group, public_oz=None):
    """SUUMO official city+town search; never expand silently to a whole ward.

    oz must have been confirmed by the provider town-selection form or
    independently verified public town page.  A chome is represented by its
    actual SUUMO town, because SUUMO's town selector does not filter chome.
    """
    if group not in _V167_SUUMO_CONDITION_LABELS:return ''
    if not isinstance(town_key,str) or '|' not in town_key:return ''
    ward,town=town_key.split('|',1)
    city=_V167_WARD_TO_CODE.get(ward)
    if not city or not town or len(town)>70:return ''
    base=town_base_name(town)
    if (not base or not re.fullmatch(r'[\w\u3040-\u30ff\u3400-\u9fff\u3005\u30fc\-]+',base)
            or not re.fullmatch(r'[\w\u3040-\u30ff\u3400-\u9fff\u3005\u30fc\-]+',town)):
        return ''
    code=(public_oz or _V167_VERIFIED_OZ).get(ward+'|'+base)
    # Crucial: a missing/unverified oz MUST NOT produce a ward-wide result.
    if not isinstance(code,str) or not re.fullmatch(re.escape(city)+r'\d{3}',code):
        return ''
    return _v189_suumo_search_url(city,group,code)


def _v189_suumo_search_url(city,group,code=None):
    """Build a ward or verified-town URL with the displayed layout conditions."""
    if city not in TOKYO_WARDS or group not in _V167_SUUMO_CONDITION_LABELS:return ''
    if code is not None and not re.fullmatch(re.escape(city)+r'\d{3}',str(code)):return ''
    params=[('ar','030'),('bs','040'),('ta','13'),('sc',city),
            ('cb','0.0'),('ct','9999999'),('mb','50' if group=='house' else '0'),('mt','9999999')]
    if code is not None:params.append(('oz',code))
    if group in DISPLAY_LAYOUT_GROUPS:
        params.extend(('md',SUUMO_LAYOUT_CODES[layout]) for layout in DISPLAY_LAYOUT_GROUPS[group])
        params.extend([('ts',SUUMO_BUILDING_TYPE_CODES['\u30de\u30f3\u30b7\u30e7\u30f3']),('cn','15')])
    else:
        # Single-family homes: do not fabricate a provider-side 40-year value.
        params.extend([('ts',SUUMO_BUILDING_TYPE_CODES['\u4e00\u6238\u5efa\u3066\u30fb\u305d\u306e\u4ed6'])])
    # User requested up to 15 min by foot, street-address sorting, 50 buildings.
    params.extend([('et','15'),('shkr1','03'),('shkr2','03'),('shkr3','03'),
                   ('shkr4','03'),('sngz',''),('po1','17'),('pc','50')])
    return 'https://suumo.jp/jj/chintai/ichiran/FR301FC001/?'+urlencode(params,doseq=True)


class _V168TownOzRegistry:
    """Small, read-only lookup for verified SUUMO town codes.

    It never modifies listing data or makes a municipality-only fallback URL.
    Fetching is sequential and restricted to wards currently visible on the map.
    """
    def __init__(self):
        self.lock=threading.RLock()
        self.codes=dict(_V167_VERIFIED_OZ)
        self.queued=set()
        self.completed=set()
        self.failed_until={}
        self.tasks=queue.Queue()
        self.worker=threading.Thread(target=self._run,daemon=True,
                                     name='sumai-v168-suumo-town-lookup')
        self.worker.start()

    def snapshot(self):
        with self.lock:return dict(self.codes)

    def request(self,ward_codes):
        now=time.monotonic()
        with self.lock:
            for code in ward_codes:
                if (code in TOKYO_WARDS and code not in self.queued
                    and code not in self.completed and now>=self.failed_until.get(code,0)):
                    self.queued.add(code)
                    self.tasks.put(code)

    def _run(self):
        # Direct, normal public pages only; no paid search API or proxies.
        web=PublicWeb()
        web.configure({})
        while True:
            code=self.tasks.get()
            try:
                regions,_,_=suumo_ward_regions(web,code,include_municipalities=False)
                found={}
                for entry in regions:
                    town=town_base_name(entry.get('suumo_town') or entry.get('town') or '')
                    oz=str(((entry.get('suumo_prepared') or {}).get('base_params') or {}).get('oz',''))
                    if town and re.fullmatch(re.escape(code)+r'\d{3}',oz):
                        found[TOKYO_WARDS[code]+'|'+town]=oz
                if not found:raise ValueError('SUUMO town selector returned no verified codes')
                with self.lock:
                    self.codes.update(found)
                    self.completed.add(code)
            except Exception:
                # On provider errors, back off for ten minutes rather than retry.
                with self.lock:self.failed_until[code]=time.monotonic()+600
            finally:
                with self.lock:self.queued.discard(code)
                self.tasks.task_done()
            # Avoid flooding the provider if a large map covers many wards.
            if not self.tasks.empty():time.sleep(2)


@st.cache_resource(show_spinner=False)
def _v168_town_oz_registry():
    return _V168TownOzRegistry()


def _v168_visible_wards(geo):
    counts={}
    for feature in geo.get('features',[]):
        name=str((feature.get('properties') or {}).get('name',''))
        ward=name.split(' ',1)[0]
        code=_V167_WARD_TO_CODE.get(ward)
        if code:counts[code]=counts.get(code,0)+1
    return [code for code,_ in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))]



def town_coverage_audit(groups,boundary_keys,group):
    """Cross-check saved rent keys against official polygon keys for all 23 wards.

    The absence of a qualifying listing does NOT prove a skipped acquisition;
    keep geometry, saved data and search progress separate in the UI.
    """
    boundaries=frozenset(boundary_keys or ())
    counts=((groups or {}).get(group) or {})
    stored={key for key,value in counts.items()
            if isinstance(value,(list,tuple)) and len(value)>=2 and value[1]>0}
    missing=sorted(stored-boundaries)
    rows=[]
    for code,ward in TOKYO_WARDS.items():
        prefix=ward+'|'
        known=sum(1 for key in boundaries if key.startswith(prefix))
        colored=sum(1 for key in stored if key.startswith(prefix) and key in boundaries)
        unmatched=sum(1 for key in missing if key.startswith(prefix))
        rows.append({'区':ward,'境界の町丁目':known,'家賃表示':colored,
                     '保存家賃なし':max(0,known-colored),'保存済み・境界不一致':unmatched})
    return {'wards':rows,'stored_without_geometry':missing,
            'geometry_keys':len(boundaries),'colored_town_keys':len(stored&boundaries),
            'missing_rent_towns':len(boundaries-stored)}


def town_colored_shapes(groups,group,features,bounds,max_features=None,include_no_data=False,suumo_oz_codes=None):
    """Join the exact saved town summary with every polygon in the viewport.

    Only polygons with positive saved-rent counts are painted by the live map.
    Optional empty-town geometry is for diagnostics; it must never inherit a
    neighboring town's rent. Stored source records are unchanged.
    """
    counts=(groups or {}).get(group,{})
    rented={}
    empty={}
    if hasattr(features,'iter_visible'):
        iterator=features.iter_visible(bounds,counts,include_empty=include_no_data)
    else:
        def old_iterator():
            for key,area in (features or {}).items():
                bbox=area['bbox']
                if bounds and (bbox[0]>bounds[2] or bbox[2]<bounds[0]
                               or bbox[1]>bounds[3] or bbox[3]<bounds[1]):
                    continue
                if not include_no_data and key not in counts:continue
                yield key,area['coordinates']
        iterator=old_iterator()
    for key,polygons in iterator:
        observation=counts.get(key)
        if observation and len(observation)>1 and observation[1]>0:
            rented.setdefault(key,[]).extend(polygons)
        elif include_no_data:
            empty.setdefault(key,[]).extend(polygons)
    selected=sorted(rented)
    limited=max_features is not None and len(selected)>max_features
    if limited:
        # Optional diagnostic cap; never requested by the live choropleth.
        selected=sorted(rented,key=lambda name:(-counts[name][1],name))[:max_features]
    result={'type':'FeatureCollection','features':[], 'render_stats':{
        'with_rent':len(selected),'without_rent':len(empty),
        'rent_total_in_window':len(rented),'wards_gray_backdrop':0}}
    # Administrative shapes are a neutral *underlay* to prevent unexplained
    # white gaps where the 2015 census town geometry has no polygon.
    # Never assign a rent or count to a ward on the basis of neighboring data.
    if hasattr(features,'iter_ward_visible') and include_no_data:
        for ward,polygons in features.iter_ward_visible(bounds):
            result['features'].append({'type':'Feature',
                'geometry':{'type':'MultiPolygon','coordinates':polygons},
                'properties':{'name':ward,'ward_backdrop':True,
                              'color':'#b7c0c3','has_data':None}})
            result['render_stats']['wards_gray_backdrop']+=1
    # Unknown towns are drawn FIRST, so the authoritative rent colors remain
    # on top, even where source geometries have shared lines or small overlaps.
    for key in sorted(empty):
        result['features'].append({'type':'Feature',
            'geometry':{'type':'MultiPolygon','coordinates':empty[key]},
            'properties':{'name':key.replace('|',' '),'has_data':False,
                          'color':'#d0d7da','count_label':'0',
                          'price_label':'n/a',
                          'suumo_ward':key.split('|',1)[0], 'suumo_name':key.split('|',1)[1],
                          'suumo_url':_v167_suumo_town_search(key,group,suumo_oz_codes),
                          'suumo_conditions':_V167_SUUMO_CONDITION_LABELS.get(group,''), 'suumo_town':town_base_name(key.split('|',1)[1])}})
    for key in selected:
        total,count=counts[key]
        avg=total/count
        result['features'].append({'type':'Feature',
            'geometry':{'type':'MultiPolygon','coordinates':rented[key]},
            'properties':{'name':key.replace('|',' '),'has_data':True,
                          'avg':round(avg), 'price_label':f'{avg/10000:.1f}\u4e07\u5186',
                          'count_label':f'{count}\u4ef6',
                          'color':town_rent_gradient(avg),
                          'suumo_ward':key.split('|',1)[0], 'suumo_name':key.split('|',1)[1],
                          'suumo_url':_v167_suumo_town_search(key,group,suumo_oz_codes),
                          'suumo_conditions':_V167_SUUMO_CONDITION_LABELS.get(group,''), 'suumo_town':town_base_name(key.split('|',1)[1])}})
    return result,limited,len(rented)


def _v156_town_render_window(bounds,center):
    """Prefetch a small surrounding ring instead of >10x screen area."""
    try:
        if bounds and len(bounds)==4:
            south,west,north,east=map(float,bounds)
            if all(map(math.isfinite,(south,west,north,east))) and south<north and west<east:
                height=north-south;width=east-west
                lat_pad=max(.004,min(.24*height,.035))
                lon_pad=max(.004,min(.24*width,.045))
                return (south-lat_pad,west-lon_pad,north+lat_pad,east+lon_pad)
    except (ValueError,TypeError,OverflowError):pass
    try:
        lat,lng=map(float,center)
    except (ValueError,TypeError):
        lat,lng=DEFAULT_CENTER
    return (lat-.031,lng-.039,lat+.031,lng+.039)


def _v156_view_is_covered(bounds,window):
    """A pan only reloads boundaries on leaving the prefetched window."""
    if not bounds:return True
    if not window or len(window)!=4:return False
    try:
        south,west,north,east=map(float,bounds)
        ws,ww,wn,we=map(float,window)
        return ws<=south<=north<=wn and ww<=west<=east<=we
    except (ValueError,TypeError):return False

def capture_map_fragment_v117():
    """Read bounds from the actual current map widget, not the v115 key.

    Importantly this never forces a full rerun or rebuilds the map when the
    user pans or pinches on an Android device.
    """
    state=st.session_state
    widget='new_map_v117_'+str(state.get('_v117_reset_count',0))
    response=state.get(widget)
    if not isinstance(response,dict):return
    bounds=viewport_bounds(response)
    if bounds:
        state['new_bounds']=bounds
        state['new_view_center']=bounds_center(bounds)
        state['new_map']=response


# V118: a stateful browser-side Leaflet component.  Do NOT use st_folium here:
# its folium-html prop and result callbacks can remount the iframe on every
# viewport update. The browser map lives independently; only bounds are sent
# to Python, while the layer changes only on explicit map revision changes.
TOWN_COMPONENT_HTML_V121 = '<!doctype html><html lang="ja"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">\n<link id="leaflet-css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">\n<style>\nhtml,body{margin:0;padding:0;width:100%;height:480px;overflow:hidden;background:#ecefee;font-family:system-ui,"Hiragino Kaku Gothic ProN",sans-serif}\n#map{height:480px;width:100%;background:#f5f6f4;position:relative}\n#notice{position:absolute;z-index:9999;left:10px;bottom:12px;max-width:80%;background:rgba(255,255,255,.95);border-radius:5px;padding:7px 10px;font-size:12px;color:#173a5e;pointer-events:none;display:none}\n#touchmode{position:absolute;z-index:1200;right:12px;top:12px;border:1px solid #ddd;border-radius:5px;background:#fff;padding:10px;color:#193b45;font-size:13px;box-shadow:0 1px 6px #999;display:none}\n/* v121: station-first labels; transparent text with crisp white halo,\n   no irregular opaque white rectangles competing with the rent polygons. */\n.station-chip{display:inline-flex;position:relative;align-items:center;gap:3px;white-space:nowrap;\n  color:#062d4f;font-weight:900;font-size:14px;line-height:18px;\n  background:none;border:0;border-radius:0;padding:0;box-shadow:none;\n  pointer-events:none;transform:translate(-50%,-50%);\n  text-shadow:-1.5px -1.5px 0 #fff,0 -2px 0 #fff,1.5px -1.5px 0 #fff,\n     2px 0 0 #fff,1.5px 1.5px 0 #fff,0 2px 0 #fff,\n     -1.5px 1.5px 0 #fff,-2px 0 0 #fff,0 0 4px #fff;}\n.station-chip::before{content:"";display:inline-block;flex:none;width:5px;height:5px;\n  border-radius:50%;background:#0064b0;border:1.3px solid #fff;\n  box-shadow:0 0 0 1px #005b9b;}\n.station-chip.minor{font-size:12px;line-height:16px;font-weight:800;color:#143b57;}\n.station-chip.minor::before{width:4px;height:4px;background:#216f9c;}\n/* Compact, overlap-checked labels let additional hubs appear at low zoom. */\n.station-chip.compact{font-size:12px;line-height:16px;}\n.station-chip.minor.compact{font-size:11px;line-height:15px;}\n/* No separate white town/ward cards. The base map has its own muted\n   geographic context; hover/tap any polygon for the exact town name. */\n.town-chip,.ward-chip{display:none}\n.leaflet-tooltip{font-family:inherit}\n</style></head>\n<body>\n<div id="map"></div><button id="touchmode" type="button">ページをスクロール</button><div id="notice" role="status"></div>\n<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js" onerror="var script=document.createElement(\'script\');script.src=\'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\';document.head.appendChild(script);"></script>\n<script>\n(function(){\'use strict\';\nvar state={map:null,overlay:null,stationLayer:null,townLabelLayer:null,wardLayer:null,townAnchors:[],groupSignature:null,stations:[],wards:[],lastBounds:null,\n           loading:false,started:false,sentBounds:\'\',revision:-1,touchPan:true,pendingArgs:null,retryCount:0,retryTimer:null};\nfunction hostMessage(type,extra){window.parent.postMessage(Object.assign({isStreamlitMessage:true,type:type},extra||{}),\'*\')}\nfunction setHeight(){hostMessage(\'streamlit:setFrameHeight\',{height:480})}\nfunction note(message){var el=document.getElementById(\'notice\');el.textContent=message||\'\';el.style.display=message?\'block\':\'none\'}\nfunction initMap(args){\n if(state.map)return;\n if(!window.L){note(\'地図を読み込み中…\');return}\n var center=Array.isArray(args.center)&&args.center.length===2?args.center:[35.6812,139.7671];\n var zoom=Number.isFinite(+args.zoom)?+args.zoom:14;\n state.map=L.map(\'map\',{center:center,zoom:zoom,zoomControl:true,preferCanvas:true,zoomSnap:.25,\n   zoomDelta:.5,wheelPxPerZoomLevel:110,scrollWheelZoom:true,gestureHandling:false,touchZoom:true,dragging:true});\n var base=L.tileLayer(\'https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png\',{\n  attribution:\'国土地理院\',maxZoom:18,keepBuffer:1,updateWhenIdle:true});\n state.map.createPane(\'grayBase\');state.map.getPane(\'grayBase\').style.zIndex=\'200\';\n state.map.getPane(\'grayBase\').style.filter=\'grayscale(1) contrast(.68) brightness(1.12)\';\n state.map.getPane(\'grayBase\').style.opacity=\'.42\';\n base.options.pane=\'grayBase\';base.addTo(state.map);\n var errors=0, backup=false;\n base.on(\'tileerror\',function(){errors++;if(errors>=6&&!backup){backup=true;\n   state.map.removeLayer(base);\n   L.tileLayer(\'https://tile.openstreetmap.org/{z}/{x}/{y}.png\',{\n     attribution:\'&copy; OpenStreetMap contributors\',maxZoom:19,keepBuffer:1,pane:\'grayBase\',updateWhenIdle:true}).addTo(state.map);\n }});\n state.map.createPane(\'townNamePane\');state.map.getPane(\'townNamePane\').style.zIndex=\'640\';state.map.getPane(\'townNamePane\').style.pointerEvents=\'none\';\n state.map.createPane(\'stationNamePane\');state.map.getPane(\'stationNamePane\').style.zIndex=\'670\';state.map.getPane(\'stationNamePane\').style.pointerEvents=\'none\';\n state.stationLayer=L.layerGroup().addTo(state.map);\n state.townLabelLayer=L.layerGroup().addTo(state.map);\n state.wardLayer=L.layerGroup().addTo(state.map);\n var touch=window.matchMedia && window.matchMedia(\'(pointer:coarse)\').matches;\n if(touch){\n   var button=document.getElementById(\'touchmode\');button.style.display=\'block\';\n   button.addEventListener(\'click\',function(){state.touchPan=!state.touchPan;\n      if(state.touchPan){state.map.dragging.enable();state.map.touchZoom.enable();button.textContent=\'ページをスクロール\';document.getElementById(\'map\').style.touchAction=\'none\'}\n      else{state.map.dragging.disable();state.map.touchZoom.disable();button.textContent=\'地図を動かす\';document.getElementById(\'map\').style.touchAction=\'pan-y\'}\n   });document.getElementById(\'map\').style.touchAction=\'none\';\n }\n var schedule=null;\n state.map.on(\'moveend zoomend\',function(){\n    if(schedule!==null)clearTimeout(schedule);\n    schedule=setTimeout(function(){schedule=null;redrawStations();emitBounds()},420);\n });\n state.map.on(\'resize\',function(){redrawStations()});\n document.addEventListener(\'visibilitychange\',function(){if(document.visibilityState===\'visible\')setTimeout(function(){state.map.invalidateSize();redrawStations()},100)});\n window.addEventListener(\'pageshow\',function(){setTimeout(function(){if(state.map){state.map.invalidateSize();redrawStations()}},200)});\n state.map.whenReady(function(){setTimeout(function(){state.map.invalidateSize();redrawStations();emitBounds()},350)});\n note(\'\');setHeight();\n}\nfunction emitBounds(){if(!state.map)return;var b=state.map.getBounds();\n var arr=[b.getSouth(),b.getWest(),b.getNorth(),b.getEast()];\n if(arr.some(function(x){return !isFinite(x)}))return;\n var key=arr.map(function(v){return v.toFixed(5)}).join(\',\')+\'|\'+state.map.getZoom();\n if(key===state.sentBounds)return;\n state.sentBounds=key;\n hostMessage(\'streamlit:setComponentValue\',{dataType:\'json\',value:{bounds:{_southWest:{lat:arr[0],lng:arr[1]},_northEast:{lat:arr[2],lng:arr[3]}},zoom:state.map.getZoom()}});\n}\nfunction updateOverlay(geo,signature){\n if(!state.map || signature===state.groupSignature)return;\n try{\n  var fresh=null;\n  if(geo && geo.features && geo.features.length){\n   fresh=L.geoJSON(geo,{smoothFactor:1.7,style:function(feature){return {color:\'#606b72\',weight:.7,fillColor:feature.properties.color||\'#bbb\',fillOpacity:.77}},\n       onEachFeature:function(feature,layer){var props=feature.properties||{},name=String(props.name||\'\'),avg=String(props.price_label||\'\'),count=String(props.count_label||\'\');\n         var el=document.createElement(\'div\');el.textContent=name+\'｜平均 \'+avg+\'｜\'+count;layer.bindPopup(el);}});\n  }\n  // All town labels remain in GeoJSON popups; do not create 700+ browser\n  // markers when stations are the only permanent geographic labels.\n  var anchors=[];\n  if(state.overlay){state.map.removeLayer(state.overlay);state.overlay=null}\n  if(fresh){fresh.addTo(state.map);state.overlay=fresh}\n  state.townAnchors=anchors;\n  state.groupSignature=signature;\n }catch(err){note(\'地図の塗り分けを表示できません: \'+(err&&err.message||\'エラー\'))}\n}\nfunction safeLabel(value){return String(value||\'\').replace(/[&<>"\']/g,function(c){return {\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#39;\'}[c]})}\nfunction reserveLabel(point,width,height,boxes,size){\n var box=[point.x-width/2,point.y-height/2,point.x+width/2,point.y+height/2];\n if(box[0]<3 || box[1]<3 || box[2]>size.x-3 || box[3]>size.y-3)return false;\n for(var i=0;i<boxes.length;i++){\n   var v=boxes[i];if(box[0]<v[2]+4&&box[2]>v[0]-4&&box[1]<v[3]+4&&box[3]>v[1]-4)return false;\n }\n boxes.push(box);return true;\n}\nfunction stationBox(p,width,height){\n return [p.x-width/2,p.y-height/2,p.x+width/2,p.y+height/2];\n}\nfunction redrawStations(){\n if(!state.map || !state.stationLayer)return;\n var m=state.map,zoom=m.getZoom(),bounds=m.getBounds(),size=m.getSize();\n if(size.x<30||size.y<30)return;\n state.stationLayer.clearLayers();\n // Explicitly clear previous town and ward labels from earlier render cycles.\n if(state.townLabelLayer)state.townLabelLayer.clearLayers();\n if(state.wardLayer)state.wardLayer.clearLayers();\n var stations=state.stations||[],boxes=[],visible=[],count=0;\n // v158: show a second tier of widely used hubs on zoomed-out screens.\n // Preserve all local-station labels at detailed zooms.  Collision detection\n // remains the final arbiter, so mobile text cannot obscure other labels.\n var wide=size.x>=520;\n var limit=zoom>=16?150:zoom>=15?120:zoom>=14?100:zoom>=13?75:zoom>=12?55:zoom>=11.5?40:zoom>=11?(wide?42:28):zoom>=10?(wide?32:22):zoom>=9?(wide?24:18):16;\n for(var i=0;i<stations.length;i++){\n   var s=stations[i];\n   if(zoom<9 && s.priority>0)continue;\n   if(zoom>=9 && zoom<11 && s.priority>1)continue;\n   if(zoom>=11 && zoom<11.5 && s.priority>2)continue;\n   if(!bounds.contains([s.lat,s.lng]))continue;\n   visible.push(s);\n }\n // Prioritize major stations, then stations nearest the viewport center.\n // This avoids a broad alphabetic bias when the map is densely populated.\n var cp=size.divideBy(2);\n visible.sort(function(a,b){\n   var pa=Number(zoom>=12?a.detail_priority:a.priority),pb=Number(zoom>=12?b.detail_priority:b.priority);\n   if(pa!==pb)return pa-pb;\n   var ap=m.latLngToContainerPoint([a.lat,a.lng]);\n   var bp=m.latLngToContainerPoint([b.lat,b.lng]);\n   var da=(ap.x-cp.x)*(ap.x-cp.x)+(ap.y-cp.y)*(ap.y-cp.y);\n   var db=(bp.x-cp.x)*(bp.x-cp.x)+(bp.y-cp.y)*(bp.y-cp.y);\n   return da-db;\n });\n for(var j=0;j<visible.length&&count<limit;j++){\n   var s=visible[j],name=String(s.name||\'\');\n   if(!name)continue;\n   var p=m.latLngToContainerPoint([s.lat,s.lng]);\n   var major=s.priority===0;\n   var compact=zoom<12;\n   var fontSize=compact?(major?12:11):(major?14:12);\n   var width=Math.min(195,Array.from(name).length*fontSize+11);\n   var height=compact?17:(major?21:19);\n   // Use a precise pixel collision box matching the compact halo label.\n   if(!reserveLabel(p,width,height,boxes,size))continue;\n   count++;\n   var html=\'<span class="station-chip\'+(major?\'\':\' minor\')+(compact?\' compact\':\'\')+\'">\'+safeLabel(name)+\'</span>\';\n   L.marker([s.lat,s.lng],{icon:L.divIcon({className:\'\',html:html,iconSize:[0,0]}),\n          interactive:false,keyboard:false,pane:\'stationNamePane\'}).addTo(state.stationLayer);\n }\n}\nfunction render(args){\n args=args||{};\n if(!state.map){\n   if(!window.L){\n      state.pendingArgs=args;\n      if(!state.retryTimer && state.retryCount<30){\n        state.retryTimer=setTimeout(function(){state.retryTimer=null;state.retryCount++;render(state.pendingArgs)},500);\n      }\n      if(state.retryCount>=30)note(\'地図ライブラリの読込に失敗しました。通信を確認してください。\');\n      return;\n   }\n   initMap(args);if(!state.map)return;\n }\n state.stations=Array.isArray(args.stations)?args.stations:state.stations;\n state.wards=Array.isArray(args.wards)?args.wards:state.wards;\n updateOverlay(args.geo,args.signature);\n redrawStations();\n setHeight();\n}\nwindow.addEventListener(\'message\',function(e){if(e.data&&e.data.type===\'streamlit:render\')render(e.data.args)});\nhostMessage(\'streamlit:componentReady\',{apiVersion:1});setHeight();\n})();\n</script>\n</body></html>\n'

# v153: Same gradient as TOWN_GRADIENT, now placed immediately above
# the map INSIDE the same component iframe. No intermediate Streamlit
# containers or buttons can separate the legend from the map.
def _v153_attached_rent_legend():
    low,high=TOWN_GRADIENT[0][0],TOWN_GRADIENT[-1][0]
    gradient=','.join(
        '#'+''.join(f'{part:02x}' for part in rgb)+f' {(price-low)/(high-low)*100:.4f}%'
        for price,rgb in TOWN_GRADIENT)
    ticks=''.join(
        f'<span style="position:absolute;left:{(price-low)/(high-low)*100:.4f}%;'
        'transform:translateX(-50%);top:0;white-space:nowrap">'
        f'{price//10000}</span>'
        for price in range(50000,450000,50000))
    return ('<div id="rent-scale" aria-label="町丁目別の平均家賃の色分け">'
            '<div class="rent-scale-title">町丁目別・平均家賃（万円）</div>'
            f'<div class="rent-scale-bar" style="background:linear-gradient(90deg,{gradient})"></div>'
            f'<div class="rent-scale-ticks">{ticks}</div>'
            '<div class="rent-scale-limits"><span>3万円以下</span><span>45万円以上</span></div>'
            '</div>')

_V153_LEGEND_HEIGHT = 64
_V153_FRAME_HEIGHT = 480 + _V153_LEGEND_HEIGHT
_V153_LEGEND_CSS = (
    '\n#rent-scale{height:64px;box-sizing:border-box;padding:4px 12px 2px;'
    'background:#f5f3ed;color:#173a5e;font-family:inherit;}\n'
    '.rent-scale-title{font-size:12px;font-weight:700;line-height:15px;margin:0 0 2px;}\n'
    '.rent-scale-bar{height:11px;border-radius:4px;}\n'
    '.rent-scale-ticks{height:14px;position:relative;font-size:10px;font-weight:600;line-height:14px;}\n'
    '.rent-scale-limits{display:flex;justify-content:space-between;'
    'font-size:10px;line-height:13px;}\n'
    '#touchmode{top:76px!important;}\n'
)
assert TOWN_COMPONENT_HTML_V121.count('<div id="map"></div>')==1
assert TOWN_COMPONENT_HTML_V121.count('</style></head>')==1
assert 'height:480px;overflow:hidden;' in TOWN_COMPONENT_HTML_V121
assert "hostMessage('streamlit:setFrameHeight',{height:480})" in TOWN_COMPONENT_HTML_V121
TOWN_COMPONENT_HTML_V121=(
    TOWN_COMPONENT_HTML_V121
    .replace('</style></head>',_V153_LEGEND_CSS+'</style></head>',1)
    .replace('<div id="map"></div>',_v153_attached_rent_legend()+'<div id="map"></div>',1)
    .replace('height:480px;overflow:hidden;',f'height:{_V153_FRAME_HEIGHT}px;overflow:hidden;',1)
    .replace("hostMessage('streamlit:setFrameHeight',{height:480})",
             f"hostMessage('streamlit:setFrameHeight',{{height:{_V153_FRAME_HEIGHT}}})",1))

# Avoid independent coarse per-polygon SVG simplification that reveals seams.
assert 'smoothFactor:1.7' in TOWN_COMPONENT_HTML_V121
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    'smoothFactor:1.7','smoothFactor:0.25',1)


# v157 - explicitly distinguish saved-rent and no-data town polygons.
_V157_STYLE_OLD = "fresh=L.geoJSON(geo,{smoothFactor:0.25,style:function(feature){return {color:'#606b72',weight:.7,fillColor:feature.properties.color||'#bbb',fillOpacity:.77}},"
_V157_STYLE_NEW = "fresh=L.geoJSON(geo,{smoothFactor:0.25,style:function(feature){var p=feature.properties||{};if(p.has_data===false)return {color:'#bec7ca',weight:.35,fillColor:'#d0d7da',fillOpacity:.54};return {color:'#606b72',weight:.7,fillColor:p.color||'#bbb',fillOpacity:.81}},"
assert TOWN_COMPONENT_HTML_V121.count(_V157_STYLE_OLD)==1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(_V157_STYLE_OLD,_V157_STYLE_NEW,1)
_V157_POPUP_OLD="var el=document.createElement('div');el.textContent=name+'\uFF5C\u5E73\u5747 '+avg+'\uFF5C'+count;layer.bindPopup(el);"
_V157_POPUP_NEW="var el=document.createElement('div');el.textContent=props.has_data===false?name+'\uFF5C\u3053\u306E\u6761\u4EF6\u306E\u4FDD\u5B58\u30C7\u30FC\u30BF\u306A\u3057':name+'\uFF5C\u5E73\u5747 '+avg+'\uFF5C'+count;layer.bindPopup(el);"
assert TOWN_COMPONENT_HTML_V121.count(_V157_POPUP_OLD)==1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(_V157_POPUP_OLD,_V157_POPUP_NEW,1)



_V159_STATION_OLD='   var s=visible[j],name=String(s.name||\'\');\n   if(!name)continue;\n   var p=m.latLngToContainerPoint([s.lat,s.lng]);\n   var major=s.priority===0;\n   var compact=zoom<12;\n   var fontSize=compact?(major?12:11):(major?14:12);\n   var width=Math.min(195,Array.from(name).length*fontSize+11);\n   var height=compact?17:(major?21:19);\n   // Use a precise pixel collision box matching the compact halo label.\n   if(!reserveLabel(p,width,height,boxes,size))continue;\n   count++;\n   var html=\'<span class="station-chip\'+(major?\'\':\' minor\')+(compact?\' compact\':\'\')+\'">\'+safeLabel(name)+\'</span>\';\n'
_V159_STATION_NEW='   var s=visible[j],name=String(s.name||\'\');\n   if(!name)continue;\n   var commuteTime=(typeof s.commute_min===\'number\' && isFinite(s.commute_min) && s.commute_min>=0 && s.commute_min<999)?Math.round(s.commute_min):null;\n   var timeLabel=commuteTime===null?\'\':\'（\'+commuteTime+\'分）\';\n   var p=m.latLngToContainerPoint([s.lat,s.lng]);\n   var major=s.priority===0;\n   var compact=zoom<12;\n   var fontSize=compact?(major?12:11):(major?14:12);\n   var width=Math.min(252,Array.from(name).length*fontSize+Array.from(timeLabel).length*(fontSize-2)+11);\n   var height=compact?17:(major?21:19);\n   // Include the travel-time suffix in the measured collision box.\n   if(!reserveLabel(p,width,height,boxes,size))continue;\n   count++;\n   var html=\'<span class="station-chip\'+(major?\'\':\' minor\')+(compact?\' compact\':\'\')+\'">\'+safeLabel(name)+(timeLabel?\'<span class="commute-time">\'+safeLabel(timeLabel)+\'</span>\':\'\')+\'</span>\';\n'
assert TOWN_COMPONENT_HTML_V121.count(_V159_STATION_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V159_STATION_OLD,_V159_STATION_NEW,1)
_V159_STATION_CSS='.station-chip.minor.compact{font-size:11px;line-height:15px;}\n'
assert TOWN_COMPONENT_HTML_V121.count(_V159_STATION_CSS)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V159_STATION_CSS,_V159_STATION_CSS+'.station-chip .commute-time{font-size:.86em;font-weight:800;letter-spacing:-.3px;}\n',1)

# v161: show travel minutes AND transfer count on each station label.
# Both originate from the same Dijkstra result, not a separate route estimate.
_V161_STATION_TIME_OLD="   var timeLabel=commuteTime===null?'':'（'+commuteTime+'分）';"
_V161_STATION_TIME_NEW=(
    "   var commuteTransfers=(typeof s.commute_transfers==='number' && "
    "isFinite(s.commute_transfers) && s.commute_transfers>=0 && "
    "Math.floor(s.commute_transfers)===s.commute_transfers)?s.commute_transfers:null;\n"
    "   var timeLabel=(commuteTime===null||commuteTransfers===null)?"
    "(state.commuteDestination?'（未計算）':''):"
    "'（'+commuteTime+'分-'+commuteTransfers+'回）';"
)
assert TOWN_COMPONENT_HTML_V121.count(_V161_STATION_TIME_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    _V161_STATION_TIME_OLD,_V161_STATION_TIME_NEW,1)
# v163: expose whether a commute destination was actually selected.
_V163_RENDER_STATIONS = ' state.stations=Array.isArray(args.stations)?args.stations:state.stations;'
assert TOWN_COMPONENT_HTML_V121.count(_V163_RENDER_STATIONS)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    _V163_RENDER_STATIONS,
    ' state.commuteDestination=String(args.commute_destination||\"\");\n'+_V163_RENDER_STATIONS,1)

# v164: an independent optional second destination. The complete dual label is
# produced in Leaflet (including two-line collision size), not painted as an
# image. Keep the v163 one-destination representation fully backward compatible.
_V164_OLD_TIME = (
    "   var timeLabel=(commuteTime===null||commuteTransfers===null)?"
    "(state.commuteDestination?'（未計算）':''):"
    "'（'+commuteTime+'分-'+commuteTransfers+'回）';"
)
_V164_NEW_TIME = """   var dual=Boolean(state.commuteDestination2);
   var timeLabel=(commuteTime===null||commuteTransfers===null)?
     (state.commuteDestination?'（未計算）':''):
     '（'+(dual?'1.':'')+commuteTime+'分-'+commuteTransfers+'回）';
   var secondMin=(typeof s.commute2_min==='number' && isFinite(s.commute2_min) && s.commute2_min>=0 && s.commute2_min<999)?Math.round(s.commute2_min):null;
   var secondTransfers=(typeof s.commute2_transfers==='number' && isFinite(s.commute2_transfers) && s.commute2_transfers>=0 &&
     Math.floor(s.commute2_transfers)===s.commute2_transfers)?s.commute2_transfers:null;
   var timeLabel2=!dual?'':(secondMin===null||secondTransfers===null)?'（2.未計算）':
     '（2.'+secondMin+'分-'+secondTransfers+'回）';"""
assert TOWN_COMPONENT_HTML_V121.count(_V164_OLD_TIME)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V164_OLD_TIME,_V164_NEW_TIME,1)
_V164_OLD_COLLISION = """   var width=Math.min(252,Array.from(name).length*fontSize+Array.from(timeLabel).length*(fontSize-2)+11);
   var height=compact?17:(major?21:19);"""
_V164_NEW_COLLISION = """   // A dual-destination label occupies two real lines. Reserve the full area
   // so that nearby stations are not drawn on top of either commute estimate.
   var nameWidth=Array.from(name).length*fontSize;
   var timeWidth=Math.max(Array.from(timeLabel).length,Array.from(timeLabel2).length)*(fontSize*.83);
   var width=Math.min(330,nameWidth+timeWidth+19);
   var height=dual?(compact?34:(major?41:37)):(compact?17:(major?21:19));"""
assert TOWN_COMPONENT_HTML_V121.count(_V164_OLD_COLLISION)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V164_OLD_COLLISION,_V164_NEW_COLLISION,1)
_V164_OLD_MARKUP = """   var html='<span class="station-chip'+(major?'':' minor')+(compact?' compact':'')+'">'+safeLabel(name)+(timeLabel?'<span class="commute-time">'+safeLabel(timeLabel)+'</span>':'')+'</span>';"""
_V164_NEW_MARKUP = """   var classes='station-chip'+(major?'':' minor')+(compact?' compact':'')+(dual?' dual':'');
   var content=dual?
     '<span class="commute-grid"><span class="station-name">'+safeLabel(name)+'</span><span class="commute-time first">'+safeLabel(timeLabel)+'</span>'+
     '<span class="station-grid-spacer" aria-hidden="true"></span><span class="commute-time second">'+safeLabel(timeLabel2)+'</span></span>':
     safeLabel(name)+(timeLabel?'<span class="commute-time">'+safeLabel(timeLabel)+'</span>':'');
   var html='<span class="'+classes+'">'+content+'</span>';"""
assert TOWN_COMPONENT_HTML_V121.count(_V164_OLD_MARKUP)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V164_OLD_MARKUP,_V164_NEW_MARKUP,1)
_V164_CSS = """
.station-chip.dual{align-items:flex-start;gap:3px;line-height:1.15}
.station-chip.dual::before{margin-top:4px}
.station-chip.dual .commute-grid{display:grid;grid-template-columns:max-content max-content;column-gap:2px;row-gap:1px;align-items:center}
.station-chip.dual .station-name{grid-column:1;grid-row:1}
.station-chip.dual .commute-time.first{grid-column:2;grid-row:1}
.station-chip.dual .station-grid-spacer{grid-column:1;grid-row:2}
.station-chip.dual .commute-time.second{grid-column:2;grid-row:2}
"""
assert TOWN_COMPONENT_HTML_V121.count('</style></head>')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V164_CSS+'</style></head>',1)
_V164_OLD_DESTINATION = ' state.commuteDestination=String(args.commute_destination||"");'
assert TOWN_COMPONENT_HTML_V121.count(_V164_OLD_DESTINATION)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    _V164_OLD_DESTINATION,
    _V164_OLD_DESTINATION+'\n state.commuteDestination2=String(args.commute_destination_2||"");',1)

# v167: clickable provider link in both colored and gray polygon popups.
_V167_POPUP_OLD = "var el=document.createElement('div');el.textContent=props.has_data===false?name+'｜この条件の保存データなし':name+'｜平均 '+avg+'｜'+count;layer.bindPopup(el);"
_V167_POPUP_NEW = _V167_POPUP_OLD.replace('layer.bindPopup(el);', "// v167: SUUMO link belongs to each polygon and follows selected rent type.\nvar suumo=String(props.suumo_url||'');\nif(/^https:\\/\\/suumo\\.jp\\/jj\\/chintai\\/ichiran\\/FR301FC001\\/\\?/.test(suumo)){\n  var link=document.createElement('a');\n  link.className='v167-suumo-link';\n  link.textContent='SUUMOで検索 ↗';\n  link.href=suumo;link.target='_blank';link.rel='noopener noreferrer';\n  el.appendChild(document.createElement('div'));\n  el.appendChild(link);\n  var help=document.createElement('div');\n  help.className='v167-suumo-note';\n  help.textContent='SUUMO町名:'+String(props.suumo_town||'')+'｜'+String(props.suumo_conditions||'')+'｜町名を指定・駅徒歩15分以内・住所順・50件（丁目は絞れません）';\n  el.appendChild(help);\n}else{\n  var pending=document.createElement('div');\n  pending.className='v167-suumo-note';\n  pending.textContent='SUUMOの町名コードが未確認（区全体では検索しません）';\n  el.appendChild(pending);\n}\nlayer.bindPopup(el);")
assert TOWN_COMPONENT_HTML_V121.count(_V167_POPUP_OLD)==1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(_V167_POPUP_OLD,_V167_POPUP_NEW,1)
_V167_CSS = ".v167-suumo-link{display:inline-block;margin-top:8px;padding:7px 11px;background:#174a6a;border-radius:6px;color:white!important;text-decoration:none;font-weight:700;font-size:13px;touch-action:manipulation}.v167-suumo-note{font-size:10px;max-width:245px;color:#5c6670;margin-top:5px}"
assert TOWN_COMPONENT_HTML_V121.count('</style></head>')==1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V167_CSS+'</style></head>',1)

# v171: layer administrative underlay below towns without inventing rent.
_V171_STYLE_OLD = _V157_STYLE_NEW
_V171_STYLE_NEW = _V171_STYLE_OLD.replace(
    "if(p.has_data===false)return",
    "if(p.ward_backdrop)return {color:'transparent',weight:0,fillColor:'#b7c0c3',fillOpacity:.26};if(p.has_data===false)return")
assert TOWN_COMPONENT_HTML_V121.count(_V171_STYLE_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V171_STYLE_OLD,_V171_STYLE_NEW,1)
_V171_EACH_OLD="onEachFeature:function(feature,layer){var props=feature.properties||{},name=String(props.name||''),avg=String(props.price_label||''),count=String(props.count_label||'');"
_V171_EACH_NEW=_V171_EACH_OLD.replace(
    "props=feature.properties||{},name=", "props=feature.properties||{};if(props.ward_backdrop){layer.options.interactive=false;return;}var name=")
assert TOWN_COMPONENT_HTML_V121.count(_V171_EACH_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V171_EACH_OLD,_V171_EACH_NEW,1)

# v172: never shade unobserved towns or ward underlays. Keep real rent colors.
_V172_STYLE_OLD = _V171_STYLE_NEW
_V172_STYLE_NEW = _V172_STYLE_OLD.replace(
    "if(p.ward_backdrop)return {color:'transparent',weight:0,fillColor:'#b7c0c3',fillOpacity:.26};if(p.has_data===false)return {color:'#bec7ca',weight:.35,fillColor:'#d0d7da',fillOpacity:.54};",
    "if(p.has_data!==true)return {color:'transparent',weight:0,fillOpacity:0};")
assert _V172_STYLE_NEW != _V172_STYLE_OLD
assert TOWN_COMPONENT_HTML_V121.count(_V172_STYLE_OLD) == 1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(_V172_STYLE_OLD, _V172_STYLE_NEW, 1)
_V172_EACH_OLD = _V171_EACH_NEW
_V172_EACH_NEW = _V172_EACH_OLD.replace(
    "if(props.ward_backdrop){layer.options.interactive=false;return;}",
    "if(props.has_data!==true){layer.options.interactive=false;return;}")
assert _V172_EACH_NEW != _V172_EACH_OLD
assert TOWN_COMPONENT_HTML_V121.count(_V172_EACH_OLD) == 1
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(_V172_EACH_OLD, _V172_EACH_NEW, 1)

# v178: time / official-reference crowd rate / transfer count in the map label.
# Do not misrepresent a per-line busiest segment rate as a measured station-pair rate.
_V178_LABEL_OLD=_V164_NEW_TIME
_V178_LABEL_NEW="""   var dual=Boolean(state.commuteDestination2);
   var pct=(typeof s.commute_crowding_pct==='number'&&isFinite(s.commute_crowding_pct)&&s.commute_crowding_pct>=0)?Math.round(s.commute_crowding_pct):null;
   var crowdLabel=pct===null?'不明':('参考'+pct+'%');
   var timeLabel=(commuteTime===null||commuteTransfers===null)?
      (state.commuteDestination?'①未計算':''):
      '①'+commuteTime+'分（'+crowdLabel+'）-'+commuteTransfers+'回';
   var secondMin=(typeof s.commute2_min==='number' && isFinite(s.commute2_min) && s.commute2_min>=0 && s.commute2_min<999)?Math.round(s.commute2_min):null;
   var secondTransfers=(typeof s.commute2_transfers==='number' && isFinite(s.commute2_transfers) && s.commute2_transfers>=0 &&
     Math.floor(s.commute2_transfers)===s.commute2_transfers)?s.commute2_transfers:null;
   var secondPct=(typeof s.commute2_crowding_pct==='number'&&isFinite(s.commute2_crowding_pct)&&s.commute2_crowding_pct>=0)?Math.round(s.commute2_crowding_pct):null;
   var secondCrowd=secondPct===null?'不明':('参考'+secondPct+'%');
   var timeLabel2=!dual?'':(secondMin===null||secondTransfers===null)?'②未計算':
     '②'+secondMin+'分（'+secondCrowd+'）-'+secondTransfers+'回';"""
assert TOWN_COMPONENT_HTML_V121.count(_V178_LABEL_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V178_LABEL_OLD,_V178_LABEL_NEW,1)
# Keep collision detection aware of the longer reference-rate labels.
_V178_WIDTH_OLD='   var width=Math.min(330,nameWidth+timeWidth+19);'
_V178_WIDTH_NEW='   var width=Math.min(460,nameWidth+timeWidth+19);'
assert TOWN_COMPONENT_HTML_V121.count(_V178_WIDTH_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V178_WIDTH_OLD,_V178_WIDTH_NEW,1)

# v181: A one-shot map navigation command, applied inside the existing
# Leaflet component. Browser map identity, saved commute settings and rent
# overlays remain intact; normal moveend sends the new viewport to Python.
_V181_RENDER_ANCHOR = " updateOverlay(args.geo,args.signature);\n redrawStations();"
assert TOWN_COMPONENT_HTML_V121.count(_V181_RENDER_ANCHOR)==1
_V181_RENDER_REPLACEMENT = """ updateOverlay(args.geo,args.signature);
 var target=args.jump;
 if(target && Array.isArray(target.bounds) && target.bounds.length===4){
   var token=String(target.id||'');
   var b=target.bounds.map(Number);
   if(token && token!==state.lastJumpToken && b.every(Number.isFinite) &&
      b[0]>=34 && b[2]<=37 && b[1]>=138 && b[3]<=141 &&
      b[0]<b[2] && b[1]<b[3]){
     state.lastJumpToken=token;
     state.map.fitBounds([[b[0],b[1]],[b[2],b[3]]],
       {padding:[30,30],maxZoom:16,animate:true,duration:0.55});
   }
 }
 redrawStations();"""
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    _V181_RENDER_ANCHOR,_V181_RENDER_REPLACEMENT,1)

# v182: the search selection carries verified polygon geometry, not just bbox.
# Outlines live in a dedicated Leaflet pane and never alter rent fill colors.
_V182_FOCUS_PANE_ANCHOR = " state.map.createPane('stationNamePane');"
assert TOWN_COMPONENT_HTML_V121.count(_V182_FOCUS_PANE_ANCHOR)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    _V182_FOCUS_PANE_ANCHOR,
    " state.map.createPane('townSearchFocusPane');state.map.getPane('townSearchFocusPane').style.zIndex='650';"+
    "state.map.getPane('townSearchFocusPane').style.pointerEvents='none';\n"+
    _V182_FOCUS_PANE_ANCHOR,1)
_V182_OLD_JUMP = _V181_RENDER_REPLACEMENT
_V182_NEW_JUMP = """ updateOverlay(args.geo,args.signature);
 // The selected district's real geometry is independent of rent observations.
 // A new command gets processed exactly once; rerendering alone does not pan.
 var target=args.jump;
 if(target && target.id && Array.isArray(target.bounds) && target.bounds.length===4 &&
    String(target.id)!==state.lastJumpToken){
   var box=target.bounds.map(Number);
   if(box.every(Number.isFinite) && box[0]>=34 && box[2]<=37 && box[1]>=138 && box[3]<=141 &&
      box[0]<box[2] && box[1]<box[3]){
     try{
       var focus=L.geoJSON(target.geo,{pane:'townSearchFocusPane',interactive:false,
         smoothFactor:1.0,style:function(){return {
           color:'#195d72',weight:2,opacity:.88,fillColor:'#69adb8',fillOpacity:.12,
           dashArray:null,lineCap:'round',lineJoin:'round',interactive:false
         }}});
       if(!focus.getLayers().length)throw new Error('検索地域の境界データがありません');
       if(state.focusLayer)state.map.removeLayer(state.focusLayer);
       state.focusLayer=focus.addTo(state.map);
       state.lastJumpToken=String(target.id);
       state.sentBounds='';
       // Avoid an animated viewport callback racing a Streamlit fragment rerun.
       state.map.invalidateSize();
       state.map.once('moveend',function(){emitBounds()});
       state.map.fitBounds([[box[0],box[1]],[box[2],box[3]]],
          {padding:[24,24],maxZoom:16,animate:false});
       setTimeout(function(){emitBounds()},120);
     }catch(err){note('地名検索の表示に失敗しました: '+(err&&err.message||'エラー'));}
   }
 }
 redrawStations();"""
assert TOWN_COMPONENT_HTML_V121.count(_V182_OLD_JUMP)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V182_OLD_JUMP,_V182_NEW_JUMP,1)
_V182_EMIT_OLD="var key=arr.map(function(v){return v.toFixed(5)}).join(',')+'|'+state.map.getZoom();"
_V182_EMIT_NEW="var key=arr.map(function(v){return v.toFixed(5)}).join(',')+'|'+state.map.getZoom()+'|'+(state.lastJumpToken||'');"
assert TOWN_COMPONENT_HTML_V121.count(_V182_EMIT_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V182_EMIT_OLD,_V182_EMIT_NEW,1)
_V182_SEND_OLD="},zoom:state.map.getZoom()}});"
_V182_SEND_NEW="},zoom:state.map.getZoom(),jump_applied:state.lastJumpToken||null}});"
assert TOWN_COMPONENT_HTML_V121.count(_V182_SEND_OLD)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V182_SEND_OLD,_V182_SEND_NEW,1)


# v184: search lives INSIDE the same stable Leaflet iframe.  A click moves
# its map immediately, then asks Python only for the exact polygon boundary.
# It is not blocked by the external Streamlit form or unrelated page reruns.
_V184_SEARCH_CSS = """
html,body{height:auto!important;min-height:0!important;overflow:hidden!important}
#town-search-panel{box-sizing:border-box;width:100%;padding:12px 13px 14px;background:#fff;border-top:1px solid #d5dfdc;font-family:inherit;color:#173a5e}
#town-search-panel label{display:block;font-size:13px;font-weight:750;margin:0 0 6px}
#town-query{width:100%;box-sizing:border-box;height:40px;border:1px solid #b9c7c3;border-radius:7px;background:#fff;color:#173a5e;padding:7px 10px;font-size:16px}
#town-submit,#town-confirm{display:block;width:100%;box-sizing:border-box;margin-top:9px;min-height:40px;border:1px solid #a5b8b0;border-radius:7px;background:#fff;color:#173a5e;font-size:14px;font-weight:650;cursor:pointer;touch-action:manipulation}
#town-submit:active,#town-confirm:active{background:#dfe9e6}
#town-select{width:100%;height:40px;margin-top:9px;border:1px solid #b9c7c3;border-radius:7px;font-size:15px;background:white;color:#173a5e}
#town-search-status{font-size:12px;line-height:1.35;color:#49616b;margin-top:7px;min-height:0}
#town-options[hidden]{display:none!important}
"""
_V184_SEARCH_DOM = """<div id=\"town-search-panel\"><label for=\"town-query\">地名を検索して地図を移動</label><input id=\"town-query\" type=\"search\" autocomplete=\"off\" placeholder=\"例：中落合、豊島区南長崎3丁目\"/><button type=\"button\" id=\"town-submit\">検索した地域へ移動</button><div id=\"town-options\" hidden><label for=\"town-select\">該当する地域を選択</label><select id=\"town-select\"></select><button type=\"button\" id=\"town-confirm\">選んだ地域に移動</button></div><div id=\"town-search-status\" role=\"status\" aria-live=\"polite\"></div></div>"""
assert TOWN_COMPONENT_HTML_V121.count('</style></head>')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V184_SEARCH_CSS+'</style></head>',1)
assert TOWN_COMPONENT_HTML_V121.count('<div id="map"></div>')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('<div id="map"></div>','<div id="map"></div>'+_V184_SEARCH_DOM,1)
assert TOWN_COMPONENT_HTML_V121.count("{height:"+str(_V153_FRAME_HEIGHT)+"}")==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace("{height:"+str(_V153_FRAME_HEIGHT)+"}","{height:Math.max(615,document.documentElement.scrollHeight)}",1)

_V184_CLIENT_SEARCH_JS = r"""
function setTownStatus(message){
 var node=document.getElementById('town-search-status');
 if(node)node.textContent=message||'';
}
function normalizeTownQuery(value){
 var text=String(value||'');
 try{text=text.normalize('NFKC')}catch(e){}
 var digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};
 text=text.replace(/([一二三四五六七八九十]+)丁目/g,function(all,part){
   if(part.indexOf('十')>=0){var halves=part.split('十');return ((digits[halves[0]]||1)*10+(digits[halves[1]]||0))+'丁目';}
   return (digits[part]||part)+'丁目';
 });
 return text.replace(/[\s\u3000、,，。・/／]/g,'').replace(/^東京都/,'').replace(/^東京/,'');
}
function townCandidates(raw){
 var wanted=normalizeTownQuery(raw);
 if(!wanted)return [];
 var ward='';
 var wards=state.searchDistricts||[];
 var sorted=wards.slice().sort(function(a,b){return b.length-a.length});
 for(var i=0;i<sorted.length;i++){
   if(wanted.indexOf(sorted[i])===0){ward=sorted[i];wanted=wanted.slice(ward.length);break;}
 }
 if(!wanted&&ward){
   var w=(state.searchWardBounds||[]).find(function(x){return x.ward===ward});
   return w?[{ward:ward,name:ward,kind:'ward',label:ward,bounds:w.bounds,rank:0}]:[];
 }
 if(/.+[0-9]{1,2}$/.test(wanted))wanted+='丁目';
 var specific=/[0-9]+丁目$/.test(wanted);
 var matched=new Map();
 (state.searchCatalog||[]).forEach(function(area){
   if(ward&&area.ward!==ward)return;
   var name=specific?area.town:area.base;
   if(!name)return;
   var rank=name===wanted?0:(name.indexOf(wanted)===0?1:(name.includes(wanted)?2:99));
   if(rank===99)return;
   var id=area.ward+'|'+name+'|'+(specific?'chome':'town');
   var entry=matched.get(id),b=area.bounds;
   if(entry){
     entry.rank=Math.min(entry.rank,rank);
     entry.bounds=[Math.min(entry.bounds[0],b[0]),Math.min(entry.bounds[1],b[1]),Math.max(entry.bounds[2],b[2]),Math.max(entry.bounds[3],b[3])];
     entry.size++;
   }else{
     matched.set(id,{ward:area.ward,name:name,kind:specific?'chome':'town',label:area.ward+' '+name,bounds:b.slice(),rank:rank,size:1});
   }
 });
 var results=Array.from(matched.values()).sort(function(a,b){return a.rank-b.rank||a.ward.localeCompare(b.ward,'ja')||a.name.localeCompare(b.name,'ja')});
 if(results.length&&results[0].rank===0)results=results.filter(function(x){return x.rank===0});
 results=results.slice(0,80);
 results.forEach(function(x){if(x.size>1&&x.kind==='town')x.label+='（全'+x.size+'区域）'});
 return results;
}
function townSendSelection(place){
 if(!state.map){setTownStatus('地図を読み込み中です。');return;}
 var b=place.bounds.map(Number);
 if(b.length!==4 || !b.every(Number.isFinite) || !(b[0]<b[2]&&b[1]<b[3])){
   setTownStatus('選択地域の範囲が不正です。');return;
 }
 // Pan and zoom immediately in the BROWSER, before any Streamlit rerun.
 // The dashed temporary rectangle is not a claim about the true boundary.
 if(state.provisionalFocus){state.map.removeLayer(state.provisionalFocus);state.provisionalFocus=null;}
 state.map.invalidateSize();
 state.map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:[25,25],maxZoom:16,animate:false});
 if(state.focusLayer){state.map.removeLayer(state.focusLayer);state.focusLayer=null;}
 state.provisionalFocus=L.rectangle([[b[0],b[1]],[b[2],b[3]]],{color:'#719da6',weight:1.5,dashArray:'4 4',fillColor:'#c0dfe2',fillOpacity:.08,interactive:false}).addTo(state.map);
 var ticket=String(Date.now())+'_'+Math.random().toString(36).slice(2,11);
 state.searchPendingTicket=ticket;
 state.sentBounds='';
 setTownStatus('「'+place.label+'」へ移動しました。正確な境界線を読み込み中…');
 var mapBounds=state.map.getBounds();
 hostMessage('streamlit:setComponentValue',{dataType:'json',value:{
   event:'town_search_v184',ticket:ticket,ward:place.ward,name:place.name,kind:place.kind,
   bounds:{_southWest:{lat:mapBounds.getSouth(),lng:mapBounds.getWest()},_northEast:{lat:mapBounds.getNorth(),lng:mapBounds.getEast()}},
   zoom:state.map.getZoom(),jump_applied:state.lastJumpToken||null
 }});
}
function submitTownQuery(){
 var input=document.getElementById('town-query');
 var placeChoices=townCandidates(input&&input.value);
 var area=document.getElementById('town-options');
 var select=document.getElementById('town-select');
 area.hidden=true;
 if(!placeChoices.length){
   setTownStatus((state.searchCatalog||[]).length?'該当する地域が見つかりません。区名や町名を確認してください。':'町丁目境界を準備中です。地図の読込後に再度検索してください。');
   return;
 }
 if(placeChoices.length===1){townSendSelection(placeChoices[0]);return;}
 state.searchChoices=placeChoices;
 select.replaceChildren();
 placeChoices.forEach(function(place,index){var option=document.createElement('option');option.value=String(index);option.textContent=place.label;select.appendChild(option)});
 area.hidden=false;
 setTownStatus('候補が'+placeChoices.length+'件あります。移動先を選択してください。');
 setHeight();
}
function initTownSearch(){
 if(state.searchUiInitialized)return;
 state.searchUiInitialized=true;
 document.getElementById('town-submit').addEventListener('click',submitTownQuery);
 document.getElementById('town-query').addEventListener('keydown',function(e){
   if(e.key==='Enter'){e.preventDefault();submitTownQuery()}
 });
 document.getElementById('town-confirm').addEventListener('click',function(){
   var index=Number(document.getElementById('town-select').value);
   var choice=(state.searchChoices||[])[index];
   if(choice){document.getElementById('town-options').hidden=true;townSendSelection(choice)}
 });
}
"""
assert TOWN_COMPONENT_HTML_V121.count('function render(args){')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V184_CLIENT_SEARCH_JS+'\nfunction render(args){',1)
assert TOWN_COMPONENT_HTML_V121.count(' state.stations=Array.isArray(args.stations)?')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(
    ' state.stations=Array.isArray(args.stations)?',
    " initTownSearch();\n state.searchCatalog=Array.isArray(args.search_catalog)?args.search_catalog:[];\n state.searchDistricts=Array.isArray(args.search_district_names)?args.search_district_names:[];\n state.searchWardBounds=Array.isArray(args.search_ward_bounds)?args.search_ward_bounds:[];\n if(args.search_error && args.search_error.ticket===state.searchPendingTicket){\n   state.searchPendingTicket=null;\n   if(state.provisionalFocus){state.map.removeLayer(state.provisionalFocus);state.provisionalFocus=null;}\n   setTownStatus(String(args.search_error.message||'地域境界の取得に失敗しました。'));\n   setTimeout(emitBounds,100);\n }\n state.stations=Array.isArray(args.stations)?",1)
assert TOWN_COMPONENT_HTML_V121.count('function emitBounds(){if(!state.map)return;')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function emitBounds(){if(!state.map)return;', 'function emitBounds(){if(!state.map||state.searchPendingTicket)return;',1)
# On exact polygon render, remove provisional bbox and release viewport messages.
old="       state.focusLayer=focus.addTo(state.map);\n       state.lastJumpToken=String(target.id);"
new="""       state.focusLayer=focus.addTo(state.map);
       if(state.provisionalFocus){state.map.removeLayer(state.provisionalFocus);state.provisionalFocus=null;}
       state.searchPendingTicket=null;
       setTownStatus('「'+String(target.label||'地域')+'」へ移動し、町域の外周を表示しました。');
       state.lastJumpToken=String(target.id);"""
assert TOWN_COMPONENT_HTML_V121.count(old)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(old,new,1)

# v185 direct client-side, persistent geography index.
_V185_LOCAL_SEARCH_JS = r'''
// v185: client-owned town lookup.  The 2015 e-Stat area source is fetched
// once, reduced to Tokyo's 23 wards and stored in IndexedDB.  On next page
// open and after app.py deployments, a local index drives instant lookup,
// exact geometry highlights and fitBounds without a Streamlit round trip.
// This is deliberately independent of the rent aggregation worker.
var _v185_oldInit=initTownSearch;
var _v185_oldSubmit=submitTownQuery;
var _v185_oldSelect=townSendSelection;
function v185Db(){
 return new Promise(function(resolve,reject){
   if(!window.indexedDB){reject(new Error('IndexedDB unsupported'));return;}
   var req=indexedDB.open('sumai_town_2015_cache',1);
   req.onupgradeneeded=function(){if(!req.result.objectStoreNames.contains('geo'))req.result.createObjectStore('geo')};
   req.onsuccess=function(){resolve(req.result)};
   req.onerror=function(){reject(req.error||new Error('IndexedDB open failed'))};
   req.onblocked=function(){reject(new Error('IndexedDB blocked'))};
 });
}
async function v185ReadCache(){
 var db=await v185Db();
 try{return await new Promise(function(resolve,reject){
   var req=db.transaction('geo','readonly').objectStore('geo').get('tokyo-23-2015-v1');
   req.onsuccess=function(){resolve(req.result||null)};
   req.onerror=function(){reject(req.error||new Error('IndexedDB read failed'))};
 });}finally{db.close()}
}
async function v185WriteCache(rows){
 var db=await v185Db();
 try{await new Promise(function(resolve,reject){
   var tx=db.transaction('geo','readwrite');
   tx.objectStore('geo').put(rows,'tokyo-23-2015-v1');
   tx.oncomplete=resolve;
   tx.onerror=function(){reject(tx.error||new Error('IndexedDB write failed'))};
   tx.onabort=function(){reject(tx.error||new Error('IndexedDB write aborted'))};
 });}finally{db.close()}
}
function v185Bounds(geometry){
 var all=[];
 function walk(value){
   if(!Array.isArray(value))return;
   if(value.length>=2&&typeof value[0]==='number'&&typeof value[1]==='number'){
     var x=value[0],y=value[1];
     if(Number.isFinite(x)&&Number.isFinite(y)&&x>=138&&x<=141&&y>=34&&y<=37)all.push([x,y]);
   }else value.forEach(walk);
 }
 walk(geometry&&geometry.coordinates);
 if(!all.length)return null;
 var s=90,w=180,n=-90,e=-180;
 all.forEach(function(p){s=Math.min(s,p[1]);w=Math.min(w,p[0]);n=Math.max(n,p[1]);e=Math.max(e,p[0])});
 return s<n&&w<e?[s,w,n,e]:null;
}
function v185NormalizeSource(doc){
 if(!doc||!Array.isArray(doc.features))throw new Error('境界データ形式に問題があります');
 var allowed=new Set(state.searchDistricts||[]);
 var result=[];
 doc.features.forEach(function(feature){
   var fullname=String((feature.properties||{}).fullname||'').split('/');
   var ward=fullname[1]||'',town=normalizeTownQuery(fullname[2]||'');
   if(!allowed.has(ward)||!town||!feature.geometry)return;
   result.push({w:ward,t:town,g:feature.geometry});
 });
 if(result.length<2000)throw new Error('東京23区の境界データが不足しています');
 return result;
}
function v185Install(rows){
 if(!Array.isArray(rows)||rows.length<100)throw new Error('境界索引が不正です');
 var catalog=[],geo=new Map(),wardBounds=new Map(),valid=0;
 rows.forEach(function(f){
   if(!f||!f.w||!f.t||!f.g)return;
   var b=v185Bounds(f.g);
   if(!b)return;
   var base=f.t.replace(/[0-9]{1,2}丁目$/,'');
   catalog.push({ward:f.w,town:f.t,base:base,bounds:b});
   var id=f.w+'|'+f.t;
   if(!geo.has(id))geo.set(id,[]);
   geo.get(id).push(f.g);
   var old=wardBounds.get(f.w);
   wardBounds.set(f.w,old?[Math.min(old[0],b[0]),Math.min(old[1],b[1]),Math.max(old[2],b[2]),Math.max(old[3],b[3])]:b.slice());
   valid++;
 });
 if(valid<100)throw new Error('町丁目境界が見つかりません');
 state._v185Geometry=geo;
 state._v185Loaded=true;
 state._v185Loading=false;
 state.searchCatalog=catalog;
 state.searchWardBounds=Array.from(wardBounds,function(p){return {ward:p[0],bounds:p[1]}});
 if(state._v185DeferredQuery!==null&&state._v185DeferredQuery!==undefined){
   var pending=state._v185DeferredQuery;
   state._v185DeferredQuery=null;
   var q=document.getElementById('town-query');
   if(q){q.value=pending;setTimeout(function(){submitTownQuery()},0)}
 }else if(document.getElementById('town-search-status').textContent.includes('準備中')){
   setTownStatus('町丁目境界を読み込みました。検索できます。');
 }
}
async function v185FetchSource(url,ms){
 var ac=new AbortController(),timer=setTimeout(function(){ac.abort()},ms);
 try{
   var res=await fetch(url,{signal:ac.signal,cache:'force-cache'});
   if(!res.ok)throw new Error('HTTP '+res.status);
   return v185NormalizeSource(await res.json());
 }finally{clearTimeout(timer)}
}
async function v185Init(){
 if(state._v185Started)return;
 state._v185Started=true;
 state._v185Loading=true;
 state._v185DeferredQuery=null;
 try{
   var saved=null;
   try{saved=await v185ReadCache()}catch(err){state._v185CacheWarning=String(err)}
   if(saved&&saved.length>=2000){v185Install(saved);return;}
   // Initial pre-registration, only when browser has no dataset.
   var urls=['https://frogcat.github.io/japan-small-area/13.json',
             'https://raw.githubusercontent.com/frogcat/japan-small-area/master/docs/13.json'];
   var lastError='';
   for(var i=0;i<urls.length;i++){
     try{
       var items=await v185FetchSource(urls[i],i===0?9500:10500);
       v185Install(items);
       try{await v185WriteCache(items)}catch(err){state._v185CacheWarning=String(err)}
       return;
     }catch(err){lastError=String(err&&err.message||err)}
   }
   throw new Error('町丁目境界の初回取得に失敗しました（'+lastError+'）。ネットワークを確認してください。');
 }catch(err){
   state._v185Loading=false;
   state._v185Failed=String(err&&err.message||err);
   if(state._v185DeferredQuery!==null){
     var pending=state._v185DeferredQuery;state._v185DeferredQuery=null;
     if((state.searchCatalog||[]).length){
       var input=document.getElementById('town-query');if(input)input.value=pending;
       setTimeout(function(){_v185_oldSubmit()},0);
     }else setTownStatus(state._v185Failed);
   }
 }
}
// v186: a calm unified silhouette, rather than thick dashed strokes on every chome.
// An SVG filter sees the COMBINED alpha of all selected polygons, so common
// internal chome borders do not receive an outline.  No external JS library.
function v186SimpleFocus(geo){
 return L.geoJSON(geo,{pane:'townSearchFocusPane',interactive:false,smoothFactor:.7,
   style:function(){return {color:'#195d72',weight:2,opacity:.88,
     fillColor:'#69adb8',fillOpacity:.12,dashArray:null,lineCap:'round',lineJoin:'round'};}});
}
function v186MakeFocus(geo){
 if(!L.svg||typeof SVGFilterElement==='undefined')return v186SimpleFocus(geo);
 var renderer=state._v186FocusRenderer;
 if(!renderer){renderer=L.svg({pane:'townSearchFocusPane',padding:.25});state._v186FocusRenderer=renderer;}
 var layer=L.geoJSON(geo,{renderer:renderer,pane:'townSearchFocusPane',interactive:false,
   smoothFactor:.6,style:function(){return {stroke:false,weight:0,
     fillColor:'#17576d',fillOpacity:1,fillRule:'evenodd'};}});
 layer._v186Renderer=renderer;
 return layer;
}
function v186UseUnifiedStyle(layer){
 // Called AFTER the layer is added; Leaflet only then constructs the SVG.
 var renderer=layer&&layer._v186Renderer;
 if(!renderer||!renderer._container||!renderer._rootGroup)return false;
 var svg=renderer._container,root=renderer._rootGroup;
 if(root.getAttribute('data-v186-focus-style')==='1')return true;
 var ns='http://www.w3.org/2000/svg';
 state._v186FocusSerial=(state._v186FocusSerial||0)+1;
 var id='sumai-town-focus-v186-'+String(state._v186FocusSerial);
 function svgTag(tag,attributes,parent){
   var el=document.createElementNS(ns,tag);
   Object.keys(attributes||{}).forEach(function(name){el.setAttribute(name,String(attributes[name]))});
   if(parent)parent.appendChild(el);
   return el;
 }
 var defs=svgTag('defs',{},null);
 var f=svgTag('filter',{id:id,x:'-15%',y:'-20%',width:'130%',height:'140%',
   'color-interpolation-filters':'sRGB'},defs);
 svgTag('feMorphology',{'in':'SourceAlpha',operator:'dilate',radius:'4',result:'wide'},f);
 svgTag('feComposite',{'in':'wide',in2:'SourceAlpha',operator:'out',result:'haloMask'},f);
 svgTag('feFlood',{'flood-color':'#ffffff','flood-opacity':'.9',result:'haloColor'},f);
 svgTag('feComposite',{'in':'haloColor',in2:'haloMask',operator:'in',result:'halo'},f);
 svgTag('feMorphology',{'in':'SourceAlpha',operator:'dilate',radius:'2',result:'thin'},f);
 svgTag('feComposite',{'in':'thin',in2:'SourceAlpha',operator:'out',result:'lineMask'},f);
 svgTag('feFlood',{'flood-color':'#15566e','flood-opacity':'.94',result:'lineColor'},f);
 svgTag('feComposite',{'in':'lineColor',in2:'lineMask',operator:'in',result:'line'},f);
 svgTag('feFlood',{'flood-color':'#3a9ca8','flood-opacity':'.11',result:'tintColor'},f);
 svgTag('feComposite',{'in':'tintColor',in2:'SourceAlpha',operator:'in',result:'fill'},f);
 var merge=svgTag('feMerge',{},f);
 ['halo','fill','line'].forEach(function(key){svgTag('feMergeNode',{'in':key},merge)});
 svg.insertBefore(defs,svg.firstChild);
 root.setAttribute('filter','url(#'+id+')');
 root.setAttribute('pointer-events','none');
 root.setAttribute('data-v186-focus-style','1');
 return true;
}
function v185TownFocus(place){
 if(!state._v185Loaded||!state.map)return false;
 var selected=[],features=[];
 var wk=String(place.ward||''),nm=String(place.name||'');
 (state.searchCatalog||[]).forEach(function(area){
   if(area.ward!==wk)return;
   if(place.kind==='ward'||(place.kind==='town'?area.base===nm:area.town===nm)){
     selected.push(area);
     (state._v185Geometry.get(area.ward+'|'+area.town)||[]).forEach(function(g){
       features.push({type:'Feature',properties:{name:area.ward+area.town},geometry:g});
     });
   }
 });
 if(!features.length)return false;
 var south=90,west=180,north=-90,east=-180;
 selected.forEach(function(area){var b=area.bounds;
   south=Math.min(south,b[0]);west=Math.min(west,b[1]);north=Math.max(north,b[2]);east=Math.max(east,b[3]);
 });
 try{
   var geometry={type:'FeatureCollection',features:features};
   var outline=v186MakeFocus(geometry);
   if(!outline.getLayers().length)return false;
   if(state.focusLayer)state.map.removeLayer(state.focusLayer);
   if(state.provisionalFocus){state.map.removeLayer(state.provisionalFocus);state.provisionalFocus=null}
   state.focusLayer=outline.addTo(state.map);
   if(outline._v186Renderer){
     try{
       if(!v186UseUnifiedStyle(outline))throw new Error('SVG renderer unavailable');
     }catch(styleError){
       // Older browsers still get a legible solid, translucent border.
       state.map.removeLayer(outline);
       outline=v186SimpleFocus(geometry);
       state.focusLayer=outline.addTo(state.map);
     }
   }
   state._v185FocusActive=true;
   state.lastJumpToken='local-'+String(Date.now());
   state.sentBounds='';
   state.map.invalidateSize();
   state.map.fitBounds([[south,west],[north,east]],{padding:[24,24],maxZoom:16,animate:false});
   setTownStatus('「'+place.label+'」に移動し、町域の外周を表示しました。');
   setTimeout(function(){emitBounds()},80);
   return true;
 }catch(err){setTownStatus('輪郭表示エラー：'+String(err&&err.message||err));return false}
}
initTownSearch=function(){_v185_oldInit();v185Init()};
submitTownQuery=function(){
 if(!state._v185Loaded&&state._v185Loading){
   var node=document.getElementById('town-query');
   state._v185DeferredQuery=String(node&&node.value||'');
   setTownStatus('初回のみ町丁目境界を登録中です。完了後、自動的に移動します。');
   return;
 }
 if(!state._v185Loaded&&state._v185Failed&&!(state.searchCatalog||[]).length){
   var retryInput=document.getElementById('town-query');
   var retryQuery=String(retryInput&&retryInput.value||'');
   state._v185Started=false;state._v185Failed=null;
   v185Init();state._v185DeferredQuery=retryQuery;
   setTownStatus('町丁目境界の登録を再試行しています。完了後、自動的に移動します。');
   return;
 }
 _v185_oldSubmit();
};
townSendSelection=function(place){
 if(state._v185Loaded){
   if(!v185TownFocus(place))setTownStatus('指定地域の輪郭が見つかりません。');
   return;
 }
 _v185_oldSelect(place);
};
'''
assert TOWN_COMPONENT_HTML_V121.count('function render(args){')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){', _V185_LOCAL_SEARCH_JS+'\nfunction render(args){',1)
# Later fragment responses must not erase the self-contained browser lookup.
needle=" state.searchCatalog=Array.isArray(args.search_catalog)?args.search_catalog:[];"
assert TOWN_COMPONENT_HTML_V121.count(needle)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(needle," if(!state._v185Loaded)state.searchCatalog=Array.isArray(args.search_catalog)?args.search_catalog:[];",1)
# A stale server jump must not override a successful instant browser search.
needle="if(target && target.id && Array.isArray(target.bounds)"
assert TOWN_COMPONENT_HTML_V121.count(needle)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(needle,"if(!state._v185FocusActive && target && target.id && Array.isArray(target.bounds)",1)

# v187: keep a wider neighborhood in view after a town/chome lookup.
# The region still fits within the viewport; large wards can automatically use
# a wider zoom. Apply to all three client-side search navigation paths (normal,
# provisional/fallback, and a delayed server jump). Manual +/- zoom is unchanged.
_V187_SEARCH_FIT_OLD = 'maxZoom:16,animate:false'
assert TOWN_COMPONENT_HTML_V121.count(_V187_SEARCH_FIT_OLD) == 3
TOWN_COMPONENT_HTML_V121 = TOWN_COMPONENT_HTML_V121.replace(
    _V187_SEARCH_FIT_OLD,'maxZoom:14,animate:false')
assert TOWN_COMPONENT_HTML_V121.count('maxZoom:14,animate:false') == 3

# v189: SUUMO follows the resolved search selection, never the visible map bbox.
_V189_SEARCH_CSS = """
#town-search-actions{display:flex;align-items:stretch;gap:9px;margin-top:9px}
#town-search-actions #town-submit{flex:1;min-width:0;width:auto;margin:0}
#town-suumo{display:flex;align-items:center;justify-content:center;flex:0 0 auto;box-sizing:border-box;min-height:40px;padding:7px 14px;border:1px solid #16695e;border-radius:7px;background:#e8f3ed;color:#173a5e;font-size:14px;font-weight:750;text-decoration:none;touch-action:manipulation}
#town-suumo[hidden]{display:none!important}
#town-suumo[aria-disabled="true"]{opacity:.65;cursor:wait}
#town-suumo-note{font-size:12px;line-height:1.4;color:#49616b;margin-top:6px}
#town-suumo-note:empty{display:none}
"""
_V189_SEARCH_JS = r"""
function v189SelectionKey(place){return String(place.ward||'')+'|'+String(place.name||'')+'|'+String(place.kind||'')}
function v189UpdateSuumo(){
 var place=state._v189Place,link=document.getElementById('town-suumo'),hint=document.getElementById('town-suumo-note');
 if(!link)return;
 link.hidden=!place;
 if(!place)return;
 var wardUrl=(state._v189WardUrls||{})[place.ward],url='',pending=false,error='';
 if(wardUrl){
   if(place.kind==='ward')url=wardUrl;
   else{
     var base=normalizeTownQuery(place.name).replace(/[0-9]{1,2}丁目$/,'');
     var oz=(state._v189Oz||{})[place.ward+'|'+base];
     var city=(state._v189WardCodes||{})[place.ward];
     if(city&&typeof oz==='string'&&new RegExp('^'+city+'[0-9]{3}$').test(oz)){
       var target=new URL(wardUrl);target.searchParams.set('oz',oz);url=target.href;
     }
   }
 }
 var reply=state._v189Reply;
 if(!url&&reply&&reply.key===v189SelectionKey(place))error=reply.error||'';
 pending=!url&&!error;
 link.textContent=pending?'SUUMO 準備中':(error?'SUUMO 再試行':'SUUMO');
 if(url){link.href=url;link.removeAttribute('aria-disabled')}
 else{link.removeAttribute('href');link.setAttribute('aria-disabled','true')}
 link.title=place.label+'の物件をSUUMOで表示';
 hint.textContent=error|| (pending?'SUUMOの町名を確認しています。':(place.kind==='chome'?'SUUMOでは丁目を含む町全体を表示します。':''));
 setHeight();
}
function v189RequestSuumo(){
 var place=state._v189Place;
 if(!place)return;
 var ticket=String(Date.now())+'_'+Math.random().toString(36).slice(2,11);
 state._v189Reply=null;state._v189Pending=true;
 hostMessage('streamlit:setComponentValue',{dataType:'json',value:{event:'town_suumo_v189',ticket:ticket,ward:place.ward,name:place.name,kind:place.kind}});
}
var _v189OldSelect=townSendSelection;
townSendSelection=function(place){
 var before=state.lastJumpToken,oldTicket=state.searchPendingTicket;
 _v189OldSelect(place);
 if(state.lastJumpToken!==before||state.searchPendingTicket!==oldTicket){
   state._v189Place={ward:place.ward,name:place.name,kind:place.kind,label:place.label};
   state._v189Reply=null;state._v189Pending=false;v189UpdateSuumo();
   // Local polygon search has already completed without a server event.
   // Unknown SUUMO codes need one selected-ward lookup, not a bbox search.
   if(state._v185Loaded&&!document.getElementById('town-suumo').hasAttribute('href'))v189RequestSuumo();
 }
};
var _v189OldInit=initTownSearch;
initTownSearch=function(){
 _v189OldInit();
 var link=document.getElementById('town-suumo');
 if(!link||link.dataset.ready)return;
 link.dataset.ready='1';
 link.addEventListener('click',function(e){
   if(!link.hasAttribute('href')){e.preventDefault();if(!state._v189Pending){v189RequestSuumo();v189UpdateSuumo()}}
 });
};
"""
_v189_action='<button type="button" id="town-submit">検索した地域へ移動</button>'
assert TOWN_COMPONENT_HTML_V121.count(_v189_action)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v189_action,
    '<div id="town-search-actions">'+_v189_action+'<a id="town-suumo" target="_blank" rel="noopener noreferrer" hidden>SUUMO</a></div><div id="town-suumo-note" role="status"></div>',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V189_SEARCH_CSS+'</style></head>',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V189_SEARCH_JS+'\nfunction render(args){',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('if(!state.map||state.searchPendingTicket)return;', 'if(!state.map||state.searchPendingTicket||state._v189Pending)return;',1)
_v189_render=' initTownSearch();'
assert TOWN_COMPONENT_HTML_V121.count(_v189_render)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v189_render,
    " state._v189WardUrls=args.suumo_ward_urls||{}; state._v189WardCodes=args.suumo_ward_codes||{};\n"
    " state._v189Oz=args.suumo_town_codes||{}; state._v189Reply=args.suumo_search_reply||null;\n"
    " if(state._v189Place && state._v189Reply && state._v189Reply.key===v189SelectionKey(state._v189Place)){state._v189Pending=false;}\n"
    " v189UpdateSuumo();\n"+_v189_render,1)


def _v189_resolve_suumo_selection(state,data,group):
    """Resolve only the selected ward/town; failed town lookup never expands scope."""
    ward=str(data.get('ward') or '')[:30]
    name=str(data.get('name') or '')[:90]
    kind=str(data.get('kind') or '')
    key=ward+'|'+name+'|'+kind
    reply={'key':key,'error':'','ticket':str(data.get('ticket') or '')[:100],'url':''}
    city=_V167_WARD_TO_CODE.get(ward)
    if not city or kind not in ('ward','town','chome'):
        reply['error']='SUUMOの検索地域を確認できません。地名を検索し直してください。'
        return reply
    if kind=='ward':
        reply['url']=_v189_suumo_search_url(city,group);return reply
    town=town_base_name(name)
    if not town or not re.fullmatch(r'[\w\u3040-\u30ff\u3400-\u9fff\u3005\u30fc\-]+',town):
        reply['error']='SUUMOの町名を確認できません。'
        return reply
    codes=_v167_public_oz_codes(state.get('ward_town_cache'))
    codes.update(_v168_town_oz_registry().snapshot())
    reply['url']=_v167_suumo_town_search(ward+'|'+name,group,codes)
    if reply['url']:return reply
    try:
        web=PublicWeb();web.configure(rental_network_settings())
        audit=state.get('_v217_suumo_audit')
        if audit is None:
            audit=AuditLog({'mode':'suumo_town'},_v209_log_secrets());state['_v217_suumo_audit']=audit
        web.audit=audit
        trace(web,'suumo_popup_lookup_start',{'ward':ward,'town':town,'ticket':reply['ticket']},stage='suumo.town_lookup')
        regions,munis,_=suumo_ward_regions(web,city,include_municipalities=False)
        state.setdefault('ward_town_cache',{})[city]={'regions':regions,'munis':munis}
        codes=_v167_public_oz_codes(state.get('ward_town_cache'))
        reply['url']=_v167_suumo_town_search(ward+'|'+name,group,codes)
        trace(web,'suumo_popup_lookup_result',{'ward':ward,'town':town,'url':reply['url'],'towns':len(codes)},stage='suumo.town_lookup')
        if not reply['url']:
            reply['error']='SUUMOで同じ町名を確認できませんでした。別の地域には変更しません。'
    except Exception as exc:
        if state.get('_v217_suumo_audit'):
            state['_v217_suumo_audit'].add('suumo.town_lookup','suumo_popup_lookup_failed','ERROR',{'ward':ward,'town':town,'exception_type':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc(),'diagnostic':getattr(exc,'diagnostic',{})})
        reply['error']='SUUMOの町名確認に失敗しました。SUUMOボタンで再試行できます。'
    return reply


# v202: home/layout view and town-search view have different specified scales.
assert TOWN_COMPONENT_HTML_V121.count('maxZoom:14,animate:false')==3
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('maxZoom:14,animate:false','maxZoom:13,animate:false')
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('padding:[24,24],maxZoom:13','padding:[38,38],maxZoom:13').replace('padding:[25,25],maxZoom:13','padding:[38,38],maxZoom:13')
_V202_HOME_VIEW_JS = r"""
function v202HomeView(args){
 var epoch=Number(args.home_view_epoch)||0;
 if(state._v202HomeEpoch===undefined){state._v202HomeEpoch=epoch;return;}
 if(epoch===state._v202HomeEpoch)return;
 state._v202HomeEpoch=epoch;
 state._v189Place=null;state._v189Pending=false;state._v189Reply=null;
 state._v217Active=null;state._v217Pending=null;state._v217Reply=null;
 state._v185FocusActive=false;state.lastJumpToken=null;state.searchPendingTicket=null;
 if(state.focusLayer){state.map.removeLayer(state.focusLayer);state.focusLayer=null;}
 if(state.provisionalFocus){state.map.removeLayer(state.provisionalFocus);state.provisionalFocus=null;}
 state.sentBounds='';setTownStatus('');
 var center=Array.isArray(args.home_center)?args.home_center:[35.669,139.757];
 state.map.setView(center,11,{animate:false});
 v189UpdateSuumo();
 setTimeout(emitBounds,80);
}
"""
assert TOWN_COMPONENT_HTML_V121.count('function render(args){')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V202_HOME_VIEW_JS+'\nfunction render(args){',1)
assert TOWN_COMPONENT_HTML_V121.count(' state._v189WardUrls=args.suumo_ward_urls||{};')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(' state._v189WardUrls=args.suumo_ward_urls||{};',
    ' v202HomeView(args);\n state._v189WardUrls=args.suumo_ward_urls||{};',1)
_v202_message='zoom:state.map.getZoom(),jump_applied:state.lastJumpToken||null'
assert TOWN_COMPONENT_HTML_V121.count(_v202_message)==2
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v202_message,
    _v202_message+',home_view_epoch:state._v202HomeEpoch')
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('kind:place.kind}});', 'kind:place.kind,home_view_epoch:state._v202HomeEpoch}});',1)


def _v202_reset_home(state):
    state['_v202_home_epoch']=int(state.get('_v202_home_epoch',0))+1
    state['new_bounds']=None
    state['new_view_center']=_V202_HOME_CENTER
    state['new_view_zoom']=_V202_HOME_ZOOM
    for key in ('new_map','_v182_map_jump','_v182_map_jump_confirmed','_v184_search_error',
                '_v189_suumo_reply','_v118_choropleth_payload'):
        state.pop(key,None)


def _v202_choose_layout(state,group):
    if group not in ('group0','group1','group2','house'):return False
    if state.get('layout_display_group')==group:return False
    state['layout_display_group']=group
    _v202_reset_home(state)
    return True


def _v202_view_zoom(value,default=11):
    try:
        zoom=float(value)
        return zoom if math.isfinite(zoom) and 1<=zoom<=20 else default
    except (ValueError,TypeError,OverflowError):return default


# v204: directions use the exact tapped point, never a town's geometric center.
_V204_NAV_JS = r"""
function v204DirectionsUrl(point){
 if(!point||typeof point.lat!=='number'||typeof point.lng!=='number'||
    !Number.isFinite(point.lat)||!Number.isFinite(point.lng)||
    point.lat < -90||point.lat > 90||point.lng < -180||point.lng > 180)return '';
 var url=new URL('https://www.google.com/maps/dir/');
 url.searchParams.set('api','1');
 url.searchParams.set('destination',point.lat.toFixed(6)+','+point.lng.toFixed(6));
 // Omitting origin lets Google Maps use the device's current location.
 // Match Burari: show walking-route confirmation; the user starts navigation.
 url.searchParams.set('travelmode','walking');
 return url.href;
}
"""
_V204_POPUP_NEW=_V167_POPUP_NEW.replace(
    "var suumo=String(props.suumo_url||'');",
    "var actions=document.createElement('div');actions.className='v204-popup-actions';el.appendChild(actions);\nvar suumo=String(props.suumo_url||'');",1).replace(
    "  el.appendChild(document.createElement('div'));\n  el.appendChild(link);",
    "  actions.appendChild(link);",1)
_V204_ROUTE_LINK = r"""
var route=document.createElement('a');
route.className='v167-suumo-link v204-route-link';
route.textContent='経路案内 ↗';route.target='_blank';route.rel='noopener noreferrer';
route.title='現在地からタップした場所までGoogleマップで案内';
route.setAttribute('aria-label','現在地からタップした場所までGoogleマップで経路案内');
route.setAttribute('aria-disabled','true');
actions.appendChild(route);
route.addEventListener('click',function(event){if(!route.hasAttribute('href'))event.preventDefault();});
// Register before bindPopup: update the URL before Leaflet opens the popup.
layer.on('click',function(event){
 var url=v204DirectionsUrl(event&&event.latlng);
 if(url){route.href=url;route.removeAttribute('aria-disabled');}
 else{route.removeAttribute('href');route.setAttribute('aria-disabled','true');}
});
layer.bindPopup(el);
"""
assert _V204_POPUP_NEW!=_V167_POPUP_NEW
assert _V204_POPUP_NEW.count('actions.appendChild(link);')==1
_V204_POPUP_NEW=_V204_POPUP_NEW.replace('layer.bindPopup(el);',_V204_ROUTE_LINK,1)
assert TOWN_COMPONENT_HTML_V121.count(_V167_POPUP_NEW)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_V167_POPUP_NEW,_V204_POPUP_NEW,1)
assert TOWN_COMPONENT_HTML_V121.count('function render(args){')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V204_NAV_JS+'\nfunction render(args){',1)
_V204_NAV_CSS="""
.v204-popup-actions{display:flex;flex-wrap:wrap;align-items:stretch;gap:7px;margin-top:8px}
.v204-popup-actions .v167-suumo-link{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;margin:0;min-height:40px;padding:8px 10px;white-space:nowrap}
.v204-route-link[aria-disabled="true"]{opacity:.55;cursor:default}
"""
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V204_NAV_CSS+'</style></head>',1)


# v205: personal named landmarks, independent of rental observations.
_V205_PIN_DOM = """<section id="v205-pins-panel" aria-label="目印ピン"><div class="v223-pin-columns"><div><label for="v205-pin-address">施設名・目印の名前で検索</label><input id="v205-pin-address" maxlength="180" placeholder="例：ヨドバシAKIBA" autocomplete="off"/></div><div id="v205-pin-saved"><label for="v205-pin-list">保存した目印</label><select id="v205-pin-list"></select><button type="button" id="v205-pin-jump">選んだピンへ移動</button></div></div><div id="v223-pin-editor" hidden><label for="v205-pin-name">ピンの名前（検索結果から自動入力・変更可）</label><input id="v205-pin-name" maxlength="60" placeholder="検索後に施設名が入ります"/><div class="v205-actions"><button type="button" id="v205-pin-search">名前で検索</button><button type="button" id="v205-pin-pick">地図をタップして指定</button><button type="button" id="v205-pin-cancel" hidden>仮ピンを取り消す</button></div></div><div id="v205-pin-candidates" hidden><label id="v206-result-label" for="v205-pin-candidate">検索結果</label><select id="v205-pin-candidate"></select><div id="v206-result-detail" role="status"></div><button type="button" id="v205-pin-add">確認して確定</button></div><div id="v205-pin-status" role="status" aria-live="polite"></div><p class="v205-help">施設名から住所と位置を検索し、仮ピンを確認してから確定します。保存先はこのブラウザーです。情報：国土地理院・<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>。</p></section>"""

_V205_PIN_CSS = """
#v205-pins-panel{padding:12px 13px;background:#fff;border-top:1px solid #d5dfdc;color:#173a5e;font:14px system-ui}
.v223-pin-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}
.v223-pin-columns>div{min-width:0}
.v223-pin-columns label{min-height:2.7em;display:flex!important;align-items:end}
#v223-pin-editor[hidden]{display:none!important}
#v205-pins-panel label{display:block;font-size:13px;font-weight:650;margin:10px 0 5px}
#v205-pins-panel input,#v205-pins-panel select{box-sizing:border-box;width:100%;min-height:40px;border:1px solid #b9c7c3;border-radius:7px;background:#fff;color:#173a5e;padding:7px 9px;font-size:16px}
#v205-pins-panel button{min-height:40px;padding:7px 10px;margin-top:8px;border:1px solid #a5b8b0;border-radius:7px;background:#eef5f1;color:#173a5e;font-size:13px;cursor:pointer;touch-action:manipulation}
#v205-pins-panel [hidden]{display:none!important}
.v205-actions{display:flex;flex-wrap:wrap;gap:7px}
#v206-result-detail{font-size:12px;line-height:1.45;color:#49616b;margin-top:7px;white-space:pre-wrap}
#v205-pin-status,.v205-help{font-size:12px;line-height:1.45;margin:7px 0 0;color:#49616b}
.v206-preview .v205-pin-dot{background:#277c91;border-style:dashed}
.v205-pin-icon{background:none;border:0}
.v205-pin-dot{display:block;width:18px;height:18px;background:#a52c40;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 1px 4px #555}
.leaflet-tooltip.v205-pin-label{color:#173a5e;background:rgba(255,255,255,.94);border:1px solid #b8c2c8;border-radius:3px;font-weight:600;font-size:10px;line-height:1.2;padding:2px 4px;white-space:nowrap!important;writing-mode:horizontal-tb!important;width:max-content!important;min-width:0!important;max-width:none!important;pointer-events:none}
@media(max-width:480px){.leaflet-tooltip.v205-pin-label{display:none!important}}
.v205-pin-popup-title{font-weight:750;color:#173a5e;margin-bottom:5px}
.v205-pin-popup-address{font-size:12px;color:#49616b;overflow-wrap:anywhere}

.v215-pin-popup .leaflet-popup-content{margin:12px!important;min-width:0!important}
.v215-pin-menu{box-sizing:border-box;width:100%;min-width:0;display:flex;flex-direction:column;gap:8px;color:#173a5e;font:13px/1.45 system-ui;white-space:normal;overflow-wrap:anywhere}
.v215-pin-menu p{margin:0;white-space:normal}
.v215-pin-menu .v205-pin-popup-title{font-size:13px;line-height:1.4;margin:0;white-space:normal}
.v215-pin-menu button{box-sizing:border-box;display:block;flex:none;width:100%;min-height:40px;padding:8px 10px;margin:0;border:1px solid #9eb4bd;border-radius:6px;background:#edf4f7;color:#173a5e;font-size:13px;line-height:1.3;white-space:normal;cursor:pointer;touch-action:manipulation}
.v215-pin-menu button.v205-pin-delete{color:#a52c40;border-color:#ba7180;background:#fff}
.v215-pin-menu .v204-popup-actions{display:flex;flex-wrap:wrap;gap:6px}
.v215-pin-menu .v218-pin-actions{display:flex;flex-wrap:nowrap;align-items:stretch;gap:6px}
.v215-pin-menu .v218-pin-actions>a,.v215-pin-menu .v218-pin-actions>button{
 box-sizing:border-box;flex:1 1 0;width:auto;min-width:0;min-height:40px;margin:0;padding:8px 6px;
 display:flex;align-items:center;justify-content:center;text-align:center;font-size:13px;line-height:1.35;white-space:normal;overflow-wrap:anywhere}
.v215-pin-menu .v215-minute-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0;font-size:13px;white-space:nowrap}
.v215-pin-menu input[type=number]{box-sizing:border-box;flex:0 0 84px;width:84px;min-width:0;min-height:40px;padding:6px;border:1px solid #9eb4bd;border-radius:6px;background:#fff;color:#173a5e;font-size:16px}
.leaflet-tooltip.v205-pin-label,.leaflet-tooltip.v205-pin-label span{white-space:nowrap!important;writing-mode:horizontal-tb!important;word-break:keep-all!important}

.v205-pin-delete{min-height:40px;padding:7px 10px;color:#a52c40;background:#fff;border:1px solid #a52c40;border-radius:6px;cursor:pointer;font-size:13px}
"""
_V205_PIN_JS = r"""
var v205StorageKey='sumai.personal-landmarks.v205';
function v205Status(text){var el=document.getElementById('v205-pin-status');if(el)el.textContent=text||'';setHeight()}
function v205Point(lat,lng){return typeof lat==='number'&&typeof lng==='number'&&Number.isFinite(lat)&&Number.isFinite(lng)&&lat>=34&&lat<=37&&lng>=138&&lng<=141}
function v205CleanPin(pin){
 if(!pin||typeof pin.id!=='string'||!pin.id||pin.id.length>100||typeof pin.name!=='string'||!pin.name.trim()||pin.name.length>60||!v205Point(pin.lat,pin.lng))return null;
 var clean={id:pin.id,name:pin.name.trim(),address:String(pin.address||'').slice(0,180),lat:pin.lat,lng:pin.lng};
 if(typeof pin.walk_minutes==='number'&&Number.isFinite(pin.walk_minutes)&&pin.walk_minutes>=1&&Number.isFinite(pin.walk_minutes*80))clean.walk_minutes=pin.walk_minutes;
 return clean;
}
function v205LoadPins(){
 try{
  var raw=localStorage.getItem(v205StorageKey);
  if(!raw)return [];
  var data=JSON.parse(raw);
  if(!data||data.schema!==1||!Array.isArray(data.pins))throw new Error('invalid pin data');
  var used=new Set();return data.pins.map(v205CleanPin).filter(function(pin){if(!pin||used.has(pin.id))return false;used.add(pin.id);return true});
 }catch(e){v205Status('保存したピンを読み込めません。このブラウザーの保存設定を確認してください。');return []}
}
function v205SavePins(pins){
 var clean=pins.map(v205CleanPin);
 if(clean.some(function(pin){return !pin}))return false;
 try{localStorage.setItem(v205StorageKey,JSON.stringify({schema:1,pins:clean}));}
 catch(e){v205Status('ピンを保存できません。ブラウザーの保存設定・空き容量を確認してください。');return false;}
 state._v205Pins=clean;v205DrawPins();v205PinList();return true;
}
function v205PinList(){
 var box=document.getElementById('v205-pin-saved'),select=document.getElementById('v205-pin-list');
 if(!box||!select)return;
 var old=select.value;select.replaceChildren();
 (state._v205Pins||[]).forEach(function(pin){var opt=document.createElement('option');opt.value=pin.id;opt.textContent=pin.name;select.appendChild(opt)});
 box.hidden=false;
 var empty=!(state._v205Pins||[]).length;select.disabled=empty;document.getElementById('v205-pin-jump').disabled=empty;
 if(empty){var placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='保存した目印はありません';select.appendChild(placeholder);}
 if((state._v205Pins||[]).some(function(p){return p.id===old}))select.value=old;
 setHeight();
}
function v215PopupOptions(){
 var element=state.map&&state.map.getContainer&&state.map.getContainer();
 var width=Math.max(120,Math.min(292,((element&&element.clientWidth)||360)-60));
 return {minWidth:width,maxWidth:width,className:'v215-pin-popup',autoPanPadding:[14,14]};
}
function v205DrawPins(){
 if(!state.map||!window.L)return;
 var pins=state._v205Pins||[],signature=JSON.stringify(pins);v214SyncRings(pins);
 if(state._v205Layer&&state._v205PinSignature===signature)return;
 if(!state.map.getPane('personalPinPane')){state.map.createPane('personalPinPane');state.map.getPane('personalPinPane').style.zIndex='690';}
 if(!state._v205Layer)state._v205Layer=L.layerGroup().addTo(state.map);
 state._v205Layer.clearLayers();state._v205Markers={};state._v205PinSignature=signature;
 pins.forEach(function(pin){
  var marker=L.marker([pin.lat,pin.lng],{pane:'personalPinPane',draggable:false,keyboard:true,title:pin.name,
   icon:L.divIcon({className:'v205-pin-icon',html:'<span class="v205-pin-dot"></span>',iconSize:[22,28],iconAnchor:[11,26],popupAnchor:[0,-25]})});
  var title=document.createElement('span');title.textContent=pin.name;
  marker.bindTooltip(title,{permanent:true,direction:'right',offset:[9,-17],className:'v205-pin-label'});
  var popup=document.createElement('div'),heading=document.createElement('div'),address=document.createElement('div');
  heading.className='v205-pin-popup-title';heading.textContent=pin.name;
  address.className='v205-pin-popup-address';address.textContent=pin.address||'地図で指定した場所';popup.className='v215-pin-menu';popup.appendChild(heading);popup.appendChild(address);
  var actions=document.createElement('div');actions.className='v204-popup-actions v218-pin-actions';
  var route=document.createElement('a');route.className='v167-suumo-link';route.textContent='経路案内 ↗';route.href=v204DirectionsUrl(pin);route.target='_blank';route.rel='noopener noreferrer';actions.appendChild(route);
  var walking=document.createElement('button');walking.type='button';walking.className='v218-walking-button';walking.textContent='徒歩圏を表示';
  walking.setAttribute('aria-label',pin.name+'から徒歩圏の分数を指定して表示');
  walking.addEventListener('click',function(){v211DeletePopup(marker,pin,null,true);});actions.appendChild(walking);
  var hint=document.createElement('div');hint.textContent='長押し・右クリックでその他の操作';hint.className='v205-pin-popup-address';
  popup.appendChild(actions);
  if(pin.walk_minutes!==undefined){
   var removeRing=document.createElement('button');removeRing.type='button';removeRing.className='v220-remove-ring';removeRing.textContent='徒歩圏を削除';
   removeRing.setAttribute('aria-label',pin.name+'の徒歩圏の線だけを削除');
   removeRing.addEventListener('click',function(){if(v214RemoveRing(pin.id)){state.map.closePopup();v205Status('徒歩圏の線を削除しました。');}});popup.appendChild(removeRing);
  }
  popup.appendChild(hint);marker.bindPopup(popup,v215PopupOptions());
  marker.addTo(state._v205Layer);state._v205Markers[pin.id]=marker;v211BindLongPress(marker,pin);
 });
}
function v205FocusPin(pin){
 if(!state.map||!pin)return;
 state.map.setView([pin.lat,pin.lng],Math.max(13,state.map.getZoom()),{animate:false});
 var marker=(state._v205Markers||{})[pin.id];if(marker)marker.openPopup();
}
function v205AddPin(name,address,lat,lng){
 name=String(name||'').trim().slice(0,60);
 if(!name||!v205Point(lat,lng)){v205Status('名前と位置を確認してください。');return false;}
 if((state._v205Pins||[]).some(function(p){return p.name===name&&Math.abs(p.lat-lat)<.000001&&Math.abs(p.lng-lng)<.000001})){v205Status('同じ目印ピンは登録済みです。');return false;}
 var id='pin_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,14);
 var pin={id:id,name:name,address:String(address||'').slice(0,180),lat:lat,lng:lng};
 if(!v205SavePins((state._v205Pins||[]).concat([pin])))return false;
 document.getElementById('v205-pin-candidates').hidden=true;
 v205FocusPin(pin);v205Status('「'+name+'」を保存しました。ピンの位置は固定されています。');return true;
}
function v206ClearPreview(){
 if(state._v206Preview&&state.map){state.map.removeLayer(state._v206Preview);state._v206Preview=null;}
 state._v211Draft=null;state._v211DraftAddress=null;state._v211DraftConfirm=null;
}
function v211CancelDraft(){
 state._v205Pick=null;state._v207DetailPending=null;v206ClearPreview();state._v211Draft=null;
 document.getElementById('v205-pin-cancel').hidden=true;document.getElementById('v205-pin-candidates').hidden=true;
 v205Status('仮ピンを取り消しました。');
}
function v211DraftDetails(){
 var item=state._v211Draft;if(!item)return;
 var address=String(item.address||'住所を確認しています…');
 document.getElementById('v206-result-detail').textContent='仮ピン（未確定）\n施設名：'+item.name+'\n住所：'+address+'\n情報：'+String(item.source||'地図で指定')+(item.source_url?'\n出典：'+item.source_url:'')+'\n緯度 '+item.lat.toFixed(6)+' / 経度 '+item.lng.toFixed(6);
 var pending=!!state._v207DetailPending||!item.address||!!item.address_error;
 document.getElementById('v205-pin-add').disabled=pending;
 if(state._v211DraftAddress)state._v211DraftAddress.textContent='住所：'+address;
 if(state._v211DraftConfirm)state._v211DraftConfirm.disabled=pending;
}
function v211ConfirmDraft(){
 var item=state._v211Draft;
 if(!item||state._v205SearchPending||state._v207DetailPending||!item.address||item.address_error){v205Status('住所を確認できてから確定してください。');return;}
 var name=document.getElementById('v205-pin-name').value.trim()||item.name;
 if(v205AddPin(name,item.address,item.lat,item.lng)){
  v206ClearPreview();state._v211Draft=null;document.getElementById('v205-pin-cancel').hidden=true;
  v205Status('「'+name+'」のピンを確定しました。長押しで徒歩の円・削除メニューを開けます。');
 }
}
function v206SelectCandidate(){
 var item=(state._v205Candidates||[])[Number(document.getElementById('v205-pin-candidate').value)];
 if(!item)return;
 v206ClearPreview();state._v211Draft=item;v207ResolveCandidateAddress(item);
 document.getElementById('v205-pin-name').value=String(item.name||item.title||'').slice(0,60);
 document.getElementById('v205-pin-cancel').hidden=false;
 if(state.map&&window.L){
  var text=document.createElement('span');text.textContent=String(item.name)+'（仮ピン・未確定）';
  var popup=document.createElement('div'),title=document.createElement('div'),address=document.createElement('div');
  popup.className='v215-pin-menu';title.className='v205-pin-popup-title';title.textContent='仮ピン：'+item.name;popup.appendChild(title);
  address.className='v205-pin-popup-address';popup.appendChild(address);state._v211DraftAddress=address;
  var confirm=document.createElement('button');confirm.type='button';confirm.textContent='確認して確定';confirm.addEventListener('click',v211ConfirmDraft);popup.appendChild(confirm);state._v211DraftConfirm=confirm;
  var cancel=document.createElement('button');cancel.type='button';cancel.textContent='取り消す';cancel.addEventListener('click',v211CancelDraft);popup.appendChild(cancel);
  state._v206Preview=L.marker([item.lat,item.lng],{title:String(item.name),
    icon:L.divIcon({className:'v205-pin-icon v206-preview',html:'<span class="v205-pin-dot"></span>',iconSize:[22,28],iconAnchor:[11,26]})}).bindTooltip(text,{permanent:true,direction:'right',className:'v205-pin-label'}).bindPopup(popup,v215PopupOptions()).addTo(state.map);
  state.map.setView([item.lat,item.lng],Math.max(13,state.map.getZoom()),{animate:false});state._v206Preview.openPopup();
 }
 v211DraftDetails();setHeight();
}
function v220DropRing(id){
 var rings=state._v214Rings;if(!rings||!rings[id])return;
 if(rings[id].halo)state.map.removeLayer(rings[id].halo);
 state.map.removeLayer(rings[id].layer);delete rings[id];
}
function v214RemoveRing(id){
 var pins=state._v205Pins||[],pin=pins.find(function(p){return p.id===id});
 if(pin&&pin.walk_minutes!==undefined){
  var next=pins.map(function(p){var copy=Object.assign({},p);if(p.id===id)delete copy.walk_minutes;return copy;});
  if(!v205SavePins(next))return false;
 }
 v220DropRing(id);return true;
}
function v214ShowRing(pin,minutes){
 minutes=Number(minutes);var radius=minutes*80;
 if(!Number.isFinite(minutes)||minutes<1||!Number.isFinite(radius)){v205Status('分数は1以上の数値で入力してください。');return false;}
 if(!state.map||!window.L||!pin||!v205Point(pin.lat,pin.lng))return false;
 var pins=state._v205Pins||[];
 if(!pins.some(function(p){return p.id===pin.id}))return false;
 var next=pins.map(function(p){return p.id===pin.id?Object.assign({},p,{walk_minutes:minutes}):p;});
 if(!v205SavePins(next))return false;
 v205Status('「'+pin.name+'」から徒歩'+minutes+'分の目安（直線半径'+Math.round(radius)+'m）を表示・保存しました。');
 return true;
}
function v220RenderRing(pin){
 // Dedicated pane: above rent polygons and focus fill, below stations/pins.
 if(!state.map.getPane('walkingRingPane'))state.map.createPane('walkingRingPane');
 var pane=state.map.getPane('walkingRingPane');pane.style.zIndex='660';pane.style.pointerEvents='none';
 var minutes=pin.walk_minutes,radius=minutes*80;
 if(!state._v214Rings)state._v214Rings=Object.create(null);
 var existing=state._v214Rings[pin.id];
 if(existing){existing.layer.setLatLng([pin.lat,pin.lng]);existing.layer.setRadius(radius);
  if(existing.halo){existing.halo.setLatLng([pin.lat,pin.lng]);existing.halo.setRadius(radius);}
  existing.minutes=minutes;
 }else{
  var halo=L.circle([pin.lat,pin.lng],{pane:'walkingRingPane',radius:radius,color:'#ffffff',weight:8,opacity:1,fill:false,interactive:false}).addTo(state.map);
  var circle=L.circle([pin.lat,pin.lng],{pane:'walkingRingPane',radius:radius,color:'#7c3aed',weight:4,opacity:1,fill:false,interactive:false}).addTo(state.map);
  state._v214Rings[pin.id]={layer:circle,halo:halo,minutes:minutes};
 }
}
function v214SyncRings(pins){
 var rings=state._v214Rings||{};
 Object.keys(rings).forEach(function(id){
  var pin=pins.find(function(p){return p.id===id});
  if(!pin||pin.walk_minutes===undefined)v220DropRing(id);
 });
 pins.forEach(function(pin){if(pin.walk_minutes!==undefined)v220RenderRing(pin);});
}
function v211DeletePopup(marker,pin,event,walkingOnly){
 if(event&&event.originalEvent){event.originalEvent.preventDefault();event.originalEvent.stopPropagation();}
 var popup=document.createElement('div'),title=document.createElement('div'),hint=document.createElement('p');
 popup.className='v215-pin-menu';title.className='v205-pin-popup-title';title.textContent=walkingOnly?pin.name+'の徒歩圏':pin.name;popup.appendChild(title);
 hint.textContent='徒歩1分＝80mの直線距離の目安です。道路・坂道・信号は反映しません。円はピンと一緒にこのブラウザーへ保存され、次回も表示します。';hint.style.fontSize='11px';popup.appendChild(hint);
 var label=document.createElement('label');label.className='v215-minute-row';label.textContent='徒歩の分数：';
 var input=document.createElement('input');input.type='number';input.min='1';input.step='1';
 input.value=String((state._v214Rings&&state._v214Rings[pin.id])?state._v214Rings[pin.id].minutes:10);
 input.setAttribute('aria-label','徒歩の分数');label.appendChild(input);popup.appendChild(label);
 var show=document.createElement('button');show.type='button';show.textContent='N分の円を表示';
 show.addEventListener('click',function(){if(v214ShowRing(pin,input.value))state.map.closePopup();});popup.appendChild(show);
 if(state._v214Rings&&state._v214Rings[pin.id]){
  var hide=document.createElement('button');hide.type='button';hide.textContent='この円を消す';
  hide.addEventListener('click',function(){if(v214RemoveRing(pin.id)){state.map.closePopup();v205Status('徒歩圏の線を削除しました。');}});popup.appendChild(hide);
 }
 if(!walkingOnly){
 var remove=document.createElement('button');remove.type='button';remove.className='v205-pin-delete';remove.textContent='ピンを削除…';
 remove.addEventListener('click',function(){v214ConfirmDelete(marker,pin)});popup.appendChild(remove);
 }
 var cancel=document.createElement('button');cancel.type='button';cancel.textContent='閉じる';cancel.addEventListener('click',function(){state.map.closePopup()});popup.appendChild(cancel);
 L.popup(v215PopupOptions()).setLatLng(marker.getLatLng()).setContent(popup).openOn(state.map);
}
function v214ConfirmDelete(marker,pin,event){
 if(event&&event.originalEvent){event.originalEvent.preventDefault();event.originalEvent.stopPropagation();}
 var popup=document.createElement('div'),title=document.createElement('div'),address=document.createElement('div');
 popup.className='v215-pin-menu';title.className='v205-pin-popup-title';title.textContent='「'+pin.name+'」を削除しますか？';popup.appendChild(title);
 address.className='v205-pin-popup-address';address.textContent=pin.address||'住所未登録';popup.appendChild(address);
 var remove=document.createElement('button');remove.type='button';remove.className='v205-pin-delete';remove.textContent='削除する';
 remove.addEventListener('click',function(){if(v205SavePins((state._v205Pins||[]).filter(function(p){return p.id!==pin.id}))){v214RemoveRing(pin.id);state.map.closePopup();v205Status('「'+pin.name+'」を削除しました。');}});popup.appendChild(remove);
 var cancel=document.createElement('button');cancel.type='button';cancel.textContent='キャンセル';cancel.addEventListener('click',function(){state.map.closePopup()});popup.appendChild(cancel);
 L.popup(v215PopupOptions()).setLatLng(marker.getLatLng()).setContent(popup).openOn(state.map);
}
function v211BindLongPress(marker,pin){
 marker.on('contextmenu',function(event){v211DeletePopup(marker,pin,event)});
 var element=marker.getElement&&marker.getElement();if(!element)return;
 var timer=null,start=null,blockedUntil=0;
 function clear(){if(timer!==null)clearTimeout(timer);timer=null;start=null;}
 element.addEventListener('contextmenu',function(event){
  event.preventDefault();event.stopPropagation();if(event.stopImmediatePropagation)event.stopImmediatePropagation();
  clear();blockedUntil=Date.now()+900;v211DeletePopup(marker,pin);
 },true);
 element.addEventListener('pointerdown',function(event){
  clear();if(event.button!==undefined&&event.button!==0)return;
  start={x:event.clientX,y:event.clientY};
  timer=setTimeout(function(){timer=null;start=null;blockedUntil=Date.now()+900;v211DeletePopup(marker,pin)},650);
 });
 element.addEventListener('pointermove',function(event){if(start&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>10)clear();});
 ['pointerup','pointercancel','pointerleave','lostpointercapture'].forEach(function(type){element.addEventListener(type,clear)});
 element.addEventListener('click',function(event){if(Date.now()<blockedUntil){event.preventDefault();event.stopImmediatePropagation();}},true);
 element.addEventListener('keydown',function(event){if(event.key==='Delete'||(event.shiftKey&&event.key==='F10')){event.preventDefault();v211DeletePopup(marker,pin)}});
 marker.on('dragstart',clear);marker.on('remove',clear);
}
function v207ResolveCandidateAddress(item){
 state._v207DetailPending=null;
 document.getElementById('v205-pin-add').disabled=false;
 if(item.address_checked||item.address)return;
 var ticket='detail_'+Date.now()+'_'+Math.random().toString(36).slice(2,12);
 state._v207DetailPending=ticket;
 document.getElementById('v205-pin-add').disabled=true;
 hostMessage('streamlit:setComponentValue',{dataType:'json',value:{event:'custom_pin_search_v205',ticket:ticket,stage:'detail',
  name:item.name,lat:item.lat,lng:item.lng,home_view_epoch:state._v202HomeEpoch}});
}
function v205SearchPin(){
 if(state._v205SearchPending)return;
 var query=document.getElementById('v205-pin-address').value.trim();
 if(query.length<2){v205Status('施設名・目印の名前を2文字以上で入力してください。');return;}
 state._v205Pick=null;state._v207DetailPending=null;document.getElementById('v205-pin-cancel').hidden=true;v206ClearPreview();
 state._v205SearchPending=String(Date.now())+'_'+Math.random().toString(36).slice(2,12);
 state._v205SearchEpoch=state._v202HomeEpoch;
 document.getElementById('v205-pin-search').disabled=true;document.getElementById('v205-pin-add').disabled=true;document.getElementById('v205-pin-candidates').hidden=true;
 v205Status('施設名から住所と位置を検索しています…');
 hostMessage('streamlit:setComponentValue',{dataType:'json',value:{event:'custom_pin_search_v205',ticket:state._v205SearchPending,query:query,home_view_epoch:state._v202HomeEpoch}});
}
function v205PinReply(reply){
 if(!reply)return;
 if(reply.stage==='detail'){
  if(reply.ticket!==state._v207DetailPending)return;
  state._v207DetailPending=null;document.getElementById('v205-pin-add').disabled=false;
  var chosen=(state._v205Candidates||[])[Number(document.getElementById('v205-pin-candidate').value)];
  if(!chosen)return;
  chosen.address=String(reply.address||'住所情報なし（地図上の位置で確認してください）');chosen.address_checked=true;
  chosen.address_error=!!reply.error;v211DraftDetails();
  v205Status(reply.error||'施設名・住所・地図上の仮の印を確認してから保存してください。');return;
 }
 if(reply.ticket!==state._v205SearchPending)return;
 state._v205SearchPending=null;document.getElementById('v205-pin-search').disabled=false;document.getElementById('v205-pin-add').disabled=false;
 state._v206Stage='places';
 state._v205Candidates=Array.isArray(reply.candidates)?reply.candidates.filter(function(p){return p&&v205Point(p.lat,p.lng)&&p.name}):[];
 var box=document.getElementById('v205-pin-candidates'),select=document.getElementById('v205-pin-candidate'),add=document.getElementById('v205-pin-add');select.replaceChildren();
 box.hidden=!state._v205Candidates.length;
 document.getElementById('v206-result-label').textContent='検索結果（施設名・所在地）';
 add.textContent='確認して確定';
 state._v205Candidates.forEach(function(p,i){var opt=document.createElement('option');opt.value=String(i);opt.textContent=String(p.name)+(p.address?' — '+p.address:'');select.appendChild(opt)});
 if(state._v205Candidates.length){
  v205Status('施設名・住所・地図上の仮の印を確認してから保存してください。');v206SelectCandidate();
 }else{v206ClearPreview();v205Status(reply.error||'施設名が見つかりません。正式名称や名前の一部で再検索してください。');}
}
function v205InitPins(args){
 if(!state.map)return;
 if(!state._v205Initialized){
  state._v205Initialized=true;state._v205Pins=v205LoadPins();
  var queryInput=document.getElementById('v205-pin-address');
  queryInput.addEventListener('input',v223PinEditor);
  queryInput.addEventListener('keydown',function(event){if(event.key==='Enter'&&!event.isComposing){event.preventDefault();v205SearchPin();}});
  v223PinEditor();v223InitLocation();
  document.getElementById('v205-pin-search').addEventListener('click',v205SearchPin);
  document.getElementById('v205-pin-candidate').addEventListener('change',v206SelectCandidate);
  document.getElementById('v205-pin-pick').addEventListener('click',function(){
   var name=document.getElementById('v205-pin-name').value.trim()||document.getElementById('v205-pin-address').value.trim();
   if(!name){v205Status('ピンの名前を入力してください。');return;}
   if(state._v205SearchPending){v205Status('施設名の検索が終わってから指定してください。');return;}
   state._v207DetailPending=null;v206ClearPreview();document.getElementById('v205-pin-candidates').hidden=true;
   state._v205Pick={name:name,address:''};
   document.getElementById('v205-pin-cancel').hidden=false;v205Status('追加する場所を地図上でタップしてください。');
  });
  document.getElementById('v205-pin-cancel').addEventListener('click',v211CancelDraft);
  document.getElementById('v205-pin-add').addEventListener('click',v211ConfirmDraft);
  document.getElementById('v205-pin-jump').addEventListener('click',function(){var id=document.getElementById('v205-pin-list').value;v205FocusPin((state._v205Pins||[]).find(function(p){return p.id===id}));});
  state.map.on('click',function(event){
   var pick=state._v205Pick,point=event&&event.latlng;if(!pick||!point)return;
   if(!v205Point(point.lat,point.lng)){v205Status('対応範囲外です。');return;}
   state._v205Pick=null;state._v206Stage='places';state._v205Candidates=[{name:pick.name,title:pick.name,address:'',lat:point.lat,lng:point.lng}];
   var select=document.getElementById('v205-pin-candidate');select.replaceChildren();
   var option=document.createElement('option');option.value='0';option.textContent=pick.name;select.appendChild(option);
   document.getElementById('v205-pin-candidates').hidden=false;document.getElementById('v205-pin-add').textContent='確認して確定';
   v206SelectCandidate();v205Status('地図に仮ピンを置きました。住所と位置を確認してから確定してください。');
  });
  v205PinList();
 }
 if(state._v205SearchPending&&state._v205SearchEpoch!==state._v202HomeEpoch){state._v205SearchPending=null;document.getElementById('v205-pin-search').disabled=false;document.getElementById('v205-pin-add').disabled=false;v205Status('表示を切り替えたため施設検索を中断しました。再度検索してください。');}
 if(state._v206PreviewEpoch!==state._v202HomeEpoch){state._v207DetailPending=null;document.getElementById('v205-pin-add').disabled=false;v206ClearPreview();state._v206PreviewEpoch=state._v202HomeEpoch;}
 v205DrawPins();v205PinReply(args.custom_pin_reply||null);
}
"""
assert TOWN_COMPONENT_HTML_V121.count('<div id="town-search-status" role="status" aria-live="polite"></div></div>')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('<div id="town-search-status" role="status" aria-live="polite"></div></div>',
    '<div id="town-search-status" role="status" aria-live="polite"></div></div>'+_V205_PIN_DOM,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V205_PIN_CSS+'</style></head>',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V205_PIN_JS+'\nfunction render(args){',1)
assert TOWN_COMPONENT_HTML_V121.count(' v202HomeView(args);')==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(' v202HomeView(args);',' v202HomeView(args);\n v205InitPins(args);',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('state.searchPendingTicket||state._v189Pending)return;',
    'state.searchPendingTicket||state._v189Pending||state._v205SearchPending||state._v207DetailPending)return;',1)


def _v205_geocode_pin(data):
    """Read-only address lookup for personal landmarks; candidates require confirmation."""
    reply={'ticket':str(data.get('ticket') or '')[:100],'candidates':[],'error':''}
    name=data.get('name');address=data.get('address')
    if (not isinstance(name,str) or not name.strip() or len(name)>60 or
        not isinstance(address,str) or not address.strip() or len(address)>180):
        reply['error']='ピンの名前と住所を確認してください。'
        return reply
    try:
        web=PublicWeb()
        doc=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address.strip()}).json()
        if not isinstance(doc,list):raise ValueError('invalid address response')
        seen=set()
        for item in doc:
            try:
                geometry=item['geometry']
                if geometry.get('type')!='Point':continue
                lng,lat=map(float,geometry['coordinates'][:2])
                if not (math.isfinite(lat) and math.isfinite(lng) and 34<=lat<=37 and 138<=lng<=141):continue
                title=normal((item.get('properties') or {}).get('title') or address.strip())[:220]
                key=(round(lat,7),round(lng,7),title)
                if key in seen:continue
                seen.add(key);reply['candidates'].append({'title':title,'lat':lat,'lng':lng})
            except (KeyError,ValueError,TypeError,AttributeError):continue
        if not reply['candidates']:reply['error']='住所が見つかりません。住所を詳しくするか、地図をタップして指定してください。'
    except Exception:
        reply['error']='住所の確認に失敗しました。再試行するか、地図をタップして指定してください。'
    return reply


def _v212_exception_details(exc):
    """Do not lose requests/urllib3 causes hidden by `raise AppError from None`."""
    chain=[];seen=set();current=exc
    while current is not None and id(current) not in seen and len(chain)<8:
        seen.add(id(current));chain.append({'type':type(current).__name__,
            'message':str(current),'errno':getattr(current,'errno',None)})
        current=current.__cause__ or current.__context__
    signature=' '.join(item['type']+' '+item['message'] for item in chain).lower()
    phase=('response_wait' if 'readtimeout' in signature else 'connect' if 'connecttimeout' in signature
           else 'tls' if 'sslerror' in signature else 'dns' if any(word in signature for word in ('nameresolution','gaierror','getaddrinfo'))
           else 'timeout_unspecified' if any(word in signature for word in ('timeout','timed out','タイムアウト')) else 'request_or_decode')
    return {'exception_chain':chain,'failure_phase':phase,'diagnostic_schema':2}


def _v208_landmark_json(web,url,params,operation):
    """Keep HTTP/decoding evidence even when the public transport wraps errors."""
    started=time.monotonic();response=None
    info={'url':url,'parameters':params,'operation':operation,'failed_stage':operation+'.request',
          'host':urlparse(url).hostname,'method':'GET','connect_timeout_seconds':4,'read_timeout_seconds':12,
          'response_received':False,'diagnostic_schema':2,'build':BUILD}
    trace(web,'landmark_request',info,stage='landmark.'+operation)
    try:
        response=web.fetch(url,params=params)
        headers=getattr(response,'headers',{}) or {}
        info.update(response_received=True,status=getattr(response,'status_code',None),
                    content_type=headers.get('Content-Type',''),retry_after=headers.get('Retry-After',''),
                    response_bytes=len(getattr(response,'content',b'')),failed_stage=operation+'.json')
        doc=response.json()
        info['elapsed_ms']=round((time.monotonic()-started)*1000)
        trace(web,'landmark_response',info,stage='landmark.'+operation)
        return doc,info
    except Exception as exc:
        details=dict(getattr(exc,'diagnostic',{}) or {})
        details={**info,**details,**_v212_exception_details(exc),'elapsed_ms':round((time.monotonic()-started)*1000)}
        if response is not None:details['response_excerpt']=str(getattr(response,'text',''))[:1500]
        # Preserve the original exception class/message and the transport diagnostics.
        exc.landmark_diagnostic=details
        raise


def _v208_landmark_failure(web,data,exc,operation,info=None):
    details={**(info or {}),**(getattr(exc,'landmark_diagnostic',{}) or {}),
             'query':str(data.get('query') or data.get('name') or '')[:180],
             'ticket':str(data.get('ticket') or '')[:100],
             'reason':str(exc),'exception_type':type(exc).__name__,
             'technical_exception':getattr(exc,'diagnostic',{}) or {},
             'traceback':traceback.format_exc(),**_v212_exception_details(exc)}
    details.setdefault('failed_stage',operation+'.validation')
    trace(web,'landmark_search_failed' if operation=='name_search' else 'landmark_address_failed',
          details,'ERROR','landmark.'+operation)


def _v209_log_secrets():
    values=[]
    for name in ('SUPABASE_SECRET_KEY','SUPABASE_SERVICE_ROLE_KEY','LANDMARK_BRAVE_API_KEY'):
        value=os.environ.get(name)
        if value is None:
            try:value=st.secrets.get(name,'')
            except Exception:value=''
        if value:values.append(str(value).strip())
    return tuple(values)


def _v208_run_landmark_lookup(state,data):
    """Reuse AuditLog, its redaction, and the existing diagnostic_log storage."""
    audit=state.get('_v208_landmark_audit')
    if audit is None:
        audit=AuditLog({'mode':'landmark','providers':['Photon / OpenStreetMap','Overpass','国土地理院']},
                       _v209_log_secrets())
        state['_v208_landmark_audit']=audit
    before_seq=audit.count
    reply=_v207_search_landmarks(data,audit=audit)
    audit.add('search','finish','INFO',{'operation':'landmark','ticket':reply['ticket'],
              'error':reply.get('error') or '', 'candidates':len(reply.get('candidates') or [])})
    request_failures=any(diagnostic_failure_event(event) for event in audit.records() if event.get('seq',0)>before_seq)
    if reply.get('error') or request_failures:
        try:
            db=Database();audit.persist(db,force=True)
            if audit.storage_error:raise AppError(audit.storage_error)
            records=audit.records()
            db.save_search({'id':audit.search_id,'status':'failed' if reply.get('error') else 'completed',
                'started_at':records[0]['time'],'finished_at':utc_now(),
                'conditions':{'mode':'landmark','providers':['Photon / OpenStreetMap','Overpass','国土地理院']},
                'summary':{'issues':[reply['error']] if reply.get('error') else ['施設名検索は成功・通信障害から復旧'],
                           'saved':0,'candidates':len(reply.get('candidates') or []),'operation':'施設名検索'}})
            state['_v208_landmark_storage_error']=''
            state['_v208_landmark_saved']=True
        except Exception as exc:
            # A storage error must not erase the original search error or the local log.
            audit.add('landmark.log','landmark_log_save_failed','ERROR',
                      {'reason':str(exc),'exception_type':type(exc).__name__,
                       'technical_exception':getattr(exc,'diagnostic',{}) or {}})
            state['_v208_landmark_storage_error']=audit.clean(str(exc))
            state['_v208_landmark_saved']=False
    if reply.get('error'):
        reply['error']+=' 詳細は「設定」→「ログ出力」で「ログ表示を更新」を押すとダウンロードできます。'
    return reply


def _v208_landmark_log_panel(state):
    audit=state.get('_v208_landmark_audit')
    with st.expander('施設名検索のログ',expanded=True):
        if audit is None:
            st.caption('施設名を検索後、「ログ表示を更新」を押してください。')
            st.download_button('詳細ログ JSON',b'','sumai_landmark_log.json','application/json',key='landmark_json_pending_v209',disabled=True)
            return
        if state.get('_v208_landmark_storage_error'):
            st.caption('保存先への記録を確認できません。下のボタンから現在のログを保存してください。')
        elif state.get('_v208_landmark_saved'):
            st.caption('失敗ログは保存済みです。検索履歴からも読み込めます。')
        events=audit.records()
        diagnostic_downloads(events,'landmark_v208')
        failures=acquisition_failure_rows(events)
        if failures:
            st.download_button('施設名検索のエラーCSV',csv_bytes_from_rows(failures),
                'sumai_landmark_errors.csv','text/csv',key='landmark_errors_v208',on_click='ignore')


_V212_NAME_CACHE={}


def _v212_photon_document(web,query):
    cached=_V212_NAME_CACHE.get(query)
    if cached and time.monotonic()-cached['time']<3600:
        trace(web,'landmark_cache_hit',{'query':query,'provider':'Photon','age_seconds':round(time.monotonic()-cached['time'])},stage='landmark.name_search')
        return cached['doc'],cached['info']
    if not hasattr(web,'local'):web.local=threading.local()
    web.local.landmark_lookup=True
    params={'q':query,'bbox':'139.4,35.45,140.05,35.95'}
    doc,info=_v208_landmark_json(web,'https://photon.komoot.io/api/',params,'name_search')
    info.update(provider='Photon',failed_stage='name_search.validation')
    try:
        if not isinstance(doc,dict) or doc.get('type')!='FeatureCollection' or not isinstance(doc.get('features'),list):
            raise ValueError('Photon response is not a GeoJSON FeatureCollection')
        elements=[];decisions=[]
        for feature in doc['features']:
            try:
                props=feature['properties'];geometry=feature['geometry']
                if geometry.get('type')!='Point':raise ValueError('geometry is not a Point')
                lng,lat=map(float,geometry['coordinates'][:2]);name=normal(props.get('name'))
                if not name:raise ValueError('facility name missing')
                def name_key(value):
                    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',str(value)).casefold()).replace('株式会社','').replace('有限会社','')
                requested=name_key(query);named=name_key(name)
                if len(requested)>=3 and not (requested in named or (len(named)>=3 and named in requested)):
                    raise ValueError('facility name does not match query')
                if not (math.isfinite(lat) and math.isfinite(lng) and 35.45<=lat<=35.95 and 139.4<=lng<=140.05):raise ValueError('outside supported region')
                if props.get('countrycode') and str(props['countrycode']).upper()!='JP':raise ValueError('country is not Japan')
                parts=[]
                for key in ('state','city','district','locality','street','housenumber'):
                    value=normal(props.get(key))
                    if value and value not in parts:parts.append(value)
                address=''.join(parts)
                if address and not props.get('housenumber'):address+='（番地未確認）'
                tags={'name':name,'addr:full':address}
                elements.append({'type':props.get('osm_type'),'id':props.get('osm_id'),'lat':lat,'lon':lng,'tags':tags})
                decisions.append({'name':name,'address':address,'lat':lat,'lng':lng,'osm_id':props.get('osm_id'),'decision':'candidate'})
            except (KeyError,TypeError,ValueError,AttributeError,IndexError) as exc:
                decisions.append({'decision':'rejected','reason':str(exc)})
        trace(web,'landmark_geocoder_results',{'query':query,'provider':'Photon','features':len(doc['features']),
              'accepted':len(elements),'decisions':decisions},stage='landmark.result')
        result={'elements':elements}
        if elements:
            if len(_V212_NAME_CACHE)>=128:_V212_NAME_CACHE.clear()
            _V212_NAME_CACHE[query]={'time':time.monotonic(),'doc':result,'info':dict(info)}
        return result,info
    except Exception as exc:
        exc.landmark_diagnostic=info
        raise


# Independent opt-in: rental search API consent is never reused for landmarks.


def _v216_setting(name,default=''):
    value=os.environ.get(name)
    if value is None:
        try:value=st.secrets.get(name,default)
        except Exception:value=default
    return value


def _v216_web_document(web,query):
    key=str(_v216_setting('LANDMARK_BRAVE_API_KEY')).strip()
    if not key or str(_v216_setting('LANDMARK_BRAVE_API_APPROVED')).upper()!='YES':
        trace(web,'landmark_web_api_disabled',{'reason':'API key and separate opt-in required'},stage='landmark.name_search')
        return None
    try:limit=max(0,int(_v216_setting('LANDMARK_BRAVE_API_MAX_REQUESTS','25')))
    except (ValueError,TypeError):limit=0
    bucket=hashlib.sha256(key.encode()).hexdigest()
    budget=_v216_api_budget()
    with budget['lock']:
        used=budget['uses'].get(bucket,0)
        if used>=limit:
            trace(web,'landmark_api_budget_exhausted',{'requests':used,'limit':limit},'WARNING','landmark.name_search')
            return None
        budget['uses'][bucket]=used+1
    started=time.monotonic();status=None
    try:
        # Never pass the key/headers/exception body through the HTTP URL logger.
        response=requests.get('https://api.search.brave.com/res/v1/web/search',
            params={'q':query+' 所在地 住所 東京','country':'JP','search_lang':'ja','count':10},
            headers={'X-Subscription-Token':key,'Accept':'application/json'},
            timeout=(4,12),allow_redirects=False)
        status=int(response.status_code)
        trace(web,'landmark_web_api_response',{'provider':'Brave Search','status':status,
            'query':query,'request_number':used+1,'elapsed_ms':round((time.monotonic()-started)*1000)},stage='landmark.name_search')
        if status!=200:raise ValueError('Search API HTTP '+str(status))
        if len(response.content)>1_000_000:raise ValueError('Search API response too large')
        doc=response.json();rows=(doc.get('web') or {}).get('results')
        if not isinstance(rows,list):raise ValueError('Search API results array missing')
        elements=[];seen=set()
        def token(text):return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',text).casefold())
        wanted=token(query)
        for row in rows:
            if not isinstance(row,dict):continue
            title=normal(html.unescape(re.sub('<[^>]*>','',str(row.get('title') or ''))))
            url=str(row.get('url') or '');parsed=urlparse(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:continue
            # A page mentioning another facility must not inherit the user's name.
            if wanted not in token(title):
                trace(web,'landmark_web_candidate_rejected',{'url':url,'reason':'query_name_not_in_title'},stage='landmark.result');continue
            text=html.unescape(re.sub('<[^>]*>','',str(row.get('description') or '')))
            addresses=_extract_detailed_addresses(text)
            addresses+=re.findall(r'東京都[^\s,、。|<>]{1,12}区[^\s,、。|<>]{1,20}?\d+-\d+(?:-\d+)?',unicodedata.normalize('NFKC',text))
            for address in dict.fromkeys(addresses):
                if address in seen:continue
                seen.add(address)
                try:
                    points,info=_v208_landmark_json(web,'https://msearch.gsi.go.jp/address-search/AddressSearch',{'q':address},'web_address_lookup')
                    if not isinstance(points,list):raise ValueError('GSI address response is not an array')
                    accepted=0
                    for point in points:
                        lng,lat=map(float,point['geometry']['coordinates'][:2]);matched=normal(point.get('properties',{}).get('title'))
                        def address_token(value):
                            value=address_key(value).replace('丁目','-').replace('番地','-').replace('番','-').replace('号','')
                            return re.sub(r'\s+','',value).rstrip('-')
                        a=address_token(address);b=address_token(matched)
                        approximate=bool(re.search(r'\d$',b)) and a.startswith(b+'-')
                        if not b or not (a==b or approximate) or not (35.45<=lat<=35.95 and 139.4<=lng<=140.05):continue
                        label=address+'（Web検索の住所候補・要確認）'
                        if a!=b:label+='／地図位置は'+matched+'の概略'
                        elements.append({'lat':lat,'lon':lng,'tags':{'name':query,'addr:full':label,'source':url}});accepted+=1
                    trace(web,'landmark_web_address_candidates',{'url':url,'page_title':title,'address':address,'accepted':accepted,'verification':'snippet_candidate_requires_user_confirmation'},stage='landmark.result')
                except Exception as exc:
                    _v208_landmark_failure(web,{'query':query},exc,'web_address_lookup')
        return {'elements':elements},{'provider':'Brave Search + 国土地理院','source':'Web検索の住所候補 / 国土地理院（要確認）'}
    except Exception as exc:
        # Tokens may occur in transport exception text. Record safe diagnostics only.
        trace(web,'landmark_web_api_failed',{'provider':'Brave Search','status':status,
            'exception_type':type(exc).__name__,'reason':str(exc).replace(key,'[REDACTED]')[:1500],
            'exception_chain':json.loads(json.dumps(_v212_exception_details(exc),ensure_ascii=False).replace(key,'[REDACTED]')),
            'elapsed_ms':round((time.monotonic()-started)*1000),
            'failure_phase':'response_validation' if status else 'connection_or_response_wait'},'ERROR','landmark.name_search')
        return None


def _v212_name_document(web,query,pattern):
    # Preserve spaces in the first query; normalize punctuation/case only for retries.
    variants=list(dict.fromkeys([query,re.sub(r'\s+','',query),
        re.sub(r'\s+','',query).upper().replace('＆','&')]))
    empty=None
    for variant in variants:
        try:
            doc,info=_v212_photon_document(web,variant)
            if doc['elements']:return doc,info
            empty=(doc,info)
            trace(web,'landmark_geocoder_empty',{'query':variant,'provider':'Photon','reason':'coverage_no_match'},stage='landmark.name_search')
        except Exception as exc:
            detail={**(getattr(exc,'diagnostic',{}) or {}),**(getattr(exc,'landmark_diagnostic',{}) or {}),**_v212_exception_details(exc)}
            trace(web,'landmark_geocoder_failed',{**detail,'query':variant,'provider':'Photon','reason':str(exc),'exception_type':type(exc).__name__},'ERROR','landmark.name_search')
            if str(detail.get('status')) in ('401','403','429'):raise
            break
    extra=_v216_web_document(web,query)
    if extra and extra[0]['elements']:return extra
    try:return _v211_name_document(web,query,pattern)
    except Exception as exc:
        if empty is None:raise
        trace(web,'landmark_supplement_failed',{'query':query,'reason':str(exc),**_v212_exception_details(exc)},'WARNING','landmark.name_search')
        # A valid primary response with zero results is not a total search outage.
        return empty


def _v211_name_document(web,query,pattern):
    """Indexed exact lookup first; partial lookup only after a successful empty result.

    Try the independent public instance only for network/5xx failures, never
    for access refusal/rate limits. Each endpoint is attempted once per query.
    """
    if not hasattr(web,'local'):web.local=threading.local()
    web.local.landmark_lookup=True
    bbox='(35.45,139.4,35.95,140.05)'
    endpoints=('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter')
    for mode in ('exact','partial'):
        literal=json.dumps(query if mode=='exact' else pattern,ensure_ascii=False)
        operator='=' if mode=='exact' else '~'
        suffix='' if mode=='exact' else ',i'
        keys=('name','name:ja','alt_name','short_name')
        q='[out:json][timeout:8];('+''.join('nwr["'+key+'"'+operator+literal+suffix+']'+bbox+';' for key in keys)+');out center tags;'
        for index,url in enumerate(endpoints):
            info={'url':url,'parameters':{'data':q},'match_mode':mode,'failed_stage':'name_search.request'}
            try:
                doc,info=_v208_landmark_json(web,url,{'data':q},'name_search')
                info.update(match_mode=mode,failed_stage='name_search.validation')
                if not isinstance(doc,dict):raise ValueError('facility response must be an object')
                if doc.get('remark'):
                    error=ValueError('Overpass remark: '+str(doc['remark'])[:1500]);error.landmark_diagnostic=info
                    raise error
                if not isinstance(doc.get('elements'),list):raise ValueError('facility response is missing elements array')
                break
            except Exception as exc:
                exc.landmark_diagnostic={**info,**(getattr(exc,'landmark_diagnostic',{}) or {})}
                detail={**(getattr(exc,'diagnostic',{}) or {}),**exc.landmark_diagnostic}
                status=detail.get('status')
                try:status=int(status) if status is not None else None
                except (TypeError,ValueError):status=None
                transient=(status is not None and 500<=status<=599) or any(word in (type(exc).__name__+' '+str(exc)+' '+str(detail)).lower() for word in ('timeout','timed out','タイムアウト','connection','接続できません','通信失敗'))
                if status in (401,403,429) or not transient or index==len(endpoints)-1:raise
                trace(web,'landmark_endpoint_fallback',{**detail,'url':url,'next_url':endpoints[index+1],
                      'match_mode':mode,'reason':str(exc),'exception_type':type(exc).__name__},'WARNING','landmark.name_search')
        if doc['elements'] or mode=='partial':return doc,info


def _v207_search_landmarks(data,audit=None):
    """User-triggered name lookup; address typing is never required."""
    ticket=str(data.get('ticket') or '')[:100]
    reply={'ticket':ticket,'stage':'places','candidates':[],'error':''}
    web=PublicWeb();web.audit=audit;info={}
    if data.get('stage')=='detail':
        reply={'ticket':ticket,'stage':'detail','address':'','error':''}
        try:
            if isinstance(data.get('lat'),bool) or isinstance(data.get('lng'),bool):raise ValueError('boolean coordinate is invalid')
            lat,lng=float(data['lat']),float(data['lng'])
            if not (math.isfinite(lat) and math.isfinite(lng) and 35.45<=lat<=35.95 and 139.4<=lng<=140.05):raise ValueError('coordinate outside supported area')
            doc,info=_v208_landmark_json(web,'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress',{'lat':lat,'lon':lng},'address_lookup')
            info['failed_stage']='address_lookup.validation'
            if not isinstance(doc,dict):raise ValueError('address response must be an object')
            result=doc.get('results') or {};code=str(result.get('muniCd') or '')
            town=normal(result.get('lv01Nm'))
            # GSI supplies town/chome only. Do not invent a street number.
            ward=TOKYO_WARDS.get(code)
            if ward and town:reply['address']='東京都'+ward+town+'（周辺住所・番地は未確認）'
            else:
                munis=municipalities(web);municipality=munis.get(code)
                if municipality and town:reply['address']=municipality[1]+municipality[2]+town+'（周辺住所・番地は未確認）'
            if not reply['address']:raise ValueError('address response has no usable municipality/town')
        except Exception as exc:
            _v208_landmark_failure(web,data,exc,'address_lookup',info)
            reply['error']='住所情報を取得できません。施設名と地図上の位置を確認してください。'
        return reply
    query=data.get('query')
    if not isinstance(query,str) or not 2<=len(query.strip())<=180:
        reply['error']='施設名・目印の名前を2文字以上で入力してください。'
        trace(web,'landmark_invalid_query',{'reason':reply['error'],'ticket':ticket},'WARNING','landmark.validation')
        return reply
    query=normal(query)
    # POSIX regex metacharacters are escaped, then encoded as an Overpass string.
    # Whitespace is omitted between search tokens so "立教 小学校" matches too.
    compact=re.sub(r'\s+','',query)
    pattern=re.sub(r'([.\[\]{}()*+?^$|\\])',r'\\\1',compact)
    try:
        doc,info=_v212_name_document(web,query,pattern)
        seen=set()
        for element in doc['elements']:
            try:
                tags=element.get('tags') or {};point=element.get('center') or element
                name=normal(tags.get('name:ja') or tags.get('name'))[:60]
                lat,lng=float(point['lat']),float(point['lon'])
                if not name or not (math.isfinite(lat) and math.isfinite(lng) and 35.45<=lat<=35.95 and 139.4<=lng<=140.05):continue
                address=normal(tags.get('addr:full'))
                if not address:
                    address=''.join(normal(tags.get(k)) for k in ('addr:province','addr:city','addr:suburb','addr:quarter','addr:neighbourhood','addr:street','addr:block_number','addr:housenumber'))
                key=(name,round(lat,5),round(lng,5))
                if key in seen:continue
                seen.add(key);reply['candidates'].append({'name':name,'title':name,'address':address[:180],
                    'lat':lat,'lng':lng,'source':info.get('source') or info.get('provider','Overpass')+' / OpenStreetMap','source_url':str(tags.get('source') or ''),'address_checked':bool(address)})
            except (KeyError,TypeError,ValueError,AttributeError):continue
        reply['candidates'].sort(key=lambda p:(re.sub(r'\s+','',p['name']).casefold()!=compact.casefold(),p['name'],p['lat'],p['lng']))
        trace(web,'landmark_result',{'query':query,'ticket':ticket,'elements':len(doc['elements']),'candidates':len(reply['candidates'])},stage='landmark.result')
        if not reply['candidates']:
            reply['error']='施設名データに一致する候補がありません。正式名称・表記を変えて検索するか、「設定」→「初期設定」の施設名検索APIを設定してください。'
            trace(web,'landmark_no_candidates',{**info,'query':query,'reason':reply['error'],'elements':len(doc['elements'])},'WARNING','landmark.result')
    except Exception as exc:
        _v208_landmark_failure(web,data,exc,'name_search',info)
        reply['error']='施設名の検索に失敗しました。時間を置いて再検索してください。'
    return reply


# v210: append last so the common feedback wins over individual component styles.
_V210_BUTTON_CSS = '\n/* v210: buttons created later (popups, zoom, candidate actions) inherit feedback. */\nbutton:not(:disabled):is(:active,:focus),a[role="button"]:is(:active,:focus),\na.leaflet-control-zoom-in:is(:active,:focus),a.leaflet-control-zoom-out:is(:active,:focus),\na.v167-suumo-link:not([aria-disabled="true"]):is(:active,:focus){\n background:#174a6a!important;color:#fff!important;border-color:#174a6a!important;\n box-shadow:0 0 0 3px rgba(23,74,106,.25)!important}\nbutton:not(:disabled):is(:active,:focus) *,a.v167-suumo-link:is(:active,:focus) *{color:inherit!important}\nbutton:not(:disabled):focus-visible,a[role="button"]:focus-visible{\n outline:3px solid #b35636;outline-offset:3px}\n#v205-pin-search:disabled,#town-submit:disabled,#town-confirm:disabled{\n background:#174a6a!important;color:#fff!important;border-color:#174a6a!important;cursor:wait}\n'
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V210_BUTTON_CSS+'</style></head>',1)

# v217: polygon SUUMO actions resolve their own clicked town, independently of
# the text search box and of rent aggregate availability.
_V217_POPUP_JS = r"""
function v217Place(props){
 var ward=String(props.suumo_ward||''),name=String(props.suumo_name||props.suumo_town||'');
 if(!ward){var parts=String(props.name||'').match(/^(.+?区)\s+(.+)$/);if(parts){ward=parts[1];name=parts[2];}}
 return {ward:ward,name:name,kind:/丁目$/.test(name)?'chome':'town'};
}
function v217Url(place,preferred){
 var city=(state._v189WardCodes||{})[place.ward];
 function valid(value){try{var u=new URL(value);return u.origin==='https://suumo.jp'&&u.pathname==='/jj/chintai/ichiran/FR301FC001/'&&u.searchParams.get('sc')===city&&new RegExp('^'+city+'[0-9]{3}$').test(u.searchParams.get('oz')||'')?u.href:''}catch(e){return ''}}
 if(!city)return '';
 var base=normalizeTownQuery(place.name).replace(/[0-9一二三四五六七八九十]+丁目.*$/,'');
 var code=(state._v189Oz||{})[place.ward+'|'+base],wardUrl=(state._v189WardUrls||{})[place.ward];
 if(code&&wardUrl){var target=new URL(wardUrl);target.searchParams.set('oz',code);var current=valid(target.href);if(current)return current;}
 return valid(preferred||'');
}
function v217UpdatePopup(entry){
 if(!entry)return;
 var request=state._v217Pending,reply=state._v217Reply;
 var key=v189SelectionKey(entry.place),url=v217Url(entry.place,entry.props.suumo_url),error='';
 if(reply&&reply.key===key){if(!url)url=v217Url(entry.place,reply.url);error=reply.error||'';}
 if(url){entry.link.href=url;entry.link.removeAttribute('aria-disabled');entry.link.textContent='SUUMOで検索 ↗';
  entry.help.textContent='SUUMO町名：'+String(entry.props.suumo_town||entry.place.name)+'｜'+String(entry.props.suumo_conditions||'')+'｜駅徒歩15分以内・住所順・50件（丁目を含む町全体）';
 }else{entry.link.removeAttribute('href');entry.link.setAttribute('aria-disabled','true');
  entry.link.textContent=request&&request.key===key?'SUUMO 確認中…':(error?'SUUMO 再試行':'SUUMOで検索');
  entry.help.textContent=error||(request&&request.key===key?'SUUMOの町名を確認しています。':'SUUMOボタンでこの町名を確認します。');}
 if(entry.layer.getPopup){var popup=entry.layer.getPopup();if(popup&&popup.isOpen&&popup.isOpen()&&popup.update)popup.update();}
}
function v217RequestPopup(entry){
 if(!entry||!entry.place.ward||!entry.place.name)return;
 var key=v189SelectionKey(entry.place);
 if(state._v217Pending&&state._v217Pending.key===key)return;
 var ticket='polygon_'+Date.now()+'_'+Math.random().toString(36).slice(2,10);
 state._v217Reply=null;state._v217Pending={ticket:ticket,key:key};
 v217UpdatePopup(entry);
 hostMessage('streamlit:setComponentValue',{dataType:'json',value:{event:'polygon_suumo_v217',ticket:ticket,
  ward:entry.place.ward,name:entry.place.name,kind:entry.place.kind,home_view_epoch:state._v202HomeEpoch}});
}
function v217AttachSuumo(layer,actions,el,props){
 var link=document.createElement('a');link.className='v167-suumo-link';link.target='_blank';link.rel='noopener noreferrer';
 link.setAttribute('role','button');link.setAttribute('tabindex','0');actions.appendChild(link);
 var help=document.createElement('div');help.className='v167-suumo-note';el.appendChild(help);
 var entry={link:link,help:help,props:props,place:v217Place(props),layer:layer};
 v217UpdatePopup(entry);
 function activate(e){if(!link.hasAttribute('href')){e.preventDefault();v217RequestPopup(entry);}}
 link.addEventListener('click',activate);
 link.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();link.click();}});
 layer.on('click',function(){state._v217Active=entry;v217UpdatePopup(entry);if(!link.hasAttribute('href'))v217RequestPopup(entry);});
}
function v217PopupReply(args){
 var reply=args.polygon_suumo_reply,pending=state._v217Pending;
 if(reply&&pending&&reply.ticket===pending.ticket&&reply.key===pending.key){state._v217Reply=reply;state._v217Pending=null;}
 v217UpdatePopup(state._v217Active);
}
"""
_v217_start=TOWN_COMPONENT_HTML_V121.index("var suumo=String(props.suumo_url||'');")
_v217_end=TOWN_COMPONENT_HTML_V121.index("var route=document.createElement('a');",_v217_start)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121[:_v217_start]+'v217AttachSuumo(layer,actions,el,props);\n'+TOWN_COMPONENT_HTML_V121[_v217_end:]
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V217_POPUP_JS+'\nfunction render(args){',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(' v189UpdateSuumo();\n initTownSearch();',' v189UpdateSuumo();\n v217PopupReply(args);\n initTownSearch();',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('state._v205SearchPending||state._v207DetailPending)return;',
 'state._v205SearchPending||state._v207DetailPending||state._v217Pending)return;',1)

_V223_LOCATION_DOM = '<div id="v223-location-bar"><button type="button" id="v223-locate">◎ 現在地を取得</button><span id="v223-location-status" role="status" aria-live="polite"></span></div>'
_V223_LOCATION_CSS = """
#v223-location-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 13px 0;background:#fff;color:#173a5e;font:13px system-ui}
#v223-locate{min-height:40px;padding:8px 14px;border:1px solid #9eb4bd;border-radius:7px;background:#fff;color:#173a5e;font-size:14px;cursor:pointer;touch-action:manipulation}
#v223-location-status{font-size:12px;line-height:1.4}
.v223-location-icon{background:none;border:0;pointer-events:none}
.v223-location-dot{display:block;box-sizing:border-box;width:22px;height:22px;border:3px solid #fff;border-radius:50%;background:#1a73e8;box-shadow:0 0 0 7px rgba(26,115,232,.18),0 1px 5px rgba(0,0,0,.45)}
"""
_V223_LOCATION_JS = r"""
function v223PinEditor(){
 var input=document.getElementById('v205-pin-address'),editor=document.getElementById('v223-pin-editor');
 if(!input||!editor)return;
 editor.hidden=!input.value.trim();
 if(editor.hidden){
  state._v205SearchPending=null;state._v207DetailPending=null;
  document.getElementById('v205-pin-search').disabled=false;
  document.getElementById('v205-pin-name').value='';v211CancelDraft();v205Status('');
 }
 setHeight();
}
function v223InitLocation(){
 var button=document.getElementById('v223-locate');if(!button||button.dataset.ready)return;
 button.dataset.ready='1';button.addEventListener('click',v223Locate);
}
function v223Locate(){
 var button=document.getElementById('v223-locate'),status=document.getElementById('v223-location-status');
 if(!button||button.disabled)return;
 if(!navigator.geolocation){status.textContent='現在地の取得に対応していません。位置情報を利用できるブラウザーで開いてください。';setHeight();return;}
 button.disabled=true;button.textContent='取得中…';status.textContent='位置情報を取得しています。';setHeight();
 function done(){button.disabled=false;button.textContent='◎ 現在地を取得';setHeight();}
 try{navigator.geolocation.getCurrentPosition(function(position){
  try{
   var point=position.coords,lat=Number(point.latitude),lng=Number(point.longitude),accuracy=Number(point.accuracy);
   if(!Number.isFinite(lat)||!Number.isFinite(lng)||Math.abs(lat)>90||Math.abs(lng)>180)throw new Error('invalid coordinates');
   if(!state.map.getPane('currentLocationPane'))state.map.createPane('currentLocationPane');
   var pane=state.map.getPane('currentLocationPane');pane.style.zIndex='680';pane.style.pointerEvents='none';
   if(state._v223Accuracy){state.map.removeLayer(state._v223Accuracy);state._v223Accuracy=null;}
   if(Number.isFinite(accuracy)&&accuracy>0)state._v223Accuracy=L.circle([lat,lng],{pane:'currentLocationPane',radius:accuracy,color:'#1a73e8',weight:1,opacity:.35,fill:true,fillColor:'#1a73e8',fillOpacity:.09,interactive:false}).addTo(state.map);
   if(state._v223Location)state._v223Location.setLatLng([lat,lng]);
   else state._v223Location=L.marker([lat,lng],{pane:'currentLocationPane',interactive:false,keyboard:false,
    icon:L.divIcon({className:'v223-location-icon',html:'<span class="v223-location-dot"></span>',iconSize:[22,22],iconAnchor:[11,11]})}).addTo(state.map);
   state.map.setView([lat,lng],Math.max(15,state.map.getZoom()),{animate:false});
   status.textContent='現在地を表示しました'+(Number.isFinite(accuracy)&&accuracy>0?'（取得精度：約'+Math.round(accuracy)+'m）':'')+'。移動後は再取得してください。';
  }catch(error){status.textContent='現在地を表示できませんでした。もう一度お試しください。';}
  done();
 },function(error){
  status.textContent=error&&error.code===1?'位置情報の利用が許可されていません。ブラウザー・端末の位置情報設定を確認してください。':error&&error.code===3?'現在地の取得が時間切れになりました。もう一度お試しください。':'現在地を取得できませんでした。端末の位置情報設定を確認して再取得してください。';done();
 },{enableHighAccuracy:true,timeout:15000,maximumAge:0});}
 catch(error){status.textContent='位置情報を取得できませんでした。ブラウザー・端末の位置情報設定を確認してください。';done();}
}
"""
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('<div id="town-search-panel">',_V223_LOCATION_DOM+'<div id="town-search-panel">',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',_V223_LOCATION_CSS+'</style></head>',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('function render(args){',_V223_LOCATION_JS+'\nfunction render(args){',1)

# v225: reserve station NAMES first when zoomed in. Long commute labels may
# expand only into space not needed by another visible station's name.
_v225_limit_start=TOWN_COMPONENT_HTML_V121.index(' var limit=zoom>=16?')
_v225_limit_end=TOWN_COMPONENT_HTML_V121.index(';',_v225_limit_start)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121[:_v225_limit_start]+TOWN_COMPONENT_HTML_V121[_v225_limit_start:_v225_limit_end].replace('var limit=','var limit=zoom>=13?stations.length:')+TOWN_COMPONENT_HTML_V121[_v225_limit_end:]
_v225_loop=' for(var j=0;j<visible.length&&count<limit;j++){'
_v225_pre=r"""
 var detail=zoom>=13,namePlacements=[];
 if(detail){
  for(var k=0;k<visible.length;k++){
   var stationName=String(visible[k].name||'');if(!stationName)continue;
   var stationPoint=m.latLngToContainerPoint([visible[k].lat,visible[k].lng]);
   if(reserveLabel(stationPoint,Array.from(stationName).length*12+19,20,boxes,size))namePlacements[k]=boxes[boxes.length-1];
  }
 }
"""
assert TOWN_COMPONENT_HTML_V121.count(_v225_loop)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v225_loop,_v225_pre+_v225_loop,1)
_v225_collision='   if(!reserveLabel(p,width,height,boxes,size))continue;'
_v225_new=r"""   var nameOnly=false;
   if(detail){
    var own=namePlacements[j];if(!own)continue;
    var others=boxes.filter(function(box){return box!==own;});
    if(reserveLabel(p,width,height,others,size))boxes=others;
    else nameOnly=true;
   }else if(!reserveLabel(p,width,height,boxes,size))continue;"""
assert TOWN_COMPONENT_HTML_V121.count(_v225_collision)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v225_collision,_v225_new,1)
_v225_markup="   var html='<span class=\"'+classes+'\">'+content+'</span>';"
assert TOWN_COMPONENT_HTML_V121.count(_v225_markup)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v225_markup,"   if(nameOnly){classes='station-chip v225-name-only';content=safeLabel(name);}\n"+_v225_markup,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',
 '.station-chip.v225-name-only{font-size:12px;line-height:18px;font-weight:850}\n</style></head>',1)

# v226: retain edge station labels without moving their geographic anchors.
_v226_old="""   var stationPoint=m.latLngToContainerPoint([visible[k].lat,visible[k].lng]);
   if(reserveLabel(stationPoint,Array.from(stationName).length*12+19,20,boxes,size))namePlacements[k]=boxes[boxes.length-1];"""
_v226_new="""   var stationPoint=m.latLngToContainerPoint([visible[k].lat,visible[k].lng]);
   var nameWidth=Array.from(stationName).length*12+19;
   var labelPoint={x:Math.max(nameWidth/2+4,Math.min(size.x-nameWidth/2-4,stationPoint.x)),
                   y:Math.max(14,Math.min(size.y-14,stationPoint.y))};
   if(reserveLabel(labelPoint,nameWidth,20,boxes,size)){namePlacements[k]=boxes[boxes.length-1];namePositions[k]=labelPoint;}"""
assert TOWN_COMPONENT_HTML_V121.count(_v226_old)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('var detail=zoom>=13,namePlacements=[];','var detail=zoom>=13,namePlacements=[],namePositions=[];',1).replace(_v226_old,_v226_new,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('if(reserveLabel(p,width,height,others,size))boxes=others;',
 'if(reserveLabel(namePositions[j],width,height,others,size))boxes=others;',1)
_v226_markup="   var html='<span class=\"'+classes+'\">'+content+'</span>';"
_v226_shift=r"""   var offsetX=detail&&namePositions[j]?namePositions[j].x-p.x:0;
   var offsetY=detail&&namePositions[j]?namePositions[j].y-p.y:0;
   var shifted=Math.abs(offsetX)>.5||Math.abs(offsetY)>.5;
   var style=shifted?' style="left:'+offsetX.toFixed(2)+'px;top:'+offsetY.toFixed(2)+'px"':'';
   if(shifted)classes+=' v226-shifted';
   var html='<span class="'+classes+'"'+style+'>'+content+'</span>';
   if(shifted){
    var length=Math.hypot(offsetX,offsetY),angle=Math.atan2(offsetY,offsetX)*180/Math.PI;
    html='<span class="v226-station-leader" style="width:'+length.toFixed(2)+'px;transform:rotate('+angle.toFixed(2)+'deg)"></span>'+html+'<span class="v226-station-anchor"></span>';
   }"""
assert TOWN_COMPONENT_HTML_V121.count(_v226_markup)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v226_markup,_v226_shift,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',
 '.station-chip.v226-shifted::before{display:none}.v226-station-anchor{position:absolute;left:-3px;top:-3px;width:6px;height:6px;box-sizing:border-box;border:1px solid #fff;background:#0064b0;border-radius:50%;pointer-events:none}.v226-station-leader{position:absolute;left:0;top:0;height:1px;background:#173a5e;transform-origin:0 50%;pointer-events:none}\n</style></head>',1)

# v227: hide the floating touch-mode toggle, including its inline display style.
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',
    '#touchmode{display:none!important}\n</style></head>',1)

# v228: third commute destination, preserving the existing station-name layout.
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(' state.commuteDestination2=String(args.commute_destination_2||"");',
 ' state.commuteDestination2=String(args.commute_destination_2||"");\n state.commuteDestination3=String(args.commute_destination_3||"");',1)
_v228_anchor='   var p=m.latLngToContainerPoint([s.lat,s.lng]);'
_v228_third=r"""
   var third=Boolean(state.commuteDestination3);
   var thirdMin=typeof s.commute3_min==='number'&&Number.isFinite(s.commute3_min)&&s.commute3_min>=0&&s.commute3_min<999?Math.round(s.commute3_min):null;
   var thirdTransfers=Number.isInteger(s.commute3_transfers)&&s.commute3_transfers>=0?s.commute3_transfers:null;
   var thirdPct=typeof s.commute3_crowding_pct==='number'&&Number.isFinite(s.commute3_crowding_pct)&&s.commute3_crowding_pct>=0?Math.round(s.commute3_crowding_pct):null;
   var timeLabel3=!third?'':thirdMin===null||thirdTransfers===null?'③未計算':'③'+thirdMin+'分（'+(thirdPct===null?'不明':'参考'+thirdPct+'%')+'）-'+thirdTransfers+'回';
"""
assert TOWN_COMPONENT_HTML_V121.count(_v228_anchor)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v228_anchor,_v228_third+_v228_anchor,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('Array.from(timeLabel2).length)*(fontSize*.83)',
 'Array.from(timeLabel2).length,Array.from(timeLabel3).length)*(fontSize*.83)',1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('var height=dual?(compact?34:(major?41:37)):',
 'var height=third?(compact?51:(major?63:57)):dual?(compact?34:(major?41:37)):',1)
_v228_old="'<span class=\"station-grid-spacer\" aria-hidden=\"true\"></span><span class=\"commute-time second\">'+safeLabel(timeLabel2)+'</span></span>':"
_v228_new="'<span class=\"station-grid-spacer\" aria-hidden=\"true\"></span><span class=\"commute-time second\">'+safeLabel(timeLabel2)+'</span>'+(third?'<span class=\"commute-time third\">'+safeLabel(timeLabel3)+'</span>':'')+'</span>':"
assert TOWN_COMPONENT_HTML_V121.count(_v228_old)==1
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace(_v228_old,_v228_new,1)
TOWN_COMPONENT_HTML_V121=TOWN_COMPONENT_HTML_V121.replace('</style></head>',
 '.station-chip.dual .commute-time.third{grid-column:2;grid-row:3}\n</style></head>',1)

@st.cache_resource(show_spinner=False)
def town_component_v121():
    _asset_revision='v228-three-commute-destinations'
    folder=os.path.join(tempfile.gettempdir(),'sumai-town-map-component-'+_asset_revision)
    os.makedirs(folder,exist_ok=True)
    path=os.path.join(folder,'index.html')
    # Component HTML is small; create once per worker process. Never include
    # any Supabase key or listing details in this static file.
    # Prevent stale legend-less HTML, including a same-byte-size predecessor.
    current=None
    try:
        with open(path,'r',encoding='utf-8') as previous:
            current=previous.read()
    except OSError:
        pass
    if current != TOWN_COMPONENT_HTML_V121:
        temp_path=path+'.tmp-'+str(os.getpid())
        with open(temp_path,'w',encoding='utf-8') as output:
            output.write(TOWN_COMPONENT_HTML_V121)
        os.replace(temp_path,path)
    return st.components.v1.declare_component('sumai_town_map_v228',path=folder)

@st.cache_resource(show_spinner=False)
def _v216_api_budget():
    # Survives ordinary Streamlit reruns; process restarts reset usage.
    return {'lock':threading.Lock(),'uses':{}}


# v159: Station-to-office approximate train travel times, OFFLINE AND READ-ONLY.
# Tokyo Metro/Toei/JR local edge minutes adapted from MIT-licensed public project:
# https://github.com/select766/tokyo-train-time-map (data/stations.csv; older timetable data)
# 2017-based core data, 2026 supplemented stations; current timetable NOT guaranteed.
# The private-rail links below use distance-based ESTIMATES, not published schedules.
# No transit API key, network request, DB update or dependency is needed.
# Explicit edge times are the minutes from previous station -> current station.
_COMMUTE_PUBLISHED_LINES = r"""
銀座線|渋谷 表参道:2 外苑前:2 青山一丁目:1 赤坂見附:2 溜池山王:2 虎ノ門:2 新橋:2 銀座:2 京橋:2 日本橋:2 三越前:2 神田:2 末広町:1 上野広小路:2 上野:1 稲荷町:2 田原町:2 浅草:1
丸ノ内線|荻窪 南阿佐ヶ谷:2 新高円寺:2 東高円寺:2 新中野:2 中野坂上:2 西新宿:2 新宿:2 新宿三丁目:2 新宿御苑前:1 四谷三丁目:2 四ツ谷:2 赤坂見附:3 国会議事堂前:2 霞ヶ関:2 銀座:3 東京:2 大手町:2 淡路町:2 御茶ノ水:1 本郷三丁目:2 後楽園:2 茗荷谷:3 新大塚:2 池袋:3
丸ノ内線支線|中野坂上 中野新橋:2 中野富士見町:2 方南町:2
日比谷線|中目黒 恵比寿:2 広尾:3 六本木:3 神谷町:4 虎ノ門ヒルズ:1 霞ヶ関:2 日比谷:2 銀座:2 東銀座:1 築地:2 八丁堀:2 茅場町:2 人形町:2 小伝馬町:2 秋葉原:2 仲御徒町:1 上野:2 入谷:2 三ノ輪:2 南千住:2 北千住:3
東西線|中野 落合:3 高田馬場:3 早稲田:3 神楽坂:2 飯田橋:2 九段下:2 竹橋:2 大手町:2 日本橋:2 茅場町:1 門前仲町:3 木場:2 東陽町:1 南砂町:3 西葛西:3 葛西:2 浦安:2 南行徳:2 行徳:2 妙典:2 原木中山:3 西船橋:3
千代田線|代々木上原 代々木公園:2 明治神宮前:2 表参道:1 乃木坂:3 赤坂:2 国会議事堂前:2 霞ヶ関:1 日比谷:2 二重橋前:2 大手町:2 新御茶ノ水:2 湯島:2 根津:2 千駄木:2 西日暮里:2 町屋:2 北千住:4 綾瀬:3 北綾瀬:4
有楽町線|和光市 地下鉄成増:3 地下鉄赤塚:2 平和台:3 氷川台:2 小竹向原:2 千川:2 要町:2 池袋:3 東池袋:2 護国寺:2 江戸川橋:3 飯田橋:2 市ヶ谷:3 麹町:2 永田町:1 桜田門:2 有楽町:2 銀座一丁目:2 新富町:1 月島:3 豊洲:2 辰巳:2 新木場:3
副都心線|和光市 地下鉄成増:3 地下鉄赤塚:2 平和台:3 氷川台:2 小竹向原:2 千川:2 要町:2 池袋:2 雑司が谷:3 西早稲田:3 東新宿:2 新宿三丁目:2 北参道:3 明治神宮前:2 渋谷:2
半蔵門線|渋谷 表参道:2 青山一丁目:2 永田町:3 半蔵門:2 九段下:2 神保町:1 大手町:3 三越前:2 水天宮前:3 清澄白河:3 住吉:3 錦糸町:2 押上:2
南北線|目黒 白金台:2 白金高輪:2 麻布十番:3 六本木一丁目:2 溜池山王:2 永田町:2 四ツ谷:2 市ヶ谷:2 飯田橋:2 後楽園:2 東大前:3 本駒込:2 駒込:2 西ヶ原:2 王子:2 王子神谷:3 志茂:2 赤羽岩淵:2
浅草線|西馬込 馬込:1 中延:2 戸越:2 五反田:2 高輪台:2 泉岳寺:2 三田:3 大門:2 新橋:2 東銀座:2 宝町:2 日本橋:1 人形町:2 東日本橋:2 浅草橋:1 蔵前:2 浅草:2 本所吾妻橋:1 押上:2
三田線|目黒 白金台:2 白金高輪:2 三田:3 芝公園:2 御成門:1 内幸町:2 日比谷:2 大手町:2 神保町:3 水道橋:2 春日:2 白山:2 千石:2 巣鴨:2 西巣鴨:2 新板橋:2 板橋区役所前:2 板橋本町:2 本蓮沼:1 志村坂上:2 志村三丁目:2 蓮根:2 西台:2 高島平:2 新高島平:2 西高島平:2
新宿線|新宿 新宿三丁目:1 曙橋:2 市ヶ谷:2 九段下:3 神保町:1 小川町:2 岩本町:2 馬喰横山:1 浜町:2 森下:1 菊川:2 住吉:2 西大島:1 大島:2 東大島:2 船堀:3 一之江:2 瑞江:3 篠崎:2 本八幡:4
大江戸線|新宿西口 東新宿:3 若松河田:2 牛込柳町:2 牛込神楽坂:2 飯田橋:2 春日:3 本郷三丁目:1 上野御徒町:3 新御徒町:1 蔵前:2 両国:3 森下:1 清澄白河:2 門前仲町:3 月島:2 勝どき:2 築地市場:3 汐留:2 大門:2 赤羽橋:3 麻布十番:2 六本木:2 青山一丁目:3 国立競技場:2 代々木:3 新宿:2 都庁前:1 西新宿五丁目:1 中野坂上:2 東中野:2 中井:2 落合南長崎:3 新江古田:2 練馬:3 豊島園:2 練馬春日町:2 光が丘:2
大江戸線都庁前連絡|都庁前 新宿西口:1
山手線|東京 有楽町:2 新橋:2 浜松町:2 田町:3 高輪ゲートウェイ:2 品川:2 大崎:3 五反田:2 目黒:2 恵比寿:3 渋谷:2 原宿:3 代々木:2 新宿:2 新大久保:2 高田馬場:2 目白:2 池袋:3 大塚:2 巣鴨:2 駒込:2 田端:2 西日暮里:2 日暮里:1 鶯谷:2 上野:2 御徒町:2 秋葉原:2 神田:2 東京:2
中央総武線|津田沼 東船橋:3 船橋:2 西船橋:3 下総中山:3 本八幡:3 市川:2 小岩:3 新小岩:4 平井:2 亀戸:3 錦糸町:2 両国:3 浅草橋:2 秋葉原:2 御茶ノ水:2 水道橋:2 飯田橋:2 市ヶ谷:2 四ツ谷:2 信濃町:2 千駄ヶ谷:2 代々木:2 新宿:2 大久保:2 東中野:2 中野:3 高円寺:2 阿佐ヶ谷:2 荻窪:2 西荻窪:3 吉祥寺:2 三鷹:3
京葉線（概算）|東京 八丁堀:3 越中島:2 潮見:3 新木場:3
""".strip()

# Supplemental operating private rail lines; station order curated, minutes
# estimated from adjacent station coordinates (never present as timetable).
_COMMUTE_APPROX_LINES = r"""
西武池袋線|池袋 椎名町 東長崎 江古田 桜台 練馬 中村橋 富士見台 練馬高野台 石神井公園 大泉学園
西武新宿線|西武新宿 高田馬場 下落合 中井 新井薬師前 沼袋 野方 都立家政 鷺ノ宮 下井草 井荻 上井草 上石神井 武蔵関
東武東上線|池袋 北池袋 下板橋 大山 中板橋 ときわ台 上板橋 東武練馬 下赤塚 成増
東急東横線|渋谷 代官山 中目黒 祐天寺 学芸大学 都立大学 自由が丘 田園調布 多摩川 新丸子 武蔵小杉 日吉 横浜
東急田園都市線|渋谷 池尻大橋 三軒茶屋 駒沢大学 桜新町 用賀 二子玉川 溝の口
東急目黒線|目黒 不動前 武蔵小山 西小山 洗足 大岡山 奥沢 田園調布 多摩川 新丸子 武蔵小杉
東急大井町線|大井町 下神明 戸越公園 中延 荏原町 旗の台 北千束 大岡山 緑が丘 自由が丘 九品仏 尾山台 等々力 上野毛 二子玉川
東急池上線|五反田 大崎広小路 戸越銀座 荏原中延 旗の台 長原 洗足池 石川台 雪が谷大塚 御嶽山 久が原 千鳥町 池上 蓮沼 蒲田
京王線|新宿 笹塚 代田橋 明大前 下高井戸 桜上水 上北沢 八幡山 芦花公園 千歳烏山 仙川
京王井の頭線|渋谷 神泉 駒場東大前 池ノ上 下北沢 新代田 東松原 明大前 永福町 西永福 浜田山 高井戸 富士見ヶ丘 久我山 吉祥寺
小田急小田原線|新宿 南新宿 参宮橋 代々木八幡 代々木上原 東北沢 下北沢 世田谷代田 梅ヶ丘 豪徳寺 経堂 千歳船橋 祖師ヶ谷大蔵 成城学園前
京急本線|品川 北品川 新馬場 青物横丁 鮫洲 立会川 大森海岸 平和島 大森町 梅屋敷 京急蒲田 京急川崎 横浜
東武スカイツリーライン|浅草 とうきょうスカイツリー 曳舟 東向島 鐘ヶ淵 堀切 牛田 北千住 小菅 五反野 梅島 西新井 竹ノ塚
京成本線|京成上野 日暮里 新三河島 町屋 千住大橋 京成関屋 堀切菖蒲園 お花茶屋 青砥 京成高砂 京成小岩
京成押上線|押上 京成曳舟 八広 四ツ木 京成立石 青砥
南武線（各停概算）|川崎 尻手 矢向 鹿島田 平間 向河原 武蔵小杉 武蔵中原 武蔵新城 武蔵溝ノ口 登戸 立川
りんかい線（概算）|大崎 大井町 品川シーサイド 天王洲アイル 東京テレポート 国際展示場 東雲 新木場
""".strip()

_COMMUTE_WALK_CONNECTIONS = r"""
赤坂見附 永田町
溜池山王 国会議事堂前
三越前 新日本橋
上野広小路 仲御徒町
上野広小路 上野御徒町
上野広小路 御徒町
仲御徒町 上野御徒町
仲御徒町 御徒町
上野御徒町 御徒町
新宿 新宿西口
淡路町 新御茶ノ水
淡路町 小川町
新御茶ノ水 小川町
後楽園 春日
日比谷 有楽町
秋葉原 岩本町
明治神宮前 原宿
三田 田町
大門 浜松町
東日本橋 馬喰横山
東日本橋 馬喰町
馬喰横山 馬喰町
虎ノ門ヒルズ 虎ノ門
高輪ゲートウェイ 泉岳寺
京成上野 上野
西武新宿 新宿
武蔵溝ノ口 溝の口
京急川崎 川崎
""".strip()

# Canonical station aliases (different script conventions, same station).
_COMMUTE_ALIASES={'霞ケ関':'霞ヶ関','霞が関':'霞ヶ関','明治神宮前〈原宿〉':'明治神宮前',
                  '武蔵溝ノ口':'溝の口'}

def _commute_key(name):
    name=str(name or '').strip()
    return _COMMUTE_ALIASES.get(name,name)

@__import__('functools').lru_cache(maxsize=1)
def commute_network_v159():
    """Undirected edge graph. All route costs are transparent, approximate minutes."""
    from collections import defaultdict
    edges=defaultdict(list)
    def link(a,b,line,mins):
        a,b=_commute_key(a),_commute_key(b)
        if a and b and a!=b and 0<mins<120:
            edges[a].append((b,line,float(mins)))
            edges[b].append((a,line,float(mins)))
    for row in _COMMUTE_PUBLISHED_LINES.splitlines():
        line,content=row.split('|',1);words=content.split()
        previous=words[0]
        for word in words[1:]:
            destination,duration=word.rsplit(':',1)
            link(previous,destination,line,float(duration))
            previous=destination
    for row in _COMMUTE_APPROX_LINES.splitlines():
        line,content=row.split('|',1);words=content.split()
        for a,b in zip(words,words[1:]):
            pa=STATION_MAP_POINTS.get(a) or STATIONS.get(a)
            pb=STATION_MAP_POINTS.get(b) or STATIONS.get(b)
            if pa is None or pb is None:
                continue   # Unknown coordinates: no invented rail travel time
            meters_between=meters(pa,pb)
            if not 100 <= meters_between <= 13000:
                continue   # Implausible link, do not fabricate duration
            estimate=max(1.5,min(18.0,meters_between*1.25/1000/28*60+0.7))
            link(a,b,line,estimate)
    for line,stations in LINES.items():
        if line=='山手線':continue  # The source above has all individual stops.
        for a,b in zip(stations,stations[1:]):
            length=meters(STATIONS[a],STATIONS[b]);mins=max(2.0,length*1.15/1000/40*60)
            # The 41-stop legacy express graph skips intermediate stations.
            # Avoid interpreting Tokyo-Shinjuku straight-line time as timetable time.
            if line=='中央線快速' and a=='東京' and b=='新宿':mins=max(14.0,mins)
            link(a,b,line,mins)
    for row in _COMMUTE_WALK_CONNECTIONS.splitlines():
        a,b=row.split();link(a,b,'徒歩連絡',5.0)
    return dict(edges)


# v162: repair unreachable DISPLAYED stations. The existing v161 station labels
# contain many valid stations omitted from the travel-time graph. This extension
# adds real, ordered operating routes, with publicly listed adjacent-stop minutes
# where available and explicitly approximate geometry-based connections elsewhere.
# Source (MIT project, timetable approximations, not real-time):
# https://github.com/select766/tokyo-train-time-map/blob/master/data/stations.csv
# Tokyo Metropolitan Government route names/stations (Nippori-Toneri Liner):
# https://www.kotsu.metro.tokyo.jp/nippori_toneri/map/
# v203: Nippori-Toneri minutes follow Toei Transportation Guide 2025 p25.
# https://www.kotsu.metro.tokyo.jp/toei-guide/guide2025.pdf
# Cumulative minutes from Nippori: 0,1,3,5,6,8,10,11,13,14,16,18,20.
# The values are published travel-time guidelines, not departure-specific schedules.
_COMMUTE_V162_TIMED_LINES = r"""
日暮里・舎人ライナー|日暮里 西日暮里:1 赤土小学校前:2 熊野前:2 足立小台:1 扇大橋:2 高野:2 江北:1 西新井大師西:2 谷在家:1 舎人公園:2 舎人:2 見沼代親水公園:2
ゆりかもめ|新橋 汐留:1 竹芝:3 日の出:2 芝浦ふ頭:1 お台場海浜公園:6 台場:2 東京国際クルーズターミナル:2 テレコムセンター:2 青海:2 東京ビッグサイト:2 有明(ゆりかもめ):2 有明テニスの森:2 市場前:2 新豊洲:1 豊洲:2
都電荒川線|面影橋 学習院下:2 鬼子母神前:1 都電雑司ヶ谷:2 東池袋四丁目:2 向原:2 大塚駅前:5 巣鴨新田:1 庚申塚:1 新庚申塚:1 西ヶ原四丁目:3 滝野川一丁目:1 飛鳥山:1 王子駅前:3 栄町:1 梶原:1 荒川車庫前:4 荒川遊園地前:1 小台:2 宮ノ前:1 熊野前:5 東尾久三丁目:1 町屋二丁目:1 町屋駅前:4 荒川七丁目:1 荒川二丁目:1 荒川区役所前:1 荒川一中前:1 三ノ輪橋:1
埼京線|浮間舟渡 北赤羽:2 赤羽:2 十条:2 板橋:2 池袋:3 新宿:5 渋谷:6 恵比寿:2 大崎:4
京浜東北線|赤羽 東十条:3 王子:3 上中里:1 田端:3 西日暮里:2 日暮里:1 鶯谷:2 上野:2 御徒町:2 秋葉原:2 神田:2 東京:2 有楽町:2 新橋:2 浜松町:2 田町:3 高輪ゲートウェイ:2 品川:2 大井町:3 大森:2 蒲田:5
宇都宮線・東海道線|赤羽 尾久:4 上野:6 東京:5 新橋:2 品川:5
常磐快速線|北千住 南千住:2 三河島:3 日暮里:2 上野:4 東京:5 新橋:2 品川:5
常磐線各駅停車|綾瀬 亀有:2 金町:2
横須賀線・総武快速線|西大井 品川:5 新橋:3 東京:3 新日本橋:2 馬喰町:2 錦糸町:4 新小岩:4 市川:5 船橋:6 津田沼:4
京葉線|新木場 葛西臨海公園:3
""".strip()

# The following are REAL, running railway lines. Stops missing from the map's
# offline coordinate dictionary are retained as route order markers, not assigned
# fabricated positions. Adjacent known stops are bridged on that exact line,
# with a conservative distance + number-of-intermediate-stops time estimate.
_COMMUTE_V162_APPROX_LINES = r"""
京王新線|新宿 初台 幡ヶ谷 笹塚
西武有楽町線|練馬 新桜台 小竹向原
東急多摩川線|多摩川 沼部 鵜の木 下丸子 武蔵新田 矢口渡 蒲田
東急世田谷線|三軒茶屋 西太子堂 若林 松陰神社前 世田谷 上町 宮の坂 山下 松原 下高井戸
東京モノレール|浜松町 天王洲アイル 大井競馬場前 流通センター 昭和島 整備場 天空橋 羽田空港第3ターミナル 羽田空港第1ターミナル 羽田空港第2ターミナル
京急空港線|京急蒲田 糀谷 大鳥居 穴守稲荷 天空橋 羽田空港第3ターミナル 羽田空港第1・第2ターミナル
東武亀戸線|曳舟 小村井 東あずま 亀戸水神 亀戸
""".strip()

# Distinct tram and metro stations should NOT be merged just because their names
# resemble one another. Explicit walking links carry a finite journey time.
_COMMUTE_V162_WALKS = (
    ('三ノ輪','三ノ輪橋',6), ('大塚','大塚駅前',3),
    ('王子','王子駅前',3), ('町屋','町屋駅前',3),
    ('雑司が谷','鬼子母神前',4),
    ('国際展示場','有明(ゆりかもめ)',5),
    ('東京テレポート','お台場海浜公園',5),
    # JR蒲田と京急蒲田は同一駅ではない: 連絡徒歩を区別する。
    ('蒲田','京急蒲田',12),
)

@__import__('functools').lru_cache(maxsize=1)
def commute_network_v162():
    """Augment v161's graph without changing its saved data or existing routes."""
    from collections import defaultdict
    graph=defaultdict(list,{name:list(links) for name,links in commute_network_v159().items()})

    def link(a,b,line,minutes):
        a,b=_commute_key(a),_commute_key(b)
        if a and b and a!=b and 0 < minutes < 150:
            graph[a].append((b,line,float(minutes)))
            graph[b].append((a,line,float(minutes)))

    for row in _COMMUTE_V162_TIMED_LINES.splitlines():
        if not row.strip():continue
        line,text=row.split('|',1)
        tokens=text.split()
        prev=tokens[0]
        for token in tokens[1:]:
            nxt,mins=token.rsplit(':',1)
            link(prev,nxt,line,float(mins))
            prev=nxt

    # Older private lines currently SKIP every edge touching an unlocated stop.
    # Bridge only the missing intermediate runs, preserving the existing segments.
    for data,new_line in ((_COMMUTE_APPROX_LINES,False),(_COMMUTE_V162_APPROX_LINES,True)):
        for row in data.splitlines():
            if not row.strip():continue
            line,stations=row.split('|',1)
            good=[]
            for index,name in enumerate(stations.split()):
                point=STATION_MAP_POINTS.get(name) or STATIONS.get(name)
                if point is not None:
                    good.append((index,name,point))
            for (i,a,pa),(j,b,pb) in zip(good,good[1:]):
                hops=j-i
                if not new_line and hops==1:
                    continue
                distance=meters(pa,pb)
                if not 90 <= distance <= 13000*hops:
                    continue
                # v161's 28km/h per-ride formula, adding dwell time per skipped stop.
                minutes=max(1.5*hops,min(18.0*hops,distance*1.25/1000/28*60+0.7*hops))
                link(a,b,line,minutes)

    # Actual rail connections for stations whose coordinates are not in the
    # label-only reference table. They are train edges, not made-up map points.
    # Useful especially for a route via 京急蒲田 to 羽田方面.
    for a,b,line,mins in (
        ('品川','京急蒲田','京急本線（概算）',13.0),
        ('京急蒲田','糀谷','京急空港線',2.5),
    ):
        link(a,b,line,mins)

    for a,b,mins in _COMMUTE_V162_WALKS:
        link(a,b,'徒歩連絡',float(mins))
    return dict(graph)

@__import__('functools').lru_cache(maxsize=32)
def commute_times_to_station_v159(destination, with_transfers=False):
    """One-to-all routing: FIRST minimize transfers, THEN minutes; 5min/change.

    Routing uses app-local approximate edges, not real timetable departures.
    Both the one-destination and two-destination labels use this same policy.
    """
    destination=_commute_key(destination)
    graph=commute_network_v162()
    if destination not in graph:return {}
    # State: (station, preceding line, walking connector used).
    # Heap ordering MUST be (transfer count, total travel minutes), not
    # (minutes, transfers). This makes Ikebukuro -> Tamachi prefer the
    # through Yamanote line instead of the faster Saikyo + Yamanote route.
    # Track whether the previous edge was an interchange walk.
    # A walking connection already consumes the required transfer time.
    # Crucially, it MUST NOT erase the boarded line, or changing from Metro
    # to tram/another operator is incorrectly displayed as "0回".
    todo=[(0,0.0,destination,'',False)]
    seen=set();best={}
    while todo:
        changes,time_used,station,last_line,walked=heapq.heappop(todo)
        state=(station,last_line,walked)
        if state in seen:continue
        seen.add(state)
        old=best.get(station)
        if old is None or (changes,time_used)<(old[1],old[0]):
            best[station]=(time_used,changes)
        for next_station,line,ride_time in graph.get(station,()):
            if line=='徒歩連絡':
                # Keep the previous railway line through the walkway.
                transfer=False
                extra=0.0
                new_line=last_line
                new_walked=True
            else:
                transfer=bool(last_line and last_line!=line)
                extra=5.0 if transfer and not walked else 0.0
                new_line=line
                new_walked=False
            key=(next_station,new_line,new_walked)
            if key not in seen:
                heapq.heappush(todo,(changes+int(transfer),time_used+ride_time+extra,
                                     next_station,new_line,new_walked))
    # Minutes and transfers are read from the SAME minimum-transfer path.
    # The default integer-minute return is backward compatible with v159/v160.
    result={name:(0 if name==destination else max(1,int(math.ceil(val[0]))),int(val[1]))
            for name,val in best.items()}
    return result if with_transfers else {name:mins for name,(mins,_changes) in result.items()}

def commute_destinations_v159():
    # Offer only stations connected to Tokyo's main operating network.
    # Small disconnected reference segments are excluded from selectable targets.
    reachable=commute_times_to_station_v159('東京')
    return ['表示しない']+sorted(name for name in reachable if name in STATION_MAP_POINTS or name in STATIONS)


# v160: destination selection follows the operating line, then its stations.
# This is a presentation change only; the v159 graph and minute estimates stay
# identical, and the existing commute_destination_v159 state remains compatible.
_COMMUTE_V160_NO_LINE = '\u8def\u7dda\u3092\u9078\u629e\u3057\u3066\u304f\u3060\u3055\u3044'
_COMMUTE_V160_NO_STATION = '\u99c5\u3092\u9078\u629e\u3057\u3066\u304f\u3060\u3055\u3044'
_COMMUTE_V160_METRO = frozenset("""
\u9280\u5ea7\u7dda \u4e38\u30ce\u5185\u7dda \u4e38\u30ce\u5185\u7dda\u652f\u7dda \u65e5\u6bd4\u8c37\u7dda \u6771\u897f\u7dda \u5343\u4ee3\u7530\u7dda
\u6709\u697d\u753a\u7dda \u526f\u90fd\u5fc3\u7dda \u534a\u8535\u9580\u7dda \u5357\u5317\u7dda""".split())
_COMMUTE_V160_TOEI = frozenset("""
\u6d45\u8349\u7dda \u4e09\u7530\u7dda \u65b0\u5bbf\u7dda \u5927\u6c5f\u6238\u7dda""".split())
_COMMUTE_V160_JR = frozenset("""
\u5c71\u624b\u7dda \u4e2d\u592e\u7dcf\u6b66\u7dda \u4eac\u8449\u7dda \u5357\u6b66\u7dda \u4eac\u6d5c\u6771\u5317\u7dda \u57fc\u4eac\u7dda
\u4e2d\u592e\u7dda\u5feb\u901f \u6771\u6d77\u9053\u7dda \u6a2a\u9808\u8cc0\u7dda \u7dcf\u6b66\u7dda\u5feb\u901f \u5e38\u78d0\u7dda\u5feb\u901f""".split())
_COMMUTE_V160_LINE_ORDER = (
    '\u5c71\u624b\u7dda','\u4eac\u6d5c\u6771\u5317\u7dda','\u57fc\u4eac\u7dda',
    '\u4e2d\u592e\u7dda\u5feb\u901f','\u4e2d\u592e\u7dcf\u6b66\u7dda',
    '\u6771\u6d77\u9053\u7dda','\u6a2a\u9808\u8cc0\u7dda','\u7dcf\u6b66\u7dda\u5feb\u901f',
    '\u5e38\u78d0\u7dda\u5feb\u901f','\u4eac\u8449\u7dda','\u5357\u6b66\u7dda',
    '\u9280\u5ea7\u7dda','\u4e38\u30ce\u5185\u7dda','\u4e38\u30ce\u5185\u7dda\u652f\u7dda',
    '\u65e5\u6bd4\u8c37\u7dda','\u6771\u897f\u7dda','\u5343\u4ee3\u7530\u7dda',
    '\u6709\u697d\u753a\u7dda','\u526f\u90fd\u5fc3\u7dda','\u534a\u8535\u9580\u7dda',
    '\u5357\u5317\u7dda','\u6d45\u8349\u7dda','\u4e09\u7530\u7dda',
    '\u65b0\u5bbf\u7dda','\u5927\u6c5f\u6238\u7dda',
)

def _commute_v160_normalized_line(raw):
    # Internal connectors and alternate names are not separate selectable routes.
    aliases = {
        '\u4eac\u8449\u7dda\uff08\u6982\u7b97\uff09':'\u4eac\u8449\u7dda',
        '\u5357\u6b66\u7dda\uff08\u5404\u505c\u6982\u7b97\uff09':'\u5357\u6b66\u7dda',
        '\u308a\u3093\u304b\u3044\u7dda\uff08\u6982\u7b97\uff09':'\u308a\u3093\u304b\u3044\u7dda',
        '\u6771\u6a2a\u7dda':'\u6771\u6025\u6771\u6a2a\u7dda',
        '\u7530\u5712\u90fd\u5e02\u7dda':'\u6771\u6025\u7530\u5712\u90fd\u5e02\u7dda',
        '\u5927\u6c5f\u6238\u7dda\u90fd\u5e81\u524d\u9023\u7d61':'\u5927\u6c5f\u6238\u7dda',
    }
    return aliases.get(raw,raw)

def _commute_v160_line_label(name):
    if name in _COMMUTE_V160_JR:
        public = '\u4e2d\u592e\u30fb\u7dcf\u6b66\u7dda\uff08\u5404\u99c5\u505c\u8eca\uff09' if name=='\u4e2d\u592e\u7dcf\u6b66\u7dda' else name
        return 'JR \uff5c '+public
    if name in _COMMUTE_V160_METRO:
        public = '\u4e38\u30ce\u5185\u7dda\uff08\u65b9\u5357\u753a\u65b9\u9762\uff09' if name=='\u4e38\u30ce\u5185\u7dda\u652f\u7dda' else name
        return '\u6771\u4eac\u30e1\u30c8\u30ed \uff5c '+public
    if name in _COMMUTE_V160_TOEI:
        return '\u90fd\u55b6\u5730\u4e0b\u9244 \uff5c '+name
    if name.startswith('\u6771\u6025'):
        return name
    return name

@__import__('functools').lru_cache(maxsize=1)
def commute_lines_v160():
    """Ordered line names -> connected station names; no external requests."""
    available=set(commute_destinations_v159())
    available.discard('\u8868\u793a\u3057\u306a\u3044')
    combined={}

    def add(line, stations):
        line=_commute_v160_normalized_line(line)
        if line not in combined: combined[line]=[]
        for original in stations:
            station=_commute_key(original.split(':',1)[0])
            if station in available and station not in combined[line]:
                combined[line].append(station)

    # Published-minute lines are in physical order. Private/JR legacy route
    # names are merged into this same, ordered set of options.
    for source in (_COMMUTE_PUBLISHED_LINES,_COMMUTE_APPROX_LINES,
                   _COMMUTE_V162_TIMED_LINES,_COMMUTE_V162_APPROX_LINES):
        for raw in source.splitlines():
            if not raw.strip():continue
            name,points=raw.split('|',1)
            add(name,points.split())
    for name,stations in LINES.items():
        if name=='\u7dcf\u6b66\u7dda\u5feb\u901f':
            # These real Sobu-Rapid stops were in v159 only via walk links.
            # Make them findable without modifying the legacy route calculator.
            stations=[stations[0],'\u65b0\u65e5\u672c\u6a4b','\u99ac\u55b0\u753a',*stations[1:]]
        add(name,stations)

    combined={key:value for key,value in combined.items() if len(value)>=2}
    ordering={v:i for i,v in enumerate(_COMMUTE_V160_LINE_ORDER)}
    return dict(sorted(
        ((_commute_v160_line_label(k),v) for k,v in combined.items()),
        key=lambda pair:(
            0 if pair[0].startswith('JR ') else 1 if pair[0].startswith('\u6771\u4eac\u30e1\u30c8\u30ed ')
            else 2 if pair[0].startswith('\u90fd\u55b6\u5730\u4e0b\u9244 ') else 3,
            ordering.get(next((name for name in combined if _commute_v160_line_label(name)==pair[0]),''),999),
            pair[0]
        )
    ))

def _commute_line_changed_v160():
    # Drop an old station on line changes; never leave a mismatched destination.
    st.session_state['commute_station_v160']=_COMMUTE_V160_NO_STATION
    st.session_state['commute_destination_v159']='\u8868\u793a\u3057\u306a\u3044'
    _v180_commit_commute_change()

def commute_current_destination_v160():
    line=st.session_state.get('commute_line_v160')
    station=st.session_state.get('commute_station_v160')
    if line is None and station is None:
        # Upgrade-safe: keep a v159 choice until the new widgets first render.
        return st.session_state.get('commute_destination_v159','\u8868\u793a\u3057\u306a\u3044')
    return station if station in commute_lines_v160().get(line,()) else '\u8868\u793a\u3057\u306a\u3044'

# v178: 2025年度最混雑1時間の路線別代表区間。国土交通省の公表値。
# https://www.mlit.go.jp/report/press/content/002015142.pdf (資料3-1～3-4)
# 注意: 各駅間の実測値ではない。代表区間値を経路上の乗車時間で加重した
# 「参考混雑率」を表示する。データ欠落区間があれば数値を示さない。
_COMMUTE_CROWD_2025_V178 = {
    # MLIT 2025, table 3-4: busiest hour 7:30-8:30, not each segment's actual crowding.
    '日暮里・舎人ライナー':(176,'赤土小学校前→西日暮里'),
    '山手線':(136,'新大久保→新宿（内回り）'),
    '京浜東北線':(158,'川口→赤羽'),
    '埼京線':(167,'板橋→池袋'),
    '中央線快速':(158,'中野→新宿'),
    '中央総武線':(151,'錦糸町→両国'),
    '東海道線':(159,'川崎→品川'),
    '横須賀線':(138,'武蔵小杉→西大井'),
    '総武線快速':(160,'新小岩→錦糸町'),
    '常磐線快速':(142,'三河島→日暮里'),
    '常磐快速線':(142,'三河島→日暮里'),  # Same line name used by the v162 transfer graph.
    '南武線':(156,'武蔵中原→武蔵小杉'),
    '南武線（各停概算）':(156,'武蔵中原→武蔵小杉'),
    '京葉線（概算）':(123,'葛西臨海公園→新木場'),
    '銀座線':(153,'赤坂見附→溜池山王'),
    '丸ノ内線':(154,'新大塚→茗荷谷'),
    '日比谷線':(163,'三ノ輪→入谷'),
    '東西線':(160,'木場→門前仲町'),
    '千代田線':(150,'町屋→西日暮里'),
    '有楽町線':(153,'東池袋→護国寺'),
    '半蔵門線':(111,'渋谷→表参道'),
    '副都心線':(117,'要町→池袋'),
    '南北線':(156,'駒込→本駒込'),
    '浅草線':(123,'本所吾妻橋→浅草'),
    '三田線':(146,'西巣鴨→巣鴨'),
    '新宿線':(152,'西大島→住吉'),
    '大江戸線':(147,'中井→東中野'),
    '東武スカイツリーライン':(142,'小菅→北千住'),
    '東武東上線':(131,'北池袋→池袋'),
    '西武池袋線':(130,'椎名町→池袋'),
    '西武新宿線':(148,'下落合→高田馬場'),
    '京成本線':(137,'大神宮下→京成船橋'),
    '京成押上線':(157,'京成曳舟→押上'),
    '京王線':(145,'下高井戸→明大前'),
    '京王井の頭線':(125,'池ノ上→駒場東大前'),
    '小田急小田原線':(150,'世田谷代田→下北沢'),
    '東急東横線':(124,'祐天寺→中目黒'),
    '東横線':(124,'祐天寺→中目黒'),
    '東急田園都市線':(138,'池尻大橋→渋谷'),
    '田園都市線':(138,'池尻大橋→渋谷'),
    '東急目黒線':(133,'不動前→目黒'),
    '東急大井町線':(118,'九品仏→自由が丘'),
    '東急池上線':(130,'大崎広小路→五反田'),
    '東急多摩川線':(112,'矢口渡→蒲田'),
    '京急本線':(120,'戸部→横浜'),
    '京急本線（概算）':(120,'戸部→横浜'),
    'りんかい線（概算）':(118,'大井町→品川シーサイド'),
}
_COMMUTE_CROWD_SOURCE_V178 = 'https://www.mlit.go.jp/report/press/content/002015142.pdf'


@__import__('functools').lru_cache(maxsize=16)
def commute_reference_crowd_v178(destination):
    """Return route minutes, transfers and approximate crowd indicator by station.

    This uses exactly the v159 transfer-first Dijkstra cost function. The rail
    segments are weighted by their actual route edge times, but each segment's
    *crowding* is only that line's published busiest-section reference.
    A missing rail-line rate yields None (not a fabricated route average).
    """
    destination=_commute_key(destination)
    graph=commute_network_v162()
    if destination not in graph:return {}
    # Preserve original five-field ordering: (transfers, minutes, station,
    # previous line, walked). Later fields never change path priority.
    pending=[(0,0.0,destination,'',False,0.0,0.0,0.0)]
    visited=set();best={}
    while pending:
        transfers,elapsed,station,last_line,walked,total_weight,covered,rail_minutes=heapq.heappop(pending)
        key=(station,last_line,walked)
        if key in visited:continue
        visited.add(key)
        previous=best.get(station)
        if previous is None or (transfers,elapsed)<(previous[1],previous[0]):
            best[station]=(elapsed,transfers,total_weight,covered,rail_minutes)
        for neighbor,line,minutes in graph.get(station,()):
            if line=='徒歩連絡':
                change=False;extra=0.0
                next_line=last_line;next_walked=True
                new_weight=total_weight;new_covered=covered;new_rail=rail_minutes
            else:
                change=bool(last_line and last_line!=line)
                extra=5.0 if change and not walked else 0.0
                next_line=line;next_walked=False
                rate=_COMMUTE_CROWD_2025_V178.get(line)
                new_weight=total_weight+(minutes*rate[0] if rate is not None else 0.0)
                new_covered=covered+(minutes if rate is not None else 0.0)
                new_rail=rail_minutes+minutes
            next_key=(neighbor,next_line,next_walked)
            if next_key not in visited:
                heapq.heappush(pending,(transfers+int(change),elapsed+minutes+extra,
                                        neighbor,next_line,next_walked,new_weight,new_covered,new_rail))
    output={}
    for name,(elapsed,transfers,weighted,covered,rail) in best.items():
        # All train edges must have an official reference, including transfer legs.
        rate=int(math.floor(weighted/covered+0.5)) if rail>0 and abs(covered-rail)<1e-6 else None
        output[name]=(0 if name==destination else max(1,int(math.ceil(elapsed))),int(transfers),rate)
    return output


def town_station_refs_v163(commute_destination='表示しない'):
    """Attach minutes and transfers from the SAME minimum-transfer route."""
    majors=set(MAJOR_STATION_LABELS)
    regional=set(REGIONAL_HUB_STATION_LABELS)
    travel=(commute_reference_crowd_v178(commute_destination)
            if commute_destination!='表示しない' else {})
    items=[]
    for name,coords in STATION_MAP_POINTS.items():
        route=travel.get(_commute_key(name))
        items.append({'name':name,'lat':float(coords[0]),'lng':float(coords[1]),
            'priority':0 if name in majors else 1 if name in regional else 2 if name in STATIONS else 3,
            'detail_priority':0 if name in majors else 1 if name in STATIONS else 2,
            'commute_min':route[0] if route is not None else None,
            'commute_transfers':route[1] if route is not None else None,
            'commute_crowding_pct':route[2] if route is not None else None})
    items.sort(key=lambda item:(item['priority'],item['name']))
    if commute_destination != '表示しない':
        missing=[item['name'] for item in items
                 if (not isinstance(item['commute_min'],int) or
                     not isinstance(item['commute_transfers'],int))]
        if missing:
            # Do not silently render a partial commute map. Never fill with
            # guessed numbers or label a missing route as 0 minutes.
            raise AppError('通勤経路データがない駅: '+', '.join(missing[:12])
                           + (' ほか' if len(missing)>12 else ''))
    return items,[{'name':name,'lat':float(p[0]),'lng':float(p[1])} for name,p in WARD_LABELS.items()]


def _commute_line_changed_v164_second():
    """Changing the second line must not disturb the first destination."""
    st.session_state['commute_station_2_v164'] = _COMMUTE_V160_NO_STATION
    _v180_commit_commute_change()


def commute_current_destination_v164_second():
    line=st.session_state.get('commute_line_2_v164',_COMMUTE_V160_NO_LINE)
    station=st.session_state.get('commute_station_2_v164',_COMMUTE_V160_NO_STATION)
    return station if station in commute_lines_v160().get(line,()) else '表示しない'


def _v228_commute_line_changed_third():
    st.session_state['commute_station_3_v228']=_COMMUTE_V160_NO_STATION
    _v180_commit_commute_change()


def commute_selected_destinations_v164():
    first=commute_current_destination_v160()
    second=commute_current_destination_v164_second()
    line=st.session_state.get('commute_line_3_v228',_COMMUTE_V160_NO_LINE)
    station=st.session_state.get('commute_station_3_v228',_COMMUTE_V160_NO_STATION)
    third=station if station in commute_lines_v160().get(line,()) else '表示しない'
    return tuple(value for value in (first,second,third) if value!='表示しない')


def town_station_refs_v164(destinations):
    destinations=tuple(destinations)
    if len(destinations)>3:raise ValueError('通勤先は3駅までです。')
    stations,wards=town_station_refs_v163(destinations[0] if destinations else '表示しない')
    for index in (2,3):
        routes=commute_reference_crowd_v178(destinations[index-1]) if len(destinations)>=index else {}
        missing=[]
        for station in stations:
            route=routes.get(_commute_key(station['name']))
            prefix='commute'+str(index)+'_'
            station[prefix+'min']=route[0] if route is not None else None
            station[prefix+'transfers']=route[1] if route is not None else None
            station[prefix+'crowding_pct']=route[2] if route is not None else None
            if len(destinations)>=index and not (route is not None and isinstance(route[0],int) and isinstance(route[1],int)):
                missing.append(station['name'])
        if missing:raise AppError('通勤先'+str(index)+'の経路データがない駅: '+', '.join(missing[:12]))
    return stations,wards


def _v228_station_line_matches(query,lines):
    def key(value):
        value=re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or ''))).casefold()
        return value[:-1] if value.endswith('駅') else value
    wanted=key(query)
    if not wanted:return []
    found={}
    for line,stations in lines.items():
        for station in stations:
            if wanted in key(station):
                found.setdefault(station,[])
                if line not in found[station]:found[station].append(line)
    return [{'station':station,'lines':found[station]} for station in sorted(found,key=lambda name:(key(name)!=wanted,not key(name).startswith(wanted),name))]


def _v228_station_lookup_ui(state,lines,ready):
    st.markdown('**駅名から路線を探す**')
    message=state.pop('_v228_station_applied','')
    if message:st.success(message)
    query=st.text_input('駅名を入力',key='v228_station_query',placeholder='例：池袋、東京、新宿（駅名の一部でも検索できます）')
    if not query.strip():return
    matches=_v228_station_line_matches(query,lines)
    if not matches:
        st.caption('アプリ内の路線データに一致する駅がありません。表記を変えて検索してください。');return
    names=[row['station'] for row in matches]
    if state.get('v228_station_match') not in names:state['v228_station_match']=names[0]
    station=st.selectbox('該当する駅',names,key='v228_station_match')
    options=next(row['lines'] for row in matches if row['station']==station)
    st.write('利用できる路線：'+' ／ '.join(options))
    if state.get('v228_station_line') not in options:state['v228_station_line']=options[0]
    line=st.selectbox('設定する路線',options,key='v228_station_line')
    slot=st.selectbox('設定する勤務先',[1,2,3],format_func=lambda value:'通勤先'+str(value),key='v228_station_slot')
    if st.button('この駅を勤務先に設定',key='v228_station_apply',disabled=not ready):
        fields={1:('commute_line_v160','commute_station_v160'),2:('commute_line_2_v164','commute_station_2_v164'),3:('commute_line_3_v228','commute_station_3_v228')}
        line_key,station_key=fields[slot];state[line_key]=line;state[station_key]=station
        if slot==1:state['commute_destination_v159']=station
        _v180_commit_commute_change()
        state['_v228_station_applied']='通勤先'+str(slot)+'に'+station+'駅を設定しました。'
        st.rerun()


@st.fragment(run_every='6s')
def town_choropleth_status():
    """Read-only progress for an exclusively manual, background map job."""
    state=st.session_state
    store=current_town_summary_worker()
    snap=store.snapshot()
    if state.get('_v183_manual_refresh_wait') and not snap['running']:
        state['_v183_manual_refresh_wait']=False
        if snap['ready'] and snap['revision']>state.get('_v183_manual_start_revision',-1):
            # The map and its controls are outside this progress fragment.
            # One normal rerun delivers the new summary to its Leaflet iframe.
            st.rerun()
    boundary_worker=town_boundary_registry();boundary_worker.start()
    shapes,ready,busy,error=boundary_worker.snapshot()
    if snap['running']:
        done=snap['progress'];total=snap.get('total_pages')
        if isinstance(total,int) and total>0:
            st.caption(f"町丁目家賃 {done}/{total}ページ（{min(100,100*done/total):.0f}%）")
            st.progress(min(1.0,done/total))
        else:
            st.caption(f"町丁目家賃 {done}/--ページ")
    elif snap['pending']:
        st.caption('保存物件に未反映の更新があります。地図上の「保存物件を再集計」を押すと反映します。')
    if busy:st.caption('地図の町丁目境界を読み込み中')
    if snap['error']:st.warning('平均家賃の集計：'+snap['error'])
    if error:st.warning('境界の一部未取得：'+error)
    if snap['ready'] and not snap['running']:
        result=snap.get('summary') or {}
        updated=result.get('updated_at')
        st.caption('町丁目家賃：保存済み集計' + ('｜更新 '+acquisition_time_jst(updated) if updated else ''))
        group_totals=result.get('groups') or {}
        if ready and hasattr(shapes,'coverage_keys'):
            report=town_coverage_audit(group_totals,shapes.coverage_keys(),
                         state.get('layout_display_group','group1'))
            if report['stored_without_geometry']:
                st.warning(f"保存家賃が地図境界に一致しない地域：{len(report['stored_without_geometry'])}町丁目")
            with st.expander('東京23区・保存家賃の地図反映状況'):
                st.dataframe(report['wards'],hide_index=True,use_container_width=True)
                if report['stored_without_geometry']:
                    st.code('\n'.join(report['stored_without_geometry'][:100]),language=None)
        if ready:st.caption('区境界：国土交通省（2021年）／町丁目：e-Stat国勢調査（2015年）')
    elif not snap['running'] and not snap['ready'] and not snap['error']:
        st.caption('町丁目家賃：まだ集計されていません。「保存物件を再集計」を押してください。')


def _v182_town_search_matches(query, boundary_path, limit=100):
    """Search all 23 wards by town, optional ward, and optional chome.

    Unqualified exact names do not guess between wards. A base-town search
    groups ALL chome polygons in one ward and a chome search targets only that
    chome. Prefix and substring matches are fallback choices if no exact match.
    """
    wanted=address_key(normal(query)).replace(' ','').replace('　','')
    wanted=re.sub(r'[、,，。・/／]', '',wanted)
    wanted=wanted.removeprefix('東京都').removeprefix('東京')
    if not wanted or len(wanted)>80 or not boundary_path or not os.path.isfile(boundary_path):return []
    selected_ward=next((w for w in sorted(TOKYO_WARDS.values(),key=len,reverse=True)
                        if wanted.startswith(w)),None)
    if selected_ward:wanted=wanted[len(selected_ward):]
    # Full district requests can be used to zoom to a ward too.
    if not wanted and selected_ward:
        try:
            with sqlite3.connect('file:'+quote(boundary_path,safe='/')+'?mode=ro',uri=True,timeout=5) as conn:
                record=conn.execute('SELECT south,west,north,east FROM ward_areas WHERE ward=?',(selected_ward,)).fetchone()
            if record and 34<=record[0]<record[2]<=37 and 138<=record[1]<record[3]<=141:
                return [{'label':selected_ward,'bounds':list(map(float,record)),
                         'ward':selected_ward,'kind':'ward','keys':[],'rank':0}]
        except (sqlite3.Error,OSError,TypeError,ValueError):pass
        return []
    # A suffix like 南長崎3 is an optional abbreviation for 南長崎3丁目.
    if re.fullmatch(r'.+?[0-9]{1,2}',wanted):wanted+='丁目'
    specific=bool(re.search(r'[0-9]+丁目$',wanted))
    try:
        with sqlite3.connect('file:'+quote(boundary_path,safe='/')+'?mode=ro',uri=True,timeout=8) as conn:
            records=conn.execute('SELECT town,south,west,north,east FROM areas').fetchall()
    except (sqlite3.Error,OSError):return []
    groups={}
    for key,south,west,north,east in records:
        if not isinstance(key,str) or '|' not in key:continue
        ward,town=key.split('|',1)
        if ward not in TOKYO_WARDS.values() or selected_ward and ward!=selected_ward:continue
        full_name=address_key(town)
        town_name=town_base_name(town)
        target=full_name if specific else address_key(town_name)
        if not wanted:continue
        if target==wanted:rank=0
        elif target.startswith(wanted):rank=1
        elif wanted in target:rank=2
        else:continue
        try:
            box=[float(south),float(west),float(north),float(east)]
            if not all(map(math.isfinite,box)) or not(34<=box[0]<box[2]<=37 and 138<=box[1]<box[3]<=141):continue
        except (TypeError,ValueError):continue
        identifier=(ward,target)
        entry=groups.get(identifier)
        if entry:
            entry['rank']=min(entry['rank'],rank)
            b=entry['bounds']
            entry['bounds']=[min(b[0],box[0]),min(b[1],box[1]),
                             max(b[2],box[2]),max(b[3],box[3])]
            if key not in entry['keys']:entry['keys'].append(key)
        else:
            groups[identifier]={'ward':ward,'name':target,'label':ward+' '+target,
                                'bounds':box,'kind':'town','rank':rank,'keys':[key]}
    if not groups:return []
    ordered=sorted(groups.values(),key=lambda x:(x['rank'],x['ward'],x['name']))
    # Exact matches across multiple wards are ALL choices; fuzzy matches are
    # candidates only when no exact town name exists.
    if ordered[0]['rank']==0:ordered=[x for x in ordered if x['rank']==0]
    for item in ordered:
        if not specific and len(item['keys'])>1:
            item['label']+='（全'+str(len(item['keys']))+'区域）'
    return ordered[:limit]


def _v182_town_selection_geometry(choice, boundary_path):
    """Read the *actual* town/ward perimeter even when rent data is absent."""
    features=[]
    try:
        with sqlite3.connect('file:'+quote(boundary_path,safe='/')+'?mode=ro',uri=True,timeout=8) as conn:
            if choice.get('kind')=='ward':
                row=conn.execute('SELECT poly FROM ward_areas WHERE ward=?',(choice.get('ward'),)).fetchone()
                rows=[(choice['ward'],row[0])] if row else []
            else:
                keys=choice.get('keys') or []
                if not keys or len(keys)>450:raise ValueError('地名検索の区域数が不正です。')
                placeholders=','.join('?' for _ in keys)
                rows=conn.execute('SELECT town,poly FROM areas WHERE town IN ('+placeholders+')',keys).fetchall()
        for key,poly in rows:
            polygons=json.loads(poly)
            if not isinstance(polygons,list) or not polygons:continue
            features.append({'type':'Feature','properties':{'name':key},
                             'geometry':{'type':'MultiPolygon','coordinates':polygons}})
    except (sqlite3.Error,OSError,json.JSONDecodeError,TypeError) as exc:
        raise ValueError('選択した地域の境界を読み込めません。') from exc
    if not features:raise ValueError('選択地域の輪郭を取得できません。')
    return {'type':'FeatureCollection','features':features}


def _v182_request_town_jump(state,choice,boundary_path):
    box=choice.get('bounds') if isinstance(choice,dict) else None
    if not isinstance(box,(tuple,list)) or len(box)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in box):
        raise ValueError('検索した地域の座標が不正です。')
    outline=_v182_town_selection_geometry(choice,boundary_path)
    # Preserve the command through polling and component rerenders until its
    # browser acknowledgment is seen; never silently assume the map moved.
    token=str(time.monotonic_ns())
    state['_v182_map_jump']={'id':token,'bounds':list(box),'geo':outline,'label':choice['label']}
    state['_v182_map_jump_confirmed']=None
    state['_v182_town_selected']=choice['label']
    state['_v181_location_search_matches']=[]
    state['_v181_location_search_note']='「'+choice['label']+'」を地図上で囲み、表示位置を移動します。'
    state['new_bounds']=tuple(box)
    state['new_view_center']=bounds_center(box)


@st.cache_data(show_spinner=False,ttl=3600)
def town_search_catalog_v184(db_path,db_mtime):
    """Small 23-ward bbox index from the EXISTING read-only town geometry DB."""
    rows=[];wards=[]
    with sqlite3.connect('file:'+quote(db_path,safe='/')+'?mode=ro',uri=True,timeout=8) as conn:
        for key,s,w,n,e in conn.execute('SELECT town,south,west,north,east FROM areas'):
            if not isinstance(key,str) or '|' not in key:continue
            ward,town=key.split('|',1)
            if ward not in TOKYO_WARDS.values():continue
            try:
                box=[float(s),float(w),float(n),float(e)]
            except (TypeError,ValueError,OverflowError):continue
            if not all(map(math.isfinite,box)) or not(34<=box[0]<box[2]<=37 and 138<=box[1]<box[3]<=141):continue
            rows.append({'ward':ward,'town':address_key(town),
                         'base':address_key(town_base_name(town)),'bounds':box})
        try:
            ward_records=conn.execute('SELECT ward,south,west,north,east FROM ward_areas')
            for ward,s,w,n,e in ward_records:
                if ward not in TOKYO_WARDS.values():continue
                try:
                    bounds=[float(s),float(w),float(n),float(e)]
                except (TypeError,ValueError,OverflowError):continue
                if all(map(math.isfinite,bounds)) and 34<=bounds[0]<bounds[2]<=37 and 138<=bounds[1]<bounds[3]<=141:
                    wards.append({'ward':ward,'bounds':bounds})
        except sqlite3.OperationalError:
            # Some existing boundary caches do not contain the optional ward table.
            # Town search must still work using the areas table.
            pass
    return rows,wards


@st.fragment
def interactive_town_choropleth(group):
    """Leaflet map persists in the browser across Streamlit fragment reruns.

    Only aggregate geometry and the selected group enter the map.  Do not
    mount folium/st_folium in this fragment: the mount/unmount cycle is the
    observed map-whitening failure when bounds callbacks cause a rerun.
    """
    state=st.session_state
    group=state.get('layout_display_group',group)
    # Only this explicit user action launches the asynchronous DB aggregation.
    if st.button('保存物件を再集計',key='recompute_v143_town_map'):
        job=current_town_summary_worker()
        previous=job.snapshot()
        if not previous['running']:
            if job.start():
                state['_v183_manual_refresh_wait']=True
                state['_v183_manual_start_revision']=previous['revision']
        st.rerun(scope='fragment')
    if st.button('表示範囲を更新（地図移動後のみ）',key='refresh_v118_town_map'):
        state['_v118_map_revision']=int(state.get('_v118_map_revision',0))+1
        st.rerun(scope='fragment')
    # Layout controls live next to the map instead of above map diagnostics.
    if group not in ('group0','group1','group2','house'):group='group1';state.layout_display_group='group1'
    if st.button('ワンルーム・1K・1DK',key='layout_group0_v174',type='primary' if group=='group0' else 'secondary',use_container_width=True):
        if _v202_choose_layout(state,'group0'):st.rerun(scope='fragment')
    if group=='group0':st.caption('マンション・築15年以内')
    g1,g2,g3=st.columns(3)
    with g1:
        if st.button('1LDK・2K・2DK',key='layout_group1',type='primary' if group=='group1' else 'secondary',use_container_width=True):
            if _v202_choose_layout(state,'group1'):st.rerun(scope='fragment')
        if group=='group1':st.caption('マンション・築15年以内')
    with g2:
        if st.button('2LDK・3K・3DK',key='layout_group2',type='primary' if group=='group2' else 'secondary',use_container_width=True):
            if _v202_choose_layout(state,'group2'):st.rerun(scope='fragment')
        if group=='group2':st.caption('マンション・築15年以内')
    with g3:
        if st.button('一戸建て',key='layout_group_house',type='primary' if group=='house' else 'secondary',use_container_width=True):
            if _v202_choose_layout(state,'house'):st.rerun(scope='fragment')
        if group=='house':st.caption('築40年以内・50㎡以上')
    reset=int(state.get('_v118_map_reset_count',0))
    revision=int(state.get('_v118_map_revision',0))
    store=current_town_summary_worker();snap=store.snapshot()
    boundary_worker=town_boundary_registry();boundary_worker.start()
    shapes,ready,busy,error=boundary_worker.snapshot()
    # Only a successful MANUAL aggregate invalidates cached rent colors.
    # The stable component key preserves browser pan/zoom.
    summary_version=(snap.get('summary') or {}).get('updated_at','')
    registry=_v168_town_oz_registry()
    old_payload=state.get('_v118_choropleth_payload')
    if isinstance(old_payload,dict):
        registry.request(_v168_visible_wards(old_payload.get('geo') or {})[:8])
    suumo_oz_codes=_v167_public_oz_codes(state.get('ward_town_cache'))
    suumo_oz_codes.update(registry.snapshot())
    oz_signature=hashlib.sha256(repr(sorted(suumo_oz_codes.items())).encode('utf-8')).hexdigest()[:12]
    # Code arrivals update popup links independently; do not close an open popup.
    signature=(BUILD,group,revision,reset,snap['revision'],summary_version,bool(ready))
    cached=state.get('_v118_choropleth_payload')
    bounds=state.get('new_bounds')
    center=bounds_center(bounds) if bounds else state.get('new_view_center',_V202_HOME_CENTER)
    needs_update=(not isinstance(cached,dict)
                  or cached.get('signature')!=signature
                  or not _v156_view_is_covered(bounds,cached.get('window')))
    if needs_update:
        window=_v156_town_render_window(bounds,center) if bounds else _V202_HOME_WINDOW
        groups=(snap['summary'] or {}).get('groups') or {}
        geo,limited,count=town_colored_shapes(groups,group,shapes if ready else {},window,include_no_data=False,
                                              suumo_oz_codes=suumo_oz_codes)
        # The window MUST be part of the Leaflet data revision: keeping the
        # same component key preserves the map position while replacing colors.
        payload_key=hashlib.sha256(repr((signature,window)).encode('utf-8')).hexdigest()[:20]
        cached={'signature':signature,'layer_signature':payload_key,
                'window':window,'geo':geo,'limited':limited,
                'ready':bool(ready and snap['ready']),
                'colored_count':geo['render_stats']['with_rent'],
                'no_data_count':geo['render_stats']['without_rent'],'center':center}
        state['_v118_choropleth_payload']=cached
        registry.request(_v168_visible_wards(geo)[:8])
    destinations=commute_selected_destinations_v164()
    if len(destinations)>=2:
        st.caption('通勤先：'+'／'.join(str(i+1)+'.'+name+'駅' for i,name in enumerate(destinations))+'｜乗換回数が少ない経路を優先（同数なら最短時間・概算）')
    elif destinations:
        st.caption(f'通勤先：{destinations[0]}駅｜乗換回数が少ない経路を優先（同数なら最短時間・概算）')
    if destinations:
        st.caption('混雑率は国土交通省2025年度の路線別「最混雑区間」を乗車時間で加重した参考値。実際の各駅間・時間帯の平均ではありません。公表値がない路線を含む経路は「不明」です。')
    if cached['ready']:
        st.caption(f"着色 {cached['colored_count']}町丁目｜保存家賃のない地域は背景地図のまま表示")
    else:
        st.caption('保存物件の再集計を押すと、家賃の色分けをバックグラウンドで作成します')
    search_catalog=[];search_ward_bounds=[]
    if ready and isinstance(shapes,TownBoundaryStore):
        try:
            search_catalog,search_ward_bounds=town_search_catalog_v184(shapes.path,os.stat(shapes.path).st_mtime_ns)
        except (OSError,sqlite3.Error,ValueError,TypeError):
            pass
    stations,wards=town_station_refs_v164(destinations)
    widget=town_component_v121()
    data=widget(center=list(center),zoom=_v202_view_zoom(state.get('new_view_zoom')),geo=cached['geo'],
                home_view_epoch=int(state.get('_v202_home_epoch',0)),home_center=list(_V202_HOME_CENTER),
                signature=cached['layer_signature'],stations=stations,wards=wards,
                search_catalog=search_catalog,search_district_names=list(TOKYO_WARDS.values()),
                search_ward_bounds=search_ward_bounds,
                suumo_ward_urls={ward:_v189_suumo_search_url(code,group) for code,ward in TOKYO_WARDS.items()},
                suumo_ward_codes=_V167_WARD_TO_CODE,
                suumo_town_codes=suumo_oz_codes,
                suumo_search_reply=state.get('_v189_suumo_reply'),
                polygon_suumo_reply=state.get('_v217_suumo_reply'),
                custom_pin_reply=state.get('_v205_pin_reply'),
                search_error=state.get('_v184_search_error'),
                commute_destination=(destinations[0] if destinations else ''),
                commute_destination_2=(destinations[1] if len(destinations)>=2 else ''),
                commute_destination_3=(destinations[2] if len(destinations)>=3 else ''),
                jump=state.get('_v182_map_jump'),
                key='town_map_v184_'+str(reset),default=None)
    if isinstance(data,dict) and data.get('home_view_epoch')==int(state.get('_v202_home_epoch',0)):
        current=viewport_bounds(data)
        if data.get('event')=='custom_pin_search_v205':
            ticket=str(data.get('ticket') or '')[:100]
            if ticket and ticket!=state.get('_v205_last_pin_ticket'):
                state['_v205_last_pin_ticket']=ticket
                state['_v205_pin_reply']=_v208_run_landmark_lookup(state,data)
                st.rerun(scope='fragment')
        if data.get('event')=='polygon_suumo_v217':
            ticket=str(data.get('ticket') or '')[:100]
            if ticket and ticket!=state.get('_v217_last_suumo_ticket'):
                state['_v217_last_suumo_ticket']=ticket
                state['_v217_suumo_reply']=_v189_resolve_suumo_selection(state,data,group)
                st.rerun(scope='fragment')
        if data.get('event')=='town_suumo_v189':
            ticket=str(data.get('ticket') or '')[:100]
            if ticket and ticket!=state.get('_v189_last_suumo_ticket'):
                state['_v189_last_suumo_ticket']=ticket
                state['_v189_suumo_reply']=_v189_resolve_suumo_selection(state,data,group)
                st.rerun(scope='fragment')
        if data.get('event')=='town_search_v184':
            ticket=str(data.get('ticket') or '')[:100]
            if ticket and ticket!=state.get('_v184_last_search_ticket'):
                state['_v184_last_search_ticket']=ticket
                state['_v184_search_error']=None
                state['_v189_suumo_reply']=_v189_resolve_suumo_selection(state,data,group)
                name=str(data.get('name') or '')[:90]
                ward=str(data.get('ward') or '')[:30]
                kind=str(data.get('kind') or '')
                options=_v182_town_search_matches((ward if kind=='ward' else ward+name),shapes.path) if ready and isinstance(shapes,TownBoundaryStore) else []
                choice=next((x for x in options if x['ward']==ward and x.get('name',x.get('label'))==name and
                            x['kind']==('ward' if kind=='ward' else 'town')),None)
                if choice is not None and (kind=='ward' or kind=='town' or kind=='chome'):
                    try:
                        _v182_request_town_jump(state,choice,shapes.path)
                    except ValueError as exc:
                        state['_v184_search_error']={'ticket':ticket,'message':str(exc)}
                else:
                    state['_v184_search_error']={'ticket':ticket,'message':'検索した町域の境界を取得できませんでした。'}
                if current:
                    state['new_bounds']=current;state['new_view_center']=bounds_center(current)
                    state['new_view_zoom']=_v202_view_zoom(data.get('zoom'),state.get('new_view_zoom',11))
                st.rerun(scope='fragment')
        command=state.get('_v182_map_jump')
        confirmed=not command or data.get('jump_applied')==command.get('id')
        if command and confirmed:
            state['_v182_map_jump_confirmed']=command['id']
            state.pop('_v182_map_jump',None)
        if current and confirmed:
            state['new_bounds']=current;state['new_view_center']=bounds_center(current)
            state['new_map']=data
            state['new_view_zoom']=_v202_view_zoom(data.get('zoom'),state.get('new_view_zoom',11))


def town_location_search_panel_v184():
    """Map reset remains external; searching is now inside the Leaflet iframe."""
    state=st.session_state
    if st.button('地図を再初期化',key='reset_v118_map'):
        _v202_reset_home(state)
        state.pop('_v182_map_jump',None)
        state.pop('_v182_map_jump_confirmed',None)
        state.pop('_v181_map_jump',None)
        state.pop('_v118_choropleth_payload',None)
        state.pop('_v184_search_error',None)
        state['_v118_map_reset_count']=int(state.get('_v118_map_reset_count',0))+1
        st.rerun()


# V147: REAL-URL REPLAY.  This mode uses the SAME fetch/parse/map-address/
# Database.save_units read-back verification as normal SUUMO collection.
# Its results are test evidence ONLY AFTER executed on actual Streamlit Cloud.
# A fetched page alone never increments the saved counter.
_V147_REPLAY_SAMPLES = {'address.residential_candidates': [('https://suumo.jp/chintai/jnc_000096346840/?bc=100527446399', 'the togoshiginza(ザトゴシギンザ)'), ('https://suumo.jp/chintai/jnc_000096372475/?bc=100527350281', '東急池上線 戸越銀座駅 4階建 築3年'), ('https://suumo.jp/chintai/jnc_000102725599/?bc=100472001215', 'Maison de Takaya'), ('https://suumo.jp/chintai/jnc_000105755826/?bc=100499997025', '東急目黒線 武蔵小山駅 6階建 築7年'), ('https://suumo.jp/chintai/jnc_000105983849/?bc=100526179697', '東急目黒線 西小山駅 4階建 新築'), ('https://suumo.jp/chintai/jnc_000107465110/?bc=100509058001', 'クレセール西小山'), ('https://suumo.jp/chintai/jnc_000107465118/?bc=100521938134', 'クレセール西小山'), ('https://suumo.jp/chintai/jnc_000107743410/?bc=100530340458', 'PASEO武蔵小山II'), ('https://suumo.jp/chintai/jnc_000107747226/?bc=100511870878', '東急目黒線 武蔵小山駅 5階建 築6年'), ('https://suumo.jp/chintai/jnc_000108366073/?bc=100385816649', '東急目黒線 武蔵小山駅 地下1地上5階建 築6年'), ('https://suumo.jp/chintai/jnc_000108960080/?bc=100520410547', '東急目黒線 西小山駅 5階建 築12年'), ('https://suumo.jp/chintai/jnc_000109369370/?bc=100523671787', '東急目黒線 武蔵小山駅 14階建 築2年'), ('https://suumo.jp/chintai/jnc_000109494882/?bc=100524565968', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000109558286/?bc=100525041093', 'GranDuo戸越銀座'), ('https://suumo.jp/chintai/jnc_000109675856/?bc=100529277837', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675858/?bc=100526302898', '東急池上線 戸越銀座駅 8階建 築1年'), ('https://suumo.jp/chintai/jnc_000109761716/?bc=100526390094', '東急池上線 荏原中延駅 7階建 築5年'), ('https://suumo.jp/chintai/jnc_000109880345/?bc=100527327813', 'ORSUS戸越銀座'), ('https://suumo.jp/chintai/jnc_000109896081/?bc=100527425910', '東急池上線 戸越銀座駅 6階建 築4年'), ('https://suumo.jp/chintai/jnc_000109925957/?bc=100527695963', 'ルーブル目黒不動前'), ('https://suumo.jp/chintai/jnc_000109951734/?bc=100530387499', 'FLレジデンス品川I'), ('https://suumo.jp/chintai/jnc_000110101746/?bc=100529346296', 'スパシエヴァロル品川荏原'), ('https://suumo.jp/chintai/jnc_000110160872/?bc=100529636331', '東急目黒線 不動前駅 6階建 築13年'), ('https://suumo.jp/chintai/jnc_000110160874/?bc=100530004824', 'ブランズ西小山'), ('https://suumo.jp/chintai/jnc_000110182962/?bc=100530535985', 'Pierre 8 武蔵小山(ピエールエイト武蔵小山)'), ('https://suumo.jp/chintai/jnc_000110202117/?bc=100529898265', '東急目黒線 不動前駅 6階建 築13年'), ('https://suumo.jp/chintai/jnc_000110208723/?bc=100530037980', 'J.リヴェール武蔵小山'), ('https://suumo.jp/chintai/jnc_000110214958/?bc=100530017918', '東急目黒線 武蔵小山駅 4階建 築3年'), ('https://suumo.jp/chintai/jnc_000110245074/?bc=100530239868', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245075/?bc=100530239869', '東急目黒線 武蔵小山駅 19階建 新築')], 'map.room_marker': [('https://suumo.jp/chintai/jnc_000107471891/?bc=100514747337', ''), ('https://suumo.jp/chintai/jnc_000107471892/?bc=100514748753', ''), ('https://suumo.jp/chintai/jnc_000107471894/?bc=100514747338', ''), ('https://suumo.jp/chintai/jnc_000109675932/?bc=100524328068', ''), ('https://suumo.jp/chintai/jnc_000109888503/?bc=100527376824', ''), ('https://suumo.jp/chintai/jnc_000110411765/?bc=100531384147', '')], 'map.address_inference': [('https://suumo.jp/chintai/jnc_000109494848/?bc=100524565984', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000109494852/?bc=100524566049', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000109494858/?bc=100524566035', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000109494873/?bc=100524566106', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000109675836/?bc=100529277835', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675837/?bc=100525551871', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675839/?bc=100529277831', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675842/?bc=100529277811', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675848/?bc=100529277857', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675849/?bc=100529277858', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675850/?bc=100525309895', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675860/?bc=100529277875', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675866/?bc=100525309936', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675873/?bc=100529276905', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675875/?bc=100529276907', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675879/?bc=100530916977', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675894/?bc=100529276925', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675897/?bc=100525310122', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675915/?bc=100529276974', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675916/?bc=100525310116', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000109675920/?bc=100525310152', '東急池上線 戸越銀座駅 8階建 新築'), ('https://suumo.jp/chintai/jnc_000110245066/?bc=100530239925', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245078/?bc=100530239948', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245080/?bc=100530239877', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245083/?bc=100530239938', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245085/?bc=100530239885', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245088/?bc=100530239889', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245091/?bc=100530239934', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110245092/?bc=100530239883', '東急目黒線 武蔵小山駅 19階建 新築'), ('https://suumo.jp/chintai/jnc_000110251165/?bc=100530295234', '東急目黒線 武蔵小山駅 19階建 新築')]}

# v148: 30 distinct real failed marker URLs (not generated candidates) from
# Supabase diagnostic_log listing_rejected map.room_marker, actual regions retained.
# Each tuple: (real SUUMO detail URL, card hint, recorded search-region label).
_V147_REPLAY_SAMPLES['map.room_marker'] = [
    ('https://suumo.jp/chintai/jnc_000107471891/?bc=100514747337', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000107471892/?bc=100514748753', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000107471894/?bc=100514747338', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000109675932/?bc=100524328068', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000109888503/?bc=100527376824', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000110411765/?bc=100531384147', '', '東京都品川区荏原'),
    ('https://suumo.jp/chintai/jnc_000107637227/?bc=100510280533', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000107637229/?bc=100510280529', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000107637230/?bc=100510280634', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000107637232/?bc=100510280532', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000108612959/?bc=100517531451', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000108942522/?bc=100520255111', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000108942523/?bc=100520255106', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000108942524/?bc=100520255107', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000109001675/?bc=100529006648', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000109019379/?bc=100511354512', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000110106082/?bc=100529400747', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000110403254/?bc=100531318529', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000110403255/?bc=100531319490', '', '東京都品川区西大井'),
    ('https://suumo.jp/chintai/jnc_000106696420/?bc=100503696998', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000109221175/?bc=100522277250', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000109221176/?bc=100522277305', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000109407426/?bc=100523801432', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000109665835/?bc=100508423376', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000109750418/?bc=100522849303', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000110019711/?bc=100528488690', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000110057083/?bc=100506984015', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000110245097/?bc=100528132839', '', '東京都品川区上大崎'),
    ('https://suumo.jp/chintai/jnc_000089700434/?bc=100529401656', '', '東京都品川区西五反田'),
    ('https://suumo.jp/chintai/jnc_000099630650/?bc=100500179180', '', '東京都品川区小山'),
]

# v149: 30 distinct actual Shinagawa URL failures in each class; save+readback is required.
_V147_REPLAY_SAMPLES = {'address.residential_candidates': [('https://suumo.jp/chintai/jnc_000107053186/?bc=100527912346', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 \u5730\u4e0b1\u5730\u4e0a14\u968e\u5efa \u7bc94\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109743113/?bc=100528741027', '\u90fd\u55b6\u6d45\u8349\u7dda \u4e94\u53cd\u7530\u99c5 \u5730\u4e0b1\u5730\u4e0a4\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000108942543/?bc=100520238287', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 3\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109396161/?bc=100516489596', '\u30b3\u30f3\u30d5\u30a9\u30fc\u30eb\u6c60\u7530\u5c71', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109396160/?bc=100526257229', '\u30b3\u30f3\u30d5\u30a9\u30fc\u30eb\u6c60\u7530\u5c71', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109264593/?bc=100522854503', '\u90fd\u55b6\u6d45\u8349\u7dda \u9ad8\u8f2a\u53f0\u99c5 \u5730\u4e0b1\u5730\u4e0a3\u968e\u5efa \u7bc96\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000108721837/?bc=100518394352', '\u90fd\u55b6\u6d45\u8349\u7dda \u9ad8\u8f2a\u53f0\u99c5 4\u968e\u5efa \u7bc94\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000091646397/?bc=100521306253', 'GRAN PASEO\u5cf6\u6d25\u5c71East', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109617559/?bc=100525457234', '\u30ed\u30fc\u30bf\u30b9\u5cf6\u6d25\u5c71(\u30ed\u30fc\u30bf\u30b9\u30b7\u30de\u30c5\u30e4\u30de)', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109609932/?bc=100526000055', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 \u5730\u4e0b1\u5730\u4e0a4\u968e\u5efa \u7bc99\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109252311/?bc=100528488680', 'SolanaTakanawadai', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109743112/?bc=100526297816', '\u90fd\u55b6\u6d45\u8349\u7dda \u4e94\u53cd\u7530\u99c5 \u5730\u4e0b1\u5730\u4e0a4\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109926013/?bc=100527644658', 'AXAS RESIDENCE \u76ee\u9ed2\u6771', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000110033956/?bc=100528553737', 'AXAS\u30ec\u30b8\u30c7\u30f3\u30b9\u76ee\u9ed2\u6771', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109334208/?bc=100523742949', 'AXAS\u30ec\u30b8\u30c7\u30f3\u30b9\u76ee\u9ed2\u6771', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109926010/?bc=100527769094', '\u30ac\u30ea\u30b7\u30a2\u5fa1\u6bbf\u5c71(\u30ac\u30ea\u30b7\u30a2\u30b4\u30c6\u30f3\u30e4\u30de)', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109396159/?bc=100493105764', '\u30b3\u30f3\u30d5\u30a9\u30fc\u30eb\u6c60\u7530\u5c71', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109027580/?bc=100529598664', '\u90fd\u55b6\u6d45\u8349\u7dda \u9ad8\u8f2a\u53f0\u99c5 14\u968e\u5efa \u7bc913\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109938040/?bc=100527747124', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 11\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000108973178/?bc=100529284242', '\u30a8\u30df\u30cd\u30f3\u30b9\u9ad8\u8f2a\u53f0', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000109019398/?bc=100520730208', '\u30a8\u30df\u30cd\u30f3\u30b9\u9ad8\u8f2a\u53f0', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000107645230/?bc=100516671731', 'JR\u4eac\u6d5c\u6771\u5317\u7dda \u5927\u4e95\u753a\u99c5 4\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109477179/?bc=100524347751', '\u4eac\u6025\u672c\u7dda \u7acb\u4f1a\u5ddd\u99c5 14\u968e\u5efa \u7bc92\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110096195/?bc=100530761884', 'JR\u4eac\u6d5c\u6771\u5317\u7dda \u5927\u4e95\u753a\u99c5 4\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109761765/?bc=100528996336', '\u4eac\u6025\u672c\u7dda \u7acb\u4f1a\u5ddd\u99c5 \u5730\u4e0b2\u5730\u4e0a4\u968e\u5efa \u7bc96\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109750459/?bc=100526804573', '\u30a8\u30b9\u30bf\u30fc\u30c8\u5927\u4e95\u753a\u30d5\u30a9\u30ec\u30b9\u30c8', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106089/?bc=100529172868', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000107565226/?bc=100527042400', 'GRAN PASEO\u5927\u4e95\u753a', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000107565225/?bc=100487088040', 'GRAN PASEO\u5927\u4e95\u753a', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109943769/?bc=100527769102', 'COMPOSITE \u5927\u4e95\u753a HILLTOP(\u30b3\u30f3\u30dd\u30b8\u30c3\u30c8\u30aa\u30aa\u30a4\u30de\u30c1\u30d2\u30eb', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95')], 'map.address_inference': [('https://suumo.jp/chintai/jnc_000109750465/?bc=100526404795', '\u90fd\u55b6\u6d45\u8349\u7dda \u4e94\u53cd\u7530\u99c5 \u5730\u4e0b1\u5730\u4e0a4\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000108806881/?bc=100520821131', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 12\u968e\u5efa \u7bc912\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000108190939/?bc=100520768033', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 11\u968e\u5efa \u7bc913\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000110090550/?bc=100530294967', 'JR\u5c71\u624b\u7dda \u4e94\u53cd\u7530\u99c5 11\u968e\u5efa \u7bc913\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000107565226/?bc=100491483218', 'GRAN PASEO\u5927\u4e95\u753a', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000107565225/?bc=100491483213', 'GRAN PASEO\u5927\u4e95\u753a', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109381645/?bc=100516198887', 'GranDuo\u5927\u4e95\u753a3', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109701525/?bc=100528766445', 'JR\u4eac\u6d5c\u6771\u5317\u7dda \u5927\u4e95\u753a\u99c5 15\u968e\u5efa \u7bc93\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106093/?bc=100529172867', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106092/?bc=100529172855', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106091/?bc=100529172865', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106090/?bc=100529172863', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110106088/?bc=100529172859', '\u4eac\u6025\u672c\u7dda \u9bab\u6d32\u99c5 3\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109743104/?bc=100523192189', '\u308a\u3093\u304b\u3044\u7dda \u54c1\u5ddd\u30b7\u30fc\u30b5\u30a4\u30c9\u99c5 15\u968e\u5efa \u7bc98\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109168145/?bc=100521835169', 'GranDuo\u5927\u4e95\u753a3', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109857093/?bc=100516671597', 'JR\u4eac\u6d5c\u6771\u5317\u7dda \u5927\u4e95\u753a\u99c5 \u5730\u4e0b2\u5730\u4e0a4\u968e\u5efa \u7bc96\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109207099/?bc=100523845600', '\u30d7\u30ec\u30b7\u30e3\u30b9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109066746/?bc=100525343222', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 \u5730\u4e0a2\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000108806878/?bc=100525582988', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 \u5730\u4e0a2\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000108612975/?bc=100518336874', '\u30d5\u30a9\u30eb\u30e2\u30f3\u30c8 N5\u53f7\u5ba4', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000108802804/?bc=100529306166', '\u30d5\u30a9\u30eb\u30e2\u30f3\u30c8', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000104334855/?bc=100485096307', '\u30af\u30ec\u30b9\u30c8\u30b3\u30fc\u30c8\u65d7\u306e\u53f0', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000107485509/?bc=100521885596', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u7bc91\u5e74', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000106081406/?bc=100522181828', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000109415888/?bc=100528766559', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000109415889/?bc=100521865920', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000110396871/?bc=100525960072', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000109770551/?bc=100519500473', '\u6771\u6025\u5927\u4e95\u753a\u7dda \u65d7\u306e\u53f0\u99c5 6\u968e\u5efa \u65b0\u7bc9', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000108065884/?bc=100515636559', '\u30a2\u30fc\u30d0\u30f3\u30b3\u30a2\u65d7\u306e\u53f0', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000106228351/?bc=100502599155', '\u30d7\u30e9\u30cd\u30bd\u30b7\u30a8\u30d7\u30e9\u30b9\u65d7\u306e\u53f0', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0')], 'map.room_marker': [('https://suumo.jp/chintai/jnc_000109194969/?bc=100513308001', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000105976932/?bc=100497753528', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000110215001/?bc=100529568288', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109800554/?bc=100526649041', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108910688/?bc=100519967556', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000105863757/?bc=100499364270', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108902719/?bc=100519967522', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108931609/?bc=100519967523', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108902717/?bc=100520094777', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108902716/?bc=100519967525', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108902711/?bc=100519906552', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000107565229/?bc=100491322868', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000110332994/?bc=100530082861', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000109676015/?bc=100525785572', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000105857698/?bc=100500188296', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u6771\u5927\u4e95'), ('https://suumo.jp/chintai/jnc_000108054768/?bc=100516650796', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000109447762/?bc=100521869691', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000094728975/?bc=100410798823', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u65d7\u306e\u53f0'), ('https://suumo.jp/chintai/jnc_000109163442/?bc=100527450047', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e2d\u5ef6'), ('https://suumo.jp/chintai/jnc_000109455030/?bc=100524157090', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e2d\u5ef6'), ('https://suumo.jp/chintai/jnc_000110081442/?bc=100524165728', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e2d\u5ef6'), ('https://suumo.jp/chintai/jnc_000110081443/?bc=100529229356', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e2d\u5ef6'), ('https://suumo.jp/chintai/jnc_000110081440/?bc=100529229921', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e2d\u5ef6'), ('https://suumo.jp/chintai/jnc_000107673121/?bc=100531229903', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u54c1\u5ddd'), ('https://suumo.jp/chintai/jnc_000107534822/?bc=100515641043', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u54c1\u5ddd'), ('https://suumo.jp/chintai/jnc_000109750456/?bc=100531208093', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u54c1\u5ddd'), ('https://suumo.jp/chintai/jnc_000109609927/?bc=100525285843', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u54c1\u5ddd'), ('https://suumo.jp/chintai/jnc_000089700434/?bc=100529401656', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000107534814/?bc=100516061660', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e94\u53cd\u7530'), ('https://suumo.jp/chintai/jnc_000107443358/?bc=100530371515', '', '\u6771\u4eac\u90fd\u54c1\u5ddd\u533a\u897f\u4e94\u53cd\u7530')]}

class V147RealUrlReplay:
    def __init__(self,db,web,group):
        self.db,self.web,self.group=db,web,group
        self.urls=list(_V147_REPLAY_SAMPLES[group]);self.lock=threading.RLock()
        self.saved=0;self.done=0;self.failed=0;self.finished=False
        self.rows=[];self.error='';self.started_at=utc_now();self.report_status='未保存'
        self.thread=threading.Thread(target=self._run,daemon=True,name='sumai-v150-real-url-verification')

    def snapshot(self):
        with self.lock:return dict(saved=self.saved,done=self.done,failed=self.failed,finished=self.finished,
                                   rows=list(self.rows),error=self.error,group=self.group,total=len(self.urls),
                                   started_at=self.started_at,report_status=self.report_status)

    def _run(self):
        b=(34,138,37,141)
        try:
            self.db.check()
            self.web.configure(getattr(self.db,'web_config',{}))
            munis=municipalities(self.web)
            for sample in self.urls:
                url,hint=sample[:2]
                recorded_region=sample[2] if len(sample)>2 else '東京都品川区荏原'
                row=dict(url=url,success=False,reason='',address='',rent=None,layout='',save_readback=False)
                try:
                    match=re.fullmatch(r'東京都([^\s]+?区)([^\s]+)',recorded_region)
                    if not match:raise AppError('過去ログの地域が不正')
                    city,town=match.groups()
                    code=next((key for key,value in TOKYO_WARDS.items() if value==city),None)
                    if not code:raise AppError('区コードを照合できない')
                    region=dict(code=code,pref='13',town=town,city_name=city,label=recorded_region)
                    if not self.web.permitted(url):raise AppError('SUUMOの自動取得が許可されていない')
                    begin_listing(self.web,'SUUMO',url)
                    reply=self.web.fetch(url)
                    soup=BeautifulSoup(reply.text,'html.parser')
                    f=suumo_detail_fields(soup)
                    f['building_name']=_v140_choose_suumo_name(f.get('building_name',''),hint)
                    layout=parsed_layout(f['raw_layout'],rough=False)
                    if not layout:raise AppError('layout not readable')
                    kind=normal(f.get('building_type'))
                    is_mansion='マンション' in kind
                    is_house=not is_mansion and suumo_house_other_type(kind)
                    if not (is_mansion or is_house):raise AppError('building type out of scope')
                    age,ym=age_info(f['raw_age'])
                    area=_identity_area(f.get('raw_area'))
                    if is_mansion:
                        if parsed_layout(f['raw_layout']) not in set().union(*map(set,SUUMO_LAYOUT_GROUPS)):
                            raise AppError('mansion layout out of scope')
                        if age is None or age>15:raise AppError('mansion age out of scope')
                    else:
                        if age is None or age>HOUSE_MAX_AGE or area is None or area<HOUSE_MIN_AREA:
                            raise AppError('house age or floor area out of scope')
                    rent=optional_amount(f['raw_rent'])
                    if not rent:raise AppError('家賃取得失敗')
                    point=_v150_suumo_location(self.web,soup,url,b,munis,region,f)
                    address=point.get('inferred_address','') if point else ''
                    if not town_chome_key(address):
                        raise AppError('地図に必要な町丁目が確定できない')
                    listing=partial_listing('SUUMO',url,'SUUMO募集',address,layout,rent,None,area,
                                            region,b,point,None,age,ym,'',
                                            dwelling_type='mansion' if is_mansion else 'house')
                    if not listing:raise AppError('保存必須項目の不一致')
                    listing['property_id']=suumo_property_id(url)
                    listing['address_quality_version']=4
                    if not listing['property_id']:raise AppError('物件IDが不正')
                    # save_units POST + GET readback verifies every field and raises
                    # on mismatch; this method's return set is the ONLY success.
                    returned=self.db.save_units([listing]);key=compact_listing_key(listing)
                    if not key or key not in returned:raise AppError('Supabase保存の再読込確認が未達')
                    row.update(success=True,address=address,rent=rent,layout=layout,
                               save_readback=True,reason='Supabase保存・町丁目/家賃/間取り/ID再読込一致')
                except SearchCancelled:
                    row['reason']='中断'
                except Exception as exc:
                    row['reason']=(str(exc) if isinstance(exc,AppError) else type(exc).__name__)[:210]
                finally:
                    with self.lock:
                        self.done+=1
                        if row['success']:self.saved+=1
                        else:self.failed+=1
                        self.rows.append(row)
        except Exception as exc:
            with self.lock:self.error=(str(exc) if isinstance(exc,AppError) else type(exc).__name__)[:280]
        finally:
            # Persist compact evidence in THIS application's Supabase namespace.
            # The report contains real per-URL POST+readback results, not HTTP-200
            # counts.  This lets an independent reviewer inspect it without
            # asking the user to download or forward a separate CSV.
            snap=self.snapshot()
            try:
                document={
                  'id':'realurl.v150.'+hashlib.sha256((self.group+self.started_at+str(time.monotonic_ns())).encode()).hexdigest(),
                  'status':'diagnostic_log','started_at':self.started_at,'finished_at':utc_now(),
                  'conditions':{'source':'v150_real_url_post_get_verification','group':self.group,
                                'required':24,'total':len(self.urls)},
                  'summary':{'processed':snap['done'],'saved':snap['saved'],'failed':snap['failed'],
                             'passed':len(self.urls)==30 and snap['saved']>=24,
                             'run_error':snap['error'],'results':snap['rows'],'events':[]}
                }
                self.db.save_search(document)
                with self.lock:self.report_status='Supabaseへ実URL別検証結果を保存済み'
            except Exception as exc:
                with self.lock:self.report_status='検証レポート保存失敗: '+type(exc).__name__
            with self.lock:self.finished=True


@st.fragment(run_every='10s')
def v147_replay_status():
    job=st.session_state.get('v147_live_test_job')
    if job is None:return
    snap=job.snapshot();saved=snap['saved'];done=snap['done'];total=snap['total']
    st.progress(done/max(1,total),text=f'実URL検証｜処理 {done}/{total}件｜保存 {saved}件｜未保存 {snap["failed"]}件')
    if snap['error']:st.error('検証エラー：'+snap['error'])
    if snap['finished']:
        st.caption(snap.get('report_status',''))
        passed=(total>=30 and saved>=24)
        st.write(('合格' if passed else '未達')+f'｜実URL {total}件｜Supabase保存・再読込成功 {saved}件｜成功率 {saved/max(1,total):.1%}')
        if snap['rows']:
            st.download_button('実URL・保存結果CSV',
                 ('URL,保存確認,家賃,間取り,町丁目または詳細住所,理由\n'+''.join(
                   f'{json.dumps(item["url"],ensure_ascii=False)},{int(item["success"])},{item["rent"] or ""},'
                   f'{item["layout"]},{json.dumps(item["address"],ensure_ascii=False)},'
                   f'{json.dumps(item["reason"],ensure_ascii=False)}\n' for item in snap['rows'])).encode('utf-8-sig'),
                   'sumai_v150_real_url_verification.csv','text/csv',key='v147_replay_export')
    else:st.caption('バックグラウンドで30URLの詳細・地図・公式住所照合・Supabase保存/再読込を確認中')


# v170 DATA ISOLATION:
# Public real-estate listings, town-rent summaries, and acquisition progress
# remain exclusively in the existing shared Supabase namespace, unchanged.
# Commute destinations are automatically persisted across redeploys for the
# same browser and application origin. Each browser carries a stable anonymous
# private ID in local storage; Supabase is the secondary backup for that ID.
# A legacy v174 commute_ref query is imported invisibly and then cleared.
# Neither setup, recovery-code copying, nor URL editing is required.
# Property data stays in rental_listing rows, never in personal backups.
# A browser can hold multiple named local profiles without a login; personal
# records are isolated from shared rental_listing and map summary records.
# Legacy v169 single-browser preferences are imported into the default profile.
# This is not account-authenticated: anyone possessing a recovery URL can use it.
_V170_PERSONAL_STORAGE_KEY = 'sumai-compass-personal-commute-v2'
_V174_PERSONAL_TOKEN_QUERY = 'commute_ref'
_V174_PERSONAL_RECORD_PREFIX = 'personal-commute.v174.'
_V169_COMMUTE_BROWSER_KEY = 'sumai-compass-commute-preferences-v1'
_V170_COMMUTE_FIELDS = ('commute_line_v160','commute_station_v160',
                        'commute_line_2_v164','commute_station_2_v164',
                        'commute_line_3_v228','commute_station_3_v228')


def _v174_commute_token(text):
    value=str(text or '').lower()
    return value if re.fullmatch(r'[0-9a-f]{40}',value) else None


def _v174_commute_backup(value):
    """Bounded, validated user/profile record; never accept client Supabase commands."""
    if not isinstance(value,dict) or value.get('schema')!=2:return None
    profiles=value.get('profiles')
    if not isinstance(profiles,dict) or len(profiles)>12 or 'primary' not in profiles:return None
    clean={}
    for key,item in profiles.items():
        if not isinstance(key,str) or not (key=='primary' or re.fullmatch(r'p_[0-9a-f]{20,40}',key)):
            return None
        if not isinstance(item,dict) or not isinstance(item.get('name'),str):return None
        name=item['name'].strip()[:24]
        if not name:return None
        prefs=item.get('prefs');valid=None
        if prefs is not None:
            if not isinstance(prefs,dict) or prefs.get('schema')!=1:return None
            prefs=dict(prefs)
            prefs.setdefault('line3',_COMMUTE_V160_NO_LINE);prefs.setdefault('station3',_COMMUTE_V160_NO_STATION)
            keys=('line1','station1','line2','station2','line3','station3')
            if not all(isinstance(prefs.get(k),str) and len(prefs[k])<=120 for k in keys):return None
            valid={'schema':1,**{k:prefs[k] for k in keys}}
        clean[key]={'name':name,'prefs':valid}
    active=value.get('active')
    if active not in clean:return None
    try:stamp=int(value.get('updated_at') or 0)
    except (ValueError,TypeError):stamp=0
    if stamp<0 or stamp>100000000000000:stamp=0
    return {'schema':2,'profiles':clean,'active':active,'updated_at':stamp}


def _v174_backup_nonempty(backup):
    return bool(backup and (len(backup['profiles'])>1 or any(
        isinstance(profile.get('prefs'),dict) and any(
            profile['prefs'].get(field) not in (_COMMUTE_V160_NO_LINE,_COMMUTE_V160_NO_STATION,'')
            for field in ('station1','station2','station3'))
        for profile in backup['profiles'].values())))


def _v174_personal_record_id(token):
    return _V174_PERSONAL_RECORD_PREFIX+hashlib.sha256(token.encode('ascii')).hexdigest()


def _v174_read_personal_backup(token):
    db=Database()
    rows=db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+db.namespace,
                  'id':'eq.'+_v174_personal_record_id(token),
                  'status':'eq.personal_commute','select':'summary','limit':1})
    if not isinstance(rows,list):raise AppError('通勤バックアップの応答形式が不正です。')
    return _v174_commute_backup((rows[0].get('summary') or {}).get('backup')) if rows else None


def _v174_save_personal_backup(token,backup):
    cleaned=_v174_commute_backup(backup)
    if cleaned is None:raise AppError('通勤設定の保存形式が不正です。')
    now=utc_now()
    Database().save_search({'id':_v174_personal_record_id(token),
        'status':'personal_commute','started_at':now,'finished_at':now,
        'conditions':{'record_type':'personal_commute','schema':2},
        'summary':{'backup':cleaned,'saved_at':now}})


def _v180_commute_backup_for_current(state):
    """Merge only this user's active commute fields into its existing profile set."""
    previous=_v174_commute_backup(state.get('_v180_browser_backup'))
    if previous is None:
        previous={'schema':2,'active':'primary','updated_at':0,
                  'profiles':{'primary':{'name':'このブラウザー','prefs':None}}}
    active=state.get('_v170_commute_active','primary')
    if active not in previous['profiles']:
        # Never create an unapproved profile or leak one person's fields to another.
        return None
    previous['profiles'][active]['prefs']=v170_commute_snapshot(state)
    previous['updated_at']=max(int(time.time_ns()//1000000),int(previous.get('updated_at') or 0)+1)
    return _v174_commute_backup(previous)


def _v180_commit_commute_change():
    """Immediately save and READ BACK changes when an actual selection changes."""
    state=st.session_state
    if not state.get('_v170_commute_loaded') or state.get('_v170_commute_pending'):
        return False
    token=_v174_commute_token(state.get('_v174_commute_token'))
    if not token:
        state['_v174_commute_backup_error']='保存先のブラウザー識別情報を確認できません。'
        return False
    backup=_v180_commute_backup_for_current(state)
    if backup is None:
        state['_v174_commute_backup_error']='通勤設定の利用者が確定していません。'
        return False
    state['_v180_browser_backup']=backup
    state['_v180_pending_backup']=backup
    try:
        _v174_save_personal_backup(token,backup)
        verified=_v174_read_personal_backup(token)
        if verified != backup:
            raise AppError('保存後の再読込で通勤先が一致しません。')
    except Exception as exc:
        state['_v174_commute_backup_error']=str(exc)[:180] if isinstance(exc,AppError) else type(exc).__name__
        return False
    signature=hashlib.sha256(json.dumps(backup,ensure_ascii=False,sort_keys=True,
                      separators=(',',':')).encode('utf-8')).hexdigest()
    state['_v174_commute_last_saved']=(token,signature)
    state['_v180_confirmed_backup']=(token,backup)
    state['_v180_pending_backup']=None
    state['_v174_commute_backup_error']=''
    return True


def _v174_cloud_sync(state,token,backup):
    cleaned=_v174_commute_backup(backup)
    if not token or cleaned is None:return False
    last=state.get('_v180_confirmed_backup')
    if (isinstance(last,tuple) and len(last)==2 and last[0]==token
            and isinstance(last[1],dict)
            and int(last[1].get('updated_at') or 0)>int(cleaned.get('updated_at') or 0)):
        # A late iframe callback must never overwrite the newer verified DB row.
        return True
    fingerprint=hashlib.sha256(json.dumps(cleaned,ensure_ascii=False,
                            sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    if state.get('_v174_commute_last_saved')==(token,fingerprint):return True
    try:
        _v174_save_personal_backup(token,cleaned)
    except Exception as exc:
        state['_v174_commute_backup_error']=str(exc)[:180] if isinstance(exc,AppError) else type(exc).__name__
        return False
    state['_v174_commute_last_saved']=(token,fingerprint)
    state['_v174_commute_backup_error']=''
    return True


def _v174_cloud_restore_if_newer(state,token,local_backup):
    try:remote=_v174_read_personal_backup(token)
    except Exception as exc:
        state['_v174_commute_backup_error']=str(exc)[:180] if isinstance(exc,AppError) else type(exc).__name__
        return None
    if remote is not None and (not _v174_backup_nonempty(local_backup)
             or remote['updated_at']>int(local_backup.get('updated_at') or 0)):
        return remote
    if _v174_backup_nonempty(local_backup):
        _v174_cloud_sync(state,token,local_backup)
    return None


def _v174_commute_url_token():
    try:return _v174_commute_token(st.query_params.get(_V174_PERSONAL_TOKEN_QUERY))
    except (AttributeError,TypeError,ValueError):return None


def _v174_record_client_token(state,message):
    """Keep the stable browser ID; silently consume a legacy URL ID once stored.

    Never advertise or require a recovery code.  Deleting the old URL argument
    happens only when the client confirms that its persistent storage works.
    """
    token=_v174_commute_token(message.get('client_token'))
    if not token:return None
    state['_v174_commute_token']=token
    if message.get('available') is True and _v174_commute_url_token():
        try:del st.query_params[_V174_PERSONAL_TOKEN_QUERY]
        except (AttributeError,KeyError,TypeError):pass
    return token

_V170_PERSONAL_STORAGE_HTML = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<style>html,body{padding:0;margin:0;height:1px;overflow:hidden}</style></head><body>
<script>
(function(){
 'use strict';
 const KEY='sumai-compass-personal-commute-v2';
 const LEGACY='sumai-compass-commute-preferences-v1';
 const MAX_PROFILES=12;
 const CLIENT_TOKEN_KEY='sumai-compass-commute-recovery-token-v174';
 const COOKIE_TOKEN_KEY='sumai_compass_commute_device_v180';
 const STORAGE_HISTORY_PREFIX='sumai-compass-personal-commute-v2-owner-';
 let persistentStorageAvailable=true;
 let lastResponse='';
 const memoryStore={};
 function getStored(key){
   try{return window.localStorage.getItem(key);}
   catch(error){persistentStorageAvailable=false;
     return Object.prototype.hasOwnProperty.call(memoryStore,key)?memoryStore[key]:null;}
 }
 function setStored(key,value){
   memoryStore[key]=String(value);
   try{window.localStorage.setItem(key,String(value));return true;}
   catch(error){persistentStorageAvailable=false;return false;}
 }
 function removeStored(key){
   delete memoryStore[key];
   try{window.localStorage.removeItem(key);}
   catch(error){persistentStorageAvailable=false;}
 }
 function host(type,extra){
   window.parent.postMessage(Object.assign({isStreamlitMessage:true,type:type},extra||{}),'*');
 }
 function report(message){
   const json=JSON.stringify(message);
   if(json===lastResponse)return;
   lastResponse=json;
   host('streamlit:setComponentValue',{dataType:'json',value:message});
 }
 function has(obj,key){return Object.prototype.hasOwnProperty.call(obj,key);}
 function safePrefs(p){
   if(!p||typeof p!=='object'||Array.isArray(p)||p.schema!==1)return null;
   p=Object.assign({line3:'路線を選択してください',station3:'駅を選択してください'},p);
   const keys=['line1','station1','line2','station2','line3','station3'];
   if(!keys.every(k=>typeof p[k]==='string'&&p[k].length<=120))return null;
   return {schema:1,line1:p.line1,station1:p.station1,line2:p.line2,station2:p.station2,line3:p.line3,station3:p.station3};
 }
 function blankData(){return {schema:2,updated_at:0,active:'primary',profiles:{primary:{name:'このブラウザー',prefs:null}}};}
 function normalize(raw){
   const result=blankData();
   if(!raw||raw.schema!==2||!raw.profiles||typeof raw.profiles!=='object')return result;
   if(raw.profiles.primary){result.profiles.primary.prefs=safePrefs(raw.profiles.primary.prefs);}
   if(Number.isSafeInteger(raw.updated_at)&&raw.updated_at>=0)result.updated_at=raw.updated_at;
   for(const [id,item] of Object.entries(raw.profiles)){
     if(id==='primary'||Object.keys(result.profiles).length>=MAX_PROFILES)continue;
     if(!/^p_[a-f0-9]{20,40}$/.test(id)||!item||typeof item.name!=='string')continue;
     const name=item.name.trim().slice(0,24);
     if(!name)continue;
     result.profiles[id]={name:name,prefs:safePrefs(item.prefs)};
   }
   if(typeof raw.active==='string'&&has(result.profiles,raw.active))result.active=raw.active;
   return result;
 }
 function load(){
   let value=getStored(KEY);
   if(value!==null){
     try{return normalize(JSON.parse(value));}
     catch(error){return blankData();}
   }
   const data=blankData();
   const legacy=getStored(LEGACY);
   if(legacy!==null){
     try{data.profiles.primary.prefs=safePrefs(JSON.parse(legacy));}
     catch(error){}
   }
   setStored(KEY,JSON.stringify(data));
   return data;
 }
 function summary(data){
   return {active:data.active,profiles:Object.entries(data.profiles).map(([id,p])=>({id:id,name:p.name})),
     prefs:data.profiles[data.active].prefs};
 }
 function write(data){
   data.updated_at=Date.now();
   setStored(KEY,JSON.stringify(data));
 }
 function validToken(v){return typeof v==='string' && /^[a-f0-9]{40}$/.test(v);}
 function cookieToken(){
   try{
     const item=document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith(COOKIE_TOKEN_KEY+'='));
     const v=item?decodeURIComponent(item.slice(COOKIE_TOKEN_KEY.length+1)):'';
     return validToken(v)?v:'';
   }catch(error){return '';}
 }
 function persistToken(token){
   const stored=setStored(CLIENT_TOKEN_KEY,token);
   let cookieSaved=false;
   try{
     document.cookie=COOKIE_TOKEN_KEY+'='+encodeURIComponent(token)+'; Max-Age=31536000; Path=/; SameSite=Lax'+
       (location.protocol==='https:'?'; Secure':'');
     cookieSaved=cookieToken()===token;
   }catch(error){}
   if(!stored && cookieSaved)persistentStorageAvailable=true;
   return stored||cookieSaved;
 }
 function recoveryToken(args){
   const fromUrl=String(args.restore_token||'');
   let token=getStored(CLIENT_TOKEN_KEY)||cookieToken()||'';
   if(validToken(fromUrl) && token!==fromUrl){
     // Never mix two persons' browser profiles when following a different
     // private recovery URL. A legacy v170 store has no token yet; import it.
     if(validToken(token)){
       const current=getStored(KEY);
       if(current!==null)setStored(STORAGE_HISTORY_PREFIX+token,current);
       const destination=getStored(STORAGE_HISTORY_PREFIX+fromUrl);
       if(destination===null)removeStored(KEY);
       else setStored(KEY,destination);
     }
     token=fromUrl;persistToken(token);
   }
   if(!validToken(token)){
     const bytes=new Uint8Array(20);window.crypto.getRandomValues(bytes);
     token=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
     persistToken(token);
   }else if(!cookieToken()){
     persistToken(token);
   }
   // The anonymous browser cookie can restore the Supabase backup even when
   // iframe localStorage is blocked by a privacy setting.
   if(cookieToken()===token)persistentStorageAvailable=true;
   return token;
 }
 function stateReport(event,request,data,token,extra){
   report(Object.assign({event:event,available:(persistentStorageAvailable||cookieToken()===token),
      request_id:request,client_token:token,backup:normalize(data)},summary(data),extra||{}));
 }
 function fallback(event,request){
   report({event:event,available:false,request_id:String(request||''),
       active:'primary',profiles:[{id:'primary',name:'このブラウザー'}],prefs:null});
 }
 function handle(args){
   const action=String(args.action||'read');
   const request=String(args.request_id||'');
   try{
     const token=recoveryToken(args);
     let data=load();
     if(action==='read'){
       stateReport('loaded',request,data,token);return;
     }
     if(action==='restore'){
       if(!args.backup || args.backup.schema!==2 || !args.backup.profiles ||
          !args.backup.profiles.primary){
         stateReport('restore_denied',request,data,token);return;
       }
       data=normalize(args.backup);write(data);
       // Keep the cloud's modification time so another browser is not
       // automatically treated as newer just because restoration occurred.
       data.updated_at=Number(args.backup.updated_at)||0;
       setStored(KEY,JSON.stringify(data));
       stateReport('restored',request,data,token);return;
     }
     if(action==='switch'){
       const id=String(args.profile_id||'');
       if(!has(data.profiles,id)){
         stateReport('invalid_profile',request,data,token);return;
       }
       data.active=id;write(data);
       stateReport('switched',request,data,token);return;
     }
     if(action==='create'){
       const name=String(args.name||'').trim().slice(0,24);
       if(!name||Object.keys(data.profiles).length>=MAX_PROFILES){
         stateReport('create_denied',request,data,token);return;
       }
       const bytes=new Uint8Array(16);window.crypto.getRandomValues(bytes);
       let id='p_'+Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
       if(has(data.profiles,id)){id='p_'+Array.from(window.crypto.getRandomValues(bytes),x=>x.toString(16).padStart(2,'0')).join('');}
       data.profiles[id]={name:name,prefs:null};data.active=id;write(data);
       stateReport('created',request,data,token);return;
     }
     if(action==='save'){
       const id=String(args.profile_id||'');
       if(id!==data.active||!has(data.profiles,id)){
         stateReport('stale_profile',request,data,token);return;
       }
       const prefs=safePrefs(args.prefs);
       if(!prefs){report({event:'invalid_prefs',available:true});return;}
       const old=JSON.stringify(data.profiles[id].prefs);
       if(old!==JSON.stringify(prefs)){
         data.profiles[id].prefs=prefs;write(data);
       }
       stateReport('saved',request,data,token,{signature:String(args.signature||''),profile_id:id});
       return;
     }
   }catch(error){fallback(action==='read'?'loaded':'failed',request);}
 }
 window.addEventListener('message',event=>{
   if(!event.data||event.data.type!=='streamlit:render')return;
   handle(event.data.args||{});
 });
 host('streamlit:componentReady',{apiVersion:1});
 host('streamlit:setFrameHeight',{height:1});
})();
</script></body></html>"""

@st.cache_resource(show_spinner=False)
def v174_personal_commute_component():
    """v174 component reloads assets without changing browser storage origin."""
    folder=os.path.join(tempfile.gettempdir(),'sumai-personal-commute-v228')
    os.makedirs(folder,exist_ok=True)
    path=os.path.join(folder,'index.html')
    previous=None
    try:
        with open(path,'r',encoding='utf-8') as handle:previous=handle.read()
    except OSError:pass
    if previous!=_V170_PERSONAL_STORAGE_HTML:
        temporary=path+'.tmp-'+str(os.getpid())
        with open(temporary,'w',encoding='utf-8') as handle:handle.write(_V170_PERSONAL_STORAGE_HTML)
        os.replace(temporary,path)
    return st.components.v1.declare_component('sumai_personal_commute_v228',path=folder)


def v170_validate_personal_commute(value,lines):
    """Check station-line membership before putting browser data in session_state."""
    if not isinstance(value,dict) or value.get('schema')!=1:return None
    result={}
    for line_key,station_key,stored_line,stored_station in (
        ('commute_line_v160','commute_station_v160','line1','station1'),
        ('commute_line_2_v164','commute_station_2_v164','line2','station2'),
        ('commute_line_3_v228','commute_station_3_v228','line3','station3')):
        line=value.get(stored_line)
        station=value.get(stored_station)
        if not isinstance(line,str) or line not in lines:
            line=_COMMUTE_V160_NO_LINE
            station=_COMMUTE_V160_NO_STATION
        elif not isinstance(station,str) or station not in lines[line]:
            station=_COMMUTE_V160_NO_STATION
        result[line_key]=line
        result[station_key]=station
    return result


def v170_commute_snapshot(state):
    """Only selected railway/station names; never property or other users' data."""
    return {'schema':1,
            'line1':str(state.get('commute_line_v160',_COMMUTE_V160_NO_LINE)),
            'station1':str(state.get('commute_station_v160',_COMMUTE_V160_NO_STATION)),
            'line2':str(state.get('commute_line_2_v164',_COMMUTE_V160_NO_LINE)),
            'station2':str(state.get('commute_station_2_v164',_COMMUTE_V160_NO_STATION)),
            'line3':str(state.get('commute_line_3_v228',_COMMUTE_V160_NO_LINE)),
            'station3':str(state.get('commute_station_3_v228',_COMMUTE_V160_NO_STATION))}


def v170_apply_browser_profile(state,message):
    """Adopt exactly the active local profile; erase the previous profile's values."""
    raw_profiles=message.get('profiles') or []
    profiles=[]
    for item in raw_profiles[:12]:
        if not isinstance(item,dict):continue
        ident=item.get('id');name=item.get('name')
        if isinstance(ident,str) and (ident=='primary' or re.fullmatch(r'p_[0-9a-f]{20,40}',ident)) and isinstance(name,str):
            profiles.append({'id':ident,'name':name[:24]})
    if not any(p['id']=='primary' for p in profiles):profiles.insert(0,{'id':'primary','name':'このブラウザー'})
    ids={p['id'] for p in profiles}
    active=message.get('active')
    if active not in ids:active='primary'
    state['_v170_commute_profiles']=profiles
    state['_v170_commute_active']=active
    state['v170_commute_person_selector']=active
    backup=_v174_commute_backup(message.get('backup'))
    if backup is not None:state['_v180_browser_backup']=backup
    restored=v170_validate_personal_commute(message.get('prefs'),commute_lines_v160())
    if restored is None:
        restored={k:(_COMMUTE_V160_NO_LINE if 'line_' in k else _COMMUTE_V160_NO_STATION)
                  for k in _V170_COMMUTE_FIELDS}
    for key,value in restored.items():state[key]=value
    first=restored['commute_station_v160']
    state['commute_destination_v159']=first if first!=_COMMUTE_V160_NO_STATION else '表示しない'


def v170_commute_profile_changed():
    """Streamlit selectbox callback runs before main() and the personal sync."""
    state=st.session_state
    target=state.get('v170_commute_person_selector')
    if target and target!=state.get('_v170_commute_active'):
        state['_v170_commute_pending']={'action':'switch','profile_id':target,
                                      'request_id':str(state.get('_v170_commute_nonce',0)+1)}
        state['_v170_commute_nonce']=state.get('_v170_commute_nonce',0)+1


def v170_commute_autosave_sync():
    """Restore browser state, back it up per recovery URL, and prevent stale writes."""
    state=st.session_state
    widget=v174_personal_commute_component()
    token=state.get('_v174_commute_token') or _v174_commute_url_token()
    if not state.get('_v170_commute_loaded'):
        message=widget(action='read',restore_token=token or '',
                       key='v170_browser_personal_prefs',default=None,height=1)
        if not isinstance(message,dict) or message.get('event')!='loaded':return False
        token=_v174_record_client_token(state,message)
        state['_v170_commute_loaded']=True
        state['_v170_commute_available']=bool(message.get('available'))
        local=_v174_commute_backup(message.get('backup'))
        if token and local is not None:
            remote=_v174_cloud_restore_if_newer(state,token,local)
            if remote is not None:
                state['_v174_commute_pending_restore']=remote
                st.rerun()
                return False
        v170_apply_browser_profile(state,message)
        st.rerun()
        return False
    pending_restore=state.get('_v174_commute_pending_restore')
    if pending_restore is not None:
        message=widget(action='restore',backup=pending_restore,restore_token=token or '',
                       key='v170_browser_personal_prefs',default=None,height=1)
        if isinstance(message,dict) and message.get('event')=='restored':
            token=_v174_record_client_token(state,message)
            state.pop('_v174_commute_pending_restore',None)
            v170_apply_browser_profile(state,message)
            if token:
                cleaned=_v174_commute_backup(message.get('backup'))
                if cleaned:
                    sig=hashlib.sha256(json.dumps(cleaned,ensure_ascii=False,
                                sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
                    state['_v174_commute_last_saved']=(token,sig)
            st.rerun()
        return False
    pending=state.get('_v170_commute_pending')
    if pending:
        kwargs={k:v for k,v in pending.items() if k in ('action','profile_id','name','request_id')}
        message=widget(**kwargs,restore_token=token or '',
                       key='v170_browser_personal_prefs',default=None,height=1)
        if isinstance(message,dict) and message.get('request_id')==pending['request_id']:
            state['_v170_commute_pending']=None
            state['_v170_commute_available']=bool(message.get('available'))
            token=_v174_record_client_token(state,message)
            if message.get('event') in ('switched','created'):
                v170_apply_browser_profile(state,message)
                if token:_v174_cloud_sync(state,token,message.get('backup'))
                if message.get('event')=='created':state['v170_person_new_name']=''
            else:
                state['_v170_commute_error']='利用者の切り替え・追加に失敗しました。'
                if message.get('active'):
                    v170_apply_browser_profile(state,message)
            st.rerun()
        return False
    active=state.get('_v170_commute_active','primary')
    prefs=v170_commute_snapshot(state)
    signature=hashlib.sha256(json.dumps(prefs,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()[:16]
    message=widget(action='save',restore_token=token or '',profile_id=active,
                   prefs=prefs,signature=signature,
                   key='v170_browser_personal_prefs',default=None,height=1)
    if isinstance(message,dict):
        if message.get('event')=='stale_profile':
            state['_v170_commute_loaded']=False
            st.rerun()
            return False
        if (message.get('event')=='saved' and message.get('signature')==signature
                and message.get('profile_id')==active):
            state['_v170_commute_available']=bool(message.get('available'))
            token=_v174_record_client_token(state,message)
            if token:_v174_cloud_sync(state,token,message.get('backup'))
    return True


def _v209_automatic_log_tools():
    controller=get_automatic_collection();snap=controller.snapshot() if controller else None
    busy=bool(snap and snap['running']);summary=(snap.get('summary') or {}) if snap else {}
    st.markdown('##### ログ・エラーCSV')
    automatic_log_download_panel(controller,busy,summary if snap else st.session_state.get('automatic_saved_summary',{}))

    try:
        # Full historical errors are queried only when explicitly requested.
        # This avoids competing 25k+ JSON rows with the primary collection job.
        error_job=get_automatic_failure_export(max_age=900,refresh=False);failure=error_job.snapshot()
        if st.button('エラー履歴を取得する（時間がかかります）',key='automatic_refresh_error_csv',disabled=busy or error_job.running):
            error_job.refresh(max_age=0)
            st.rerun()
        if failure['loaded']:
            st.download_button(f"エラー履歴CSV（{failure['row_count']}行）",
                               failure['csv'],'sumai_all_acquisition_errors.csv','text/csv',
                               key='automatic_all_errors_csv',on_click='ignore',use_container_width=True)
            status='過去ログの統合が未完了' if failure.get('phase')=='partial' else '過去ログも裏で統合中' if failure['running'] and failure.get('phase')=='historical_backfill' else '裏で最新化中' if failure['running'] else '最新'
            st.caption(f"エラーCSV：{status}｜最終同期 {acquisition_time_jst(failure.get('updated_at'))}｜同じ物件で複数エラーがあれば複数行です。")
        else:
            st.download_button('全取得エラーCSV',b'','sumai_all_acquisition_errors.csv','text/csv',
                               key='automatic_all_errors_csv_wait',disabled=True,use_container_width=True)
            st.caption('大量の履歴照会は自動実行しません。収集停止後に「エラーCSVを手動更新（過去全件）」を押してください。')
        if failure['error']:st.error('エラーCSVの自動更新に失敗しました：'+failure['error'])
    except Exception as exc:
        st.error('エラーCSVを出力できませんでした：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))


def _v209_saved_log_tools(state):
    if st.button('検索履歴を読み込む',key='new_history_load'):
        try: state.new_history=Database().history()
        except AppError as exc: st.error(str(exc))
    if state.get('new_history'):
        st.dataframe([{'開始（UTC）':r['started_at'],'結果':r['status'],'表示範囲':str(r['conditions'].get('bounds','')),
                       '保存確認':r['summary'].get('saved',0)} for r in state.new_history],hide_index=True)
    if state.get('new_history'):
        chosen=st.selectbox('作業ログを取り出す検索',state.new_history,format_func=lambda r:r['started_at']+'｜'+r['status'],key='diagnostic_history')
        if st.button('選んだ検索の詳細作業ログを読み込む',key='diagnostic_load'):
            try:
                state.saved_diagnostics=Database().load_diagnostics(chosen['id'])
                if not state.saved_diagnostics:st.info('この検索には保存された詳細ログがありません。v30以降で再検索してください。')
            except AppError as exc:st.error(str(exc))
    if state.get('saved_diagnostics'):diagnostic_downloads(state.saved_diagnostics,'saved')
    with st.expander('前回のログと比較して改善を確認'):
        baseline=st.file_uploader('前回の解析用JSONログを選択',type=['json'],key='diagnostic_baseline')
        if baseline:
            try:
                old=json.loads(baseline.getvalue().decode('utf-8-sig')).get('events')
                if not isinstance(old,list) or not all(isinstance(e,dict) for e in old):raise ValueError()
                compare_target=st.radio('比較する今回のログ',['現在の検索','読み込んだ保存ログ'],key='diagnostic_compare_target')
                current=(active_job().audit.records() if active_job() else []) if compare_target=='現在の検索' else state.get('saved_diagnostics',[])
                if current:
                    st.dataframe(compare_logs(old,current),hide_index=True)
                    old_start=next((e.get('details',{}) for e in old if e.get('code')=='start'),{})
                    new_start=next((e.get('details',{}) for e in current if e.get('code')=='start'),{})
                    if old_start.get('bounds')!=new_start.get('bounds') or old_start.get('providers')!=new_start.get('providers'):st.info('表示範囲・取得元が異なります。差分だけで改善効果を判断できません。')
                    st.caption('差分はログの観測件数です。精度は同じ物件の正解データとの照合で確認してください。')
                else:st.info('比較対象として現在の検索ログまたは保存ログを読み込んでください。')
            except (ValueError,UnicodeError,AttributeError):st.error('このアプリからダウンロードした解析用JSONログを選択してください。')


def _v209_initial_settings(state):
            st.subheader('施設名検索API（任意）')
            st.write('通常はPhoton / OpenStreetMapを使います。会社名などが見つからない場合は、Brave Search APIで住所候補を探し、国土地理院で位置を検索します。住所候補は検索ページの記載で、正確性は未確認です。仮ピンの住所と位置を必ず確認してください。')
            st.caption('利用する場合はBrave Search APIのキーを取得し、StreamlitのSecretsに次の3項目を設定してください。APIの利用料は契約によります。未設定では呼び出しません。上限は実行プロセスごとのリクエスト数で、再起動するとリセットされます。請求上限はAPI提供元で管理してください。')
            st.code('LANDMARK_BRAVE_API_KEY = "取得したAPIキー"\nLANDMARK_BRAVE_API_APPROVED = "YES"\nLANDMARK_BRAVE_API_MAX_REQUESTS = 25',language='toml')
            enabled=bool(_v216_setting('LANDMARK_BRAVE_API_KEY')) and str(_v216_setting('LANDMARK_BRAVE_API_APPROVED')).upper()=='YES'
            st.caption('施設名Web検索API：'+('有効' if enabled else '未設定（通常の施設名検索のみ）'))
            st.markdown('[APIキーの取得・料金確認](https://brave.com/search/api/)')
            st.subheader('実URL・Supabase保存検証（追加課金なし）')
            st.caption('旧版の失敗ログから固定抽出した実URLを再検索し、住所・家賃・間取りを保存してSupabaseで読み戻します。24/30以上で合格。')
            groups={'詳細住所確定失敗 30 URL':'map.address_inference',
                    'GSI住所候補なし 30 URL':'address.residential_candidates',
                    '地図マーカー欠落 30 URL（過去ログから抽出）':'map.room_marker'}
            option=st.selectbox('検証する主要エラー',list(groups),key='v147_replay_group')
            existing=state.get('v147_live_test_job')
            running=bool(existing and not existing.snapshot()['finished'])
            if st.button('実URLで再取得し、Supabase保存・再読込を検証する',key='v147_real_url_replay',
                         disabled=running or automatic_busy() or bool(active_job() and not active_job().snapshot()['finished'])):
                try:
                    db=Database();db.web_config=rental_network_settings()
                    job=V147RealUrlReplay(db,PublicWeb(),groups[option]);state.v147_live_test_job=job
                    job.thread.start()
                except AppError as exc:st.error(str(exc))
            if state.get('v147_live_test_job'):st.caption('検証結果・CSVは「設定」→「ログ出力」で確認できます。')
            st.divider()
            st.subheader('新しいアプリの初期設定')
            with st.expander('既存DBの整理（過去エラーのみ削除・物件は保持）',expanded=False):
                st.warning('自動収集を停止したうえで、保存領域名をSecretsと照合してください。実行すると過去のエラーログと診断ログが削除され、復元できません。募集情報・取得済みID・町の進捗は削除しません。')
                st.caption('以下は既定値 SUPABASE_NAMESPACE = sumai-compass 用です。別の値なら SQL 内の namespace を置換してください。')
                st.code(DB_MAINTENANCE_SQL,language='sql')
                st.download_button('既存エラーログ整理SQL',DB_MAINTENANCE_SQL,
                                   'supabase_cleanup_ver105.sql','text/plain')
            st.write('1．SupabaseのSQL Editorに、以下のSQLを貼り付けてRunを押してください。')
            st.code(SQL,language='sql')
            st.download_button('セットアップSQLをダウンロード',SQL,'supabase_rebuild_01.sql','text/plain')
            st.write('2．StreamlitのSettings → Secretsに、接続先とサーバー用のSecret keyを設定してください。')
            st.code('SUPABASE_URL = "https://プロジェクトID.supabase.co"\nSUPABASE_SECRET_KEY = "sb_secret_から始まるキー"\nSUPABASE_NAMESPACE = "sumai-compass"',language='toml')
            st.write('物件取得にプロキシを使用する場合は、同じSecretsへ追加してください。Supabase・地名取得には適用しません。')
            st.code('RENTAL_HTTP_PROXIES = ["http://ユーザー:パスワード@ホスト:ポート", "http://別のユーザー:パスワード@別のホスト:ポート"]',language='toml')
            try:st.caption('設定済みプロキシ：'+str(len(rental_network_settings()['proxies']))+'経路')
            except AppError as exc:st.error(str(exc))
            st.caption('第1段階：Google・Yahoo!検索ページを通常のHTTPナビゲーションとして取得します。旧版のアプリ内robots判定で事前遮断せず、取得できた場合は元の物件掲載ページで建物と住所を照合します。公開先が実際に403/429を返す場合は停止します。')
            st.caption('第1段階専用のHTTP(S)プロキシを使う場合は下記Secretsに設定できます。設定しなければ直接通信です。OS・他アプリ・共有環境のVPN/プロキシ設定を変更しません。')
            st.code('STAGE1_SEARCH_DIRECT = "YES"\nSTAGE1_SEARCH_HTTP_PROXIES = ["http://ユーザー:パスワード@ホスト:ポート"]',language='toml')
            st.caption('サイトのrobots.txt上の方針や利用規約が変更されることがあります。HTTPヘッダーやプロキシによる取得成功を保証しません。Google・Yahoo!の実際のアクセス制限・CAPTCHAを突破する処理はありません。検索APIは任意で追加できます。')
            st.caption('検索APIは課金される場合があります。既定では無効です。利用者が契約・キー取得後に明示的に有効化した場合のみ要求します。上限はアプリ実行プロセスごとであり、請求上限ではありません。')
            st.code('STAGE1_SEARCH_API_PROVIDER = "brave"\nSTAGE1_SEARCH_API_KEY = "契約したサービスのAPIキー"\nSTAGE1_SEARCH_API_APPROVED = "YES"\nSTAGE1_SEARCH_API_MAX_REQUESTS = 25',language='toml')
            st.caption('Googleの既存Custom Search API契約がある場合は PROVIDER = "google_legacy" とし、STAGE1_GOOGLE_CSE_ID = "既存の検索エンジンID" を追加してください。Yahoo! JAPANのウェブ検索APIは提供を終了しています。')
            st.caption('追加対策：同じ経路で公開トップページを確認してCookieを引き継ぎ、物件サイトごとに同時通信2件・通信開始は最低1秒間隔で取得します。403の経路とURLは60秒休止し、別の公開条件ページも確認します。')
            st.caption('利用するプロキシサービスの接続URLを設定してください。設定済みの経路で403・空応答・通信失敗が出た場合は別の経路へ切り替えます。認証情報は作業ログへ出力しません。')
            st.write('3．接続確認が成功したら、「住まいを探す」の地図を動かして検索してください。')
            if st.button('Supabase接続・テーブル構造を診断',key='new_check'):
                try:
                    db=Database();db.check()
                    st.success('3テーブルへの少量データ照会に成功。接続先・認証・基本構造は正常です。大量の取得履歴照会でのHTTP 500は別途発生する可能性があります。')
                except AppError as exc:
                    st.error(str(exc))
                    diagnostic=getattr(exc,'diagnostic',{})
                    if diagnostic:st.json(diagnostic)
            st.caption('HTTP 500が繰り返される場合：下記の補助インデックスSQLをSupabase SQL Editorで1回実行してください。既存レコードは削除しません。')
            performance_sql='''create index if not exists housing_searches_v1_status_id on public.housing_searches_v1(namespace,status,id);
    create index if not exists housing_searches_v1_acquisition_recent on public.housing_searches_v1(namespace,status,finished_at,id);
    analyze public.housing_searches_v1;'''
            st.code(performance_sql,language='sql')
            st.download_button('DB検索高速化SQLをダウンロード',performance_sql,'supabase_indexes.sql','text/plain',key='database_index_sql')
            st.caption('旧アプリの物件や検索状態を使用しません。旧テーブルのデータは削除しません。')
            st.caption(f'実行中の版：{BUILD}')
            with st.expander('取得・集計の範囲'):
                st.write('SUUMOのみで検索します。マンションは築15年以内、ワンルーム/1K/1DK・1LDK/2K/2DK・2LDK/3K/3DK。一戸建て・その他は築40年以内・50㎡以上。地図の色分けに必要な町丁目はSUUMO掲載所在地を優先し、不足時のみ物件固有地図から照合します。')
                st.caption('住所照合：SUUMOの所在地を市区・町丁目に正規化。町丁目を特定できない場合だけ物件固有のマーカーから国土地理院の逆ジオコーディングを利用します。番地を作りません。')
                st.write('データベースへ保存する募集項目は家賃・間取り・種別・町丁目（既存の詳細住所は保持）・取得日時です。UTCで保存し画面はJST表示します。管理費・面積・築年数・画像・緯度経度は募集データに保存しません。')
                st.write('募集情報は既存SupabaseのJSON領域を利用し、追加SQLは不要です。過去のschema 3〜7は書き換えず、集計時に町丁目へ正規化。新規schema 8のデータと同じ区域で表示します。')
                st.write('SUUMOの詳細ページ「所在地」から町丁目を確認します。所在地が不十分な場合だけ、物件固有地図から町丁目を照合します。番地や建物座標を推測して保存しません。')
                st.write('地図範囲内の居住地名タイルと100m間隔の地点・範囲の端から地名・丁目を判定します。候補数・物件数・ページ数による打ち切りは行いません。通信失敗やページ送りの異常は未完了として表示します。掲載サイト側の非公開情報・取得制限や、地名データの欠落は取得できません。')
                st.write('地図は町丁目別の平均家賃を着色します。町丁目のみ判明した物件に仮の建物座標は付けません。過去の保存内容は維持し、地図用の集計だけを更新します。')
                st.write('検索と保存はサーバーのバックグラウンドで実行し、画面は定期的に進捗を更新します。サーバーの休止・再起動を越えて実行することはできません。募集終了物件の自動削除は行いません。')
                st.caption('地名取得の代替経路：Geolonia Japanese Addresses v2（デジタル庁アドレス・ベース・レジストリ由来、CC BY 4.0）。町代表点を使って近隣自治体の全町丁目を検索します。地名データの欠落や境界の完全な網羅は保証できません。')
                st.link_button('町丁目データの出典・ライセンス','https://github.com/geolonia/japanese-addresses-v2')


def _v209_log_tools(state):
    st.subheader('ログ出力')
    st.button('ログ表示を更新',key='settings_refresh_logs_v209')
    _v208_landmark_log_panel(state)
    if state.get('_v217_suumo_audit'):
        with st.expander('SUUMO町名検索のログ'):
            diagnostic_downloads(state['_v217_suumo_audit'].records(),'suumo_town_v217')
    with st.expander('手動検索の作業ログ'):
        if active_job() is None:st.caption('手動検索の作業ログはまだありません。')
        else:background_status()
    with st.expander('自動収集のログ・エラーCSV'):
        _v209_automatic_log_tools()
    with st.expander('保存済みのログ・検索履歴'):
        _v209_saved_log_tools(state)
    render_memory_diagnostic_v124()
    if state.get('v147_live_test_job'):
        with st.expander('実URL検証の結果・CSV'):
            v147_replay_status()


def main():
    st.set_page_config(page_title='住まいコンパス｜新しい住まいを探す',page_icon='🏡',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(CSS,unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><span class="badge">SUMAI COMPASS · {BUILD}</span><h1>次の住まいを、地図から。</h1><p>掲載物件を確認し、家賃と暮らしやすさを比べます。<br>間取りと家賃帯を、取得できた情報で比較します。</p></div>',unsafe_allow_html=True)
    state=st.session_state
    state.setdefault('new_units',[]);state.setdefault('new_search',None);state.setdefault('new_saved_keys',[])
    state.setdefault('new_map_loaded',False);state.setdefault('address_point_cache',{})
    state.setdefault('layout_display_group','group1')
    if not state.get('_v202_home_initialized'):
        state['_v202_home_initialized']=True
        _v202_reset_home(state)
    commute_storage_ready=v170_commute_autosave_sync()
    restore_manual_search()
    restore_automatic_collection()
    request=state.pop('new_request',None)
    if request is not None:
        try: start_search(request)
        except AppError as exc: state.new_search={'status':'failed','summary':{'issues':[str(exc)],'saved':0,'confirmed':0}}
    preferences=state.get('new_preferences',{})
    try:state.current_storage_key=manual_registry_key(Database())
    except Exception:state.current_storage_key=None
    # Choropleth display does not auto-restore thousands of old per-listing points.
    # The Saved Data tab can still load them explicitly for detailed export.
    tabs=st.tabs(['住まいを探す','保存データ','通勤','設定'])
    with tabs[0]:
        st.subheader('地図を動かして、探す地域を表示してください')
        selected_group=state.get('layout_display_group','group1')
        bounds=state.get('new_bounds')
        center=bounds_center(bounds) if bounds else state.get('new_view_center',DEFAULT_CENTER)
        radius=bounds_radius(bounds) if bounds else 1500
        interactive_town_choropleth(selected_group)
        town_location_search_panel_v184()
        # Only the map is a fragment; the search is an ordinary form.
        background_progress()
        # Initial component defaults are not real viewport bounds. Only the browser callback makes the search ready.
        bounds=state.get('new_bounds')
        if bounds:
            center=bounds_center(bounds);radius=bounds_radius(bounds)
            south,west,north,east=bounds
            st.caption(f'検索する表示範囲：緯度 {south:.5f}〜{north:.5f}／経度 {west:.5f}〜{east:.5f}')
        else: st.info('地図の表示範囲を受信しています。表示後に地図を少し動かしてください。対象は東京と周辺地域です。')
        saved_provider_defaults=preferences.get('new_providers',['SUUMO'])
        provider_defaults=[PROVIDER_DISPLAY.get(canonical_provider(x),x) for x in saved_provider_defaults]
        provider_defaults=[x for x in dict.fromkeys(provider_defaults) if x in PROVIDER_OPTIONS] or ['SUUMO']
        if 'new_providers' in state:
            current_provider_state=[PROVIDER_DISPLAY.get(canonical_provider(x),x) for x in list(state.get('new_providers',[]))]
            current_provider_state=[x for x in dict.fromkeys(current_provider_state) if x in PROVIDER_OPTIONS]
            if not current_provider_state:current_provider_state=['SUUMO']
            if list(state.get('new_providers',[]))!=current_provider_state:state.new_providers=current_provider_state
        providers=st.multiselect('物件の取得元（SUUMOのみ）',list(PROVIDER_OPTIONS),default=provider_defaults,key='new_providers',disabled=True)
        st.caption('現在はSUUMOのみを取得元に使用します。')
        scope=st.radio('検索方法',['地図の表示範囲','区・町名を選択'],horizontal=True,key='manual_search_scope')
        searching=automatic_busy() or bool(active_job() and not active_job().snapshot()['finished'])
        if scope=='区・町名を選択':
            ward_code=st.selectbox('検索する区',list(TOKYO_WARDS),index=list(TOKYO_WARDS).index(state.get('manual_selected_ward','13116')),format_func=lambda code:TOKYO_WARDS[code],key='manual_ward',disabled=searching)
            state.manual_selected_ward=ward_code
            ward_town_selector(ward_code,'manual',disabled=searching)
        st.button('表示中の地名から全件検索・保存' if scope=='地図の表示範囲' else '選択した区・町名を検索・保存',type='primary',use_container_width=True,
                  on_click=remember_search,key='new_start',disabled=searching or (scope=='地図の表示範囲' and (not bounds or not providers)))
        result=state.new_search
        if result:
            summary=result['summary'];saved=summary.get('saved',0);confirmed=summary.get('confirmed',0)
            if result.get('conditions',{}).get('mode')=='published_map_position_repair':st.info(f"位置の再確認：{summary.get('position_attempted',0)}件｜位置改善 {summary.get('position_updated',0)}件｜未確認 {summary.get('position_pending',0)}件｜全募集を保持")
            processed=int(summary.get('processed',0));errors=int(summary.get('errors',0));skipped=int(summary.get('already_acquired',0))
            metrics=f'処理 {processed}件｜保存 {saved}件｜未保存 {errors}件｜スキップ {skipped}件'
            if result['status']=='completed': st.success('検索完了｜'+metrics)
            elif result['status']=='cancelled': st.info('検索中断｜'+metrics)
            elif result['status']=='partial': st.error('検索終了｜'+metrics)
            else: st.error('検索失敗｜'+metrics)
            for issue in summary.get('issues',[])[:8]: st.write('・'+issue)
            if result.get('conditions',{}).get('regions'):
                with st.expander('今回検索した地名・丁目'):
                    for label in result['conditions']['regions']: st.write(label)
        st.caption('町丁目別の集計対象は保存済みの募集データです。件数と平均家賃は区・町丁目単位で集計します。')
        # Detailed 23-ward coverage and aggregation status are low-priority UI.
        town_choropleth_status()
    with tabs[1]:
        st.caption('物件と家賃の集計は共通のSupabaseに保存し、全利用者で同じデータを参照します。通勤設定は共有しません。')
        if state.get('saved_map_restore_message'):st.caption(state.saved_map_restore_message)
        automatic_collection_panel()
        st.subheader('保存した物件')
        st.caption('地図は自動集計した町丁目の平均家賃を表示します。ここは個々の物件の確認・CSV出力用です。')
        if st.button('保存物件の一覧を読み込む',key='new_load',use_container_width=True,
                     disabled=bool(active_job() and not active_job().snapshot()['finished'])):
            try:start_saved_load(bounds,state.get('address_point_cache'))
            except AppError as exc:st.error(str(exc))
        saved_load_progress()
        if state.get('new_notice'):st.caption(state.new_notice)
        count=len(state.get('new_units') or [])
        if count:
            st.caption(f'一覧に読み込んだ物件：{count:,}件')
            if st.button('一覧をCSVにする',key='mobile_prepare_units_csv'):
                state['_mobile_units_export']={'key':(id(state.new_units),len(state.new_units)),
                    'bytes':csv_bytes(state.new_units)}
            ready=state.get('_mobile_units_export')
            if ready and ready.get('key')==(id(state.new_units),len(state.new_units)):
                st.download_button('CSVをダウンロード',ready['bytes'],'sumai_rebuild_units.csv',
                                   'text/csv',use_container_width=True,on_click='ignore')
        if st.toggle('その他の操作を表示（CSV取込・再読込）',
                     key='show_saved_advanced_v122',value=False):
            st.caption('通常は操作不要です。保存データの修復・CSV取込に使用します。ログは「設定」→「ログ出力」で確認できます。')
            if st.button('保存物件を全件読み直す',key='new_load_full',use_container_width=False,
                         disabled=bool(active_saved_load() and not active_saved_load().snapshot()['finished'])):
                try:start_saved_load(bounds,state.get('address_point_cache'),force_full=True)
                except AppError as exc:st.error(str(exc))
            st.caption('地図の集計は「住まいを探す」タブの「保存物件を再集計」で開始します。')
            upload=st.file_uploader('このアプリのCSVを追加する',type=['csv'])
            if st.button('CSVの物件をSupabaseへ保存',disabled=upload is None or bool(active_job() and not active_job().snapshot()['finished']),key='new_import'):
                try:
                    rows=read_csv(upload.getvalue());saved=Database().save_units(rows)
                    state.new_map_loaded=False;state.new_units=[];state.new_saved_keys=[]
                    state.new_notice=f'CSVから{len(saved)}件を保存しました。地図反映には「保存物件を再集計」を押してください。';st.rerun()
                except AppError as exc: st.error(str(exc))
            if st.button('画面上の未保存物件を再保存',key='new_retry_save',disabled=not state.new_units or bool(active_job() and not active_job().snapshot()['finished'])):
                try:
                    saved=Database().save_units(state.new_units);state.new_saved_keys=list(set(state.new_saved_keys)|saved)
                    state.new_notice=f'{len(saved)}件の保存を確認しました。';st.rerun()
                except AppError as exc: st.error(str(exc))
    with tabs[2]:
        st.subheader('通勤の目安')
        if not commute_storage_ready:
            st.caption('このブラウザー専用の通勤設定を読み込み・切り替え中です。')
        elif not state.get('_v170_commute_available',True):
            st.warning('ブラウザーが保存を制限しているため、通勤設定の永続保存・利用者切り替えは利用できません。')
        else:
            st.caption('通勤設定はこのブラウザーに自動保存し、クラウドにもバックアップします。更新後も自動復元します。物件データは全員共通です。')
        if state.get('_v174_commute_backup_error'):
            st.warning('通勤設定をクラウドに保存・確認できていません。通信状態またはSupabaseの接続設定を確認してください。')
        if state.pop('_v170_commute_error',None):
            st.warning('利用者プロファイルの操作に失敗しました。')
        profiles=state.get('_v170_commute_profiles',[{'id':'primary','name':'このブラウザー'}])
        profile_ids=[p['id'] for p in profiles]
        current_profile=state.get('_v170_commute_active','primary')
        if state.get('v170_commute_person_selector') not in profile_ids:
            state['v170_commute_person_selector']=current_profile
        st.selectbox('通勤設定の利用者（このブラウザー内）',profile_ids,
                     format_func=lambda ident:next((p['name'] for p in profiles if p['id']==ident),ident),
                     key='v170_commute_person_selector',on_change=v170_commute_profile_changed,
                     disabled=(not commute_storage_ready or not state.get('_v170_commute_available',True)))
        with st.expander('同じ端末を複数人で使う場合：利用者を追加'):
            st.text_input('新しい利用者名',key='v170_person_new_name',max_chars=24,
                          placeholder='例：本人・家族',disabled=not commute_storage_ready)
            if st.button('利用者を追加（通勤先は未設定から開始）',key='v170_create_person',
                         disabled=(not commute_storage_ready or not state.get('_v170_commute_available',True)
                                   or len(profile_ids)>=12)):
                name=str(state.get('v170_person_new_name','')).strip()
                if name:
                    state['_v170_commute_nonce']=state.get('_v170_commute_nonce',0)+1
                    state['_v170_commute_pending']={'action':'create','name':name,
                                                    'request_id':str(state['_v170_commute_nonce'])}
                    st.rerun()
                else:st.warning('利用者名を入力してください。')
        st.caption('通勤先1・2・3は選択中の利用者ごとに自動保存します。保存・復元操作は不要です。')
        # Destination 1: retain the existing state keys and the v159 upgrade path.
        available_lines=commute_lines_v160()
        _v228_station_lookup_ui(state,available_lines,commute_storage_ready)
        if 'commute_line_v160' not in st.session_state:
            previously=st.session_state.get('commute_destination_v159','表示しない')
            matching_line=next((name for name,stations in available_lines.items()
                                if previously in stations),_COMMUTE_V160_NO_LINE)
            st.session_state['commute_line_v160']=matching_line
            st.session_state['commute_station_v160']=(previously if matching_line!=_COMMUTE_V160_NO_LINE
                                                      else _COMMUTE_V160_NO_STATION)
        line_options=[_COMMUTE_V160_NO_LINE,*available_lines]
        if st.session_state.get('commute_line_v160') not in line_options:
            st.session_state['commute_line_v160']=_COMMUTE_V160_NO_LINE
            st.session_state['commute_station_v160']=_COMMUTE_V160_NO_STATION
        st.markdown('**通勤先1**')
        first_line=st.selectbox(
            '① 路線（通勤先1）',line_options,
            key='commute_line_v160',on_change=_commute_line_changed_v160,
            help='鉄道会社・路線名から選べます。',disabled=not commute_storage_ready)
        first_station_options=[_COMMUTE_V160_NO_STATION,*available_lines.get(first_line,())]
        if st.session_state.get('commute_station_v160') not in first_station_options:
            st.session_state['commute_station_v160']=_COMMUTE_V160_NO_STATION
        first_station=st.selectbox(
            '② 駅（通勤先1）',first_station_options,
            key='commute_station_v160',on_change=_v180_commit_commute_change,
            disabled=(first_line==_COMMUTE_V160_NO_LINE or not commute_storage_ready))
        st.session_state['commute_destination_v159']=(
            first_station if first_station!=_COMMUTE_V160_NO_STATION else '表示しない')

        # Destination 2 is optional, independent of destination 1.
        st.markdown('**通勤先2（任意）**')
        if st.session_state.get('commute_line_2_v164') not in line_options:
            st.session_state['commute_line_2_v164']=_COMMUTE_V160_NO_LINE
            st.session_state['commute_station_2_v164']=_COMMUTE_V160_NO_STATION
        second_line=st.selectbox(
            '① 路線（通勤先2）',line_options,
            key='commute_line_2_v164',on_change=_commute_line_changed_v164_second,
            help='2駅目が不要な場合は「路線を選択してください」のままで構いません。',disabled=not commute_storage_ready)
        second_station_options=[_COMMUTE_V160_NO_STATION,*available_lines.get(second_line,())]
        if st.session_state.get('commute_station_2_v164') not in second_station_options:
            st.session_state['commute_station_2_v164']=_COMMUTE_V160_NO_STATION
        second_station=st.selectbox(
            '② 駅（通勤先2）',second_station_options,
            key='commute_station_2_v164',on_change=_v180_commit_commute_change,
            disabled=(second_line==_COMMUTE_V160_NO_LINE or not commute_storage_ready))

        st.markdown('**通勤先3（任意）**')
        if state.get('commute_line_3_v228') not in line_options:
            state['commute_line_3_v228']=_COMMUTE_V160_NO_LINE
            state['commute_station_3_v228']=_COMMUTE_V160_NO_STATION
        third_line=st.selectbox('① 路線（通勤先3）',line_options,key='commute_line_3_v228',on_change=_v228_commute_line_changed_third,disabled=not commute_storage_ready)
        third_options=[_COMMUTE_V160_NO_STATION,*available_lines.get(third_line,())]
        if state.get('commute_station_3_v228') not in third_options:state['commute_station_3_v228']=_COMMUTE_V160_NO_STATION
        st.selectbox('② 駅（通勤先3）',third_options,key='commute_station_3_v228',on_change=_v180_commit_commute_change,disabled=(third_line==_COMMUTE_V160_NO_LINE or not commute_storage_ready))
        chosen=commute_selected_destinations_v164()
        if chosen:
            st.caption('設定中：'+'　'.join(str(i+1)+'. '+name+'駅' for i,name in enumerate(chosen)))
        st.caption('地図には「①35分（参考136%）-2回」の形式で表示します。混雑率は国土交通省2025年度の路線代表最混雑区間から計算した参考値です。')
        st.caption('設定後、「住まいを探す」タブの地図に反映します。乗換回数最少を優先し、同数の場合に所要時間最短の経路を選びます（アプリ内路線データによる概算）。'
                   '現行時刻表・待ち時間・改札内移動・職場までの徒歩時間は含みません。')
        options=physical_units([r for r in state.new_units if has_point(r) and r.get('coordinate_precision')!='town'])
        if options:
            i=st.selectbox('出発する物件',range(len(options)),format_func=lambda i:options[i]['title'])
            destination=st.selectbox('勤務先の最寄り駅',list(STATIONS),key='new_destination')
            walk=st.number_input('勤務先までの徒歩（分）',min_value=0,max_value=60,value=8)
            r=options[i];nearest=min(STATIONS,key=lambda s:meters((r['latitude'],r['longitude']),STATIONS[s]))
            route=commute(nearest,destination)
            home=math.ceil(meters((r['latitude'],r['longitude']),STATIONS[nearest])*1.25/80)
            st.metric('ドアから勤務先までの概算',f"約{math.ceil(home+route['minutes']+walk+(3 if route['path'] else 0))}分")
            st.write(f"登録駅で最も近い{nearest}駅まで徒歩約{home}分｜乗り換え{route['transfers']}回")
            for a,b,line in route['path']: st.caption(f'{a} → {b}（{line}）')
            st.link_button('実際の経路・時刻をGoogleマップで確認','https://www.google.com/maps/dir/?'+urlencode({'api':1,'origin':f"{r['latitude']},{r['longitude']}",'destination':f'{STATIONS[destination][0]},{STATIONS[destination][1]}','travelmode':'transit'}))
            used=list(dict.fromkeys(line for _,_,line in route['path']))
            if used:
                st.dataframe([{'利用路線':line,'混雑率':str(CROWD[line][0])+'%','公表区間':CROWD[line][1],'調査時間帯':CROWD[line][2]} for line in used],hide_index=True)
                st.caption('国土交通省2025年度の代表区間・最混雑1時間の公表値。選択した経路・方向・時刻の混雑予測ではありません。')
                st.link_button('混雑率の原資料',CROWD_SOURCE)
            st.caption('乗換回数最少を優先（同数なら最短時間）。登録41駅・11路線による概算。時刻表・直通運転・道路の徒歩経路は使用していません。徒歩は直線距離×1.25、列車は距離から推定、乗り換えは5分です。')
        else: st.info('物件を検索するか、保存物件を読み込むと通勤を比較できます。')
    with tabs[3]:
        st.subheader('設定')
        state.setdefault('_v209_settings_page','logs')
        logs_button,initial_button=st.columns(2)
        with logs_button:
            if st.button('ログ出力',key='settings_logs_v209',use_container_width=True,
                         type='primary' if state['_v209_settings_page']=='logs' else 'secondary'):
                state['_v209_settings_page']='logs'
        with initial_button:
            if st.button('初期設定',key='settings_initial_v209',use_container_width=True,
                         type='primary' if state['_v209_settings_page']=='initial' else 'secondary'):
                state['_v209_settings_page']='initial'
        if state['_v209_settings_page']=='initial':_v209_initial_settings(state)
        else:_v209_log_tools(state)


if __name__=='__main__':
    main()
