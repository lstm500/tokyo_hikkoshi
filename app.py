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
import traceback
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, quote

from branca.element import MacroElement, Template
import folium
import requests
import streamlit as st
from streamlit_folium import st_folium
from bs4 import BeautifulSoup

BUILD = "REBUILD-01-v96"

# ============================================================================
# NON-NEGOTIABLE SUUMO ADDRESS POLICY -- DO NOT DELETE OR WEAKEN
# ============================================================================
# 1) SUUMO listing/list/detail-page textual address MUST NOT be used to create,
#    geocode, complete, correct, compare, or validate the saved property address.
# 2) Property position MUST come from that listing's own published map page.
# 3) Detailed address inference MUST start from the map-derived coordinate.
# 4) Search-scope municipality/town names may be used only to verify that a map
#    point belongs to the requested search scope; they are not address input.
# 5) A saved acquisition ID whose linked saved address is only town/chome-level
#    MUST NOT suppress re-fetch.  It remains a re-acquisition target.
# 6) Missing room-marker/map-image metadata, GSI residential-tile failure, or a
#    first-pass detailed-address miss MUST NOT immediately reject the listing.
#    The app must continue through the property-specific kankyo page, published
#    Google-map center hints, and then independent building-identity corroboration.
# 7) Independent corroboration may use building name / age / layout / floor area,
#    but MUST NOT use the SUUMO textual address as a query, hint, or validator.
# 8) In addition to the existing mansion searches, SUUMO must also search
#    detached houses / rental houses with age <= 40 years and area >= 50 sqm.
# These rules are part of the application specification. Future edits must keep them.
SUUMO_TEXT_ADDRESS_FORBIDDEN = True
SUUMO_MAP_ONLY_ADDRESS_POLICY = 'map_coordinate_plus_identity_corroboration_v2'

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
SEARCH_TASK_WORKERS = 6
DETAIL_WORKERS = 6
DETAIL_PAGE_WORKERS = 4
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
UNIT_TABLE = "housing_units_v1"
SEARCH_TABLE = "housing_searches_v1"
PLACE_TABLE = "housing_places_v1"
ADDRESS_POINT_KIND = "saved_address_point_v1"
GROUPS = {"1K・1L・1DK": ("1K", "1L", "1DK"), "1LDK・2DK": ("1LDK", "2DK"),
          "2LDK・3DK": ("2LDK", "3DK"), "3LDK": ("3LDK",)}
LAYOUTS = tuple(x for group in GROUPS.values() for x in group)
SUUMO_LAYOUT_GROUPS = (("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
SUUMO_LAYOUTS = tuple(x for group in SUUMO_LAYOUT_GROUPS for x in group)
SUUMO_LAYOUT_CODES = {"1LDK":"04","2K":"05","2DK":"06","2LDK":"07","3K":"08","3DK":"09"}
HOMES_LAYOUT_GROUPS = (("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
DISPLAY_LAYOUT_GROUPS = {"group1": ("1LDK", "2K", "2DK"), "group2": ("2LDK", "3K", "3DK")}
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
BANDS = (100000,125000,150000,175000,200000,225000,250000,275000,300000,350000,400000)
# Perceptual rent scale. The five bands up to 200,000 yen deliberately use clearly
# separated hues (navy -> blue -> cyan -> green -> yellow). Above 200,000 yen the
# scale stays in a warm orange-to-burgundy family so the lower-price differences remain
# immediately readable on the grayscale base map.
COLORS = ("#17357A","#0067D9","#00A9D6","#00A870","#E8B900","#F39A1F",
          "#F06B24","#E94A35","#D9342B","#BC2733","#941D3B","#65102F")
RENT_BAND_LABELS = (f'{BANDS[0]/10000:g}万円以下',) + tuple(
    f'{lower/10000:g}〜{upper/10000:g}万円' for lower,upper in zip(BANDS,BANDS[1:])
) + (f'{BANDS[-1]/10000:g}万円超',)
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
[data-testid="stExpander"] details{background:#fff!important;color:#203f39!important}
[data-baseweb="select"] svg{fill:#203f39!important}
[data-testid="stFileUploader"] section{background:#fff!important;color:#203f39!important}
.unit{background:#fff;border-radius:16px;padding:18px;margin-bottom:12px;border:1px solid #e0e4da}
.unit h3{font-size:18px;margin:0 0 8px}.unit a{color:#286b5d}
@media(max-width:600px){.hero{padding:18px}.hero h1{font-size:26px}.block-container{padding-top:1rem}}
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
    return (re.sub(r'^(\d+)S(LDK|DK|K)$',r'\1\2',match[1]) if rough else match[1]) if match else '1R' if value=='ワンルーム' else None


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
    # Do not force a re-fetch for older non-chome notations unless they are empty;
    # the explicit user requirement here is to re-fetch town/chome-only rows.
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
    # SUUMO/HOMES must never manufacture a property position from the search town or a textual address.
    # Their saved address must come from a provider map location (SUUMO marker / HOMES image pin).
    if provider in ('HOME’S','HOMES','SUUMO') and not precise:
        return None
    if not precise:
        okay=isinstance(point,(list,tuple)) and len(point)==2 and in_rectangle(point,bounds)
        location=dict(latitude=point[0] if okay else None,longitude=point[1] if okay else None,
                      location_method='町丁目の検索地点（建物位置未確認）' if okay else '地域のみ確認・地図位置未確認',
                      map_address=region.get('label','') if region else '',address_match='町丁目単位')
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載募集'),address=str(address or ''),layout=layout,
             rent=rent if rent and rent>0 else None,fees=fees,area=area,floor=floor,structure=structure,age=age,built_ym=built_ym,
             provider=provider,listing_url=url,fetched_at=utc_now(),coordinate_precision=location.pop('coordinate_precision','building') if precise else 'town',
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
    return {'proxies':routes}


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
        if len(points)>=12000 and normal(address) not in points:points.pop(next(iter(points)))
        points[normal(address)]=(time.monotonic(),(float(lat),float(lng),str(title)))


def cached_saved_position(memory,address):
    if not memory:return None
    with memory['lock']:
        entry=memory['points'].get(normal(address))
        return entry[1] if entry and time.monotonic()-entry[0]<86400 else None


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
        if not okay or not self.key: raise AppError('「初期設定」の接続情報をStreamlit Secretsに設定してください。')
        if not re.fullmatch(r'[\w-]{1,80}',self.namespace,flags=re.ASCII): raise AppError('保存領域名の形式を確認してください。')
        self.headers={'apikey':self.key,'Content-Type':'application/json','Accept':'application/json'}
        if not self.key.startswith('sb_secret_'):
            try:
                parts=self.key.split('.')
                payload=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
                if len(parts)!=3 or payload.get('role')!='service_role': raise ValueError()
            except Exception: raise AppError('Secret keyまたはservice_roleキーを設定してください。') from None
            self.headers['Authorization']='Bearer '+self.key
        self.session=requests.Session();self.address_points=saved_position_memory()
    def call(self,method,table,params=None,data=None,prefer=None):
        if table not in (UNIT_TABLE,SEARCH_TABLE,PLACE_TABLE): raise AppError('保存先が不正です。')
        headers=dict(self.headers)
        if prefer: headers['Prefer']=prefer
        try:
            response=self.session.request(method,self.url+'/rest/v1/'+table,params=params,json=data,
                                          headers=headers,timeout=(4,15),allow_redirects=False)
        except requests.RequestException: raise AppError('Supabaseに接続できません。検索結果は画面に保持されます。') from None
        if getattr(self,'audit',None) and not getattr(self,'_saving_audit',False):
            self.audit.add('database','db_response','INFO' if 200<=response.status_code<300 else 'ERROR',{'method':method,'table':table,'status':response.status_code,'bytes':len(response.content)})
        if response.status_code in (401,403): raise AppError('SupabaseのSecret key・権限を確認してください。')
        if response.status_code==404: raise AppError('新しい保存テーブルがありません。「初期設定」のSQLを実行してください。')
        if not 200<=response.status_code<300: raise AppError(f'Supabase処理失敗（HTTP {response.status_code}）。「初期設定」のSQL・接続先を確認してください。')
        if not response.content.strip(): return None
        try: return response.json()
        except ValueError: raise AppError('Supabaseからの応答を確認できません。') from None
    def check(self):
        for table,select in ((UNIT_TABLE,'key,latitude,longitude,layout,rent,fees,area,age,structure'),
                             (SEARCH_TABLE,'id,status,conditions,summary'), (PLACE_TABLE,'key,title,kind,latitude,longitude')):
            rows=self.call('GET',table,{'namespace':'eq.'+self.namespace,'select':select,'limit':1})
            if not isinstance(rows,list): raise AppError('保存テーブルの応答形式が不正です。')
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
            unique[property_id]={'namespace':self.namespace,'id':'acquired.'+hashlib.sha256(property_id.encode()).hexdigest(),
                'status':'rental_acquisition','started_at':stamp,'finished_at':stamp,
                'conditions':{'provider':'SUUMO','property_id':property_id,'schema':2},
                'summary':{'last_success_at':stamp,'listing_key':compact_listing_key(row)}}
        return list(unique.values())

    def save_units(self,rows):
        address_points={}
        for r in rows:
            address=normal(r.get('address') or r.get('inferred_address')) if isinstance(r,dict) else ''
            if address and isinstance(r,dict) and has_point(r) and r.get('coordinate_precision') not in ('town','unknown'):
                address_points[address]=(float(r['latitude']),float(r['longitude']),normal(r.get('map_address') or address))
        unique={}
        for r in rows:
            compact=compact_saved_listing(r)
            if compact:
                key=compact_listing_key(compact);unique[key]=newest_observation(unique.get(key),compact)
        saved=set();items=list(unique.items());self.last_save_stats={'new':0,'updated':0,'failed':0}
        if not hasattr(self,'job_new_keys'):self.job_new_keys=set()
        if not hasattr(self,'covered_acquisitions'):self.covered_acquisitions={}
        for offset in range(0,len(items),200):
            batch=items[offset:offset+200]
            unknown=[key for key,compact in batch if not compact.get('fetched_at')]
            if unknown:
                previous=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'status':'eq.rental_listing',
                    'select':'id,summary','id':'in.('+','.join('listing.'+key for key in unknown)+')','limit':200})
                if not isinstance(previous,list):raise AppError('以前の取得日時を確認できません。')
                stamps={str(r.get('id','')).removeprefix('listing.'):((r.get('summary') or {}).get('listing') or {}).get('fetched_at') for r in previous}
                for key,compact in batch:
                    if not compact.get('fetched_at') and stamps.get(key):compact['fetched_at']=stamps[key]
            payload=[dict(namespace=self.namespace,id='listing.'+key,status='rental_listing',started_at=utc_now(),finished_at=None,
                conditions={'record_type':'rental_listing','schema':7,'fields':['property_id','rent','layout','address','dwelling_type','fetched_at'],
                            'address_origin':'suumo_room_marker_to_gsi_residential_address;homes_map_image'},summary={'listing':compact}) for key,compact in batch]
            result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},payload,'resolution=ignore-duplicates,return=representation')
            if not isinstance(result,list):raise AppError('Supabaseから新規保存の確認が得られません。')
            inserted={str(r.get('id','')).removeprefix('listing.') for r in result if isinstance(r,dict) and r.get('id')}
            self.job_new_keys.update(inserted)
            updates=[r for r in payload if r['id'].removeprefix('listing.') not in inserted]
            ledger=self.acquisition_payload([compact for _,compact in batch])
            rest=updates+ledger
            if rest:
                response=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},rest,'resolution=merge-duplicates,return=representation')
                returned={r.get('id') for r in response if isinstance(r,dict)} if isinstance(response,list) else set()
                if not {r['id'] for r in rest}<=returned:raise AppError('募集情報の更新・取得済みIDの保存を確認できません。')
            for row in ledger:self.covered_acquisitions[row['conditions']['property_id']]=(row['finished_at'],row['summary']['listing_key'])
            saved.update(key for key,_ in batch)
            self.last_save_stats['new']+=len(inserted);self.last_save_stats['updated']+=len(updates)
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
        """Load recent SUUMO acquisition IDs, but re-fetch chome-only saved rows.

        Performance rule: this runs in the search worker, never in the Streamlit UI
        thread, and is cached for 15 minutes.  It uses one keyset scan of saved
        listings rather than one query per property.
        """
        cached=getattr(self,'acquisition_cache',None)
        if cached is not None and time.monotonic()-getattr(self,'acquisition_cache_at',0)<900:return cached
        ledger={};listing_key_by_property={};offset=0;cutoff=acquisition_cutoff().isoformat(timespec='seconds');pages=0;raw=0
        while True:
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'status':'eq.rental_acquisition',
                'finished_at':'gte.'+cutoff,'select':'conditions,summary,finished_at','order':'id.asc','limit':1000,'offset':offset})
            if not isinstance(rows,list):raise AppError('過去3か月の取得済み物件IDを確認できません。')
            pages+=1;raw+=len(rows)
            for row in rows:
                conditions=row.get('conditions') or {};summary=row.get('summary') or {}
                property_id=conditions.get('property_id');stamp=summary.get('last_success_at') or row.get('finished_at')
                if not property_id or not re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',str(property_id)):continue
                if not recent_acquisition(stamp):continue
                previous=ledger.get(property_id)
                if previous is None or str(stamp)>str(previous):
                    ledger[property_id]=stamp;listing_key_by_property[property_id]=normal(summary.get('listing_key'))
            if len(rows)<1000:break
            offset+=len(rows)

        # Only quality-check IDs that actually have a linked listing key.  One scan
        # builds key->address for all current saved listings and stays off the UI thread.
        wanted={k for k in listing_key_by_property.values() if k};addresses={};addresses_by_property={};last_id='';listing_pages=0
        # Scan saved listings once.  This also covers old acquisition-ledger rows that
        # predate listing_key: property_id in the saved listing is used as the join key.
        while True:
            params={'namespace':'eq.'+self.namespace,'status':'eq.rental_listing','select':'id,summary','order':'id.asc','limit':1000}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('取得済み物件の住所品質を確認できません。')
            listing_pages+=1
            if not rows:break
            for item in rows:
                rid=str(item.get('id') or '')
                if not rid.startswith('listing.'):continue
                key=rid.removeprefix('listing.')
                listing=((item.get('summary') or {}).get('listing') or {})
                if not isinstance(listing,dict):continue
                address=normal(listing.get('address') or listing.get('inferred_address'))
                if key in wanted:addresses[key]=address
                pid=normal(listing.get('property_id'))
                if re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',pid):addresses_by_property[pid]=address
            new_last=str(rows[-1].get('id') or '')
            if not new_last or new_last==last_id:break
            last_id=new_last
            if len(rows)<1000:break

        out={};requeued_incomplete=0;requeued_missing=0
        for property_id,stamp in ledger.items():
            key=listing_key_by_property.get(property_id)
            address=addresses.get(key) if key else None
            if address is None:address=addresses_by_property.get(property_id)
            # Acquisition ID alone is never enough: no saved listing or a town/chome-
            # only saved address means this property is a re-acquisition target.
            if address is None:requeued_missing+=1;continue
            if saved_address_requires_reacquisition(address):
                requeued_incomplete+=1;continue
            out[property_id]=stamp
        self.last_acquisition_load_stats={'pages':pages,'raw_records':raw,'recent_ids':len(out),'full_listing_scan':bool(wanted),
            'listing_pages':listing_pages,'requeued_incomplete_address':requeued_incomplete,'requeued_missing_listing':requeued_missing}
        self.acquisition_cache=out;self.acquisition_cache_at=time.monotonic()
        return out

    def load_units(self,bounds=None,layouts=None,on_progress=None,cancel_event=None):
        """Load saved rental rows using keyset pagination and report storage-vs-display counts.

        Older app versions stored rows without a provider property_id and keyed them by
        rent/layout/address.  Newer rows are keyed by property_id.  Both records can remain
        in Supabase after migration.  They are not silently described as separate current
        listings: the diagnostic explicitly reports how many legacy rows are shadowed by an
        ID-backed row, while unmatched legacy rows remain visible.
        """
        out=[];last_id=''
        self.last_load_diagnostic={'time':utc_now(),'bounds':list(bounds) if bounds else None,'layouts':list(layouts) if layouts is not None else None,
                                   'pages':[],'excluded':{'invalid':0,'layout':0,'legacy_schema':0},'raw_records':0,'accepted_records':0,
                                   'property_id_records':0,'legacy_records':0,'legacy_shadowed':0,'legacy_unmatched':0,
                                   'storage_schema':7,'geocode_on_load':True,'pagination':'id_keyset'}
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            params={'namespace':'eq.'+self.namespace,'select':'id,summary,conditions','status':'eq.rental_listing','order':'id.asc','limit':1000}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('保存した募集情報を読み取れません。')
            self.last_load_diagnostic['pages'].append({'table':SEARCH_TABLE,'after_id':last_id or None,'returned':len(rows),'server_filters':['namespace','rental_listing']})
            self.last_load_diagnostic['raw_records']+=len(rows)
            if not rows:break
            page_start=len(out)
            for item in rows:
                meta=item.get('conditions') or {}
                if int(meta.get('schema') or 0) not in (4,5,6,7):
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
            if len(rows)<1000:break
        property_rows=[r for r in out if r.get('property_id')]
        legacy_rows=[r for r in out if not r.get('property_id')]
        represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in property_rows}
        shadowed=[r for r in legacy_rows if r['key'] in represented]
        unmatched=[r for r in legacy_rows if r['key'] not in represented]
        display_rows=property_rows+unmatched
        unique=list({r['key']:r for r in display_rows}.values())
        shadowed_storage_ids={r.get('storage_id') for r in shadowed}
        self.last_load_record_audit=[]
        for r in out:
            if r.get('property_id'):classification='物件ID確認済み・地図表示対象'
            elif r.get('storage_id') in shadowed_storage_ids:classification='旧形式IDなし・ID確認済みデータと家賃/間取り/住所一致・二重表示候補'
            else:classification='旧形式IDなし・独立レコード・地図表示対象'
            self.last_load_record_audit.append({'Supabase行ID':r.get('storage_id'),'物件ID':r.get('property_id') or '',
                '家賃':r.get('rent'),'間取り':r.get('layout'),'住所':r.get('address'),'取得日時':r.get('fetched_at') or '',
                '分類':classification})
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
                'status':'eq.rental_listing','started_at':'gte.'+str(since),'order':'id.asc','limit':1000}
            if last_id:params['id']='gt.'+last_id
            rows=self.call('GET',SEARCH_TABLE,params)
            if not isinstance(rows,list):raise AppError('最新の保存物件を再確認できません。')
            if not rows:break
            for item in rows:
                meta=item.get('conditions') or {}
                if int(meta.get('schema') or 0) not in (4,5,6,7):continue
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
            if len(rows)<1000:break
        property_rows=[r for r in out if r.get('property_id')]
        represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in property_rows}
        out=[r for r in out if r.get('property_id') or r['key'] not in represented]
        return list({r['key']:r for r in out}.values())

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
        return saved

    def load_address_points(self,addresses,cancel_event=None):
        """Load persistent address coordinates in pages and return only requested addresses."""
        wanted={saved_address_point_key(a):normal(a) for a in addresses if saved_address_point_key(a)}
        if not wanted:return {}
        out={};offset=0
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            rows=self.call('GET',PLACE_TABLE,{'namespace':'eq.'+self.namespace,'kind':'eq.'+ADDRESS_POINT_KIND,
                'select':'key,title,latitude,longitude','order':'key.asc','limit':1000,'offset':offset})
            if not isinstance(rows,list):raise AppError('住所座標キャッシュを読み取れません。')
            for row in rows:
                key=str(row.get('key') or '')
                address=wanted.get(key)
                if not address:continue
                try:point=(float(row['latitude']),float(row['longitude']),address)
                except (KeyError,ValueError,TypeError):continue
                if has_point({'latitude':point[0],'longitude':point[1]}):out[address]=point
            if len(out)>=len(wanted) or len(rows)<1000:break
            offset+=len(rows)
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
        # HOME'S browser transport is lazy: Chromium is launched only after a genuine
        # server-side 403 from the normal HTTP path.  It is serialized because a
        # single human browser session is the intended navigation model.
        self.homes_browser_lock=threading.RLock();self.homes_browser_driver=None;self.homes_transport_mode='http';self.homes_browser_last_start=0.0;self.homes_browser_error=''
    def configure(self,config):
        self.proxy_routes=list((config or {}).get('proxies',[]))
        trace(self,'transport_config',{'proxy_count':len(self.proxy_routes),'rental_route':'proxy' if self.proxy_routes else 'direct','other_services':'direct'},stage='http_config')
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
        with policy['lock']:
            gate=policy['gates'].setdefault(host,threading.Lock())
            slots=policy['slots'].setdefault(host,threading.BoundedSemaphore(PER_HOST_SLOTS))
        while not slots.acquire(timeout=.1):self.check_cancel()
        try:
            while not gate.acquire(timeout=.1):self.check_cancel()
            try:
                interval=HOMES_BROWSER_MIN_INTERVAL if host=='www.homes.co.jp' else PER_HOST_MIN_INTERVAL;parser=self.robots.get(urlparse(url).scheme+'://'+urlparse(url).netloc)
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
            if route:options['proxies']={'http':route,'https':route}
            referer=getattr(self.session_primed,'references',{}).get((root,index+1))
            if referer:options['headers']={**options.get('headers',{}),'Referer':referer}
            for attempt in range(3):
                try:
                    response=self.route_request(session,method,url,options);status=response.status_code;info=self.response_info(response)
                    trace(self,'transport_attempt',{'url':url,'method':method,'route':'proxy' if route else 'direct','route_number':index+1 if route else None,'attempt':attempt+1,'status':status,'bytes':len(response.content),**info},stage='transport')
                    last=response
                    blocked=status==403 or info['challenge_page']
                    if rental and blocked and attempt==0 and host!='www.homes.co.jp' and u.path not in ('/','/robots.txt'):
                        if self.prime_session(session,root,options,index+1):
                            headers=dict(options.get('headers',{}));headers['Referer']=root+'/';options['headers']=headers
                            trace(self,'session_retry',{'url':url,'route_number':index+1,'change':'same-route public entry cookies and actual Referer'},stage='transport');continue
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
                        if attempt<2:
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
                    if attempt<2:self.pause(2**attempt);continue
                    break
            if len(ordered)>1:trace(self,'proxy_switch',{'url':url,'failed_route_number':index+1,'remaining_routes':len(ordered)-ordered.index(index)-1},'WARNING','transport')
        if isinstance(last,Exception):raise last
        if last is None:
            error=AppError(host+'：取得経路の待機中です（直前の403・429等）。設定した全経路の待機が終わってから再検索してください。');error.diagnostic={'skipped_routes':skipped};raise error
        return last
    def _fetch_uncached(self,url,method='GET',**kwargs):
        allowed=tuple(RENTAL_HOSTS)+('mreversegeocoder.gsi.go.jp','msearch.gsi.go.jp','maps.gsi.go.jp','cyberjapandata.gsi.go.jp','japanese-addresses-v2.geoloniamaps.com','geolonia.github.io','img01.suumo.com','img02.suumo.com','maps.googleapis.com','maps.google.com','html.duckduckgo.com','www.google.com','search.yahoo.co.jp','myhome.nifty.com','www.mansion-review.jp','www.e-room.co','www.housecom.jp','vidax-gotanda.jp')
        u=urlparse(url)
        if u.scheme!='https' or u.hostname not in allowed or u.username or u.password: raise AppError('取得先が不正です。')
        # After one genuine HOME'S 403, use one persistent real Chromium session for
        # the rest of this run instead of repeatedly sending blocked raw HTTP requests.
        if method=='GET' and u.hostname=='www.homes.co.jp' and u.path!='/robots.txt' and self.homes_transport_mode=='browser':
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
                    raise AppError('取得先が別のサイトに移動しました。')
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
            if method=='GET' and u.hostname=='www.homes.co.jp' and u.path!='/robots.txt' and exc.response.status_code==403 and HOMES_BROWSER_FALLBACK:
                try:
                    trace(self,'homes_http403_browser_retry',{'url':url,'parameters':kwargs.get('params') or {},'decision':'real_chromium_once'},'WARNING','transport')
                    return self.homes_browser_fetch(url,kwargs.get('params'))
                except AppError as browser_exc:
                    diagnostic=dict(getattr(browser_exc,'diagnostic',{}) or {});diagnostic.setdefault('status',403);diagnostic['raw_http_status']=403
                    browser_exc.diagnostic=diagnostic;self.homes_browser_error=str(browser_exc)
                    raise browser_exc from None
            message=f'{u.hostname}：HTTP {exc.response.status_code}（公開ページの取得失敗）。'
            if method=='GET' and not kwargs.get('params'):self.failed_detail_urls[url]=message
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
        if method=='GET' and not kwargs.get('params') and '/chintai/' in u.path:self.http_cache[url]=response
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
            with self.cache_lock:self.http_cache[key]=result
            if geo:
                with GEO_HTTP_LOCK:
                    if len(GEO_HTTP_CACHE)>12000:GEO_HTTP_CACHE.clear()
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
        trace(self,'robots_allowed' if permitted else 'http_403',{'url':url,'rule_source':root+'/robots.txt','permitted':permitted},'INFO' if permitted else 'WARNING','robots')
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
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),message=str(exc) if isinstance(exc,AppError) else type(exc).__name__)
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
        if key not in cache and len(cache)>=512:
            oldest=next(iter(cache));cache.pop(oldest,None);times.pop(oldest,None)
        cache[key]=features;times[key]=time.monotonic() if cached_at is None else cached_at


def _jhj_tile(web,x,y,z=18):
    """Fetch GSI residential-address points once per tile. Images are never persisted."""
    cache=getattr(web,'jhj_tile_cache',None)
    if cache is None:cache={};web.jhj_tile_cache=cache
    key=(z,x,y)
    with web.cache_lock:
        at=getattr(web,'jhj_tile_cached_at',{}).get(key,0)
        if key in cache and time.monotonic()-at<(3600 if not cache[key] else 86400):return cache[key]
        cache.pop(key,None)
    with GEO_HTTP_LOCK:empty_at=GEO_EMPTY_ADDRESS_TILES.get(key)
    if empty_at is not None and time.monotonic()-empty_at<3600:
        _cache_jhj_features(web,key,[],empty_at);trace(web,'address_tile_empty',{'tile':list(key),'source':'cached_NoSuchKey_404'},stage='address.tiles');return []
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
                if len(GEO_EMPTY_ADDRESS_TILES)>12000:GEO_EMPTY_ADDRESS_TILES.clear()
                GEO_EMPTY_ADDRESS_TILES[key]=time.monotonic()
            trace(web,'address_tile_empty',{'url':url,'tile':list(key),'status':404,'server_error_code':'NoSuchKey','reason':'この住所タイルのオブジェクトが存在しないため空タイルとして扱う'},stage='address.tiles')
            return []
        trace(web,'address_tile_failed',{'url':url,'exception_type':type(exc).__name__,'reason':str(exc),'diagnostic':getattr(exc,'diagnostic',{})},'WARNING','address.tiles')
        raise
    _cache_jhj_features(web,key,features)
    return features


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
        if len(cache)>=12000:cache.pop(next(iter(cache)))
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
        cached_tiles=[(t,tile_cache[(18,*t)],None) for t in tiles if (18,*t) in tile_cache and now-tile_times.get((18,*t),0)<(3600 if not tile_cache[(18,*t)] else 86400)]
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
    best=min(candidates,key=lambda c:c[0])
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
            trace(web,'image_georef_missing',{'image_host':urlparse(url).hostname,'reason':'地図画像の基準座標・ズームを読み取れない'},'WARNING','image_location');continue
        supported=urlparse(url).hostname in ('maps.googleapis.com','maps.google.com') and 'staticmap' in urlparse(url).path
        size=re.fullmatch(r'(\d+)x(\d+)',params.get('size',[''])[0])
        if not supported or not size:
            trace(web,'image_georef_missing',{'reason':'地図画像の投影方式・元の画像サイズを検証できない'},'WARNING','image_location');continue
        try:
            if not web.permitted(url):continue
            response=web.fetch(url)
            from PIL import Image
            actual=Image.open(io.BytesIO(response.content)).size
            expected=(int(size[1])*int(scale),int(size[2])*int(scale))
            if actual!=expected:
                trace(web,'image_georef_missing',{'reason':'元画像と宣言サイズが一致せず、拡縮・切り抜きの可能性','actual_size':list(actual),'expected_size':list(expected)},'WARNING','image_location');continue
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

def suumo_ward_regions(web,code):
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
    return list(regions.values()),municipalities(web),0


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
        'layout_filters':{layout:('md',code) for layout,code in SUUMO_LAYOUT_CODES.items()},
    }
    cache[key]=result
    trace(web,'suumo_filters_ready',{'city':region.get('city_name',''),'town':wanted,'selection_method':method,
          'mansion':result['mansion'],'age15':result['age15'],'layout_filters':result['layout_filters']},stage='search_conditions')
    return result


def suumo_group_params(prepared,layouts,page=1):
    params=dict(prepared['base_params'])
    params['ts']=SUUMO_BUILDING_TYPE_CODES['マンション']  # 建物の種類：マンション（アパートは除外）
    params['cn']='15'                # 築年数：15年以内
    params['md']=[SUUMO_LAYOUT_CODES[x] for x in layouts]
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


def suumo_detail_fields(soup):
    """Read SUUMO identity/spec fields while deliberately excluding textual address."""
    visible=' '.join(soup.stripped_strings);structured=json_listing_fields(soup)
    # HARD RULE -- DO NOT REMOVE: SUUMO textual address is deliberately not read.
    # Location/address acquisition uses only this listing's published property map,
    # then (only on fallback) independent sources queried by non-address identity.
    address=''
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
    return {'visible':visible,'address':normal(address),'raw_layout':normal(raw_layout),'raw_rent':normal(raw_rent),
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
    for url in bc_links:add(url)
    bc=suumo_bc_id(detail_url)
    if bc:add(f'https://suumo.jp/chintai/bc_{bc}/kankyo/')
    # A detail page can contain a canonical BC detail link but no direct surroundings link.
    for a in soup.select('a[href]'):
        target=urljoin(detail_url,a.get('href','')).split('#')[0]
        u=urlparse(target);m=re.fullmatch(r'/chintai/bc_(\d+)/?',u.path)
        if u.hostname=='suumo.jp' and m:add(f'https://suumo.jp/chintai/bc_{m.group(1)}/kankyo/')
    for url in jnc_links:add(url)
    for url in explicit:add(url)
    u=urlparse(detail_url)
    if '/jnc_' in u.path:add(u._replace(path=u.path.rstrip('/')+'/kankyo/',fragment='').geturl())
    return candidates


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
_EXTERNAL_IDENTITY_HOSTS=('myhome.nifty.com','www.mansion-review.jp','www.e-room.co','www.housecom.jp','vidax-gotanda.jp')


def _normalize_japanese_address(value):
    text=normal(value).translate(_JP_DIGIT_TRANS).replace(' ','').replace('　','')
    text=text.replace('番地','番').replace('番の','番')
    text=re.sub(r'(\d+)番号(\d+)号',r'\1番\2号',text)
    text=re.sub(r'(\d+)番(\d+)号',r'\1-\2',text)
    text=re.sub(r'(\d+)番$',r'\1番',text)
    return text


def _detailed_address(value):
    text=_normalize_japanese_address(value)
    return bool(text and re.search(r'(?:丁目|大字|字).{0,30}\d+(?:番|-|－)\d+(?:号)?$',text))


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


def _cross_source_address_from_identity(web,identity,map_point,munis,region_code=None):
    """Fallback for SUUMO map/GSI failures. Never uses SUUMO textual address."""
    name=normal(identity.get('building_name'))
    if len(name)<3:return None
    query='"'+name+'"'
    ym=_identity_year_month(identity.get('raw_age'))
    if ym:query+=f' {ym[0]}年{ym[1]}月'
    layout=parsed_layout(identity.get('raw_layout'))
    if layout:query+=' '+layout
    search_urls=(
        'https://html.duckduckgo.com/html/?q='+quote(query),
        'https://www.google.com/search?q='+quote(query),
        'https://search.yahoo.co.jp/search?p='+quote(query),
    )
    result_links=[];search_errors=[]
    for search_url in search_urls:
        try:
            reply=web.fetch(search_url);soup=BeautifulSoup(reply.text,'html.parser')
            result_links.extend(x for x in _external_result_links(soup,reply.url) if x not in result_links)
        except Exception as exc:search_errors.append({'url':search_url,'exception_type':type(exc).__name__,'reason':str(exc)})
        if len(result_links)>=8:break
    candidates={}
    for target in result_links[:8]:
        try:
            if not web.permitted(target):continue
            reply=web.fetch(target);soup=BeautifulSoup(reply.text,'html.parser');text=' '.join(soup.stripped_strings)
        except Exception as exc:
            trace(web,'external_identity_page_failed',{'url':target,'exception_type':type(exc).__name__,'reason':str(exc)},'WARNING','address.fallback');continue
        score,evidence=_identity_match_score(text,identity)
        if score<5 or 'building_name' not in evidence:continue
        host=urlparse(target).hostname or ''
        for address in _extract_detailed_addresses(text):
            key=_normalize_japanese_address(address)
            row=candidates.setdefault(key,{'address':key,'hosts':set(),'score':0,'evidence':set(),'urls':[]})
            row['hosts'].add(host);row['score']=max(row['score'],score);row['evidence'].update(evidence);row['urls'].append(target)
    ranked=sorted(candidates.values(),key=lambda r:(len(r['hosts']),r['score']),reverse=True)
    for row in ranked:
        # Two independent hosts are preferred; a single host needs the strongest
        # identity match (name + build month + area/layout) before acceptance.
        if len(row['hosts'])<2 and row['score']<8:continue
        validated=_gsi_validate_external_address(web,row['address'],munis,region_code)
        if not validated:continue
        ref=tuple(validated['reference_point']);distance=meters(map_point,ref) if map_point else None
        if distance is not None and distance>2000:continue
        validated.update(distance_m=distance if distance is not None else 0,
                         external_hosts=sorted(row['hosts']),external_urls=row['urls'][:4],
                         identity_evidence=sorted(row['evidence']))
        trace(web,'external_identity_address_ok',{'building_name':name,'address':validated['address'],'map_point':list(map_point) if map_point else None,'address_point':validated['reference_point'],'distance_m':round(distance,1) if distance is not None else None,'hosts':validated['external_hosts'],'identity_evidence':validated['identity_evidence']},stage='address.fallback')
        return validated
    trace(web,'external_identity_address_unresolved',{'building_name':name,'search_errors':search_errors,'result_links':result_links[:8],'candidate_count':len(candidates),'reason':'独立ソースで詳細住所を十分に照合できない'},'WARNING','address.fallback')
    return None


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
    inferred=map_point_to_residential_address(web,(lat,lng),munis,expected_code=region_code,expected_towns=strict_towns)
    external=None
    if not inferred:
        external=_cross_source_address_from_identity(web,identity,(lat,lng),munis,region_code)
        inferred=external
    if not inferred:return None
    address=inferred['address']
    if not _detailed_address(address):return None
    external_used=bool(external)
    location={'latitude':float(inferred.get('reference_point',[lat,lng])[0]) if external_used else lat,
              'longitude':float(inferred.get('reference_point',[lat,lng])[1]) if external_used else lng,
              'location_method':('SUUMO物件固有地図→建物名・築年月・間取り/面積を独立ソース照合→GSI住所確認' if external_used else method),
              'map_address':address,'inferred_address':address,
              'address_match':('独立ソースで建物同一性を照合しGSIで番地確認' if external_used else '地図座標から番地・住居番号相当まで推定'),
              'coordinate_precision':'building' if external_used else 'listing_map',
              'position_source_url':source,
              'address_precision':('独立複数ソース照合＋GSI住所検索' if external_used else 'GSI住居表示・街区符号/基礎番号'),
              'address_distance_m':round(float(inferred.get('distance_m') or 0),2),
              'suumo_map_hint_latitude':lat,'suumo_map_hint_longitude':lng,
              'suumo_map_position_kind':position_kind}
    if external_used:
        location['address_identity_sources']=external.get('external_hosts',[])
        location['address_identity_evidence']=external.get('identity_evidence',[])
    with web.cache_lock:
        if len(web.suumo_location_cache)>=12000:web.suumo_location_cache.pop(next(iter(web.suumo_location_cache)))
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
                    page_candidates.append(url)
            emit('candidate',len(page_candidates)+page_skipped)

            scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
            def detail_work(item):
                while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
                try:return detail_work_unbounded(item)
                finally:web.detail_slots.release()
            def detail_work_unbounded(item):
                web.check_cancel()
                index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                emit('message',f'条件{group_number} 詳細を見る {index}/{len(page_candidates)}件');emit('detail',1)
                try:
                    begin_listing(web,'SUUMO',url)
                    if not web.permitted(url):raise AppError('SUUMO詳細ページの自動取得が許可されていません。')
                    detail_reply=web.fetch(url);detail=BeautifulSoup(detail_reply.text,'html.parser');fields=suumo_detail_fields(detail)
                    # The detail-page address is intentionally not used for location/address acquisition.
                    # Only rent/layout/building type/age are read here; address comes from the property map below.
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

                    try:location=suumo_location_from_kankyo_image(web,detail,url,bounds,munis,collection_towns,region.get('code'),fields)
                    except AppError as exc:
                        location=None;trace(web,'location_pending',{'url':url,'message':str(exc),'source':'SUUMO地図・周辺環境の物件マーカー'},'WARNING','location')
                    address=location.get('inferred_address','') if location else ''
                    if not address:
                        reject_listing(web,'SUUMO',url,'map.address_inference','location_failure','物件固有地図・GSI住居表示・独立ソース照合の全経路で詳細住所を確定できない',location,'地図座標または建物同一性の独立照合から番地・住居番号相当まで確認');emit('rejected',1);trace(web,'location_pending',{'url':url,'reason':'物件固有地図→GSI→独立ソース照合まで実施したが詳細住所を確定できないため保存しない','source':'SUUMO地図・周辺環境の物件マーカー'},'WARNING','location');return

                    # Rent, layout, inferred address and acquisition time survive Database.save_units().
                    row=partial_listing('SUUMO',url,'SUUMO掲載募集',address,layout,rent,None,None,region,bounds,location,None,age,ym,'')
                    if not row:reject_listing(web,'SUUMO',url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・詳細住所・物件URL');emit('rejected',1);return
                    row['property_id']=suumo_property_id(url)
                    row['address_source']=location['location_method']
                    emit('unit',row)
                except (AppError,ValueError,TypeError) as exc:
                    trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection');emit('rejected',1);emit('issue','SUUMO詳細確認｜'+str(exc));trace(web,'detail_optional_error',{'url':url,'message':str(exc)},'WARNING','collector')

            for item,_,error in bounded_results(list(enumerate(page_candidates,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
                if error:
                    url=item[1] if isinstance(item,tuple) and len(item)>1 else ''
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
    suumo_house_collect(web,region,bounds,munis,emit)


def suumo_house_collect(web,region,bounds,munis,emit):
    """SUUMO third building-type category: house/other, <=40y, >=50 sqm."""
    prepared=suumo_prepare_region(web,region);filter_params=dict(prepared['base_params']);filter_params.update(SUUMO_HOUSE_PARAMS)
    current_url=prepared['url'];current_params=filter_params;seen_pages=set();seen_urls=set();seen_ids=set();page_number=1;found=0
    collection_towns=[town_base_name(region.get('town',''))];scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    emit('message','SUUMO条件3｜一戸建て・その他（アパート除外）｜築40年以内｜50㎡以上')
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
            rooms=building.select('tr.js-cassette_link') or [x for x in building.select('tr') if x.select_one('a[href*="/chintai/"]')]
            for room in rooms:
                links=[]
                for a in room.select('a[href]'):
                    label=normal(a.get_text(' ',strip=True));url=urljoin('https://suumo.jp',a.get('href','')).split('#')[0]
                    if safe_url(url,'SUUMO') and ('詳細を見る' in label or re.search(r'/chintai/(?:bc_|jnc_)',urlparse(url).path)):
                        links.append((0 if '詳細を見る' in label else 1,url))
                if not links:continue
                url=min(links,key=lambda x:x[0])[1]
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
                    layout=parsed_layout(fields['raw_layout'],rough=False)
                    if not layout:
                        reject_listing(web,'SUUMO',url,'detail.layout','missing_data','一戸建ての間取りを読み取れない',fields['raw_layout'],'間取り');emit('rejected',1);return
                    type_text=normal(fields.get('building_type'))
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
                    try:location=suumo_location_from_kankyo_image(web,detail,url,bounds,munis,collection_towns,region.get('code'),fields)
                    except AppError as exc:
                        location=None;trace(web,'location_pending',{'url':url,'message':str(exc),'source':'SUUMO一戸建て地図・周辺環境'},'WARNING','location')
                    address=location.get('inferred_address','') if location else ''
                    if not address:
                        reject_listing(web,'SUUMO',url,'map.address_inference','location_failure','一戸建ての物件固有地図から詳細住所を確定できない',location,'地図座標または独立照合から番地まで確認');emit('rejected',1);return
                    row=partial_listing('SUUMO',url,'SUUMO一戸建て掲載募集',address,layout,rent,None,area,region,bounds,location,'一戸建て',age,ym,'',dwelling_type='house')
                    if not row:
                        reject_listing(web,'SUUMO',url,'listing.validation','invalid_data','一戸建ての保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'area':area,'address':address},'家賃・間取り・50㎡以上・詳細住所');emit('rejected',1);return
                    row['property_id']=suumo_property_id(url);row['address_source']=location['location_method'];emit('unit',row)
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
    if not found:trace(web,'empty',{'provider':'SUUMO','region':region.get('label'),'group':'一戸建て','reason':'築40年以内・50㎡以上の候補なし'},stage='collector')


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

    with web.provider_lock:web.provider_cache[cache_key]=dict(request)
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
        with web.provider_lock:web.provider_cache[cache_key]=list(links)
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
    if not getattr(db,'collection_checked',False):db.check();db.collection_checked=True
    db.save_search(search);log('保存先確認OK。新しい検索を開始しました。')
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
        if 'SUUMO' in conditions['providers']:web.acquired_ids=db.load_recent_acquisition_ids()
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
        if 'SUUMO' in conditions['providers'] and not getattr(web,'acquired_ids',None):
            web.acquired_ids=db.load_recent_acquisition_ids()
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
            requested_label=normal(conditions.get('auto_task_label'))
            if requested_label:
                matches=[(i,region,provider) for i,(region,provider) in enumerate(jobs)
                         if region['label']+'｜'+PROVIDER_DISPLAY.get(provider,provider)==requested_label]
                if len(matches)==1:
                    index=matches[0][0]
                    conditions['auto_total_tasks']=len(jobs);conditions['auto_task_index']=index
                    jobs=[jobs[index]]
                elif (not matches and provider_fallbacks and
                      requested_label.endswith('｜HOMES')):
                    # The current HOME'S task has become unavailable. Do not use
                    # its old numeric index to run a *different SUUMO town*.
                    conditions['auto_homes_task_skipped_403']=True
                    conditions['auto_total_tasks']=len(jobs)
                    jobs=[]
                else:
                    raise AppError('自動収集の対象町・取得元が一致しません。誤った町を検索しないため停止します：'+requested_label)
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
                    screen['status'].info(f'検索実行中｜処理 {processed_now}件｜保存 {len(saved)}件｜エラー {errors_now}件｜スキップ {skipped}件')
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
                metrics=f'処理 {processed_now}件｜保存 {len(saved)}件｜エラー {errors_now}件｜スキップ {skipped}件'
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
    for offset in range(0,len(remaining),200):
        chunk=remaining[offset:offset+200]
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
    final_errors=max(0,int(detail)-len(saved))
    final_processed=len(saved)+final_errors
    final_candidate=int(skipped)+final_processed
    terminal_observed=rejected+unsaved
    if terminal_observed!=final_errors or int(candidate)!=final_candidate:
        audit.add('accounting','candidate_accounting_mismatch','ERROR',{
            'discovered_candidates':candidate,'detail_started':detail,'saved':len(saved),'skipped':skipped,
            'terminal_rejected':rejected,'unsaved':unsaved,'ui_processed':final_processed,'ui_errors':final_errors,
            'accounted_candidates':final_candidate,'reason':'候補・処理・保存・スキップと内部イベントの件数差を検出'})
    state.live_metrics={'processed':final_processed,'saved':len(saved),'errors':final_errors,'skipped':skipped}
    search['summary'].update(confirmed=len(units),elapsed=round(time.monotonic()-started,3),new_saved=len(saved & getattr(db,'job_new_keys',set())),updated_saved=len(saved-getattr(db,'job_new_keys',set())),processed=final_processed,errors=final_errors,candidate=final_candidate,discovered_candidate=candidate,already_acquired=skipped,rejected=rejected,detail=detail,last_saved_at=getattr(state,'new_last_saved_at',None),saved=len(saved),unsaved=unsaved,acquisition_ids_pending=len(acquisition_pending),issues=issues[-100:],provider_fallbacks=provider_fallbacks,homes_http403_fallback=bool(provider_fallbacks))
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
    screen['status'].info(f'検索終了｜処理 {final_processed}件｜保存 {len(saved)}件｜エラー {final_errors}件｜スキップ {skipped}件')
    audit.add('search','finish','INFO',{'status':search['status'],'summary':search['summary']})
    audit.persist(db,force=True)
    search['summary']['diagnostics']={'search_id':audit.search_id,'events':audit.count,'persisted_events':audit.persisted_events,
                                     'persisted_failures':audit.persisted_failures,'pending_failures':len(audit.pending_failures)}
    search['finished_at']=utc_now()
    try: db.save_search(search)
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
 'unknown':('現時点で理由を特定できない','前後の通信・解析・除外イベントを照合する。再現URLと読取項目を追加してから修正する。原因を断定しない。')}

DIAG_ADVICE.update({'address_tile_empty':('GSIの住所タイルにデータがない','HTTP 404とNoSuchKeyが両方確認された空タイル。取得できた周辺タイルの同一町丁目の住所候補を比較する。その他の通信失敗と区別する。'),'listing_rejected':('物件の除外理由と工程・取得値・期待値','failed_stage/observed/expected/evidenceで条件不一致と読取・住所推定失敗を区別する。'),'listing_failed':('物件の通信・解析処理に失敗','HTTP状態・例外型・直前の工程を確認する。原因未確定を確定扱いしない。')})

def diagnosis_code(message):
    text=str(message)
    if '取得経路の待機中' in text:return 'route_cooldown'
    if 'アクセス検証画面' in text:return 'http_challenge'
    if 'プロキシ接続失敗' in text:return 'proxy_error'
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
        self.lock=threading.RLock();self.count=0;self.offset=0;self.chunk=0;self.last_save=0.;self.persisted_events=0
        self.storage_error=None;self.search_id=hashlib.sha256(os.urandom(32)).hexdigest();self.secrets=tuple(str(v) for v in secrets if v)
        self.search_mode=conditions.get('mode');self.ward_code=conditions.get('ward_code');self.town_codes=list(conditions.get('town_codes') or [])
        self.pending_failures=[];self.persisted_failures=0;self.failure_storage_error=None
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
            if diagnostic_failure_event(event):self.pending_failures.append(event)
    def records(self):
        with self.lock:
            with open(self.path,encoding='utf-8') as f:return [json.loads(line) for line in f if line.strip()]
    def _persist_failures(self,db):
        """Flush failure events on every persist poll, independent of the 30-second normal-log cadence."""
        while True:
            with self.lock:
                batch=list(self.pending_failures[:100])
            if not batch:
                self.failure_storage_error=None;return
            try:
                db._saving_audit=True
                saved=save_diagnostic_failures_compat(db,batch)
            except Exception as exc:
                self.failure_storage_error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                return
            finally:
                db._saving_audit=False
            with self.lock:
                seqs={int(e.get('seq',-1)) for e in batch}
                self.pending_failures=[e for e in self.pending_failures if int(e.get('seq',-1)) not in seqs]
                self.persisted_failures+=saved
    def persist(self,db,force=False):
        # Error/warning events are written on every poll (normally within ~0.35 s during search).
        # The complete verbose log remains chunked every 30 seconds to avoid needless DB traffic.
        self._persist_failures(db)
        if not force and time.monotonic()-self.last_save<30:return
        self.last_save=time.monotonic()
        while True:
            with self.lock:
                with open(self.path,encoding='utf-8') as f:
                    f.seek(self.offset);events=[]
                    for _ in range(200):
                        line=f.readline()
                        if not line:break
                        events.append(json.loads(line))
                    end=f.tell()
            if not events:
                self.storage_error=None;return
            row=dict(id=f'{self.search_id}.log.{self.chunk:08d}',status='diagnostic_log',started_at=events[0]['time'],finished_at=events[-1]['time'],
                     conditions={'search_id':self.search_id,'build':BUILD,'chunk':self.chunk},summary={'events':events})
            try:
                db._saving_audit=True;db.save_search(row)
            except Exception as exc:
                self.storage_error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                self.add('log_storage','save','ERROR',{'message':self.storage_error,'local_download_available':True});return
            finally:db._saving_audit=False
            self.offset=end;self.chunk+=1;self.persisted_events+=len(events)
            if not force:
                self.storage_error=None;return
            # A failure could have been appended while the normal chunk was being saved.
            self._persist_failures(db)


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


def display_pipeline(units,bounds,load=None):
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
        records.append({'key':r.get('key'),'title':r.get('title'),'provider':r.get('provider'),'listing_url':r.get('listing_url'),
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


def display_diagnostic_downloads(report):
    stages=report['stages'];mapping=report['map']
    st.caption(f"地図の〇 {mapping['colored_unique_positions']}個｜読み込んだ募集 {stages['loaded']}件｜現在の範囲 {stages['in_bounds']}件｜座標あり {mapping['markers_total']}件｜位置未確認 {mapping['unconfirmed_positions']}件")
    st.caption(f"地図の〇＝家賃色を付けて地図上で区別できる座標位置。完全に同じ座標へ重なる募集は1個として数えます。家賃帯対象 {mapping['colored_points']}件｜家賃未確認 {mapping['gray_points']}件｜重なり {mapping['overlapping_points']}件。")
    with st.expander('表示が少ない原因・全募集の診断ログ'):
        st.dataframe([{'理由':DISPLAY_REASONS[k],'募集件数':v} for k,v in report['reason_counts'].items()],hide_index=True)
        text=['住まいコンパス 1募集1点の表示診断',json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False,indent=2),'募集ごとの判定：']
        text.extend(json.dumps(r,ensure_ascii=False) for r in report['records'])
        st.download_button('表示原因ログをTXTでダウンロード','\n'.join(text).encode('utf-8-sig'),'sumai_display_log.txt','text/plain',key='display_txt',on_click='ignore')
        st.download_button('表示原因ログをJSONでダウンロード',json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'),'sumai_display_log.json','application/json',key='display_json',on_click='ignore')
        output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(report['records'][0]) if report['records'] else ['key','reason']);writer.writeheader();writer.writerows(report['records'])
        st.download_button('全募集の表示判定をCSVでダウンロード',output.getvalue().encode('utf-8-sig'),'sumai_display_decisions.csv','text/csv',key='display_csv',on_click='ignore')
        for advice in report['improvements']:st.write('・'+advice)


def rent_color(price):
    """Monthly rent plus management fees; each threshold belongs to the lower band."""
    return COLORS[sum(price>cut for cut in BANDS)]


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
    """Keep ward names and major stations readable above dense translucent rent points."""
    _template=Template("""{% macro script(this, kwargs) %}
    var map = {{ this._parent.get_name() }};
    var pane = map.getPane('referenceLabelPane');
    if (pane) pane.style.pointerEvents = 'none';
    var wardLabels = {{ this.ward_payload }};
    var stationLabels = {{ this.station_payload }};
    wardLabels.forEach(function(item) {
        L.marker([item.lat,item.lng], {pane:'referenceLabelPane',interactive:false,
            icon:L.divIcon({className:'',iconSize:null,html:
                '<div style="white-space:nowrap;transform:translate(-50%,-50%);font-size:15px;font-weight:900;letter-spacing:.08em;color:#202020;text-shadow:-2px -2px 0 rgba(255,255,255,.98),2px -2px 0 rgba(255,255,255,.98),-2px 2px 0 rgba(255,255,255,.98),2px 2px 0 rgba(255,255,255,.98),0 0 5px rgba(255,255,255,1);">'+item.name+'</div>'})
        }).addTo(map);
    });
    stationLabels.forEach(function(item) {
        L.circleMarker([item.lat,item.lng],{pane:'referenceLabelPane',radius:3.3,color:'#202020',weight:1.2,fill:true,fillColor:'#ffffff',fillOpacity:.96,interactive:false}).addTo(map);
        L.marker([item.lat,item.lng], {pane:'referenceLabelPane',interactive:false,
            icon:L.divIcon({className:'',iconSize:null,iconAnchor:[0,11],html:
                '<div style="white-space:nowrap;transform:translate(-50%,-100%);padding:1px 4px;border-radius:4px;background:rgba(255,255,255,.86);border:1px solid rgba(40,40,40,.35);font-size:11.5px;font-weight:850;color:#1f1f1f;box-shadow:0 1px 2px rgba(0,0,0,.12);">'+item.name+'駅</div>'})
        }).addTo(map);
    });
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='MapReferenceLabels'
        wards=[{'name':name,'lat':point[0],'lng':point[1]} for name,point in WARD_LABELS.items()]
        stations=[{'name':name,'lat':STATIONS[name][0],'lng':STATIONS[name][1]} for name in MAJOR_STATION_LABELS if name in STATIONS]
        self.ward_payload=json.dumps(wards,ensure_ascii=False,separators=(',',':')).replace('<','\u003c').replace('>','\u003e').replace('&','\u0026')
        self.station_payload=json.dumps(stations,ensure_ascii=False,separators=(',',':')).replace('<','\u003c').replace('>','\u003e').replace('&','\u0026')

def rental_map(rows,center,radius,cells,facilities):
    m=folium.Map(location=center,zoom_start=15,tiles=None,control_scale=True,prefer_canvas=True)
    folium.map.CustomPane('monotoneBase',z_index=200,pointer_events=False).add_to(m)
    MonotoneBase().add_to(m)
    folium.TileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png',
        attr='国土地理院',name='駅名・地名のモノトーン地図',max_zoom=18,pane='monotoneBase').add_to(m)
    folium.map.CustomPane('rentIndividualPane',z_index=410,pointer_events=False).add_to(m)
    # Reference labels live above the rent dots.  Rent dots remain translucent so the
    # underlying roads, station names and ward geography stay legible even in dense areas.
    folium.map.CustomPane('referenceLabelPane',z_index=625,pointer_events=False).add_to(m)
    MapReferenceLabels().add_to(m)
    rental_features(rows,cells,facilities).add_to(m)
    return m


def listing_card(r,persisted):
    price=f"{float(r.get('rent') or 0)/10000:g}万円 / 月" if r.get('rent') else '家賃未確認'
    dwelling='一戸建て' if listing_dwelling_type(r)=='house' else 'マンション'
    return '<div class="unit"><h3>'+html.escape(r.get('address') or '住所未確認')+'</h3><b>'+price+'</b> · '+html.escape(dwelling)+' · '+html.escape(r.get('layout') or '間取り未確認')+'<p>住所は地図からの推定値です。<br>データ取得日時：'+html.escape(acquisition_time_jst(r.get('fetched_at')))+'</p></div>'


class IndividualRentPoints(MacroElement):
    """One Canvas renderer; one noninteractive colored point per acquired listing."""
    _template=Template("""{% macro script(this, kwargs) %}
    var {{ this.get_name() }}_data = {{ this.payload }};
    var {{ this.get_name() }}_map = {{ this._parent._parent.get_name() }};
    var {{ this.get_name() }}_renderer = {{ this.get_name() }}_map._individualRentCanvas;
    if (!{{ this.get_name() }}_renderer) {
        {{ this.get_name() }}_renderer = L.canvas({padding:0.3,pane:'rentIndividualPane'});
        {{ this.get_name() }}_map._individualRentCanvas = {{ this.get_name() }}_renderer;
    }
    {{ this.get_name() }}_data.forEach(function(item) {
        L.circleMarker([item.lat,item.lng],{renderer:{{ this.get_name() }}_renderer,
            radius:5.8,color:'#ffffff',weight:item.approx?1.35:0.75,opacity:0.62,interactive:false,
            dashArray:item.approx?'2,2':null,fill:true,fillColor:item.color,fillOpacity:0.70})
        .addTo({{ this._parent.get_name() }});
    });
    {% endmacro %}""")
    def __init__(self,rows):
        super().__init__();self._name='IndividualRentPoints';self.items=[]
        for r in rows:
            if not has_point(r):continue
            self.items.append({'key':r['key'],'lat':r['latitude'],'lng':r['longitude'],
                'color':rent_color(monthly_price(r)) if r.get('rent') else '#777777',
                'approx':r.get('coordinate_precision') in ('town','address')})
        self.payload=json.dumps(self.items,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def rental_features(rows,cells,facilities):
    m=folium.FeatureGroup(name='1募集1点・家賃帯')
    IndividualRentPoints(rows).add_to(m)
    for f in facilities:
        folium.CircleMarker((f['lat'],f['lng']),radius=6,color='#fff',fill=True,fill_color='#555555',fill_opacity=1,tooltip=html.escape(f['name'])).add_to(m)
    return m


def commute(origin,destination):
    edges={name:[] for name in STATIONS}
    for line,stations in LINES.items():
        for a,b in zip(stations,stations[1:]):
            minutes=meters(STATIONS[a],STATIONS[b])*1.15/1000/40*60
            edges[a].append((b,line,minutes));edges[b].append((a,line,minutes))
    todo=[(0,origin,'',0,())];seen=set()
    while todo:
        minutes,node,last,changes,path=heapq.heappop(todo)
        if (node,last) in seen: continue
        seen.add((node,last))
        if node==destination: return dict(minutes=minutes,transfers=changes,path=path)
        for next_node,line,duration in edges[node]:
            change=int(bool(last) and last!=line)
            heapq.heappush(todo,(minutes+duration+5*change,next_node,line,changes+change,path+((node,next_node,line),)))
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
    return [hydrated_listing(row,cache.get(row.get('address'))) for row in rows],cache


class SavedLoadJob:
    """DB rows become available independently of geocoding; no Streamlit in worker."""
    def __init__(self,db,bounds,cache=None):
        self.db=db;self.bounds=bounds;self.token=hashlib.sha256(os.urandom(32)).hexdigest();self.server_key=manual_registry_key(db)
        self.lock=threading.RLock();self.cancel_event=threading.Event();self.rows={};self.cache={}  # Session points lack timestamps; reuse only the TTL-controlled server cache.
        self.phase='database';self.finished=False;self.error='';self.complete_db=False;self.pages=0;self.total_addresses=0;self.done_addresses=0;self.placed=0;self.persistent_cache_hits=0;self.geocoded_addresses=0;self.diagnostic={};self.started=time.monotonic();self.started_at_utc=utc_now();self.ended=None
        self.catchup_rounds=0;self.catchup_rows=0;self.catchup_changes=0;self.latest_checked_at=None;self.storage_record_audit=[]
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-saved-loader')
    def snapshot(self):
        with self.lock:
            end=self.ended if self.ended is not None else time.monotonic();diag=dict(self.diagnostic or {})
            return {'token':self.token,'phase':self.phase,'finished':self.finished,'error':self.error,'complete_db':self.complete_db,
                'rows':len(self.rows),'pages':self.pages,'addresses':self.total_addresses,'done_addresses':self.done_addresses,'placed':self.placed,
                'unique_positions':unique_map_position_count(self.rows.values()),
                'visible_colored_dots':visible_colored_dot_count(self.rows.values()),
                'raw_records':int(diag.get('raw_records') or 0),'accepted_records':int(diag.get('accepted_records') or 0),
                'property_id_records':int(diag.get('property_id_records') or 0),'legacy_records':int(diag.get('legacy_records') or 0),
                'legacy_shadowed':int(diag.get('legacy_shadowed') or 0),'legacy_unmatched':int(diag.get('legacy_unmatched') or 0),
                'persistent_cache_hits':self.persistent_cache_hits,'geocoded_addresses':self.geocoded_addresses,
                'catchup_rounds':self.catchup_rounds,'catchup_rows':self.catchup_rows,'catchup_changes':self.catchup_changes,
                'latest_checked_at':self.latest_checked_at,'elapsed':round(end-self.started,1),'stopping':self.cancel_event.is_set()}
    def export(self):
        with self.lock:return [dict(r) for r in self.rows.values()],dict(self.cache),dict(self.diagnostic)
    def export_storage_record_audit(self):
        with self.lock:return [dict(r) for r in self.storage_record_audit]
    def request_stop(self):self.cancel_event.set()
    def run(self):
        web=PublicWeb();web.cancel_event=self.cancel_event;web.address_points=getattr(self.db,'address_points',None)
        def page(rows,diag):
            with self.lock:
                for row in rows:
                    point=self.cache.get(row['address']) or cached_saved_position(web.address_points,row['address'])
                    if point:self.cache[row['address']]=point
                    self.rows[row['key']]=hydrated_listing(row,point)
                self.pages+=1;self.placed=sum(has_point(r) for r in self.rows.values());self.diagnostic=diag
        def row_signature(row):
            return (row.get('property_id'),row.get('rent'),row.get('layout'),row.get('address'),listing_dwelling_type(row),row.get('fetched_at'))
        def remove_legacy_duplicates(rows):
            rows=list(rows)
            represented={hashlib.sha256(json.dumps(listing_identity_payload(r),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in rows if r.get('property_id')}
            rows=[r for r in rows if r.get('property_id') or compact_listing_key(r) not in represented]
            return {compact_listing_key(r):r for r in rows if compact_listing_key(r)}
        try:
            stored=self.db.load_units(None,on_progress=page,cancel_event=self.cancel_event)
            with self.lock:
                self.diagnostic=dict(getattr(self.db,'last_load_diagnostic',self.diagnostic) or self.diagnostic)
                self.storage_record_audit=[dict(r) for r in getattr(self.db,'last_load_record_audit',[]) if isinstance(r,dict)]
                known_storage_ids={str(r.get('Supabase行ID') or '') for r in self.storage_record_audit if r.get('Supabase行ID')}
            addresses=list(dict.fromkeys(r['address'] for r in stored));known_addresses=set(addresses);by_address={a:[] for a in addresses}
            # Persistent cache is loaded in one paged DB pass.  This avoids repeating
            # GSI address searches on every click after the first successful placement.
            persistent={}
            try:persistent=self.db.load_address_points(addresses,self.cancel_event)
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
                            for r in new_storage:
                                self.storage_record_audit.append({'Supabase行ID':r.get('storage_id'),'物件ID':r.get('property_id') or '',
                                    '家賃':r.get('rent'),'間取り':r.get('layout'),'住所':r.get('address'),'取得日時':r.get('fetched_at') or '',
                                    '分類':'物件ID確認済み・地図表示対象' if r.get('property_id') else '旧形式IDなし・最新差分'})
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


def start_saved_load(bounds,cache=None,db=None):
    db=db or Database();key=manual_registry_key(db);jobs,lock=saved_load_registry()
    with lock:
        current=jobs.get(key)
        if current and not current.snapshot()['finished']:job=current
        else:
            job=SavedLoadJob(db,bounds,cache);jobs[key]=job;job.thread.start()
    st.session_state.saved_load_token=job.token;st.session_state.saved_load_key=key
    st.session_state.pop('saved_load_export',None)
    st.session_state.pop('saved_record_audit_export',None)
    st.session_state.pop('saved_load_applied_token',None)
    st.session_state.pop('new_notice',None)
    return job


def manual_search_running():
    jobs,lock=job_registry();token=st.session_state.get('new_job_token');server_key=st.session_state.get('manual_server_key')
    with lock:
        job=jobs.get(token)
        if job and not job.finished:return True
        return any(getattr(j,'server_key',None)==server_key and not j.finished for j in jobs.values()) if server_key else False


def apply_saved_load(job):
    if active_saved_load() is not job or manual_search_running():return False
    rows,cache,diag=job.export();state=st.session_state
    state.address_point_cache=cache;state.new_units=rows;state.new_saved_keys=[r['key'] for r in rows]
    state.new_search=None;state.new_map_loaded=True;state.new_load_diagnostic=diag;state.pop('new_job_token',None)
    positioned=sum(has_point(r) for r in rows);positions=unique_map_position_count(rows)
    dots=visible_colored_dot_count(rows)
    state.new_notice=f"地図の〇 {dots}個｜保存物件 {len(rows)}件｜座標あり {positioned}件｜位置未確認 {sum(not has_point(r) for r in rows)}件。"
    return True


@st.fragment(run_every='2s')
def saved_load_progress():
    job=active_saved_load()
    if not job:return
    snap=job.snapshot();searching=manual_search_running()
    applied=st.session_state.get('saved_load_applied_token')==job.token

    # A completed saved-data load must transition to the map automatically.  In v61
    # the worker could already be complete (e.g. 735/735 addresses) while the fragment
    # continued to show an ever-increasing elapsed timer and waited for another button.
    if snap['finished'] and snap['phase']=='complete' and not applied and not searching:
        if apply_saved_load(job):
            st.session_state.saved_load_applied_token=job.token
            st.rerun()

    if snap['phase']=='database':
        st.info(f"保存物件を読み込み中｜{snap['pages']}ページ確認｜経過 {snap['elapsed']}秒")
    elif snap['phase']=='refreshing':
        st.info(f"最新の保存分を確認中｜地図の〇 {snap['visible_colored_dots']}個｜保存物件 {snap['rows']}件｜座標あり {snap['placed']}件｜経過 {snap['elapsed']}秒")
    elif snap['finished'] and snap['phase']=='complete':
        checked=('｜最新確認 '+acquisition_time_jst(snap['latest_checked_at'])) if snap.get('latest_checked_at') else ''
        st.success(f"読み込み完了｜地図の〇 {snap['visible_colored_dots']}個｜保存物件 {snap['rows']}件｜座標あり {snap['placed']}件｜地図準備完了{checked}｜所要 {snap['elapsed']}秒")
    elif snap['phase']=='failed':
        st.error(f"読み込み停止｜確認済み {snap['rows']}物件｜所要 {snap['elapsed']}秒")
    elif snap['phase']=='stopped':
        st.info(f"読み込みを中止しました｜確認済み {snap['rows']}物件｜所要 {snap['elapsed']}秒")
    else:
        st.info(f"地図準備中｜地図の〇 {snap['visible_colored_dots']}個｜保存物件 {snap['rows']}件｜座標あり {snap['placed']}件｜経過 {snap['elapsed']}秒")
    if snap['error']:st.error(snap['error'])
    if snap['phase']=='stopped':st.caption('中止しました。読み込み済みの物件は保持しています。')
    if searching:st.caption('物件検索が終わると読み込み済みデータを地図へ反映できます。')
    elif snap['finished'] and snap['phase']=='complete':st.caption('読み込み終了直前に、処理中にSupabaseへ追加・更新された保存物件を再確認してから地図へ反映しています。収集が続いている場合は、下の読込ボタンをもう一度押すとその時点の最新状態へ更新できます。')
    else:st.caption('地図操作は継続できます。読み込み途中でも下のボタンで現在までの物件を地図へ反映できます。')
    if not applied and st.button('読み込んだ物件を地図へ反映',key='apply_saved_load',disabled=not snap['rows'] or searching):
        if apply_saved_load(job):
            if snap['finished'] and snap['phase']=='complete':st.session_state.saved_load_applied_token=job.token
            st.rerun()
    if not snap['finished'] and st.button('読み込み・地図準備を中止（読み込み済みを保持）',key='stop_saved_load',disabled=snap['stopping']):job.request_stop()
    if st.button('地図表示対象の物件CSVを準備',key='prepare_saved_load_csv',disabled=not snap['rows']):
        rows,_,_=job.export();st.session_state.saved_load_export={'token':job.token,'csv':csv_bytes(rows),'count':len(rows)}
    export=st.session_state.get('saved_load_export')
    if export and export['token']==job.token:
        st.download_button(f"地図表示対象 {export['count']}件のCSV",export['csv'],'sumai_saved_units.csv','text/csv',key='download_saved_load_csv',on_click='ignore')


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
        self.new_units=[];self.new_search=None;self.new_saved_keys=[];self.new_regions=[];self.live_metrics={'processed':0,'saved':0,'errors':0,'skipped':0}

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
            self.audit.add('search',diagnosis_code(str(exc)),'ERROR',{'message':str(exc) if isinstance(exc,AppError) else type(exc).__name__,'traceback':traceback.format_exc()})
            self.state.new_search={'status':'failed','conditions':conditions,'summary':{'issues':[str(exc) if isinstance(exc,AppError) else '検索処理エラー（'+type(exc).__name__+'）'],'saved':len(self.state.new_saved_keys),'confirmed':len(self.state.new_units)}}
        finally:
            try:self.audit.persist(db,force=True)
            except Exception:pass
            with self.lock: self.finished=True
    def request_stop(self):
        self.cancel_event.set()
        with self.lock:self.message='中止要求を受け付けました。新しい取得を止め、通信終了後に取得済みデータを保存・確認して終了します。'
    def snapshot(self):
        with self.lock:
            return dict(progress=self.progress,message=self.message,log=self.text,finished=self.finished,stopping=getattr(self,'cancel_event',threading.Event()).is_set(),
                        updated_at=getattr(self,'updated_at',None),last_saved_at=getattr(self.state,'new_last_saved_at',None),units=list(self.state.new_units),saved=list(self.state.new_saved_keys),
                        live_metrics=dict(getattr(self.state,'live_metrics',{}) or {}),result=self.state.new_search)

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
            if key!=token and value.finished and time.monotonic()-getattr(value,'created',time.monotonic())>7200: jobs.pop(key,None)
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
    m=re.search(r'処理\s*(\d+)件.*?保存\s*(\d+)件.*?エラー\s*(\d+)件.*?スキップ\s*(\d+)件',message)
    if m:return dict(zip(keys,map(int,m.groups())))
    # Compatibility with older running collectors: '詳細' means detail processing
    # started; '除外' is a completed failure.  Only completed outcomes are shown.
    saved_m=re.search(r'(?:保存確認|保存)\s*(\d+)(?:件)?',message);error_m=re.search(r'(?:除外|エラー)\s*(\d+)(?:件)?',message);skip_m=re.search(r'(?:取得済みスキップ|スキップ)\s*(\d+)(?:件)?',message)
    saved=int(saved_m.group(1)) if saved_m else 0;errors=int(error_m.group(1)) if error_m else 0;skipped=int(skip_m.group(1)) if skip_m else 0
    return {'processed':saved+errors,'saved':saved,'errors':errors,'skipped':skipped}


@st.fragment(run_every='2s')
def background_progress():
    """Render live worker progress and buttons inside the fragment-owned container."""
    job=active_job()
    if job is None or job.snapshot()['finished']:
        controller=get_automatic_collection()
        auto=controller.snapshot() if controller else None
        if auto and auto['running']:
            current=auto.get('current',{})
            live=snapshot_count_metrics(current)
            live_text=f"処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜エラー {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件"
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
            st.info(f"検索終了｜処理 {int(summary.get('processed',0))}件｜保存 {int(summary.get('saved',0))}件｜エラー {int(summary.get('errors',0))}件｜スキップ {int(summary.get('already_acquired',0))}件")
            if summary.get('unsaved'):st.error('未保存 '+str(summary['unsaved'])+'件。保存に失敗したデータはサーバーのメモリに保持しています。作業ログを確認してください。')
            if summary.get('acquisition_ids_pending'):st.error('取得済みIDの保存未確認 '+str(summary['acquisition_ids_pending'])+'件。次回は再取得対象になります。')
            if st.button('取得済みデータを地図へ反映',key='refresh_finished_map'):refresh_map()
            return
        live=snapshot_count_metrics(snap)
        live_text=f"処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜エラー {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件"
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


@st.fragment(run_every='10s')
def background_status():
    """Render current-search diagnostics with download buttons always available."""
    job=active_job()
    if job is None:return
    snap=job.snapshot()
    st.caption(f"作業ログ {job.audit.count}イベント｜Supabase保存確認 {job.audit.persisted_events}イベント")
    try:
        cache=st.session_state.get('diagnostic_export_cache')
        cache_key=(job.audit.search_id,int(job.audit.count))
        if not isinstance(cache,dict) or cache.get('key')!=cache_key:
            cache={'key':cache_key,'events':job.audit.records(),'prepared_at':utc_now()}
            st.session_state.diagnostic_export_cache=cache
        diagnostic_downloads(cache.get('events') or [],'current')
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

@st.fragment
def interactive_rental_map(pins,cells,facilities):
    state=st.session_state
    # Only the initial mount receives an explicit center/zoom.  After Leaflet is live,
    # the browser owns its viewport; Python records only the bounds needed for search.
    # This prevents a pan/zoom -> fragment rerun -> setView feedback loop.
    kwargs=dict(key='new_map',height=480,use_container_width=True,returned_objects=['bounds'],
                feature_group_to_add=rental_features(pins,cells,facilities),on_change=capture_map_fragment)
    if not state.get('new_map'):
        kwargs['center']=state.get('new_view_center',DEFAULT_CENTER)
        kwargs['zoom']=state.get('new_view_zoom',15)
    st_folium(rental_map([],DEFAULT_CENTER,1500,[],[]),**kwargs)
    if state.pop('map_initial_bounds_ready',False):
        st.rerun()


AUTO_COLLECTION_ID='automatic.collection.settings'

def automatic_task_plan(settings):
    """Return the exact interleaved SUUMO/HOME'S town/provider task order."""
    if not isinstance(settings,dict):return []
    regions=settings.get('auto_regions') or [];munis=settings.get('auto_munis') or {}
    if not regions:return []
    providers=[canonical_provider(p) for p in (settings.get('providers') or AUTOMATIC_REGION_PROVIDERS)]
    providers=[p for p in dict.fromkeys(providers) if p in AUTOMATIC_REGION_PROVIDERS]
    target_sets=[]
    for provider in providers:
        groups=suumo_area_groups(regions,munis) if provider=='SUUMO' else homes_area_groups(regions,munis)
        target_sets.append((provider,groups))
    plan=[]
    for number in range(max((len(groups) for _,groups in target_sets),default=0)):
        for provider,groups in target_sets:
            if number>=len(groups):continue
            g=groups[number];town=g.get('suumo_town') if provider=='SUUMO' else g.get('homes_town')
            plan.append({'index':len(plan),'provider':provider,
                         'label':(g.get('label') or ('東京都'+str(g.get('city_name') or '')+str(town or g.get('town') or '')))+'｜'+PROVIDER_DISPLAY.get(provider,provider),
                         'town':town or g.get('town') or ''})
    return plan


def reconcile_automatic_task_progress(summary, old_plan, new_plan, just_completed_label=None):
    """Preserve completed provider/town tasks when HOME'S disappears or returns.

    Task indices are positions in an *interleaved provider list*, not stable town
    identities.  Reusing a numeric index after a 403 can run the wrong town;
    resetting it to zero discards successfully completed SUUMO work.  Always
    transfer progress by full, provider-qualified task labels instead.
    """
    old_labels=list(summary.get('task_labels') or [task['label'] for task in old_plan])
    raw_completed=summary.get('cycle_completed_indices')
    if raw_completed is None:
        # Compatibility with older settings that stored only the next index.
        raw_completed=list(range(max(0,int(summary.get('next_task_index',0) or 0))))
    completed_labels={old_labels[int(i)] for i in raw_completed
                      if str(i).isdigit() and 0<=int(i)<len(old_labels)}
    if just_completed_label:
        completed_labels.add(just_completed_label)
    new_labels=[task['label'] for task in new_plan]
    completed=[i for i,label in enumerate(new_labels) if label in completed_labels]
    summary['task_labels']=new_labels
    summary['total_tasks']=len(new_labels)
    summary['cycle_completed_indices']=completed
    if new_labels and len(completed)==len(new_labels):
        if not summary.get('cycle_reset_pending'):
            summary['last_cycle_completed_at']=utc_now()
        summary['cycle_reset_pending']=True
        next_index=0
    else:
        summary.pop('cycle_reset_pending',None)
        completed_set=set(completed)
        next_index=next((i for i in range(len(new_labels)) if i not in completed_set),0)
    summary['next_task_index']=next_index
    summary['current_task_index']=next_index
    summary['current_task_label']=new_labels[next_index] if new_labels else ''
    return next_index


def load_persisted_error_events_fast(db):
    """Load current per-error rows first so a usable CSV becomes available quickly."""
    events=[];seen=set();last_id=''
    while True:
        params={'namespace':'eq.'+db.namespace,'status':'eq.diagnostic_error','select':'id,summary','order':'id.asc','limit':1000}
        if last_id:params['id']='gt.'+last_id
        rows=db.call('GET',SEARCH_TABLE,params)
        if not isinstance(rows,list):raise AppError('保存済み取得エラーを読み取れません。')
        if not rows:break
        for row in rows:
            event=(row.get('summary') or {}).get('event')
            if not isinstance(event,dict) or not diagnostic_failure_event(event):continue
            key=(str(event.get('search_id') or ''),str(event.get('seq') or ''),str(event.get('stage') or ''),str(event.get('code') or ''))
            if key in seen:continue
            seen.add(key);events.append(event)
        new_last=str(rows[-1].get('id') or '')
        if not new_last or new_last==last_id:break
        last_id=new_last
        if len(rows)<1000:break
    events.sort(key=lambda e:(str(e.get('time') or ''),str(e.get('search_id') or ''),int(e.get('seq') or 0)))
    return events


class AutomaticFailureExport:
    """Refresh the unified error CSV in the background; the UI never waits for preparation."""
    def __init__(self,db):
        self.db=db;self.lock=threading.RLock();self.running=False;self.loaded=False;self.error='';self.phase='idle'
        self.rows=[];self.csv=csv_bytes_from_rows([]);self.updated_at=None;self.updated_monotonic=0.;self.thread=None
    def refresh(self,max_age=60):
        with self.lock:
            if self.running:return
            if self.loaded and time.monotonic()-self.updated_monotonic<max_age:return
            self.running=True;self.error='';self.phase='current_errors'
            self.thread=threading.Thread(target=self._run,daemon=True,name='housing-error-export')
            self.thread.start()
    def _run(self):
        try:
            fast_events=load_persisted_error_events_fast(self.db)
            fast_rows=acquisition_failure_rows(fast_events)
            with self.lock:
                self.rows=fast_rows;self.csv=csv_bytes_from_rows(fast_rows);self.updated_at=utc_now()
                self.updated_monotonic=time.monotonic();self.loaded=True;self.error='';self.phase='historical_backfill'
            # Older builds stored errors only inside diagnostic-log chunks. Merge those
            # after the current per-error rows are already downloadable.
            events=load_diagnostic_failures_compat(self.db)
            rows=acquisition_failure_rows(events)
            with self.lock:
                self.rows=rows;self.csv=csv_bytes_from_rows(rows);self.updated_at=utc_now()
                self.updated_monotonic=time.monotonic();self.loaded=True;self.error='';self.phase='complete'
        except Exception as exc:
            with self.lock:
                self.error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                if self.loaded:self.phase='partial'
        finally:
            with self.lock:self.running=False
    def snapshot(self):
        with self.lock:
            return {'running':self.running,'loaded':self.loaded,'error':self.error,'phase':self.phase,'rows':list(self.rows),
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
        self.lock=threading.RLock();self.io_lock=threading.Lock();self.stop_event=threading.Event()
        self.request_starts={};self.shared_web=PublicWeb();self.shared_web.cancel_event=self.stop_event
        self.last_job=None;self.current=None;self.message='検索候補の町名を確認しています';self.progress=0.;self.error=''
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-automatic-collection')
    def ensure_plan(self):
        with self.lock:
            if self.settings.get('auto_regions') and self.settings.get('auto_munis'):
                plan=automatic_task_plan(self.settings)
                if plan:
                    if self.summary.get('task_labels')!=[p['label'] for p in plan]:
                        reconcile_automatic_task_progress(self.summary,[],plan)
                    else:
                        self.summary['total_tasks']=len(plan)
                    return plan
        self.shared_web.cancel_event=self.stop_event
        if hasattr(self.shared_web,'configure'):self.shared_web.configure(getattr(self.db,'web_config',{}))
        regions,munis,_=suumo_ward_regions(self.shared_web,self.settings['ward_code'])
        regions=select_ward_towns(regions,self.settings.get('town_codes',[]))
        if not regions:raise AppError('自動収集する町が見つかりません。')
        with self.lock:
            self.settings['auto_regions']=regions;self.settings['auto_munis']=munis
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
            self.message=f'検索候補 {len(plan)}町を確認しました。'
        self.persist()
        return plan
    def persist(self):
        with self.io_lock:
            with self.lock:
                record={'id':AUTO_COLLECTION_ID,'status':'automatic_collection_settings','started_at':self.settings.get('created_at',utc_now()),
                        'finished_at':None,'conditions':json.loads(json.dumps(self.settings)), 'summary':json.loads(json.dumps(self.summary))}
            self.db.save_search(record)
    def snapshot(self):
        with self.lock:
            job=self.current
            result=dict(settings=dict(self.settings),summary=dict(self.summary),message=self.message,error=self.error,build=getattr(self,'build',None),
                        running=self.thread.is_alive(),stopping=self.stop_event.is_set(),progress=self.progress)
        if job:
            snap=job.snapshot();result.update(message=snap['message'],progress=snap['progress'],current=snap)
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
                with self.lock:
                    if self.stop_event.is_set():break
                    plan=automatic_task_plan(self.settings)
                    if not plan:raise AppError('自動収集する町が見つかりません。')
                    total=len(plan);index=int(self.summary.get('next_task_index',0) or 0)%total
                    if self.summary.pop('cycle_reset_pending',False):
                        self.summary['cycle_completed_indices']=[]
                        self.summary['cycle_number']=int(self.summary.get('cycle_number',1) or 1)+1
                    self.summary['task_labels']=[p['label'] for p in plan];self.summary['total_tasks']=total
                    self.summary['current_task_index']=index;self.summary['current_task_label']=plan[index]['label']
                    conditions={'bounds':[34,138,37,141],'ward_code':self.settings['ward_code'],'providers':list(self.settings['providers']),
                                'mode':'automatic_collection','auto_task_index':index,'auto_task_label':plan[index]['label'],
                                'town_codes':list(self.settings.get('town_codes',[]))}
                    for key in ('auto_regions','auto_munis'):
                        if self.settings.get(key):conditions[key]=self.settings[key]
                    self.summary['last_started_at']=utc_now();self.summary['current_status']='running'
                    self.message='検索中｜'+plan[index]['label']
                    job=SearchJob(self.db,conditions)
                    if hasattr(job,'state'):job.state.request_starts=self.request_starts;job.state.shared_web=self.shared_web
                    self.current=job
                self.persist()
                job.run(self.db,conditions)
                snap=job.snapshot();result=snap.get('result') or {};status=result.get('status','failed');incomplete=bool(result.get('summary',{}).get('incomplete_tasks'))
                with self.lock:
                    self.last_job=job;self.current=None;self.progress=1.;self.summary['last_finished_at']=utc_now();self.summary['last_status']=status
                    self.summary['current_status']='stopped';self.summary['last_task']=conditions.get('auto_task_label',self.summary.get('current_task_label','地域判定'))
                    self.summary['last_summary']=result.get('summary',{})
                    self.summary['saved_observations']=int(self.summary.get('saved_observations',0))+len(snap['saved'])
                    stats=result.get('summary',{})
                    for name,source in (('new_saved_observations','new_saved'),('updated_saved_observations','updated_saved'),('failed_save_observations','unsaved'),('processed_observations','processed'),('error_observations','errors'),('detail_observations','detail'),('skipped_observations','already_acquired'),('rejected_observations','rejected')):
                        self.summary[name]=int(self.summary.get(name,0))+int(stats.get(source,0))
                    self.summary['statistics_started_at']=self.summary.get('statistics_started_at') or self.summary['last_started_at']
                    self.summary['collection_seconds']=float(self.summary.get('collection_seconds',0))+float(stats.get('elapsed',0))
                    self.summary['confirmed_observations']=int(self.summary.get('confirmed_observations',0))+len(snap['units'])
                    if status in ('completed','partial') and not incomplete:
                        total=max(1,int(conditions.get('auto_total_tasks',len(plan)) or len(plan)))
                        index=int(conditions.get('auto_task_index',index) or 0)%total
                        completed={int(x) for x in self.summary.get('cycle_completed_indices',[]) if isinstance(x,(int,float)) or str(x).isdigit()}
                        completed.add(index);self.summary['cycle_completed_indices']=sorted(i for i in completed if 0<=i<total)
                        next_index=(index+1)%total;self.summary['next_task_index']=next_index;self.summary['total_tasks']=total
                        self.summary['completed_runs']=int(self.summary.get('completed_runs',0))+1
                        if next_index==0:
                            self.summary['last_cycle_completed_at']=utc_now();self.summary['cycle_reset_pending']=True
                        for key in ('auto_regions','auto_munis'):
                            if conditions.get(key):self.settings[key]=conditions[key]
                    self.summary.pop('next_run_at',None)
                    if status=='failed' or incomplete:
                        self.settings['enabled']=False;self.stop_event.set()
                        self.error='今回の町の全ページ確認を完了できませんでした。原因を確認して同じ町から再開してください。'
                    self.message='取得に失敗したため自動収集を中止しました。' if status=='failed' or incomplete else '取得・保存処理を中止しました。' if status=='cancelled' else '保存完了。次の町へ進みます。'
                self.persist()
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

@st.cache_resource
def automatic_registry():
    return {},threading.RLock()

def automatic_registry_key(db):
    return hashlib.sha256((db.url+'|'+db.namespace).encode()).hexdigest()

def get_automatic_collection():
    controller=st.session_state.get('automatic_collection_controller')
    if controller:
        registry,lock=automatic_registry()
        with lock:controller=registry.get(automatic_registry_key(controller.db),controller)
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
            if controller and controller.thread.is_alive():
                st.session_state.automatic_collection_controller=controller;return
            rows=db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+db.namespace,'id':'eq.'+AUTO_COLLECTION_ID,'select':'conditions,summary','limit':1})
            if not isinstance(rows,list):raise AppError('自動収集設定を読み取れません。')
            if not rows:return
            settings=rows[0].get('conditions') or {};summary=rows[0].get('summary') or {}
            settings['providers']=['SUUMO']
            settings.pop('homes_disabled_reason',None)
            settings.pop('homes_disabled_at',None)
            validate_automatic_settings(settings);validate_automatic_summary(summary)
            st.session_state.automatic_saved_settings=settings;st.session_state.automatic_saved_summary=summary
            if not settings.get('enabled'):return
            controller=AutomaticCollection(db,settings,summary);registry[key]=controller
            st.session_state.automatic_collection_controller=controller;controller.thread.start()
    except Exception as exc:st.session_state.automatic_settings_error=str(exc) if isinstance(exc,AppError) else '自動収集設定の復元に失敗しました（'+type(exc).__name__+'）'

def validate_automatic_settings(settings):
    if not isinstance(settings,dict):raise AppError('自動収集設定の形式が不正です。')
    if settings.get('ward_code') not in TOKYO_WARDS:raise AppError('東京23区から対象区を選択してください。')
    codes=settings.get('town_codes',[])
    if not isinstance(codes,list) or any(not isinstance(c,str) or not re.fullmatch(re.escape(settings['ward_code'])+r'\d{3}',c) for c in codes):raise AppError('対象町名の設定が不正です。')
    providers=[canonical_provider(p) for p in (settings.get('providers') or [])]
    if providers!=['SUUMO']:raise AppError('SUUMO-only automatic collection requires SUUMO.')

def validate_automatic_summary(summary):
    if not isinstance(summary,dict):raise AppError('自動収集の進捗形式が不正です。')
    for key in ('next_task_index','total_tasks','completed_runs','saved_observations','confirmed_observations'):
        try:
            if key in summary and int(summary[key])<0:raise ValueError()
        except (ValueError,TypeError):raise AppError('自動収集の進捗が不正です：'+key) from None
    if not isinstance(summary.get('last_summary',{}),dict):raise AppError('自動収集の前回結果が不正です。')

def start_automatic_collection(ward_code,interval_minutes=0,town_codes=None):
    settings={'enabled':True,'ward_code':ward_code,'providers':list(AUTOMATIC_REGION_PROVIDERS),'interval_minutes':0,'created_at':utc_now(),'town_codes':sorted(set(town_codes or []))}
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
        same_scope=(prior.get('ward_code')==settings['ward_code'] and
                    sorted(prior.get('town_codes',[]))==settings['town_codes'])
        summary=dict(previous_summary) if same_scope else {}
        for item in ('auto_regions','auto_munis'):
            if same_scope and prior.get(item):settings[item]=prior[item]
        controller=AutomaticCollection(db,settings,summary);controller.persist()
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
    def handoff():
        try:
            controller.request_stop();controller.thread.join()
            latest=controller.snapshot();settings=dict(desired_settings);settings['enabled']=True
            replacement=AutomaticCollection(controller.db,settings,dict(latest.get('summary') or {}));replacement.persist()
            with registry_lock:registry[key]=replacement
            replacement.thread.start()
        finally:
            with pending_lock:pending.discard(key)
    threading.Thread(target=handoff,daemon=True,name='housing-automatic-upgrade').start()
    return True


@st.fragment(run_every='8s')
def automatic_collection_panel():
    st.subheader('アプリ内の自動収集（SUUMO）')
    st.caption('画面を閉じてもサーバー稼働中は収集します。休止・再起動では止まり、次にアプリを開くと保存した設定・町の順番から再開します。')
    controller=get_automatic_collection();snap=controller.snapshot() if controller else None
    if controller:
        try:
            bridged=persist_cached_controller_failures(controller)
            if bridged:st.session_state['_compat_failures_last_saved']=bridged
            st.session_state.pop('_compat_failures_error',None)
        except Exception as exc:
            st.session_state['_compat_failures_error']=str(exc) if isinstance(exc,AppError) else type(exc).__name__
    busy=bool(snap and snap['running'])
    if controller and getattr(controller,'build',None)!=BUILD and busy:
        schedule_automatic_upgrade(controller)
    saved=snap['settings'] if snap else st.session_state.get('automatic_saved_settings',{})
    ward_code=st.selectbox('自動収集する区',list(TOKYO_WARDS),index=list(TOKYO_WARDS).index(saved.get('ward_code','13116')),format_func=lambda code:TOKYO_WARDS[code],key='automatic_ward')
    town_codes=ward_town_selector(ward_code,'automatic',saved.get('town_codes',[]),disabled=busy)
    st.caption('SUUMOのみで町ごとに最後のページまで検索します。マンションは築15年以内、1LDK/2K/2DK・2LDK/3K/3DK、一戸建て・その他は築40年以内・50㎡以上を対象とします。')
    manual=active_job();manual_busy=bool(manual and not manual.snapshot()['finished'])
    if st.button('選択した区で自動収集を開始・再開',key='automatic_start',disabled=busy or manual_busy):
        try:
            controller=start_automatic_collection(ward_code,town_codes=town_codes);snap=controller.snapshot();busy=True
        except AppError as exc:st.error(str(exc))
    if st.button('自動収集を中止（取得済みデータを保存）',key='automatic_stop',disabled=not busy or bool(snap and snap['stopping'])):
        try:
            controller.request_stop();snap=controller.snapshot()
            st.session_state.automatic_saved_settings=snap['settings'];st.session_state.automatic_saved_summary=snap['summary']
            st.success('停止を要求しました。保存済みデータは保持します。')
        except AppError as exc:st.error('実行の停止は要求しましたが、停止設定の保存に失敗しました：'+str(exc))
    if snap:
        summary=snap['summary'];st.write(snap['message']);st.progress(min(1.,max(0.,snap['progress'])))
        current=snap.get('current') or {};live=snapshot_count_metrics(current)
        if current:st.caption(f"現在｜処理 {int(live.get('processed',0))}件｜保存 {int(live.get('saved',0))}件｜エラー {int(live.get('errors',0))}件｜スキップ {int(live.get('skipped',0))}件")
        cumulative_processed=int(summary.get('processed_observations',0))
        cumulative_saved=int(summary.get('saved_observations',0))
        cumulative_errors=int(summary.get('error_observations',0))
        cumulative_skipped=int(summary.get('skipped_observations',0))
        st.caption(f'累計｜処理 {cumulative_processed}件｜保存 {cumulative_saved}件｜エラー {cumulative_errors}件｜スキップ {cumulative_skipped}件')

        labels=list(summary.get('task_labels') or [p['label'] for p in automatic_task_plan(snap.get('settings') or {})])
        if labels:
            total=len(labels);current_index=int(summary.get('current_task_index',summary.get('next_task_index',0)) or 0)%total
            completed={int(x) for x in summary.get('cycle_completed_indices',[]) if isinstance(x,(int,float)) or str(x).isdigit()}
            completed={i for i in completed if 0<=i<total}
            current_label=labels[current_index] if busy else '停止中'
            st.markdown(f"**検索対象 {total}町｜完了 {len(completed)}町｜現在 {current_label}**")
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

    st.markdown('#### ログ・エラー出力')
    if controller:
        with controller.lock:log_job=controller.current or controller.last_job
        if log_job:
            try:
                cache=st.session_state.get('automatic_log_cache')
                search_id=log_job.audit.search_id;now=time.monotonic()
                stale=not isinstance(cache,dict) or cache.get('search_id')!=search_id or now-float(cache.get('at',0) or 0)>=60 or not busy
                if stale:
                    cache={'search_id':search_id,'count':int(log_job.audit.count),'at':now,'events':log_job.audit.records()}
                    st.session_state.automatic_log_cache=cache
                diagnostic_downloads(cache.get('events') or [],'automatic')
            except Exception as exc:
                st.error('詳細ログを出力できませんでした：'+type(exc).__name__)
        else:
            st.caption('詳細ログは収集開始後に自動で表示されます。')

    try:
        error_job=get_automatic_failure_export(max_age=120,refresh=not busy);failure=error_job.snapshot()
        if failure['loaded']:
            st.download_button(f"全取得エラーCSV（1エラー1行・{len(failure['rows'])}行）",
                               failure['csv'],'sumai_all_acquisition_errors.csv','text/csv',
                               key='automatic_all_errors_csv',on_click='ignore',use_container_width=True)
            status='過去ログも裏で統合中' if failure['running'] and failure.get('phase')=='historical_backfill' else '裏で最新化中' if failure['running'] else '最新'
            st.caption(f"エラーCSV：{status}｜最終同期 {acquisition_time_jst(failure.get('updated_at'))}｜同じ物件で複数エラーがあれば複数行です。")
        else:
            st.download_button('全取得エラーCSV',b'','sumai_all_acquisition_errors.csv','text/csv',
                               key='automatic_all_errors_csv_wait',disabled=True,use_container_width=True)
            st.caption('収集中は取得処理を優先します。エラーCSVは停止・完了後に裏で自動読込します。操作は不要です。')
        if failure['error']:st.error('エラーCSVの自動更新に失敗しました：'+failure['error'])
    except Exception as exc:
        st.error('エラーCSVを出力できませんでした：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))

    st.caption('物件ID・取得成功日時は再取得の管理情報として保存します。詳細住所と募集データの保存に成功した物件だけが取得済みになります。')
    st.caption('蓄積した物件は下の「最新の保存物件を読み込む・反映」で地図へ表示できます。収集中も地図の自動再描画は行いません。')

def main():
    st.set_page_config(page_title='住まいコンパス｜新しい住まいを探す',page_icon='🏡',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(CSS,unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><span class="badge">SUMAI COMPASS · {BUILD}</span><h1>次の住まいを、地図から。</h1><p>掲載物件を確認し、家賃と暮らしやすさを比べます。<br>間取りと家賃帯を、取得できた情報で比較します。</p></div>',unsafe_allow_html=True)
    state=st.session_state
    state.setdefault('new_units',[]);state.setdefault('new_search',None);state.setdefault('new_saved_keys',[])
    state.setdefault('new_facilities',[])
    state.setdefault('new_map_loaded',False);state.setdefault('address_point_cache',{})
    state.setdefault('layout_display_group','group1')
    restore_manual_search()
    restore_automatic_collection()
    request=state.pop('new_request',None)
    if request is not None:
        try: start_search(request)
        except AppError as exc: state.new_search={'status':'failed','summary':{'issues':[str(exc)],'saved':0,'confirmed':0}}
    preferences=state.get('new_preferences',{})
    try:state.current_storage_key=manual_registry_key(Database())
    except Exception:state.current_storage_key=None
    tabs=st.tabs(['住まいを探す','保存データ','通勤・周辺施設','初期設定'])
    with tabs[0]:
        st.subheader('地図を動かして、探す地域を表示してください')
        st.caption('地図に見えている四角い範囲が検索対象です。駅名の選択や取得件数の上限はありません。')
        st.caption('検索中の取得済み物件は「取得済みデータを地図へ反映」で表示できます。進捗と中断ボタンは地図のすぐ下に表示します。')
        st.caption('保存する募集データは家賃・間取り・種別（マンション／一戸建て）・詳細住所（推定）・データ取得日時です。地図画像や緯度経度は保存しません。')
        selected_group=state.get('layout_display_group','group1')
        if selected_group not in ('group1','group2','house'):selected_group='group1';state.layout_display_group='group1'
        g1,g2,g3=st.columns(3)
        if g1.button('1LDK・2K・2DK',key='layout_group1',type='primary' if selected_group=='group1' else 'secondary',use_container_width=True):
            if state.layout_display_group!='group1':state.layout_display_group='group1';st.rerun()
        if g2.button('2LDK・3K・3DK',key='layout_group2',type='primary' if selected_group=='group2' else 'secondary',use_container_width=True):
            if state.layout_display_group!='group2':state.layout_display_group='group2';st.rerun()
        if g3.button('一戸建て',key='layout_group_house',type='primary' if selected_group=='house' else 'secondary',use_container_width=True):
            if state.layout_display_group!='house':state.layout_display_group='house';st.rerun()
        selected_group=state.get('layout_display_group','group1')
        selected_layouts=set(DISPLAY_LAYOUT_GROUPS[selected_group]) if selected_group in DISPLAY_LAYOUT_GROUPS else set()
        display_label={'group1':'1LDK・2K・2DK','group2':'2LDK・3K・3DK','house':'一戸建て'}[selected_group]
        st.caption('表示中：'+display_label+'。保存データ自体は変更せず、地図・件数・一覧だけを切り替えます。')
        bounds=state.get('new_bounds')
        center=bounds_center(bounds) if bounds else state.get('new_view_center',DEFAULT_CENTER)
        radius=bounds_radius(bounds) if bounds else 1500
        if state.get('new_map_loaded'):
            if selected_group=='house':map_units=[r for r in state.new_units if listing_dwelling_type(r)=='house']
            else:map_units=[r for r in state.new_units if listing_dwelling_type(r)!='house' and parsed_layout(r.get('layout')) in selected_layouts]
        else:map_units=[]
        rows,pins,cells,display_report=display_pipeline(map_units,bounds,state.get('new_load_diagnostic'))
        job=active_job()
        if job:
            map_signature=(tuple(bounds) if bounds else None,len(map_units),tuple(display_report['stages'].values()),tuple(display_report['map'].values()))
            if state.get('diagnostic_map_signature')!=map_signature:
                state.diagnostic_map_signature=map_signature
                job.audit.add('map','render','INFO',{'bounds':bounds,'loaded_units':len(map_units),'aggregation':False,'monthly_limit':None,'renderer':'Canvas','display_stages':display_report['stages'],'display_reasons':display_report['reason_counts'],'display_map':display_report['map']})
        facilities=[f for f in state.new_facilities if bounds and in_rectangle((f['lat'],f['lng']),bounds)]
        # Leaflet clips points to its viewport. Keeping every loaded point in the layer
        # lets a pan reveal previously off-screen properties without a full app rerun.
        def legend_chip(color,label,emphasis=False):
            return (f'<span style="display:inline-flex;align-items:center;gap:5px;padding:3px 7px;'
                    f'border-radius:999px;border:{"1.5px" if emphasis else "1px"} solid #c7cec9;'
                    f'background:#ffffff;color:#203b38;font-size:12px;font-weight:{700 if emphasis else 500}">'
                    f'<i style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{color};'
                    f'border:1px solid #ffffff;box-shadow:0 0 0 1px #6f7773"></i>{label}</span>')
        legend=(
            '<div style="display:flex;flex-wrap:wrap;gap:5px 7px;margin:2px 0 5px">'+
            ''.join(legend_chip(color,label,True) for color,label in zip(COLORS[:5],RENT_BAND_LABELS[:5]))+
            '</div><div style="display:flex;flex-wrap:wrap;gap:5px 7px;margin:0 0 8px">'+
            ''.join(legend_chip(color,label,False) for color,label in zip(COLORS[5:],RENT_BAND_LABELS[5:]))+
            '</div>')
        st.markdown(legend,unsafe_allow_html=True)
        interactive_rental_map(map_units,cells,facilities)
        if st.button('現在の範囲の件数・一覧を更新',key='refresh_viewport_summary'):
            st.rerun()
        st.caption('地図を動かすと読み込み済みの物件を表示します。下の件数・一覧は「現在の範囲の件数・一覧を更新」で更新できます。')
        # Fragment owns its widget container directly, including partial reruns.
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
            st.caption('区・町名指定はSUUMOを対象に、マンションは築15年以内・①1LDK/2K/2DK→②2LDK/3K/3DK、一戸建ては築40年以内・50㎡以上を検索・保存します。所在地文字列は使わず、各詳細ページの物件地図から詳細住所を推定します。')
        st.button('表示中の地名から全件検索・保存' if scope=='地図の表示範囲' else '選択した区・町名を検索・保存',type='primary',use_container_width=True,
                  on_click=remember_search,key='new_start',disabled=searching or (scope=='地図の表示範囲' and (not bounds or not providers)))
        st.caption('検索開始時の表示範囲から検索する町名を決めます。SUUMOでは、マンションは築15年以内の2間取り群、一戸建ては築40年以内・50㎡以上を最後のページまで検索します。所在地文字列は住所推定に使わず、各詳細ページの物件地図から位置を取得し、番地まで推定します。住所は実所在地未確認の推定値です。保存は家賃・間取り・種別・詳細住所（推定）・データ取得日時です。')
        background_status()
        result=state.new_search
        if result:
            summary=result['summary'];saved=summary.get('saved',0);confirmed=summary.get('confirmed',0)
            if result.get('conditions',{}).get('mode')=='published_map_position_repair':st.info(f"位置の再確認：{summary.get('position_attempted',0)}件｜位置改善 {summary.get('position_updated',0)}件｜未確認 {summary.get('position_pending',0)}件｜全募集を保持")
            processed=int(summary.get('processed',0));errors=int(summary.get('errors',0));skipped=int(summary.get('already_acquired',0))
            metrics=f'処理 {processed}件｜保存 {saved}件｜エラー {errors}件｜スキップ {skipped}件'
            if result['status']=='completed': st.success('検索完了｜'+metrics)
            elif result['status']=='cancelled': st.info('検索中断｜'+metrics)
            elif result['status']=='partial': st.error('検索終了｜'+metrics)
            else: st.error('検索失敗｜'+metrics)
            for issue in summary.get('issues',[])[:8]: st.write('・'+issue)
            if result.get('conditions',{}).get('regions'):
                with st.expander('今回検索した地名・丁目'):
                    for label in result['conditions']['regions']: st.write(label)
        st.caption('KPIは地図の〇の数です。家賃色を付けて地図上で区別できる座標位置を1個として数え、同一座標への重なりで水増ししません。保存物件件数とは別です。')
        if state.get('new_map_loaded'):display_diagnostic_downloads(display_report)
        c1,c2,c3,c4=st.columns(4)
        c1.metric('地図の〇（KPI）',display_report['map']['colored_unique_positions'])
        c2.metric('現在の範囲の募集',len(rows))
        c3.metric('家賃帯のある募集',display_report['map']['colored_points'])
        c4.metric('位置未確認の募集',display_report['map']['unconfirmed_positions'])
        if not state.get('new_map_loaded'):st.info('取得済みデータは「地図へ反映」で表示します。保存データは「保存データ」から読み込めます。')
        elif not rows:st.info('この表示範囲に配置できる保存データがありません。')
        if rows:
            with st.expander('募集を1件ずつ確認'):
                chosen=st.selectbox('確認する募集',rows,format_func=lambda r:(r.get('title') or '')+'｜'+str(r.get('layout') or '間取り未確認')+'｜'+(f"{monthly_price(r)/10000:g}万円" if r.get('rent') else '家賃未確認')+'｜'+r['key'][:8],key='individual_listing')
                st.markdown(listing_card(chosen,chosen['key'] in state.new_saved_keys),unsafe_allow_html=True)
        if rows:
            with st.expander('取得できた情報をすべて表で見る（未確認も保持）'):
                st.dataframe([{'物件ID':r.get('property_id','未記録'),'種別':'一戸建て' if listing_dwelling_type(r)=='house' else 'マンション','家賃（万円）':float(r['rent'])/10000 if r.get('rent') else None,'間取り':r.get('layout'),'住所（推定）':r.get('address'),'データ取得日時':acquisition_time_jst(r.get('fetched_at'))} for r in physical_units(rows)],hide_index=True)
    with tabs[1]:
        automatic_collection_panel()
        st.subheader('Supabaseに保存した物件')
        st.caption('Supabaseの保存物件を最新状態で読み込みます。読み込み中に自動収集で追加・更新された物件も、完了直前に再確認して反映します。重複する過去形式の保存行は1物件として扱います。')
        if st.button('最新の保存物件を読み込む・反映',key='new_load',use_container_width=True,disabled=bool(active_job() and not active_job().snapshot()['finished'])):
            try:start_saved_load(bounds,state.get('address_point_cache'))
            except AppError as exc:st.error(str(exc))
        saved_load_progress()
        if state.get('new_notice'): st.success(state.new_notice)
        st.caption('DBには家賃・間取り・種別（マンション／一戸建て）・詳細住所（推定）・データ取得日時を保存します。読み込み時に住所を一時的に座標化して地図へ色付けし、その座標は保存しません。')
        st.download_button('現在の物件データをCSVで保存',csv_bytes(state.new_units),'sumai_rebuild_units.csv','text/csv',use_container_width=True)
        upload=st.file_uploader('このアプリのCSVを追加する',type=['csv'])
        if st.button('CSVの物件をSupabaseへ保存',disabled=upload is None or bool(active_job() and not active_job().snapshot()['finished']),key='new_import'):
            try:
                rows=read_csv(upload.getvalue());saved=Database().save_units(rows)
                state.new_map_loaded=False;state.new_units=[];state.new_saved_keys=[]
                state.new_notice=f'CSVから{len(saved)}件を保存しました。地図へ色付けする場合は保存データを読み込んでください。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('画面上の未保存物件を再保存',key='new_retry_save',disabled=not state.new_units or bool(active_job() and not active_job().snapshot()['finished'])):
            try:
                saved=Database().save_units(state.new_units);state.new_saved_keys=list(set(state.new_saved_keys)|saved)
                state.new_notice=f'{len(saved)}件の保存を確認しました。';st.rerun()
            except AppError as exc: st.error(str(exc))
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
    with tabs[2]:
        st.subheader('通勤の目安')
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
            st.caption('登録41駅・11路線による概算。時刻表・直通運転・道路の徒歩経路は使用していません。徒歩は直線距離×1.25、列車は距離から推定、乗り換えは5分です。')
        else: st.info('物件を検索するか、保存物件を読み込むと通勤を比較できます。')
        st.subheader('地図の中心付近の施設')
        kind=st.selectbox('施設の種類',['スーパー','コンビニ','公園','病院'])
        if st.button('地図の中心から1kmの施設を地図に追加',key='new_facilities_fetch',disabled=not bounds):
            try:
                state.new_facilities=fetch_facilities(center,kind);state.new_facility_center=center
                saved=Database().save_places(state.new_facilities,kind)
                state.new_notice=f'{len(saved)}施設を取得し、Supabaseへの保存を確認しました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('保存した周辺施設を読み込む',key='new_places_load'):
            try:
                state.new_facilities=Database().load_places(center,kind);state.new_facility_center=center
                state.new_notice=f'{len(state.new_facilities)}施設を読み込みました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        st.link_button('Googleマップで施設を探す','https://www.google.com/maps/search/?'+urlencode({'api':1,'query':f'{center[0]},{center[1]} '+kind}))
        st.caption('OpenStreetMapに登録された施設。登録漏れや位置の誤差がある場合があります。')
    with tabs[3]:
        st.subheader('新しいアプリの初期設定')
        st.write('1．SupabaseのSQL Editorに、以下のSQLを貼り付けてRunを押してください。')
        st.code(SQL,language='sql')
        st.download_button('セットアップSQLをダウンロード',SQL,'supabase_rebuild_01.sql','text/plain')
        st.write('2．StreamlitのSettings → Secretsに、接続先とサーバー用のSecret keyを設定してください。')
        st.code('SUPABASE_URL = "https://プロジェクトID.supabase.co"\nSUPABASE_SECRET_KEY = "sb_secret_から始まるキー"\nSUPABASE_NAMESPACE = "sumai-compass"',language='toml')
        st.write('物件取得にプロキシを使用する場合は、同じSecretsへ追加してください。Supabase・地名取得には適用しません。')
        st.code('RENTAL_HTTP_PROXIES = ["http://ユーザー:パスワード@ホスト:ポート", "http://別のユーザー:パスワード@別のホスト:ポート"]',language='toml')
        try:st.caption('設定済みプロキシ：'+str(len(rental_network_settings()['proxies']))+'経路')
        except AppError as exc:st.error(str(exc))
        st.caption('追加対策：同じ経路で公開トップページを確認してCookieを引き継ぎ、物件サイトごとに同時通信2件・通信開始は最低1秒間隔で取得します。403の経路とURLは60秒休止し、別の公開条件ページも確認します。')
        st.caption('利用するプロキシサービスの接続URLを設定してください。設定済みの経路で403・空応答・通信失敗が出た場合は別の経路へ切り替えます。認証情報は作業ログへ出力しません。')
        st.write('3．接続確認が成功したら、「住まいを探す」の地図を動かして検索してください。')
        if st.button('新しい保存先の接続を確認',key='new_check'):
            try: Database().check();st.success('物件・検索履歴の新しい保存先に接続できました。')
            except AppError as exc: st.error(str(exc))
        st.caption('旧アプリの物件や検索状態を使用しません。旧テーブルのデータは削除しません。')
        st.caption(f'実行中の版：{BUILD}')
    with st.expander('取得・集計の範囲'):
        st.write('SUUMOのみで検索します。マンションは築15年以内、1LDK/2K/2DKまたは2LDK/3K/3DK。一戸建て・その他は築40年以内・50㎡以上を対象とします。住所は物件固有の地図の座標から推定します。')
        st.write('データベースへ保存する募集項目は家賃・間取り・種別（マンション／一戸建て）・詳細住所（推定）・データ取得日時です。取得日時はUTCで保存し、画面では日本時間で表示します。管理費・面積・築年数・画像・緯度経度・掲載URLは募集データとして保存しません。')
        st.write('募集情報は既存SupabaseのJSON保存領域へ保存するため、追加SQLは不要です。schema 4・5の保存データを読み込みます。旧データの取得日時は未記録と表示します。')
        st.write('SUUMOは物件固有地図の座標を起点に、国土地理院の住居表示住所データから詳細住所を推定します。一覧・詳細ページの所在地文字列から住所や位置を補完しません。')
        st.write('地図範囲内の居住地名タイルと100m間隔の地点・範囲の端から地名・丁目を判定します。候補数・物件数・ページ数による打ち切りは行いません。通信失敗やページ送りの異常は未完了として表示します。掲載サイト側の非公開情報・取得制限や、地名データの欠落は取得できません。')
        st.write('検索中は取得した物件の座標を使って地図へ逐次反映します。保存データ読込時は住所から座標を一時生成します。座標はDBへ保存しません。')
        st.write('検索と保存はサーバーのバックグラウンドで実行し、画面は1秒ごとに進捗を表示します。サーバーの休止・再起動を越えて実行することはできません。募集終了物件の自動削除は行いません。')
        st.caption('地名取得の代替経路：Geolonia Japanese Addresses v2（デジタル庁アドレス・ベース・レジストリ由来、CC BY 4.0）。町代表点を使って近隣自治体の全町丁目を検索します。地名データの欠落や境界の完全な網羅は保証できません。')
        st.link_button('町丁目データの出典・ライセンス','https://github.com/geolonia/japanese-addresses-v2')


if __name__=='__main__':
    main()
